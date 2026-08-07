"""Optional RTP/Opus playback for the Linux desktop receiver."""

import json
import os
import socket
import threading

from monitorize.platform.process_utils import gst_has_element
from monitorize.streaming.audio_sender import (
    CHANNELS,
    CONTROL_PREFIX,
    PACKET_MS,
    RTP_PAYLOAD_TYPE,
    SAMPLE_RATE,
    TRANSPORT,
)


AUDIO_PORT = 7120
INITIAL_ATTEMPTS = 5
RETRY_DELAY_SECONDS = 0.5
CONTROL_LIMIT = 4096


def build_start(port):
    message = json.dumps({
        "transport": TRANSPORT,
        "type": "start",
        "port": int(port),
    }, separators=(",", ":")).encode("utf-8")
    return CONTROL_PREFIX + message + b"\n"


def parse_ready(data):
    if not data.startswith(CONTROL_PREFIX):
        return None
    try:
        message = json.loads(data[len(CONTROL_PREFIX):].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    expected = {
        "status": "ready",
        "version": 1,
        "transport": TRANSPORT,
        "codec": "OPUS",
        "sampleRate": SAMPLE_RATE,
        "channels": CHANNELS,
        "packetMs": PACKET_MS,
        "rtpPt": RTP_PAYLOAD_TYPE,
    }
    if not isinstance(message, dict) or any(
        message.get(key) != value for key, value in expected.items()
    ):
        return None
    return message


def audio_pipeline(sink="pulsesink"):
    pipeline = [
        "udpsrc", "name=audio_source", "buffer-size=262144",
        "caps=application/x-rtp,media=audio,encoding-name=OPUS,"
        f"payload={RTP_PAYLOAD_TYPE},clock-rate={SAMPLE_RATE}", "!",
        "rtpjitterbuffer", "name=audio_jitter", "latency=80",
        "drop-on-latency=true", "do-lost=true", "!",
        "rtpopusdepay", "!",
        "opusdec", "name=audio_decoder", "plc=true", "!",
        "audioconvert", "!", "audioresample", "!",
        f"audio/x-raw,format=S16LE,layout=interleaved,rate={SAMPLE_RATE},channels={CHANNELS}",
        "!", sink, "name=audio_sink", "sync=true",
    ]
    if sink == "pulsesink":
        pipeline.extend([
            "async=false", "client-name=Monitorize",
            "buffer-time=40000", "latency-time=10000",
        ])
    return pipeline


def attach_udp_socket(pipeline, raw_socket, gio):
    source = pipeline.get_by_name("audio_source")
    if source is None:
        raise RuntimeError("audio UDP source is missing")
    gst_socket = gio.Socket.new_from_fd(os.dup(raw_socket.fileno()))
    source.set_property("socket", gst_socket)
    source.set_property("close-socket", True)
    return gst_socket


def _load_gst():
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, Gst

    Gst.init(None)
    return Gst, Gio


def _negotiate(host, udp_port, control_port=AUDIO_PORT):
    with socket.create_connection((host, control_port), timeout=0.5) as control:
        control.settimeout(0.5)
        control.sendall(build_start(udp_port))
        response = b""
        while b"\n" not in response and len(response) < CONTROL_LIMIT:
            chunk = control.recv(CONTROL_LIMIT - len(response))
            if not chunk:
                break
            response += chunk
    ready = parse_ready(response.split(b"\n", 1)[0])
    if ready is None:
        raise RuntimeError("audio control response rejected")
    return ready


class LinuxAudioReceiver:
    def __init__(self, log=None):
        self.log = log or (lambda _message: None)
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = None
        self._host = ""

    def start(self, host):
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and self._host == host:
                return
        self.stop()
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._run,
            args=(host, stop_event),
            name="MonitorizeLinuxAudioReceiver",
            daemon=True,
        )
        with self._lock:
            self._host = host
            self._stop_event = stop_event
            self._thread = thread
        thread.start()

    def stop(self):
        with self._lock:
            stop_event = self._stop_event
            thread = self._thread
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        with self._lock:
            if self._thread is thread:
                self._thread = None
                self._stop_event = None
                self._host = ""

    def _emit(self, message):
        try:
            self.log(message)
        except Exception:
            pass

    def _run(self, host, stop_event):
        attempts = 0
        ever_connected = False

        def mark_connected():
            nonlocal ever_connected
            ever_connected = True

        try:
            while not stop_event.is_set():
                try:
                    self._receive_once(host, stop_event, mark_connected)
                except Exception as exc:
                    attempts += 1
                    if ever_connected:
                        self._emit(f"Audio interrupted; retrying: {exc}")
                    elif attempts >= INITIAL_ATTEMPTS:
                        self._emit(
                            "Audio unavailable — the host may have Enable Audio disabled "
                            f"or be missing an audio plugin ({exc})."
                        )
                        return
                if stop_event.wait(RETRY_DELAY_SECONDS):
                    return
        finally:
            with self._lock:
                if self._stop_event is stop_event:
                    self._thread = None
                    self._stop_event = None
                    self._host = ""

    def _receive_once(self, host, stop_event, mark_connected):
        Gst, Gio = _load_gst()
        sinks = [
            sink for sink in ("pulsesink", "autoaudiosink")
            if gst_has_element(sink)
        ]
        if not sinks:
            raise RuntimeError("no GStreamer audio sink is installed")

        raw_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        raw_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
        raw_socket.bind(("", 0))
        try:
            raw_socket.sendto(b"\0", (host, AUDIO_PORT))
            _negotiate(host, raw_socket.getsockname()[1])
            for index, sink in enumerate(sinks):
                pipeline = None
                gst_socket = None
                try:
                    pipeline = Gst.parse_launch(" ".join(audio_pipeline(sink)))
                    gst_socket = attach_udp_socket(pipeline, raw_socket, Gio)
                    bus = pipeline.get_bus()
                    result = pipeline.set_state(Gst.State.PLAYING)
                    if result == Gst.StateChangeReturn.FAILURE:
                        raise RuntimeError("GStreamer rejected the audio pipeline")
                    connected = False
                    message_types = (
                        Gst.MessageType.ERROR
                        | Gst.MessageType.EOS
                        | Gst.MessageType.STATE_CHANGED
                    )
                    while not stop_event.is_set():
                        message = bus.timed_pop_filtered(
                            100 * Gst.MSECOND, message_types
                        )
                        if message is None:
                            continue
                        if message.type == Gst.MessageType.STATE_CHANGED:
                            if message.src == pipeline:
                                _old, new, _pending = message.parse_state_changed()
                                if new == Gst.State.PLAYING and not connected:
                                    connected = True
                                    mark_connected()
                                    self._emit(
                                        f"Audio connected: Opus "
                                        f"{SAMPLE_RATE // 1000} kHz mono, "
                                        f"80 ms jitter buffer ({sink})."
                                    )
                            continue
                        if message.type == Gst.MessageType.ERROR:
                            error, _debug = message.parse_error()
                            raise RuntimeError(error.message)
                        raise RuntimeError("audio stream ended")
                    return
                except Exception as exc:
                    if stop_event.is_set() or index == len(sinks) - 1:
                        raise
                    self._emit(f"PulseAudio playback unavailable; trying system audio: {exc}")
                finally:
                    if pipeline is not None:
                        pipeline.set_state(Gst.State.NULL)
                    gst_socket = None
        finally:
            raw_socket.close()
