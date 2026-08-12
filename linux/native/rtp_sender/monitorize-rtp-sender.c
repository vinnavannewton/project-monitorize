#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/prctl.h>
#include <time.h>
#include <unistd.h>

#define MAX_PACKET 2048
#define MAX_BATCH 16
#define RTP_MEDIA_PT 96
#define RTP_FEC_PT 122
#define MAX_QUEUED_PACKETS 512
#define MAX_QUEUED_FRAMES 3

struct packet {
    struct packet *next;
    uint64_t received_ns;
    uint32_t timestamp;
    size_t length;
    unsigned char data[MAX_PACKET];
};

static volatile sig_atomic_t running = 1;
static struct packet *head;
static struct packet *tail;
static size_t queued_packets;
static uint64_t next_send_ns;
static uint64_t pacing_bytes_per_second;
static uint64_t interval_bytes;
static uint64_t interval_packets;
static uint64_t interval_drops;
static uint64_t interval_errors;
static pthread_mutex_t queue_mutex = PTHREAD_MUTEX_INITIALIZER;
static int notify_write_fd = -1;

static uint64_t monotonic_ns(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000000000ULL + (uint64_t)now.tv_nsec;
}

static void stop_signal(int unused) {
    (void)unused;
    running = 0;
    if (notify_write_fd >= 0) {
        const unsigned char byte = 1;
        (void)write(notify_write_fd, &byte, 1);
    }
}

static int parse_rtp(const unsigned char *data, size_t length, uint32_t *timestamp) {
    if (length < 12 || (data[0] >> 6) != 2) return 0;
    size_t offset = 12 + (size_t)(data[0] & 0x0f) * 4;
    if (data[0] & 0x10) {
        if (offset + 4 > length) return 0;
        uint16_t extension_words;
        memcpy(&extension_words, data + offset + 2, sizeof(extension_words));
        size_t extension = 4 + (size_t)ntohs(extension_words) * 4;
        if (offset + extension > length) return 0;
        offset += extension;
    }
    if (offset >= length) return 0;
    memcpy(timestamp, data + 4, sizeof(*timestamp));
    *timestamp = ntohl(*timestamp);
    int payload_type = data[1] & 0x7f;
    return payload_type == RTP_MEDIA_PT || payload_type == RTP_FEC_PT;
}

static void flush_queue_locked(void) {
    while (head) {
        struct packet *next = head->next;
        free(head);
        head = next;
    }
    tail = NULL;
    queued_packets = 0;
}

static size_t count_queued_frames_locked(void) {
    if (!head) return 0;
    size_t count = 1;
    uint32_t ts = head->timestamp;
    for (struct packet *p = head->next; p; p = p->next) {
        if (p->timestamp != ts) {
            count++;
            ts = p->timestamp;
        }
    }
    return count;
}

static void drop_oldest_frame_locked(void) {
    if (!head) return;
    uint32_t ts = head->timestamp;
    size_t dropped = 0;
    while (head && head->timestamp == ts) {
        struct packet *next = head->next;
        free(head);
        head = next;
        queued_packets--;
        dropped++;
    }
    if (!head) tail = NULL;
    interval_drops++;
    printf("DROP timestamp=%u reason=stale-frame packets=%zu queued=%zu\n",
           ts, dropped, queued_packets);
    fflush(stdout);
}

static int resolve_destination(int socket_fd, const char *host, const char *port) {
    struct addrinfo hints = {0};
    struct addrinfo *addresses = NULL;
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_DGRAM;
    int error = getaddrinfo(host, port, &hints, &addresses);
    if (error != 0) return -1;
    int result = connect(socket_fd, addresses->ai_addr, addresses->ai_addrlen);
    freeaddrinfo(addresses);
    return result;
}

