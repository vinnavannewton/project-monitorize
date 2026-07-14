"""Build and launch low-latency PipeWire to H.264 GStreamer pipelines."""

import shlex
import subprocess
import re
from functools import lru_cache

from .video_transport import (
    FEC_PAYLOAD_TYPE, INITIAL_FEC_PERCENT, MTU, RTP_PAYLOAD_TYPE,
    wait_for_client,
)

VALID_ENCODER_PROFILES = {"Low Latency", "Balanced", "Quality"}


@lru_cache(maxsize=None)
def _gst_inspect(element):
    try:
        result = subprocess.run(
            ["gst-inspect-1.0", element], capture_output=True, text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""


def get_encoder(preference: str = "cpu") -> str | None:
    """
    Return the encoder name based on user preference.
    
    Parameters
    ----------
    preference : str
        One of: 'nvidia', 'vaapi', 'cpu'.
    """
    pref = preference.lower()
    
    if pref == "nvidia":
        if _gst_inspect("nvh264enc"):
            return "nvh264enc"
        print("[Pipeline] NVIDIA NVENC is unavailable; using CPU x264enc")
        return None
        
    elif pref == "vaapi":
        for enc in ("vah264enc", "vah264lpenc", "vaapih264enc"):
            info = _gst_inspect(enc)
            if info and "nvidia" not in info.lower():
                return enc
        return "vah264enc"  
        
    return None


def _encoder_profile(value):
    return value if value in VALID_ENCODER_PROFILES else "Low Latency"


def _probe_encoder_properties(encoder):
    """Drop properties not exposed by the installed GStreamer encoder."""
    tokens = shlex.split(encoder)
    if not tokens:
        return encoder
    info = _gst_inspect(tokens[0])
    if not info:
        return encoder
    supported = set(re.findall(r"^\s{2,}([a-zA-Z0-9_-]+)\s+:\s", info, re.MULTILINE))
    if not supported:
        return encoder
    return " ".join(
        token for index, token in enumerate(tokens)
        if index == 0 or "=" not in token or token.split("=", 1)[0] in supported
    )


def _hw_encoder_params(
    enc_name, bitrate, key_int, fps=60, intra_refresh=False, wifi_mode=False,
    encoder_profile="Low Latency",
):
    """Return GStreamer property string for a detected hardware encoder."""
    encoder_profile = _encoder_profile(encoder_profile)
    one_frame_kbits = max(1, (bitrate + max(fps, 1) - 1) // max(fps, 1))
    if enc_name == "nvh264enc":
        common = (
            f"nvh264enc bitrate={bitrate} vbv-buffer-size={one_frame_kbits} "
            f"zerolatency=true bframes=0 rc-lookahead=0 rc-mode=cbr "
            f"gop-size={key_int} tune=ultra-low-latency strict-gop=true "
            f"repeat-sequence-header=true aud=true num-surfaces=1 ref-frames=1"
        )
        if encoder_profile == "Low Latency":
            return f"{common} preset=p1"
        preset = "p3" if encoder_profile == "Balanced" else "p5"
        return f"{common} preset={preset}"
    elif enc_name in ("vah264enc", "vah264lpenc") and wifi_mode and encoder_profile == "Low Latency":
        return (
            f"{enc_name} rate-control=cbr bitrate={bitrate} cabac=false "
            f"cpb-size={one_frame_kbits} key-int-max={key_int} ref-frames=1 "
            f"b-frames=0 target-usage=7 async-depth=1 aud=true"
        )
    elif enc_name == "vaapih264enc" and encoder_profile == "Low Latency":
        return (
            f"{enc_name} rate-control=cqp init-qp=20 cabac=false "
            f"keyframe-period={key_int} max-bframes=0 quality-level=7 aud=true"
        )
    elif enc_name == "vaapih264enc":
        quality = 5 if encoder_profile == "Balanced" else 3
        return (
            f"{enc_name} rate-control=cqp init-qp=20 cabac=true "
            f"keyframe-period={key_int} max-bframes=0 quality-level={quality} aud=true"
        )
    elif encoder_profile != "Low Latency":
        usage = 5 if encoder_profile == "Balanced" else 3
        refs = 1 if encoder_profile == "Balanced" else 2
        return (
            f"{enc_name} rate-control=cbr bitrate={bitrate} cabac=true cpb-size=2000 "
            f"key-int-max={key_int} ref-frames={refs} b-frames=0 "
            f"target-usage={usage} aud=true"
        )
    
    return (
        f"{enc_name} rate-control=cqp qpi=20 qpp=22 cabac=false cpb-size=2000 "
        f"key-int-max={key_int} ref-frames=1 b-frames=0 target-usage=7 aud=true"
    )


def _cpu_encoder_params(
    bitrate, key_int, intra_refresh=False, encoder_profile="Low Latency"
):
    """Return GStreamer property string for optimised CPU x264enc."""
    ir_opt = " intra-refresh=true" if intra_refresh else ""
    encoder_profile = _encoder_profile(encoder_profile)
    if encoder_profile == "Low Latency":
        return (
            f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={bitrate} "
            f"key-int-max={key_int} byte-stream=true bframes=0 ref=1 "
            f"sliced-threads=true mb-tree=false threads=0 sync-lookahead=0 "
            f"vbv-buf-capacity=17 aud=true{ir_opt}"
        )
    speed = "superfast" if encoder_profile == "Balanced" else "veryfast"
    refs = 1 if encoder_profile == "Balanced" else 2
    return (
        f"x264enc tune=zerolatency speed-preset={speed} bitrate={bitrate} "
        f"key-int-max={key_int} byte-stream=true bframes=0 ref={refs} "
        f"sliced-threads=true mb-tree=false threads=0{ir_opt}"
    )


def build_pipeline(*, pw_fd, node_id, width, height, fps, bitrate, port,
                   hw_encoder=None, host="127.0.0.1", stream_type="Speed",
                   wifi_mode=False, preserve_source_size=False,
                   preserve_source_rate=False, target_object=None,
                   encoder_profile="Low Latency", nvidia_memory="cuda",
                   rtp_endpoint=None):
    """
    Build a full gst-launch-1.0 argv list.

    Parameters
    ----------
    pw_fd : int or None
        PipeWire FD (None for GNOME Mutter which uses path-only).
    node_id : int
        PipeWire node ID.
    width, height, fps, bitrate, port : int
        Stream parameters.
    hw_encoder : str or None
        Element name from detect_igpu_encoder(), or None for CPU fallback.
    """
    
    zero_copy = hw_encoder != "nvh264enc" or nvidia_memory == "gl"
    always_copy = "false" if hw_encoder and zero_copy else "true"
    keepalive_ms = max(1, round(1000 / max(fps, 1)))
    if target_object is not None:
        src = (
            f"pipewiresrc target-object={target_object} do-timestamp=true "
            f"always-copy={always_copy} keepalive-time={keepalive_ms}"
        )
    elif pw_fd is not None:
        src = (
            f"pipewiresrc fd={pw_fd} path={node_id} do-timestamp=true "
            f"always-copy={always_copy} keepalive-time={keepalive_ms}"
        )
    else:
        src = (
            f"pipewiresrc path={node_id} do-timestamp=true "
            f"always-copy={always_copy} keepalive-time={keepalive_ms}"
        )

    queue = "queue max-size-buffers=1 max-size-time=0 max-size-bytes=0 leaky=downstream"

    
    
    if stream_type == "Stability":
        key_int = 15
        intra_refresh = not bool(rtp_endpoint)
    else:
        key_int = max(fps // 2, 15)
        intra_refresh = False

    if hw_encoder:
        rate_filter = (
            f"videorate skip-to-first=false ! "
            f"'video/x-raw(ANY),framerate={fps}/1'"
            if wifi_mode else ""
        )
        dimensions = "" if preserve_source_size else f",width={width},height={height}"
        if hw_encoder == "nvh264enc":
            if nvidia_memory == "gl":
                convert = (
                    "glupload ! glcolorconvert ! glcolorscale ! "
                    f"'video/x-raw(memory:GLMemory),format=RGBA{dimensions}'"
                )
            elif nvidia_memory == "system":
                scale = "" if preserve_source_size else " ! videoscale"
                convert = (
                    f"videoconvert n-threads=4{scale} ! "
                    f"video/x-raw,format=NV12{dimensions}"
                )
            else:
                convert = (
                    "cudaupload ! cudaconvertscale ! "
                    f"'video/x-raw(memory:CUDAMemory),format=NV12{dimensions}'"
                )
        else:
            postproc = "vapostproc" if hw_encoder in ("vah264enc", "vah264lpenc") else "vaapipostproc"
            convert = f"{postproc} ! 'video/x-raw(memory:VAMemory),format=NV12{dimensions}'"
        encoder = _hw_encoder_params(
            hw_encoder, bitrate, key_int, fps=fps,
            intra_refresh=intra_refresh, wifi_mode=wifi_mode,
            encoder_profile=encoder_profile,
        )
        encoder = _probe_encoder_properties(encoder)
    else:
        rate_filter = (
            f"videorate skip-to-first=false ! video/x-raw,framerate={fps}/1"
            if wifi_mode or not preserve_source_rate else ""
        )
        dimensions = "" if preserve_source_size else f",width={width},height={height}"
        scale = "" if preserve_source_size else " ! videoscale"
        convert = f"videoconvert n-threads=4{scale} ! video/x-raw,format=I420{dimensions}"
        encoder = _cpu_encoder_params(
            bitrate, key_int, intra_refresh=intra_refresh,
            encoder_profile=encoder_profile,
        )
        encoder = _probe_encoder_properties(encoder)

    parse = "h264parse config-interval=1"
    negotiated_profile = (
        rtp_endpoint[3] if rtp_endpoint and len(rtp_endpoint) > 3 else None
    )
    if negotiated_profile == "high":
        caps_out = "video/x-h264,profile=high,stream-format=byte-stream,alignment=au"
    elif hw_encoder:
        caps_out = "video/x-h264,stream-format=byte-stream,alignment=au"
    else:
        caps_out = "video/x-h264,profile=baseline,stream-format=byte-stream,alignment=au"

    
    
    if rtp_endpoint:
        client_host, client_port, *endpoint_options = rtp_endpoint
        ssrc = f" ssrc={endpoint_options[0]}" if endpoint_options else ""
        sink = (
            f"rtph264pay aggregate-mode=none config-interval=-1 "
            f"mtu={MTU} pt={RTP_PAYLOAD_TYPE}{ssrc} ! "
            f"udpsink host={client_host} port={client_port} bind-port={port} "
            f"sync=false async=false buffer-size=262144 qos-dscp=48"
        )
    else:
        sink = f"tcpserversink host={host} port={port} sync=false sync-method=2 recover-policy=2 buffers-max=3 buffers-soft-max=2 qos-dscp=48"

    taskset_prefix = []
    if not hw_encoder:
        import os
        cores = os.cpu_count() or 1
        if cores > 1:
            taskset_prefix = ["taskset", "-c", f"1-{cores - 1}"]

    elements = [src]
    if rate_filter:
        elements.append(rate_filter)
    elements.extend([queue, convert, encoder, parse, caps_out, sink])

    pipeline = [*taskset_prefix, "gst-launch-1.0", "-e"]
    for index, element in enumerate(elements):
        pipeline.extend(shlex.split(element))
        if index != len(elements) - 1:
            pipeline.append("!")
    return pipeline


def _launch(argv, pass_fds=None, target_fps=60, target_bitrate=8000):
    if "rtph264pay" in argv:
        import sys
        gst_index = argv.index("gst-launch-1.0")
        elements = argv[gst_index + 2:]
        bind_port = next(
            int(token.split("=", 1)[1]) for token in elements
            if token.startswith("bind-port=")
        )
        runner = [
            sys.executable, "-m", "monitorize.streaming.gst_session",
            "--control-port", str(bind_port), " ".join(elements),
        ]
        runner[5:5] = [
            "--pacing-bitrate", str(target_bitrate),
            "--target-fps", str(target_fps),
        ]
        argv = [*argv[:gst_index], *runner]
    kwargs = {"shell": False}
    if pass_fds:
        kwargs["pass_fds"] = pass_fds
    proc = subprocess.Popen(argv, **kwargs)
    print(f"[GStreamer] PID: {proc.pid}")
    return proc


def _failed_during_startup(proc, timeout=1.0):
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


def _nvidia_memory_candidates():
    encoder = _gst_inspect("nvh264enc")
    candidates = []
    if "memory:CUDAMemory" in encoder and all(
        _gst_inspect(element)
        for element in ("cudaupload", "cudaconvertscale")
    ):
        candidates.append("cuda")
    candidates.append("system")
    return candidates


def launch_with_fallback(*, pw_fd, node_id, width, height, fps, bitrate, port,
                         hw_encoder=None, pass_fds=None,
                         host="127.0.0.1", server_mode=False,
                         target_object=None, preserve_source_size=None,
                         preserve_source_rate=False):
    """
    Launch the streaming pipeline.

    Returns the subprocess.Popen object.
    """
    import os
    stream_type = os.environ.get("MONITORIZE_STREAM_TYPE", "Speed")
    encoder_profile = os.environ.get("MONITORIZE_ENCODER_PROFILE", "Low Latency")
    if preserve_source_size is None:
        preserve_source_size = os.environ.get("MONITORIZE_PRESERVE_SOURCE_SIZE") == "1"
    rtp_endpoint = None
    if server_mode and os.environ.get("MONITORIZE_VIDEO_TRANSPORT") == "rtp-udp-v1":
        rtp_endpoint = wait_for_client(
            port, width=width, height=height, fps=fps, bitrate=bitrate
        )
    modes = [None]
    if hw_encoder == "nvh264enc":
        requested = os.environ.get("MONITORIZE_NVIDIA_MEMORY", "auto").lower()
        modes = (
            [requested]
            if requested in {"gl", "cuda", "system"}
            else _nvidia_memory_candidates()
        )

    for mode in modes:
        pipeline = build_pipeline(
            pw_fd=pw_fd, node_id=node_id,
            width=width, height=height, fps=fps, bitrate=bitrate, port=port,
            hw_encoder=hw_encoder, host=host, stream_type=stream_type,
            wifi_mode=server_mode, preserve_source_size=preserve_source_size,
            preserve_source_rate=preserve_source_rate, target_object=target_object,
            encoder_profile=encoder_profile,
            nvidia_memory=mode or "cuda",
            rtp_endpoint=rtp_endpoint,
        )
        label = f"{hw_encoder} ({mode})" if mode else (hw_encoder or "x264enc (CPU)")
        print(f"\n[Pipeline] Encoder: {label}")
        print(f"[GStreamer] {shlex.join(pipeline)}\n")
        proc = _launch(
            pipeline, pass_fds=pass_fds,
            target_fps=fps, target_bitrate=bitrate,
        )
        if not _failed_during_startup(proc):
            print("[Pipeline] READY", flush=True)
            return proc
        if not hw_encoder:
            print("[Pipeline] CPU encoder failed during startup", flush=True)
            return proc
        print(f"[Pipeline] {label} failed during startup; trying fallback")

    print("[Pipeline] Hardware encoder paths failed; retrying CPU x264enc")
    pipeline = build_pipeline(
        pw_fd=pw_fd, node_id=node_id,
        width=width, height=height, fps=fps, bitrate=bitrate, port=port,
        hw_encoder=None, host=host, stream_type=stream_type,
        wifi_mode=server_mode, preserve_source_size=preserve_source_size,
        preserve_source_rate=preserve_source_rate, target_object=target_object,
        encoder_profile=encoder_profile,
        rtp_endpoint=rtp_endpoint,
    )
    print(f"[GStreamer] {shlex.join(pipeline)}\n")
    proc = _launch(
        pipeline, pass_fds=pass_fds,
        target_fps=fps, target_bitrate=bitrate,
    )
    if _failed_during_startup(proc):
        print("[Pipeline] CPU fallback failed during startup", flush=True)
    else:
        print("[Pipeline] READY", flush=True)
    return proc
