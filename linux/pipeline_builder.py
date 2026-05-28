"""
pipeline_builder.py — GStreamer pipeline construction with DMA-buf zero-copy path.

Capture tier (tried in order):
  1. DMA-buf zero-copy  : pipewiresrc (always-copy=false) → memory:DMABuf caps
  2. CPU copy fallback  : pipewiresrc (always-copy=true)  → system memory

Encoder tier (tried in order):
  1. va plugin family   : vapostproc (GPU colour-convert) + vah264lpenc / vah264enc
  2. vaapi plugin family: vapostproc (GPU colour-convert) + vaapih264enc
  3. CPU               : videoconvert + x264enc

The best combo is attempted first; on failure (process exits < 4 s) the next
tier is tried automatically — same as how Sunshine degrades gracefully when
DMA-buf import fails on a driver that doesn't support it.

Requires (Debian/Ubuntu):
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good
  gstreamer1.0-plugins-bad gstreamer1.0-vaapi          (for vaapih264enc)
  gstreamer1.0-va                                       (for vah264enc / vapostproc)
  libgstreamer-plugins-bad1.0-dev is NOT needed at runtime.
"""

import subprocess
import time


# ---------------------------------------------------------------------------
# Encoder detection
# ---------------------------------------------------------------------------

def detect_igpu_encoder():
    """
    Probe for an iGPU VA-API H.264 encoder.
    Returns a (family, element_name) tuple or (None, None).

    family is 'va' (gst-va) or 'vaapi' (gst-vaapi) — controls which colour-
    convert element to pair it with.
    """
    for enc in ("vah264lpenc", "vah264enc"):
        try:
            r = subprocess.run(["gst-inspect-1.0", enc],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and "nvidia" not in r.stdout.lower():
                print(f"[Pipeline] Detected va-family encoder: {enc}")
                return ("va", enc)
        except Exception:
            continue

    # Fall back to older gst-vaapi plugin
    try:
        r = subprocess.run(["gst-inspect-1.0", "vaapih264enc"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and "nvidia" not in r.stdout.lower():
            print("[Pipeline] Detected vaapi-family encoder: vaapih264enc")
            return ("vaapi", "vaapih264enc")
    except Exception:
        pass

    print("[Pipeline] No iGPU encoder found — will use CPU x264enc")
    return (None, None)


def _check_vapostproc():
    """Return True if vapostproc (gst-va) is available."""
    try:
        r = subprocess.run(["gst-inspect-1.0", "vapostproc"],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Pipeline builders
# ---------------------------------------------------------------------------

def _src_element(pw_fd, node_id, use_dmabuf: bool) -> str:
    """Build the pipewiresrc element string."""
    always_copy = "false" if use_dmabuf else "true"
    base = f"pipewiresrc do-timestamp=true always-copy={always_copy} keepalive-time=1"
    if pw_fd is not None:
        return f"{base} fd={pw_fd} path={node_id}"
    return f"{base} path={node_id}"


def _dmabuf_caps(width, height) -> str:
    """Caps filter that requests DMA-buf memory from pipewiresrc."""
    # No format constraint here — let the compositor give us whatever it has
    # (BGRA, BGR, etc.) and let vapostproc convert on the GPU.
    return f"video/x-raw(memory:DMABuf),width={width},height={height}"


def _va_encoder_params(enc_name, bitrate, key_int) -> str:
    if enc_name in ("vah264enc", "vah264lpenc"):
        return (f"{enc_name} rate-control=cbr bitrate={bitrate} "
                f"key-int-max={key_int} ref-frames=1 b-frames=0 target-usage=7")
    # vaapih264enc
    return (f"{enc_name} rate-control=cbr bitrate={bitrate} "
            f"keyframe-period={key_int} max-bframes=0 quality-level=7")


def _cpu_encoder_params(bitrate, key_int) -> str:
    return (
        f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={bitrate} "
        f"key-int-max={key_int} byte-stream=true "
        f"option-string=\"bframes=0:ref=1:sliced-threads=0:"
        f"rc-lookahead=0:sync-lookahead=0:threads=4:"
        f"vbv-bufsize=500:vbv-maxrate={bitrate}\""
    )


def build_dmabuf_pipeline(*, pw_fd, node_id, width, height, fps, bitrate, port,
                           enc_family, enc_name, host="127.0.0.1"):
    """
    Zero-copy DMA-buf pipeline (Sunshine-style):

      pipewiresrc (always-copy=false)
        → memory:DMABuf caps
        → videorate
        → vapostproc  (GPU colour-convert + scale, stays in GPU memory)
        → video/x-raw(memory:DMABuf),format=NV12
        → vah264enc / vaapih264enc  (GPU encode from DMA-buf)
        → h264parse → tcpclientsink

    The frame never touches CPU RAM between capture and encode.
    """
    src      = _src_element(pw_fd, node_id, use_dmabuf=True)
    dmabuf   = _dmabuf_caps(width, height)
    fps_caps = f"video/x-raw(memory:DMABuf),framerate={fps}/1"

    # vapostproc: GPU-side scale + colour-convert (replaces videoconvert + videoscale)
    # Outputs NV12 DMA-buf that vah264enc / vaapih264enc can consume directly.
    vapost   = (f"vapostproc ! "
                f"video/x-raw(memory:DMABuf),format=NV12,width={width},height={height}")

    key_int  = max(fps // 2, 15)
    encoder  = _va_encoder_params(enc_name, bitrate, key_int)
    queue    = "queue max-size-buffers=1 max-size-time=0 leaky=downstream"
    parse    = "h264parse config-interval=-1"
    caps_out = "video/x-h264,stream-format=byte-stream,alignment=au"
    sink     = f"tcpclientsink host={host} port={port} sync=false"

    pipeline = (
        f"gst-launch-1.0 -e "
        f"{src} ! {dmabuf} ! "
        f"videorate skip-to-first=true ! {fps_caps} ! "
        f"{queue} ! {vapost} ! "
        f"{encoder} ! {parse} ! {caps_out} ! {sink}"
    )
    return pipeline


def build_pipeline(*, pw_fd, node_id, width, height, fps, bitrate, port,
                   hw_encoder=None, host="127.0.0.1"):
    """
    CPU-copy pipeline (original path, kept as the final fallback).
    hw_encoder: element name string (vah264enc etc.) or None → x264enc.
    """
    src      = _src_element(pw_fd, node_id, use_dmabuf=False)
    framerate = f"videorate skip-to-first=true ! video/x-raw,framerate={fps}/1"
    queue    = "queue max-size-buffers=1 max-size-time=0 leaky=downstream"
    key_int  = max(fps // 2, 15)

    convert  = (f"videoconvert n-threads=4 ! videoscale ! "
                f"video/x-raw,format=NV12,width={width},height={height}")

    if hw_encoder:
        encoder = _va_encoder_params(hw_encoder, bitrate, key_int)
    else:
        encoder = _cpu_encoder_params(bitrate, key_int)

    parse    = "h264parse config-interval=-1"
    caps_out = "video/x-h264,stream-format=byte-stream,alignment=au"
    sink     = f"tcpclientsink host={host} port={port} sync=false"

    pipeline = (
        f"gst-launch-1.0 -e "
        f"{src} ! {framerate} ! {queue} ! {convert} ! "
        f"{encoder} ! {parse} ! {caps_out} ! {sink}"
    )
    return pipeline


# ---------------------------------------------------------------------------
# Launcher with 4-tier fallback
# ---------------------------------------------------------------------------

def _run_pipeline(pipeline_str, pass_fds=None, probe_timeout=4):
    """
    Launch a gst-launch-1.0 pipeline and wait.
    Returns (proc, succeeded) where succeeded=True means it ran > probe_timeout seconds.
    """
    kwargs = {"shell": True}
    if pass_fds:
        kwargs["pass_fds"] = pass_fds

    proc = subprocess.Popen(pipeline_str, **kwargs)
    try:
        proc.wait(timeout=probe_timeout)
    except subprocess.TimeoutExpired:
        return proc, True   # still running — pipeline is working

    return proc, False      # exited within probe window — treat as failure


def launch_with_fallback(*, pw_fd, node_id, width, height, fps, bitrate, port,
                          hw_encoder=None, pass_fds=None,
                          host="127.0.0.1", server_mode=False):
    """
    Try pipelines from best (DMA-buf zero-copy) to safest (CPU x264enc).

    Tier 1: DMA-buf + vapostproc + va-family encoder   (zero-copy, like Sunshine)
    Tier 2: DMA-buf + vapostproc + vaapi-family encoder (zero-copy, older driver)
    Tier 3: CPU videoconvert   + va-family encoder      (GPU encode, CPU copy)
    Tier 4: CPU videoconvert   + x264enc                (full CPU, always works)

    Tiers 1-3 are only attempted if a compatible encoder was detected.
    Each tier gets probe_timeout seconds to prove it's running before falling back.

    host       : TCP sink target — '127.0.0.1' for USB (ADB reverse) or the
                 Android's IP for direct Wi-Fi.
    server_mode: informational; reserved for future tcpserversink support.
    """
    enc_family, enc_name = (None, None)
    if hw_encoder:
        # hw_encoder may be a bare name — figure out family
        if hw_encoder in ("vah264enc", "vah264lpenc"):
            enc_family, enc_name = "va", hw_encoder
        elif hw_encoder == "vaapih264enc":
            enc_family, enc_name = "vaapi", hw_encoder

    has_vapostproc = _check_vapostproc() if enc_name else False

    tiers = []

    # Tiers 1 & 2: DMA-buf path (requires vapostproc + a va/vaapi encoder)
    if enc_name and has_vapostproc:
        tiers.append({
            "label": f"DMA-buf zero-copy + {enc_name}",
            "pipeline": build_dmabuf_pipeline(
                pw_fd=pw_fd, node_id=node_id,
                width=width, height=height, fps=fps, bitrate=bitrate, port=port,
                enc_family=enc_family, enc_name=enc_name, host=host,
            ),
        })

    # Tier 3: CPU copy + HW encode (no vapostproc needed)
    if enc_name:
        tiers.append({
            "label": f"CPU copy + {enc_name}",
            "pipeline": build_pipeline(
                pw_fd=pw_fd, node_id=node_id,
                width=width, height=height, fps=fps, bitrate=bitrate, port=port,
                hw_encoder=enc_name, host=host,
            ),
        })

    # Tier 4: Full CPU fallback (always last)
    tiers.append({
        "label": "CPU copy + x264enc (full CPU fallback)",
        "pipeline": build_pipeline(
            pw_fd=pw_fd, node_id=node_id,
            width=width, height=height, fps=fps, bitrate=bitrate, port=port,
            hw_encoder=None, host=host,
        ),
    })

    for i, tier in enumerate(tiers):
        label    = tier["label"]
        pipeline = tier["pipeline"]
        is_last  = (i == len(tiers) - 1)

        print(f"\n[Pipeline] Trying tier {i+1}/{len(tiers)}: {label}")
        print(f"[GStreamer] {pipeline}\n")

        proc, ok = _run_pipeline(pipeline, pass_fds=pass_fds)

        if ok:
            print(f"[Pipeline] {label} — running OK")
            proc.wait()
            return proc

        rc = proc.returncode
        if is_last:
            print(f"[Pipeline] {label} — exited (rc={rc}). No more tiers.")
            return proc

        print(f"[Pipeline] {label} — exited early (rc={rc}), trying next tier...")
        time.sleep(0.5)   # brief pause before retry

    return proc  # unreachable but keeps linters happy