static int send_due(int socket_fd) {
    pthread_mutex_lock(&queue_mutex);
    if (!head) {
        pthread_mutex_unlock(&queue_mutex);
        return 0;
    }
    uint64_t now = monotonic_ns();
    uint64_t deadline_ns = next_send_ns;
    pthread_mutex_unlock(&queue_mutex);
    if (deadline_ns > now) {
        struct timespec deadline = {
            .tv_sec = (time_t)(deadline_ns / 1000000000ULL),
            .tv_nsec = (long)(deadline_ns % 1000000000ULL),
        };
        while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &deadline, NULL) == EINTR) {}
        now = monotonic_ns();
    }

    pthread_mutex_lock(&queue_mutex);
    if (!head) {
        pthread_mutex_unlock(&queue_mutex);
        return 0;
    }
    struct mmsghdr messages[MAX_BATCH] = {0};
    struct iovec vectors[MAX_BATCH] = {0};
    struct packet *packet = head;
    unsigned count = 0;
    while (packet && count < MAX_BATCH) {
        vectors[count].iov_base = packet->data;
        vectors[count].iov_len = packet->length;
        messages[count].msg_hdr.msg_iov = &vectors[count];
        messages[count].msg_hdr.msg_iovlen = 1;
        packet = packet->next;
        count++;
    }

    int sent = sendmmsg(socket_fd, messages, count, MSG_DONTWAIT);
    if (sent < 0) {
        if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
            interval_errors++;
            fprintf(stderr, "ERROR send failed: %s\n", strerror(errno));
        }
        pthread_mutex_unlock(&queue_mutex);
        return 0;
    }
    size_t sent_bytes = 0;
    for (int index = 0; index < sent; index++) {
        struct packet *done = head;
        head = done->next;
        sent_bytes += done->length + 28;
        interval_bytes += done->length + 28;
        interval_packets++;
        queued_packets--;
        free(done);
    }
    if (!head) tail = NULL;
    next_send_ns = (next_send_ns > now ? next_send_ns : now) +
        sent_bytes * 1000000000ULL / pacing_bytes_per_second;
    pthread_mutex_unlock(&queue_mutex);
    return sent;
}

static int enqueue_packet(int input_fd) {
    struct packet *packet = calloc(1, sizeof(*packet));
    if (!packet) return -1;
    ssize_t length = recv(input_fd, packet->data, sizeof(packet->data), 0);
    if (length < 0) {
        free(packet);
        return errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR ? 0 : -1;
    }
    if (!parse_rtp(packet->data, (size_t)length, &packet->timestamp)) {
        free(packet);
        return 0;
    }
    packet->length = (size_t)length;
    packet->received_ns = monotonic_ns();
    pthread_mutex_lock(&queue_mutex);

    /* Frame-aware dropping: when a new frame starts arriving, ensure the
     * queue holds at most MAX_QUEUED_FRAMES by evicting the oldest. */
    if (!tail || packet->timestamp != tail->timestamp) {
        size_t frames = count_queued_frames_locked();
        while (frames >= MAX_QUEUED_FRAMES) {
            drop_oldest_frame_locked();
            next_send_ns = monotonic_ns();
            frames--;
        }
    }

    /* Safety net: hard-flush if packets grow extreme despite frame dropping. */
    if (queued_packets >= MAX_QUEUED_PACKETS) {
        flush_queue_locked();
        next_send_ns = monotonic_ns();
        interval_drops++;
        printf("DROP timestamp=%u reason=safety-flush\n", packet->timestamp);
        fflush(stdout);
    }

    if (tail) tail->next = packet;
    else head = packet;
    tail = packet;
    queued_packets++;
    pthread_mutex_unlock(&queue_mutex);

    if (notify_write_fd >= 0) {
        const unsigned char byte = 1;
        (void)write(notify_write_fd, &byte, 1);
    }

    return 1;
}

struct receiver_args {
    int input_fd;
};

