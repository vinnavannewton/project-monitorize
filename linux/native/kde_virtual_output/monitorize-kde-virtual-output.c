#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <inttypes.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <wayland-client.h>

#include "zkde-screencast-unstable-v1-client-protocol.h"

enum {
    CURSOR_HIDDEN = 1,
    CURSOR_EMBEDDED = 2,
};

struct app_state;

struct stream_state {
    struct app_state *app;
    struct zkde_screencast_stream_unstable_v1 *proxy;
    uint32_t node_id;
    uint64_t object_serial;
    bool ready;
    bool failed;
    bool closed;
    bool reported;
    char error[256];
};

struct output_state {
    struct app_state *app;
    struct wl_output *proxy;
    uint32_t registry_name;
    char *name;
    struct output_state *next;
};

struct app_state {
    struct wl_display *display;
    struct wl_registry *registry;
    struct zkde_screencast_unstable_v1 *screencast;
    uint32_t screencast_version;
    struct output_state *outputs;
    struct output_state *target;
    struct stream_state owner;
    struct stream_state capture;
    const char *base_name;
    const char *description;
    char output_name[256];
    int width;
    int height;
};

static volatile sig_atomic_t running = 1;

static void handle_signal(int signum)
{
    (void)signum;
    running = 0;
}

static void print_json_string(const char *value)
{
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)(value ? value : ""); *p; ++p) {
        switch (*p) {
        case '"':
            fputs("\\\"", stdout);
            break;
        case '\\':
            fputs("\\\\", stdout);
            break;
        case '\n':
            fputs("\\n", stdout);
            break;
        case '\r':
            fputs("\\r", stdout);
            break;
        case '\t':
            fputs("\\t", stdout);
            break;
        default:
            if (*p < 0x20) {
                printf("\\u%04x", *p);
            } else {
                putchar(*p);
            }
        }
    }
    putchar('"');
}

static void emit_error(const char *message)
{
    fputs("{\"event\":\"error\",\"message\":", stdout);
    print_json_string(message);
    fputs("}\n", stdout);
    fflush(stdout);
}

static void emit_ready(const char *event, const struct app_state *app,
                       const struct stream_state *stream)
{
    printf("{\"event\":\"%s\",\"name\":", event);
    print_json_string(app->output_name);
    printf(",\"node_id\":%" PRIu32 ",\"target_object\":", stream->node_id);
    if (stream->object_serial) {
        printf("\"%" PRIu64 "\"", stream->object_serial);
    } else {
        fputs("null", stdout);
    }
    fputs("}\n", stdout);
    fflush(stdout);
}

static void stream_closed(void *data,
                          struct zkde_screencast_stream_unstable_v1 *stream)
{
    (void)stream;
    ((struct stream_state *)data)->closed = true;
}

static void stream_created(void *data,
                           struct zkde_screencast_stream_unstable_v1 *stream,
                           uint32_t node)
{
    (void)stream;
    struct stream_state *state = data;
    state->node_id = node;
    state->ready = true;
}

static void stream_failed(void *data,
                          struct zkde_screencast_stream_unstable_v1 *stream,
                          const char *error)
{
    (void)stream;
    struct stream_state *state = data;
    state->failed = true;
    snprintf(state->error, sizeof(state->error), "%s", error ? error : "KWin stream failed");
}

static void stream_serial(void *data,
                          struct zkde_screencast_stream_unstable_v1 *stream,
                          uint32_t high, uint32_t low)
{
    (void)stream;
    ((struct stream_state *)data)->object_serial = ((uint64_t)high << 32) | low;
}

static const struct zkde_screencast_stream_unstable_v1_listener stream_listener = {
    .closed = stream_closed,
    .created = stream_created,
    .failed = stream_failed,
    .serial = stream_serial,
};

static void output_geometry(void *data, struct wl_output *output, int32_t x, int32_t y,
                            int32_t physical_width, int32_t physical_height,
                            int32_t subpixel, const char *make, const char *model,
                            int32_t transform)
{
    (void)data;
    (void)output;
    (void)x;
    (void)y;
    (void)physical_width;
    (void)physical_height;
    (void)subpixel;
    (void)make;
    (void)model;
    (void)transform;
}

static void output_mode(void *data, struct wl_output *output, uint32_t flags,
                        int32_t width, int32_t height, int32_t refresh)
{
    (void)data;
    (void)output;
    (void)flags;
    (void)width;
    (void)height;
    (void)refresh;
}

static void output_done(void *data, struct wl_output *output)
{
    (void)data;
    (void)output;
}

