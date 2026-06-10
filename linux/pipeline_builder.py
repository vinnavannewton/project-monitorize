"""
pipeline_builder.py — Shared GStreamer pipeline construction with iGPU HW encoding + CPU fallback.

Detects available VA-API H.264 encoders (AMD/Intel iGPU only, skips NVIDIA dGPU).
Falls back to optimised x264enc if no hardware encoder is found.
"""

import subprocess


def detect_igpu_encoder():
    """
    Detect a hardware H.264 encoder.
    Prioritizes NVIDIA dGPU (nvh264enc) first, then falls back to VA-API (iGPU),
    and finally CPU (x264enc).
    """
    
    try:
        result = subprocess.run(
            ["gst-inspect-1.0", "nvh264enc"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            print("[Pipeline] Detected NVIDIA GPU encoder: nvh264enc")
            return "nvh264enc"
    except Exception:
        pass

    
    for enc in ("vah264enc", "vah264lpenc", "vaapih264enc"):
        try:
            result = subprocess.run(
                ["gst-inspect-1.0", enc],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and "nvidia" not in result.stdout.lower():
                print(f"[Pipeline] Detected iGPU encoder: {enc}")
                return enc
        except Exception:
            continue

    print("[Pipeline] No hardware encoder found — will use CPU x264enc")
    return None


def _hw_encoder_params(enc_name, bitrate, key_int):
    """Return GStreamer property string for a detected hardware encoder."""
    if enc_name == "nvh264enc":
        return (
            f"nvh264enc bitrate={bitrate} zerolatency=true bframes=0 "
            f"rc-mode=cbr gop-size={key_int} tune=ultra-low-latency preset=p1"
        )
    elif enc_name == "vaapih264enc":
        return (
            f"{enc_name} rate-control=cbr bitrate={bitrate} "
            f"keyframe-period={key_int} max-bframes=0 quality-level=7"
        )
    
    
    
    
    return (
        f"{enc_name} rate-control=cbr bitrate={bitrate} cpb-size=2000 "
        f"key-int-max={key_int} ref-frames=1 b-frames=0 target-usage=7"
    )


def _cpu_encoder_params(bitrate, key_int):
    """Return GStreamer property string for optimised CPU x264enc."""
    vbv_buf = int(bitrate * 0.15)
    return (
        f"x264enc tune=zerolatency speed-preset=ultrafast intra-refresh=true bitrate={bitrate} "
        f"key-int-max={key_int} byte-stream=true "
        f"option-string=\"bframes=0:ref=1:sliced-threads=1:mb-tree=0:"
        f"rc-lookahead=0:sync-lookahead=0:threads=0:"
        f"vbv-bufsize={vbv_buf}:vbv-maxrate={bitrate}\""
    )


def build_pipeline(*, pw_fd, node_id, width, height, fps, bitrate, port,
                   hw_encoder=None, host="127.0.0.1"):
    """
    Build a full gst-launch-1.0 pipeline string.

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
    
    if pw_fd is not None:
        src = f"pipewiresrc fd={pw_fd} path={node_id} do-timestamp=true always-copy=true keepalive-time=1"
    else:
        src = f"pipewiresrc path={node_id} do-timestamp=true always-copy=true keepalive-time=1"

    
    
    
    
    
    
    framerate = f"videoconvert ! videorate skip-to-first=true drop-only=true ! video/x-raw,framerate={fps}/1"

    
    queue = "queue max-size-buffers=1 max-size-time=0 max-size-bytes=0 leaky=downstream"

    
    key_int = max(fps // 2, 15)   
    if hw_encoder:
        if hw_encoder == "nvh264enc":
            
            convert = f"cudaupload ! cudaconvertscale ! 'video/x-raw(memory:CUDAMemory),format=NV12,width={width},height={height}'"
        else:
            
            convert = f"videoconvert n-threads=4 ! videoscale ! video/x-raw,format=NV12,width={width},height={height}"
        encoder = _hw_encoder_params(hw_encoder, bitrate, key_int)
    else:
        
        convert = f"videoconvert n-threads=4 ! videoscale ! video/x-raw,format=NV12,width={width},height={height}"
        encoder = _cpu_encoder_params(bitrate, key_int)

    
    parse = "h264parse config-interval=1"
    caps_out = "video/x-h264,stream-format=byte-stream,alignment=au"

    
    if host != "127.0.0.1":
        
        sink = f"tcpserversink host={host} port={port} sync=false sync-method=2 recover-policy=1 buffers-max=2 buffers-soft-max=1"
    else:
        sink = f"tcpclientsink host=127.0.0.1 port={port} sync=false"

    pipeline = (
        f"exec gst-launch-1.0 -e "
        f"{src} ! {framerate} ! {queue} ! {convert} ! "
        f"{encoder} ! {parse} ! {caps_out} ! {sink}"
    )
    return pipeline


def launch_with_fallback(*, pw_fd, node_id, width, height, fps, bitrate, port,
                         hw_encoder=None, pass_fds=None,
                         host="127.0.0.1", server_mode=False):
    """
    Launch the streaming pipeline. If a hardware encoder was requested and the
    process exits within 4 seconds (negotiation failure), automatically retry
    with the CPU fallback pipeline.

    Returns the subprocess.Popen object.
    """
    pipeline = build_pipeline(
        pw_fd=pw_fd, node_id=node_id,
        width=width, height=height, fps=fps, bitrate=bitrate, port=port,
        hw_encoder=hw_encoder, host=host,
    )
    label = hw_encoder or "x264enc (CPU)"
    print(f"\n[Pipeline] Encoder: {label}")
    print(f"[GStreamer] {pipeline}\n")

    kwargs = {"shell": True}
    if pass_fds:
        kwargs["pass_fds"] = pass_fds

    proc = subprocess.Popen(pipeline, **kwargs)

    
    if hw_encoder:
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            
            print(f"[Pipeline] HW encoder ({hw_encoder}) running OK")
            proc.wait()
            return proc

        
        rc = proc.returncode
        if rc != 0:
            print(f"[Pipeline] HW encoder failed (exit {rc}), falling back to CPU x264enc")
            pipeline = build_pipeline(
                pw_fd=pw_fd, node_id=node_id,
                width=width, height=height, fps=fps, bitrate=bitrate, port=port,
                hw_encoder=None, host=host,
            )
            print(f"[GStreamer] {pipeline}\n")
            proc = subprocess.Popen(pipeline, **kwargs)
            proc.wait()
        return proc
    else:
        proc.wait()
        return proc
