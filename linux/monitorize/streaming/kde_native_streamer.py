"""KWin-native virtual output and capture lifecycle."""

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from monitorize.platform.kde_virtual_monitor import (
    configure_native_virtual_output,
    virtual_slot,
    wait_for_output_absent,
)
from monitorize.streaming.pipeline_builder import (
    launch_with_fallback,
    prepare_rtp_endpoint,
)


HELPER_NAME = "monitorize-kde-virtual-output"
HELPER_EVENT_TIMEOUT = 10


def find_helper():
    override = os.environ.get("MONITORIZE_KDE_HELPER", "").strip()
    candidates = [override, str(Path(sys.executable).with_name(HELPER_NAME))]
    from_path = shutil.which(HELPER_NAME)
    if from_path:
        candidates.append(from_path)
    return next(
        (path for path in candidates if path and os.path.isfile(path) and os.access(path, os.X_OK)),
        "",
    )


def _read_helper_event(process, expected, timeout=HELPER_EVENT_TIMEOUT):
    deadline = time.monotonic() + timeout
    last_line = ""
    while time.monotonic() < deadline:
        ready, _write, _error = select.select(
            [process.stdout], [], [], max(0, deadline - time.monotonic())
        )
        if not ready:
            break
        line = process.stdout.readline()
        if not line:
            break
        last_line = line.strip()
        try:
            event = json.loads(last_line)
        except ValueError:
            continue
        if event.get("event") == "error":
            raise RuntimeError(event.get("message") or "KWin helper failed")
        if event.get("event") == expected:
            return event
    detail = last_line or f"helper exited with code {process.poll()}"
    raise RuntimeError(f"Timed out waiting for {expected}: {detail}")


def _stop_process(process, timeout=2):
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _stop_helper(process):
    if not process or process.poll() is not None:
        return
    try:
        process.stdin.write("quit\n")
        process.stdin.flush()
        process.wait(timeout=2)
    except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
        _stop_process(process)


def _controller_event(event):
    print(f"MONITORIZE_EVENT {json.dumps(event, separators=(',', ':'))}", flush=True)


def _start_capture_wakeup(output_name):
    process = subprocess.Popen(
        [
            sys.executable, "-m", "monitorize.streaming.kde_capture_wakeup",
            output_name,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        _read_helper_event(process, "ready", timeout=3)
    except RuntimeError as exc:
        _stop_process(process)
        print(f"[KDE Native] WARNING: capture wake-up unavailable: {exc}", flush=True)
        return None
    print(f"[KDE Native] Capture wake-up active on {output_name}", flush=True)
    return process


def run_native_streamer(
    slot,
    width,
    height,
    fps,
    bitrate,
    mode,
    port,
    encoder,
    host,
):
    slot_info = virtual_slot(slot)
    output_name = slot_info["output_name"]
    if not wait_for_output_absent(output_name):
        print(
            f"[ERROR] {output_name} is already active; stop its existing Monitorize session",
            flush=True,
        )
        return 1

    helper_path = find_helper()
    if not helper_path:
        print(
            "[ERROR] KDE native helper is missing. Re-run the Monitorize installer.",
            flush=True,
        )
        return 1

    helper = subprocess.Popen(
        [
            helper_path,
            slot_info["base_name"],
            slot_info["description"],
            str(width),
            str(height),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    gst = None
    wakeup = None

    def cleanup(*_args):
        _stop_process(gst)
        _stop_process(wakeup)
        _stop_helper(helper)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        owner = _read_helper_event(helper, "owner_ready")
        if owner.get("name") != output_name:
            raise RuntimeError(f"KWin helper created unexpected output {owner.get('name')}")

        ok, actual, message = configure_native_virtual_output(
            output_name, width, height, fps
        )
        if not ok:
            raise RuntimeError(message)
        print(f"[KDE Native] {message}", flush=True)
        _controller_event({
            "type": "kde_output_ready",
            "slot": slot,
            **actual,
            "requested_width": width,
            "requested_height": height,
            "requested_fps": fps,
        })

        codec = os.environ.get("MONITORIZE_VIDEO_CODEC", "h264")
        if codec not in ("h264", "h265"):
            codec = "h264"
        rtp_endpoint = prepare_rtp_endpoint(
            width=actual["width"], height=actual["height"], fps=fps,
            bitrate=bitrate, port=port, server_mode=mode == "wifi",
            codec=codec,
        )

        if rtp_endpoint is not None:
            wakeup = _start_capture_wakeup(output_name)

        capture_path = "owner"
        capture = owner
        if rtp_endpoint is None or fps > 60:
            capture_path = "post-mode"
            helper.stdin.write("capture\n")
            helper.stdin.flush()
            capture = _read_helper_event(helper, "capture_ready")
            if capture.get("name") != output_name:
                raise RuntimeError(
                    f"KWin captured unexpected output {capture.get('name')}"
                )
        node_id = int(capture.get("node_id") or 0)
        target_object = capture.get("target_object")
        if not node_id and not target_object:
            raise RuntimeError("KWin returned no usable PipeWire target")
        print(
            f"[KDE Native] Capture path={capture_path} "
            f"node={node_id or 'none'} target={target_object or 'none'}",
            flush=True,
        )
        _controller_event({
            "type": "kde_capture_ready",
            "slot": slot,
            "node_id": node_id,
            "target_object": target_object,
        })

        print(f"[Monitorize KDE] Streaming native {output_name} ({mode} mode).", flush=True)
        gst = launch_with_fallback(
            pw_fd=None,
            node_id=node_id,
            target_object=target_object,
            width=actual["width"],
            height=actual["height"],
            fps=fps,
            bitrate=bitrate,
            port=port,
            hw_encoder=encoder,
            host=host,
            server_mode=mode == "wifi",
            preserve_source_size=True,
            preserve_source_rate=True,
            rtp_endpoint=rtp_endpoint,
        )
        while gst.poll() is None:
            if helper.poll() is not None:
                remaining = helper.stdout.read().strip()
                print(
                    f"[ERROR] KDE native helper exited unexpectedly"
                    f"{f': {remaining}' if remaining else ''}",
                    flush=True,
                )
                _stop_process(gst)
                return 1
            time.sleep(0.1)
        return gst.returncode or 0
    except (BrokenPipeError, OSError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] KDE native virtual display failed: {exc}", flush=True)
        return 1
    finally:
        cleanup()