static void output_scale(void *data, struct wl_output *output, int32_t factor)
{
    (void)data;
    (void)output;
    (void)factor;
}

static void output_name(void *data, struct wl_output *output, const char *name)
{
    (void)output;
    struct output_state *state = data;
    free(state->name);
    state->name = strdup(name ? name : "");
    if (state->name && strcmp(state->name, state->app->output_name) == 0) {
        state->app->target = state;
    }
}

static void output_description(void *data, struct wl_output *output,
                               const char *description)
{
    (void)data;
    (void)output;
    (void)description;
}

static const struct wl_output_listener output_listener = {
    .geometry = output_geometry,
    .mode = output_mode,
    .done = output_done,
    .scale = output_scale,
    .name = output_name,
    .description = output_description,
};

static void registry_global(void *data, struct wl_registry *registry, uint32_t name,
                            const char *interface, uint32_t version)
{
    struct app_state *app = data;
    if (strcmp(interface, zkde_screencast_unstable_v1_interface.name) == 0) {
        uint32_t bind_version = version < 6 ? version : 6;
        app->screencast = wl_registry_bind(
            registry, name, &zkde_screencast_unstable_v1_interface, bind_version);
        app->screencast_version = bind_version;
        return;
    }
    if (strcmp(interface, wl_output_interface.name) != 0 || version < 4) {
        return;
    }
    struct output_state *output = calloc(1, sizeof(*output));
    if (!output) {
        return;
    }
    output->app = app;
    output->registry_name = name;
    output->proxy = wl_registry_bind(registry, name, &wl_output_interface, 4);
    output->next = app->outputs;
    app->outputs = output;
    wl_output_add_listener(output->proxy, &output_listener, output);
}

static void registry_global_remove(void *data, struct wl_registry *registry,
                                   uint32_t name)
{
    (void)registry;
    struct app_state *app = data;
    struct output_state **link = &app->outputs;
    while (*link) {
        struct output_state *output = *link;
        if (output->registry_name != name) {
            link = &output->next;
            continue;
        }
        if (app->target == output) {
            app->target = NULL;
        }
        *link = output->next;
        wl_output_destroy(output->proxy);
        free(output->name);
        free(output);
        return;
    }
}

static const struct wl_registry_listener registry_listener = {
    .global = registry_global,
    .global_remove = registry_global_remove,
};

static int parse_positive_int(const char *value, const char *label)
{
    char *end = NULL;
    long parsed = strtol(value, &end, 10);
    if (!end || *end != '\0' || parsed <= 0 || parsed > INT32_MAX) {
        fprintf(stderr, "Invalid %s: %s\n", label, value);
        return -1;
    }
    return (int)parsed;
}

static bool valid_name(const char *value)
{
    size_t length = strlen(value);
    if (!length || length > 200) {
        return false;
    }
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p) {
        if ((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z')
            || (*p >= '0' && *p <= '9') || strchr("._-", *p)) {
            continue;
        }
        return false;
    }
    return true;
}

static int dispatch_once(struct app_state *app)
{
    int result = wl_display_dispatch(app->display);
    return result < 0 && errno == EINTR ? 0 : result;
}

static void start_capture(struct app_state *app)
{
    if (app->capture.proxy || !app->target) {
        return;
    }
    app->capture.proxy = zkde_screencast_unstable_v1_stream_output(
        app->screencast, app->target->proxy, CURSOR_EMBEDDED);
    zkde_screencast_stream_unstable_v1_add_listener(
        app->capture.proxy, &stream_listener, &app->capture);
    wl_display_flush(app->display);
}