static void *receive_loop(void *opaque) {
    const struct receiver_args *args = opaque;
    while (running) {
        struct pollfd descriptor = {.fd = args->input_fd, .events = POLLIN};
        int ready = poll(&descriptor, 1, 100);
        if (ready < 0 && errno != EINTR) break;
        if (descriptor.revents & POLLIN) {
            while (enqueue_packet(args->input_fd) > 0) {}
        }
    }
    return NULL;
}

static void report_stats(uint64_t now, uint64_t *last_report_ns) {
    uint64_t elapsed = now - *last_report_ns;
    if (elapsed < 1000000000ULL) return;
    pthread_mutex_lock(&queue_mutex);
    double queue_delay_ms = head ? (double)(now - head->received_ns) / 1000000.0 : 0.0;
    printf("STAT txKbps=%.0f txPps=%.1f queuePackets=%zu queueDelayMs=%.2f "
           "droppedFrames=%llu sendErrors=%llu pacingKbps=%llu\n",
           (double)interval_bytes * 8.0 * 1000000.0 / (double)elapsed,
           (double)interval_packets * 1000000000.0 / (double)elapsed,
           queued_packets, queue_delay_ms,
           (unsigned long long)interval_drops,
           (unsigned long long)interval_errors,
           (unsigned long long)(pacing_bytes_per_second * 8 / 1000));
    fflush(stdout);
    interval_bytes = interval_packets = interval_drops = interval_errors = 0;
    pthread_mutex_unlock(&queue_mutex);
    *last_report_ns = now;
}

