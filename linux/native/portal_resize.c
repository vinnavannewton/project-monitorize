/* Keep a portal virtual stream negotiated at an exact size (KWin 6.8+).
 * No frame copies and no input devices. The parent owns the portal session. */
#include <pipewire/pipewire.h>
#include <spa/param/video/format-utils.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

struct state {
    struct pw_main_loop *loop;
    struct pw_stream *stream;
    uint32_t width, height;
    int matched, streaming, announced, failed;
};

static void announce(struct state *s) {
    if (s->matched && s->streaming && !s->announced) {
        s->announced = 1;
        printf("READY %u %u\n", s->width, s->height);
        fflush(stdout);
    }
}

static void state_changed(void *data, enum pw_stream_state old,
                          enum pw_stream_state state, const char *error) {
    (void)old;
    struct state *s = data;
    if (state == PW_STREAM_STATE_ERROR ||
        (s->announced && state == PW_STREAM_STATE_UNCONNECTED)) {
        fprintf(stderr, "Virtual display negotiation stopped: %s\n", error ? error : "disconnected");
        s->failed = 1;
        pw_main_loop_quit(s->loop);
    }
    s->streaming = state == PW_STREAM_STATE_STREAMING;
    announce(s);
}

static void format_changed(void *data, uint32_t id, const struct spa_pod *param) {
    struct state *s = data;
    if (!param || id != SPA_PARAM_Format) return;
    struct spa_video_info_raw info = {0};
    if (spa_format_video_raw_parse(param, &info) < 0 ||
        info.size.width != s->width || info.size.height != s->height) {
        fprintf(stderr, "Compositor did not negotiate the requested virtual display size\n");
        s->failed = 1;
        pw_main_loop_quit(s->loop);
        return;
    }
    s->matched = 1;
    announce(s);
}

static void process(void *data) {
    struct state *s = data;
    struct pw_buffer *buffer;
    while ((buffer = pw_stream_dequeue_buffer(s->stream)))
        pw_stream_queue_buffer(s->stream, buffer);
}

static void stop(void *data, int signal_number) {
    (void)signal_number;
    pw_main_loop_quit(((struct state *)data)->loop);
}

int main(int argc, char **argv) {
    if (argc != 7) return 2; /* fd, node, width, height, fps, object serial */
    struct state s = {0};
    int fd = atoi(argv[1]);
    uint32_t node = (uint32_t)strtoul(argv[2], NULL, 10);
    s.width = (uint32_t)strtoul(argv[3], NULL, 10);
    s.height = (uint32_t)strtoul(argv[4], NULL, 10);
    uint32_t fps = (uint32_t)strtoul(argv[5], NULL, 10);
    if (fd < 0 || !node || !s.width || !s.height || s.width > 16384 || s.height > 16384 || !fps) return 2;
    pw_init(&argc, &argv);
    s.loop = pw_main_loop_new(NULL);
    struct pw_context *context = pw_context_new(pw_main_loop_get_loop(s.loop), NULL, 0);
    struct pw_core *core = pw_context_connect_fd(context, fd, NULL, 0);
    if (!core) return 1;
    struct pw_properties *props = pw_properties_new(
        PW_KEY_MEDIA_TYPE, "Video", PW_KEY_MEDIA_CATEGORY, "Capture",
        PW_KEY_MEDIA_ROLE, "Screen", PW_KEY_NODE_NAME, "monitorize-virtual-mode", NULL);
    if (*argv[6]) {
        pw_properties_set(props, PW_KEY_TARGET_OBJECT, argv[6]);
        node = PW_ID_ANY;
    }
    s.stream = pw_stream_new(core, "Monitorize virtual display mode", props);
    const struct pw_stream_events events = {
        PW_VERSION_STREAM_EVENTS, .state_changed = state_changed,
        .param_changed = format_changed, .process = process,
    };
    struct spa_hook listener;
    pw_stream_add_listener(s.stream, &listener, &events, &s);
    uint8_t buffer[1024];
    struct spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));
    struct spa_rectangle size = SPA_RECTANGLE(s.width, s.height);
    struct spa_fraction rate = SPA_FRACTION(fps, 1);
    const struct spa_pod *params[] = {spa_pod_builder_add_object(&b,
        SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat,
        SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
        SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
        SPA_FORMAT_VIDEO_format, SPA_POD_CHOICE_ENUM_Id(4, SPA_VIDEO_FORMAT_BGRx,
            SPA_VIDEO_FORMAT_BGRA, SPA_VIDEO_FORMAT_RGBx, SPA_VIDEO_FORMAT_RGBA),
        SPA_FORMAT_VIDEO_size, SPA_POD_Rectangle(&size),
        SPA_FORMAT_VIDEO_maxFramerate, SPA_POD_Fraction(&rate))};
    pw_loop_add_signal(pw_main_loop_get_loop(s.loop), SIGTERM, stop, &s);
    pw_loop_add_signal(pw_main_loop_get_loop(s.loop), SIGINT, stop, &s);
    if (pw_stream_connect(s.stream, PW_DIRECTION_INPUT, node,
                          PW_STREAM_FLAG_AUTOCONNECT, params, 1) < 0) s.failed = 1;
    else pw_main_loop_run(s.loop);
    pw_stream_destroy(s.stream);
    pw_core_disconnect(core);
    pw_context_destroy(context);
    pw_main_loop_destroy(s.loop);
    return s.failed ? 1 : 0;
}
