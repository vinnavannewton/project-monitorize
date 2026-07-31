#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <netdb.h>
#include <poll.h>
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
#define CEILING_BYTES_PER_SECOND 25000000ULL
#define MAX_QUEUED_PACKETS 4096

struct packet {
    struct packet *next;
    uint64_t received_ns;
    uint32_t timestamp;
    size_t length;
    int idr;
    unsigned char data[MAX_PACKET];
};

static volatile sig_atomic_t running = 1;
static struct packet *head;
static struct packet *tail;
static size_t queued_packets;
static uint64_t next_send_ns;
static uint64_t interval_bytes;
static uint64_t interval_packets;
static uint64_t interval_drops;
static uint64_t interval_errors;
static uint32_t last_hard_drop_timestamp;
static int have_hard_drop_timestamp;

static uint64_t monotonic_ns(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000000000ULL + (uint64_t)now.tv_nsec;
}

static void stop_signal(int unused) {
    (void)unused;
    running = 0;
}

static int parse_rtp(const unsigned char *data, size_t length, uint32_t *timestamp,
                     int *idr) {
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
    *idr = 0;
    int payload_type = data[1] & 0x7f;
    if (payload_type == RTP_MEDIA_PT) {
        int nal_type = data[offset] & 0x1f;
        if (nal_type == 5) *idr = 1;
        if (nal_type == 28 && offset + 1 < length && (data[offset + 1] & 0x1f) == 5)
            *idr = 1;
    }
    return payload_type == RTP_MEDIA_PT || payload_type == RTP_FEC_PT;
}

static void flush_queue(void) {
    while (head) {
        struct packet *next = head->next;
        free(head);
        head = next;
    }
    tail = NULL;
    queued_packets = 0;
}

static int timestamp_is_idr(uint32_t timestamp) {
    for (struct packet *packet = head; packet; packet = packet->next)
        if (packet->timestamp == timestamp && packet->idr) return 1;
    return 0;
}

static size_t distinct_timestamps(void) {
    size_t count = 0;
    uint32_t previous = 0;
    int have_previous = 0;
    for (struct packet *packet = head; packet; packet = packet->next) {
        if (!have_previous || packet->timestamp != previous) {
            count++;
            previous = packet->timestamp;
            have_previous = 1;
        }
    }
    return count;
}

static int drop_oldest_frame(uint32_t protected_timestamp) {
    uint32_t target = 0;
    int found = 0;
    for (struct packet *packet = head; packet; packet = packet->next) {
        if (packet->timestamp != protected_timestamp && !timestamp_is_idr(packet->timestamp)) {
            target = packet->timestamp;
            found = 1;
            break;
        }
    }
    if (!found) return 0;

    struct packet **link = &head;
    while (*link) {
        struct packet *packet = *link;
        if (packet->timestamp == target) {
            *link = packet->next;
            if (tail == packet) tail = NULL;
            free(packet);
            queued_packets--;
        } else {
            if (!packet->next) tail = packet;
            link = &packet->next;
        }
    }
    if (!head) tail = NULL;
    interval_drops++;
    printf("DROP timestamp=%u reason=queue-overflow\n", target);
    fflush(stdout);
    return 1;
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

static int send_due(int socket_fd, uint32_t *started_timestamp) {
    if (!head) return 0;
    uint64_t now = monotonic_ns();
    if (next_send_ns > now) {
        struct timespec deadline = {
            .tv_sec = (time_t)(next_send_ns / 1000000000ULL),
            .tv_nsec = (long)(next_send_ns % 1000000000ULL),
        };
        while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &deadline, NULL) == EINTR) {}
        now = monotonic_ns();
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
        return 0;
    }
    size_t sent_bytes = 0;
    for (int index = 0; index < sent; index++) {
        struct packet *done = head;
        head = done->next;
        *started_timestamp = done->timestamp;
        sent_bytes += done->length + 28;
        interval_bytes += done->length + 28;
        interval_packets++;
        queued_packets--;
        free(done);
    }
    if (!head) tail = NULL;
    next_send_ns = (next_send_ns > now ? next_send_ns : now) +
        sent_bytes * 1000000000ULL / CEILING_BYTES_PER_SECOND;
    return sent;
}