int main(int argc, char **argv) {
    if (argc != 7) {
        fprintf(stderr, "Usage: %s BIND_PORT DEST_HOST DEST_PORT FPS SEND_BUFFER PACING_KBPS\n", argv[0]);
        return 2;
    }
    int bind_port = atoi(argv[1]);
    int fps = atoi(argv[4]);
    int send_buffer = atoi(argv[5]);
    unsigned long long pacing_kbps = strtoull(argv[6], NULL, 10);
    if (bind_port < 1 || bind_port > 65535 || fps < 1 || fps > 240 ||
        send_buffer < 262144 || send_buffer > 2097152 ||
        pacing_kbps < 1000 || pacing_kbps > 250000) return 2;
    pacing_bytes_per_second = pacing_kbps * 1000 / 8;
    signal(SIGINT, stop_signal);
    signal(SIGTERM, stop_signal);
    if (prctl(PR_SET_PDEATHSIG, SIGTERM) < 0 || getppid() == 1) return 1;
    setvbuf(stdout, NULL, _IOLBF, 0);

    int input_fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    int output_fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (input_fd < 0 || output_fd < 0) {
        fprintf(stderr, "ERROR socket creation failed: %s\n", strerror(errno));
        return 1;
    }
    /* Match Sunshine's video class (DSCP 40); DSCP 48 is its audio class. */
    int traffic_class = 40 << 2;
    setsockopt(output_fd, IPPROTO_IP, IP_TOS, &traffic_class, sizeof(traffic_class));
    int receive_buffer = 2 * 1024 * 1024;
    setsockopt(input_fd, SOL_SOCKET, SO_RCVBUF, &receive_buffer, sizeof(receive_buffer));
    setsockopt(output_fd, SOL_SOCKET, SO_SNDBUF, &send_buffer, sizeof(send_buffer));

    struct sockaddr_in input_address = {0};
    input_address.sin_family = AF_INET;
    input_address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(input_fd, (struct sockaddr *)&input_address, sizeof(input_address)) < 0) {
        fprintf(stderr, "ERROR input bind failed: %s\n", strerror(errno));
        return 1;
    }
    struct sockaddr_in output_address = {0};
    output_address.sin_family = AF_INET;
    output_address.sin_addr.s_addr = htonl(INADDR_ANY);
    output_address.sin_port = htons((uint16_t)bind_port);
    if (bind(output_fd, (struct sockaddr *)&output_address, sizeof(output_address)) < 0 ||
        resolve_destination(output_fd, argv[2], argv[3]) < 0) {
        fprintf(stderr, "ERROR output setup failed: %s\n", strerror(errno));
        return 1;
    }

    socklen_t address_length = sizeof(input_address);
    getsockname(input_fd, (struct sockaddr *)&input_address, &address_length);
    int notify_pipe[2];
    if (pipe2(notify_pipe, O_CLOEXEC | O_NONBLOCK) < 0) {
        fprintf(stderr, "ERROR notification pipe failed: %s\n", strerror(errno));
        return 1;
    }
    notify_write_fd = notify_pipe[1];
    struct receiver_args receiver_args = {.input_fd = input_fd};
    pthread_t receiver_thread;
    if (pthread_create(&receiver_thread, NULL, receive_loop, &receiver_args) != 0) {
        fprintf(stderr, "ERROR receiver thread failed\n");
        return 1;
    }
    printf("READY inputPort=%u pacingKbps=%llu\n", ntohs(input_address.sin_port),
           pacing_kbps);

    uint64_t last_report_ns = monotonic_ns();
    char command[512];
    while (running) {
        pthread_mutex_lock(&queue_mutex);
        int timeout_ms = head ? 0 : 100;
        pthread_mutex_unlock(&queue_mutex);
        struct pollfd descriptors[2] = {
            {.fd = STDIN_FILENO, .events = POLLIN},
            {.fd = notify_pipe[0], .events = POLLIN},
        };
        int ready = poll(descriptors, 2, timeout_ms);
        if (ready < 0 && errno != EINTR) break;
        if (descriptors[0].revents & POLLIN) {
            if (!fgets(command, sizeof(command), stdin)) running = 0;
            else if (!strncmp(command, "DEST ", 5)) {
                char host[256], port[16];
                if (sscanf(command + 5, "%255s %15s", host, port) == 2 &&
                    resolve_destination(output_fd, host, port) == 0) {
                    pthread_mutex_lock(&queue_mutex);
                    flush_queue_locked();
                    next_send_ns = monotonic_ns();
                    pthread_mutex_unlock(&queue_mutex);
                    printf("DEST host=%s port=%s\n", host, port);
                } else {
                    printf("ERROR invalid destination\n");
                }
            } else if (!strncmp(command, "QUIT", 4)) running = 0;
            else if (!strncmp(command, "FLUSH", 5)) {
                pthread_mutex_lock(&queue_mutex);
                flush_queue_locked();
                next_send_ns = monotonic_ns();
                pthread_mutex_unlock(&queue_mutex);
                printf("FLUSH\n");
            } else if (!strncmp(command, "RATE ", 5)) {
                unsigned long long pacing_kbps;
                if (sscanf(command + 5, "%llu", &pacing_kbps) == 1 &&
                    pacing_kbps >= 1000 && pacing_kbps <= 250000) {
                    pthread_mutex_lock(&queue_mutex);
                    flush_queue_locked();
                    next_send_ns = monotonic_ns();
                    pacing_bytes_per_second = pacing_kbps * 1000 / 8;
                    pthread_mutex_unlock(&queue_mutex);
                    printf("RATE pacingKbps=%llu\n", pacing_kbps);
                } else {
                    printf("ERROR invalid rate\n");
                }
            }
        }
        if (descriptors[1].revents & POLLIN) {
            unsigned char bytes[64];
            while (read(notify_pipe[0], bytes, sizeof(bytes)) > 0) {}
        }
        send_due(output_fd);
        report_stats(monotonic_ns(), &last_report_ns);
    }

    running = 0;
    pthread_join(receiver_thread, NULL);
    pthread_mutex_lock(&queue_mutex);
    flush_queue_locked();
    pthread_mutex_unlock(&queue_mutex);
    notify_write_fd = -1;
    close(notify_pipe[0]);
    close(notify_pipe[1]);
    close(input_fd);
    close(output_fd);
    return 0;
}