static int run_control_loop(struct app_state *app)
{
    const int wayland_fd = wl_display_get_fd(app->display);
    char command[64];
    while (running) {
        if (app->owner.failed || app->capture.failed) {
            emit_error(app->owner.failed ? app->owner.error : app->capture.error);
            return 1;
        }
        if (app->owner.closed || (app->capture.proxy && app->capture.closed)) {
            emit_error("KWin closed the native screencast stream");
            return 1;
        }
        if (app->capture.ready && !app->capture.reported) {
            app->capture.reported = true;
            emit_ready("capture_ready", app, &app->capture);
        }

        if (wl_display_dispatch_pending(app->display) < 0) {
            emit_error("Wayland dispatch failed");
            return 1;
        }
        if (wl_display_flush(app->display) < 0 && errno != EAGAIN) {
            emit_error("Wayland flush failed");
            return 1;
        }

        struct pollfd fds[] = {
            {.fd = wayland_fd, .events = POLLIN},
            {.fd = STDIN_FILENO, .events = POLLIN},
        };
        int result = poll(fds, 2, -1);
        if (result < 0) {
            if (errno == EINTR) {
                continue;
            }
            emit_error("Wayland poll failed");
            return 1;
        }
        if (fds[0].revents & (POLLERR | POLLHUP | POLLNVAL)) {
            emit_error("KWin Wayland connection closed");
            return 1;
        }
        if (fds[0].revents & POLLIN && dispatch_once(app) < 0) {
            emit_error("Wayland dispatch failed");
            return 1;
        }
        if (fds[1].revents & POLLIN) {
            if (!fgets(command, sizeof(command), stdin)) {
                return 0;
            }
            command[strcspn(command, "\r\n")] = '\0';
            if (strcmp(command, "capture") == 0) {
                if (!app->target) {
                    emit_error("The Monitorize virtual output disappeared");
                    return 1;
                }
                start_capture(app);
            } else if (strcmp(command, "quit") == 0) {
                return 0;
            }
        }
        if (fds[1].revents & (POLLHUP | POLLERR | POLLNVAL)) {
            return 0;
        }
    }
    return 0;
}

static void close_stream(struct stream_state *stream)
{
    if (!stream->proxy) {
        return;
    }
    zkde_screencast_stream_unstable_v1_close(stream->proxy);
    stream->proxy = NULL;
}

static void cleanup(struct app_state *app)
{
    close_stream(&app->capture);
    close_stream(&app->owner);
    if (app->screencast) {
        zkde_screencast_unstable_v1_destroy(app->screencast);
        app->screencast = NULL;
    }
    if (app->display) {
        wl_display_flush(app->display);
    }
    while (app->outputs) {
        struct output_state *output = app->outputs;
        app->outputs = output->next;
        wl_output_destroy(output->proxy);
        free(output->name);
        free(output);
    }
    if (app->registry) {
        wl_registry_destroy(app->registry);
    }
    if (app->display) {
        wl_display_disconnect(app->display);
    }
}

int main(int argc, char **argv)
{
    if (argc != 5) {
        fprintf(stderr, "Usage: %s NAME DESCRIPTION WIDTH HEIGHT\n", argv[0]);
        return 2;
    }

    struct app_state app = {0};
    app.base_name = argv[1];
    app.description = argv[2];
    app.width = parse_positive_int(argv[3], "width");
    app.height = parse_positive_int(argv[4], "height");
    app.owner.app = &app;
    app.capture.app = &app;
    if (!valid_name(app.base_name) || app.width < 0 || app.height < 0
        || snprintf(app.output_name, sizeof(app.output_name), "Virtual-%s", app.base_name)
            >= (int)sizeof(app.output_name)) {
        fprintf(stderr, "Invalid virtual output arguments\n");
        return 2;
    }

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);
    signal(SIGPIPE, SIG_IGN);

    app.display = wl_display_connect(NULL);
    if (!app.display) {
        emit_error("Could not connect to the KDE Wayland session");
        return 2;
    }
    app.registry = wl_display_get_registry(app.display);
    wl_registry_add_listener(app.registry, &registry_listener, &app);
    if (wl_display_roundtrip(app.display) < 0 || !app.screencast) {
        emit_error("KWin did not expose zkde_screencast_unstable_v1; reinstall the authorized Monitorize helper");
        cleanup(&app);
        return 2;
    }
    if (app.screencast_version < 4) {
        emit_error("KWin's native screencast protocol is too old");
        cleanup(&app);
        return 2;
    }

    app.owner.proxy =
        zkde_screencast_unstable_v1_stream_virtual_output_with_description(
            app.screencast, app.base_name, app.description,
            app.width, app.height, wl_fixed_from_double(1.0), CURSOR_HIDDEN);
    zkde_screencast_stream_unstable_v1_add_listener(
        app.owner.proxy, &stream_listener, &app.owner);
    wl_display_flush(app.display);

    while (running && !app.owner.failed && !app.owner.closed
           && (!app.owner.ready || !app.target)) {
        if (dispatch_once(&app) < 0) {
            app.owner.failed = true;
            snprintf(app.owner.error, sizeof(app.owner.error), "Wayland dispatch failed");
        }
    }
    if (!running) {
        cleanup(&app);
        return 0;
    }
    if (app.owner.failed || app.owner.closed || !app.owner.ready || !app.target) {
        emit_error(app.owner.error[0] ? app.owner.error
                                      : "KWin did not create the named virtual output");
        cleanup(&app);
        return 1;
    }

    emit_ready("owner_ready", &app, &app.owner);
    int result = run_control_loop(&app);
    cleanup(&app);
    return result;
}
