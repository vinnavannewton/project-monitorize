"""
pipeline_builder.py — Shared GStreamer pipeline construction with iGPU HW encoding + CPU fallback.

Detects available VA-API H.264 encoders (AMD/Intel iGPU only, skips NVIDIA dGPU).
Falls back to optimised x264enc if no hardware encoder is found.
"""

import subprocess


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
        return "nvh264enc"
        
    elif pref == "vaapi":
        for enc in ("vah264enc", "vah264lpenc", "vaapih264enc"):
            try:
                res = subprocess.run(["gst-inspect-1.0", enc], capture_output=True, text=True, timeout=5)
                if res.returncode == 0 and "nvidia" not in res.stdout.lower():
                    return enc
            except Exception:
                continue
        return "vah264enc"  
        
    return None


def _hw_encoder_params(enc_name, bitrate, key_int):
    """Return GStreamer property string for a detected hardware encoder."""
    if enc_name == "nvh264enc":
        return (
            f"nvh264enc bitrate={bitrate} zerolatency=true bframes=0 rc-lookahead=0 "
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
    return (
        f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={bitrate} "
        f"key-int-max={key_int} byte-stream=true bframes=0 ref=1 "
        f"sliced-threads=false mb-tree=false threads=1"
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
        src = f"pipewiresrc fd={pw_fd} path={node_id} do-timestamp=true always-copy=true keepalive-time=1000"
    else:
        src = f"pipewiresrc path={node_id} do-timestamp=true always-copy=true keepalive-time=1000"

    
    
    
    
    
    
    framerate = f"videoconvert ! videorate skip-to-first=false ! video/x-raw,framerate={fps}/1"

    
    queue = "queue max-size-buffers=1 max-size-time=0 max-size-bytes=0 leaky=downstream"

    
    key_int = max(fps // 2, 15)   
    if hw_encoder:
        if hw_encoder == "nvh264enc":
            
            convert = f"cudaupload ! cudaconvertscale ! 'video/x-raw(memory:CUDAMemory),format=NV12,width={width},height={height}'"
        else:
            
            convert = f"videoconvert n-threads=4 ! videoscale ! video/x-raw,format=NV12,width={width},height={height}"
        encoder = _hw_encoder_params(hw_encoder, bitrate, key_int)
    else:
        
        convert = f"videoconvert n-threads=4 ! videoscale ! video/x-raw,format=I420,width={width},height={height}"
        encoder = _cpu_encoder_params(bitrate, key_int)

    
    if hw_encoder:
        parse = "h264parse config-interval=1"
        caps_out = "video/x-h264,stream-format=byte-stream,alignment=au"
    else:
        
        
        parse = "h264parse config-interval=-1"
        
        caps_out = "video/x-h264,profile=baseline,stream-format=byte-stream,alignment=au"

    
    
    sink = f"tcpserversink host={host} port={port} sync=false sync-method=2 recover-policy=2 buffers-max=10 buffers-soft-max=5"


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
    Launch the streaming pipeline.

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
    print(f"[GStreamer] PID: {proc.pid}")
    return proc