static int enqueue_packet(int input_fd, uint32_t protected_timestamp, uint64_t frame_ns) {
    struct packet *packet = calloc(1, sizeof(*packet));
    if (!packet) return -1;
    ssize_t length = recv(input_fd, packet->data, sizeof(packet->data), 0);
    if (length < 0) {
        free(packet);
        return errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR ? 0 : -1;
    }
    if (!parse_rtp(packet->data, (size_t)length, &packet->timestamp, &packet->idr)) {
        free(packet);
        return 0;
    }
    packet->length = (size_t)length;
    packet->received_ns = monotonic_ns();
    if (queued_packets >= MAX_QUEUED_PACKETS) {
        if (!have_hard_drop_timestamp || last_hard_drop_timestamp != packet->timestamp) {
            last_hard_drop_timestamp = packet->timestamp;
            have_hard_drop_timestamp = 1;
            interval_drops++;
            printf("DROP timestamp=%u reason=hard-queue-limit\n", packet->timestamp);
            fflush(stdout);
        }
        free(packet);
        return 1;
    }
    if (tail) tail->next = packet;
    else head = packet;
    tail = packet;
    queued_packets++;

    while (head && (distinct_timestamps() > 2 ||
           monotonic_ns() - head->received_ns > frame_ns * 2)) {
        if (!drop_oldest_frame(protected_timestamp)) break;
    }
    return 1;
}

static void report_stats(uint64_t now, uint64_t *last_report_ns) {
    uint64_t elapsed = now - *last_report_ns;
    if (elapsed < 1000000000ULL) return;
    double queue_delay_ms = head ? (double)(now - head->received_ns) / 1000000.0 : 0.0;
    printf("STAT txKbps=%.0f txPps=%.1f queuePackets=%zu queueDelayMs=%.2f "
           "droppedFrames=%llu sendErrors=%llu ceilingKbps=200000\n",
           (double)interval_bytes * 8.0 * 1000000.0 / (double)elapsed,
           (double)interval_packets * 1000000000.0 / (double)elapsed,
           queued_packets, queue_delay_ms,
           (unsigned long long)interval_drops,
           (unsigned long long)interval_errors);
    fflush(stdout);
    interval_bytes = interval_packets = interval_drops = interval_errors = 0;
    *last_report_ns = now;
}

int main(int argc, char **argv) {
    if (argc != 6) {
        fprintf(stderr, "Usage: %s BIND_PORT DEST_HOST DEST_PORT FPS SEND_BUFFER\n", argv[0]);
        return 2;
    }
    int bind_port = atoi(argv[1]);
    int fps = atoi(argv[4]);
    int send_buffer = atoi(argv[5]);
    if (bind_port < 1 || bind_port > 65535 || fps < 1 || fps > 240 ||
        send_buffer < 262144 || send_buffer > 2097152) return 2;

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
    int traffic_class = 0xc0;
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
    printf("READY inputPort=%u ceilingKbps=200000\n", ntohs(input_address.sin_port));

    uint64_t frame_ns = 1000000000ULL / (uint64_t)fps;
    uint64_t last_report_ns = monotonic_ns();
    uint32_t started_timestamp = UINT32_MAX;
    char command[512];
    while (running) {
        uint64_t now = monotonic_ns();
        int timeout_ms = head && next_send_ns > now
            ? (int)((next_send_ns - now + 999999ULL) / 1000000ULL) : 100;
        if (head && next_send_ns <= now) timeout_ms = 0;
        struct pollfd descriptors[2] = {
            {.fd = input_fd, .events = POLLIN},
            {.fd = STDIN_FILENO, .events = POLLIN},
        };
        int ready = poll(descriptors, 2, timeout_ms);
        if (ready < 0 && errno != EINTR) break;
        if (descriptors[1].revents & POLLIN) {
            if (!fgets(command, sizeof(command), stdin)) running = 0;
            else if (!strncmp(command, "DEST ", 5)) {
                char host[256], port[16];
                if (sscanf(command + 5, "%255s %15s", host, port) == 2 &&
                    resolve_destination(output_fd, host, port) == 0) {
                    flush_queue();
                    next_send_ns = monotonic_ns();
                    started_timestamp = UINT32_MAX;
                    printf("DEST host=%s port=%s\n", host, port);
                } else {
                    printf("ERROR invalid destination\n");
                }
            } else if (!strncmp(command, "QUIT", 4)) running = 0;
        }
        if (descriptors[0].revents & POLLIN) {
            while (enqueue_packet(input_fd, started_timestamp, frame_ns) > 0) {}
        }
        while (send_due(output_fd, &started_timestamp) > 0 &&
               next_send_ns <= monotonic_ns()) {}
        report_stats(monotonic_ns(), &last_report_ns);
    }

    flush_queue();
    close(input_fd);
    close(output_fd);
    return 0;
}
