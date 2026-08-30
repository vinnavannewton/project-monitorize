"""Sunshine-backed virtual-display session lifecycle."""

import json
import os
import sys

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal, pyqtSlot

try:
    from PyQt6.QtDBus import QDBusConnection
except ImportError:
    QDBusConnection = None

from monitorize.config import app_log
from monitorize.config.validation import (
    DEFAULT_FPS,
    DEFAULT_PRIMARY_RESOLUTION,
    DEFAULT_SECONDARY_RESOLUTION,
    sanitize_display_type,
    sanitize_fps,
    sanitize_resolution,
)
from monitorize.platform.gnome_virtual_monitor import (
    save_current_virtual_layout as save_current_gnome_virtual_layout,
)
from monitorize.platform.gpu_discovery import normalize_pci_id, resolve_encoding_gpu
from monitorize.platform.process_utils import stop_processes
from monitorize.platform.sunshine_service import (
    check_sunshine_health,
    is_sunshine_running,
    save_sunshine_config,
    start_sunshine,
    stop_sunshine,
    sync_sunshine_stream_config,
)
from monitorize.platform.utils import LINUX_DIR


GNOME_LAYOUT_CHANGE_DEBOUNCE_MS = 750
GNOME_DISPLAY_CONFIG_SERVICE = "org.gnome.Mutter.DisplayConfig"
GNOME_DISPLAY_CONFIG_PATH = "/org/gnome/Mutter/DisplayConfig"
GNOME_DISPLAY_CONFIG_IFACE = "org.gnome.Mutter.DisplayConfig"
GNOME_DISPLAY_CONFIG_SIGNAL = "MonitorsChanged"


class StreamingController(QObject):
    streamingChanged = pyqtSignal(bool)
    statusChanged = pyqtSignal(str)
    secondStreamChanged = pyqtSignal(bool)
    primaryReadyChanged = pyqtSignal(bool)
    logAppended = pyqtSignal(str, str)

    def __init__(self, de, local_ip="", parent=None):
        super().__init__(parent)
        self.de = de
        self.local_ip = local_ip
        self.streaming = False
        self.status = ""
        self.primary_ready = False
        self.streamer = None
        self.third_streamer = None
        self.third_streaming = False
        self.third_ready = False
        self.generation = 0
        self.third_generation = 0
        self.event_buffer = ""
        self.third_event_buffer = ""
        self.gnome_outputs = {}
        self.width, self.height = DEFAULT_PRIMARY_RESOLUTION
        self.fps = DEFAULT_FPS
        self.display_type = "Extend"
        self.encoder = "Auto"
        self.gpu_id = ""
        self.codec = "Auto"
        self.native_pen_touch = True
        self.audio_enabled = False
        self.third_width, self.third_height = DEFAULT_SECONDARY_RESOLUTION
        self.third_fps = DEFAULT_FPS
        self.third_encoder = "Auto"
        self.third_gpu_id = ""
        self.third_codec = "Auto"
        self.third_native_pen_touch = True
        self.third_audio_enabled = False
        self.pending_options = None
        self._is_stopping = False

        self.sunshine_watchdog_timer = QTimer(self)
        self.sunshine_watchdog_timer.setInterval(1000)
        self.sunshine_watchdog_timer.timeout.connect(self._check_sunshine_health)

        self.gnome_layout_change_timer = QTimer(self)
        self.gnome_layout_change_timer.setSingleShot(True)
        self.gnome_layout_change_timer.setInterval(GNOME_LAYOUT_CHANGE_DEBOUNCE_MS)
        self.gnome_layout_change_timer.timeout.connect(self._save_gnome_virtual_layout)
        self.gnome_display_config_bus = None
        self.gnome_display_config_connected = False
        self._gnome_monitors_changed_slot = self._on_gnome_monitors_changed

    def _set_streaming(self, value):
        if self.streaming == value:
            return
        self.streaming = value
        self.streamingChanged.emit(value)

    def _set_status(self, value):
        if self.status == value:
            return
        self.status = value
        self.statusChanged.emit(value)

    def _set_primary_ready(self, value):
        if self.primary_ready == value:
            return
        self.primary_ready = value
        self.primaryReadyChanged.emit(value)

    def update_ip(self, value):
        self.local_ip = value

    def start(
        self,
        res,
        fps,
        display_type="Extend",
        encoder="Auto",
        codec="Auto",
        native_pen_touch=True,
        enable_audio=False,
        options=None,
        gpu_id="",
    ):
        self.stop()
        self.generation += 1
        self.width, self.height = sanitize_resolution(res, DEFAULT_PRIMARY_RESOLUTION)
        self.fps = sanitize_fps(fps)
        self.display_type = sanitize_display_type(display_type)
        self.encoder = str(encoder or "Auto")
        self.gpu_id = normalize_pci_id(gpu_id)
        self.codec = str(codec or "Auto")
        self.native_pen_touch = bool(native_pen_touch)
        self.audio_enabled = bool(enable_audio)
        self.event_buffer = ""
        self.pending_options = options
        self._set_streaming(True)
        self._set_primary_ready(False)

        if self.display_type == "Mirror":
            if not self._start_instance(1, "", self.width, self.height):
                self._set_streaming(False)
                return
            self._set_primary_ready(True)
            self._set_status("Sunshine is mirroring the primary display — ready for Moonlight")
            self._start_pending_second(options)
            return

        self._set_status(f"Creating a virtual display on {self.de.capitalize()}…")
        self.streamer = self._start_display_process(
            "primary", self.width, self.height, self.fps, self.generation
        )

    def _start_display_process(self, slot, width, height, fps, generation):
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        if self.de == "gnome" and slot == "additional" and self.gnome_outputs.get("primary"):
            env.insert("MONITORIZE_GNOME_PRIMARY_OUTPUT", self.gnome_outputs["primary"])

        process = QProcess(self)
        process.setWorkingDirectory(LINUX_DIR)
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        if slot == "primary":
            process.readyReadStandardOutput.connect(
                lambda: self._read_display_process("primary", generation, process)
            )
            process.finished.connect(
                lambda code, status: self._display_finished(
                    "primary", code, status, generation, process
                )
            )
        else:
            process.readyReadStandardOutput.connect(
                lambda: self._read_display_process("additional", generation, process)
            )
            process.finished.connect(
                lambda code, status: self._display_finished(
                    "additional", code, status, generation, process
                )
            )
        process.errorOccurred.connect(
            lambda _error: self._process_error(slot, generation, process)
        )
        process.start(
            sys.executable,
            [
                "-m",
                "monitorize.streaming.headless_virtual_display",
                str(width),
                str(height),
                str(fps),
                slot,
                str(self.de),
            ],
        )
        if self.de == "gnome":
            self._connect_gnome_display_config_signal()
        return process

    @staticmethod
    def _structured_event(line):
        if not line.startswith("MONITORIZE_EVENT "):
            return None
        try:
            return json.loads(line.split(" ", 1)[1])
        except (TypeError, ValueError):
            return None

    def _read_display_process(self, slot, generation, process):
        current_generation = self.generation if slot == "primary" else self.third_generation
        current_process = self.streamer if slot == "primary" else self.third_streamer
        if generation != current_generation or process is not current_process:
            return
        raw = bytes(process.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not raw:
            return
        self.logAppended.emit("DISPLAY", raw)
        buffer_name = "event_buffer" if slot == "primary" else "third_event_buffer"
        combined = getattr(self, buffer_name) + raw
        lines = combined.split("\n")
        setattr(self, buffer_name, lines.pop())
        for line in lines:
            event = self._structured_event(line)
            if event and event.get("type") == "headless_ready":
                self._display_ready(slot, event)
            elif line.startswith("[ERROR]"):
                self._set_status(line.removeprefix("[ERROR]").strip())

    def _display_ready(self, slot, event):
        output_name = str(event.get("name") or "")
        instance = 1 if slot == "primary" else 2
        if not output_name:
            self._set_status("Virtual display started without an output name")
            return
        width = int(event.get("width") or (self.width if instance == 1 else self.third_width))
        height = int(event.get("height") or (self.height if instance == 1 else self.third_height))
        fps = float(event.get("fps") or (self.fps if instance == 1 else self.third_fps))
        if self.de == "gnome":
            self.gnome_outputs[slot] = output_name

        if instance == 1:
            self.width, self.height = width, height
        else:
            self.third_width, self.third_height = width, height

        if not self._start_instance(
            instance,
            output_name,
            width,
            height,
            pipewire_node=event.get("node_id") if self.de == "gnome" else None,
            offset_x=int(event.get("offset_x") or 0),
            offset_y=int(event.get("offset_y") or 0),
        ):
            if instance == 1:
                QTimer.singleShot(0, self.stop)
            else:
                QTimer.singleShot(0, self.stop_third)
            return

        if instance == 1:
            self._set_primary_ready(True)
            self._set_status(
                f"Virtual display {output_name} ({width}x{height}@{fps:g}Hz) is ready for Moonlight"
            )
            self._start_pending_second(self.pending_options)
        else:
            self.third_ready = True
            self._set_status(
                f"Second display {output_name} ({width}x{height}@{fps:g}Hz) is ready on Sunshine port 49089"
            )
            self.secondStreamChanged.emit(True)

    def _start_instance(
        self,
        instance,
        output_name,
        width,
        height,
        pipewire_node=None,
        offset_x=0,
        offset_y=0,
    ):
        encoder = self.encoder if instance == 1 else self.third_encoder
        gpu_id = self.gpu_id if instance == 1 else self.third_gpu_id
        codec = self.codec if instance == 1 else self.third_codec
        native_pen_touch = (
            self.native_pen_touch if instance == 1 else self.third_native_pen_touch
        )
        audio = self.audio_enabled if instance == 1 else self.third_audio_enabled
        if self.de == "kde" and not output_name and os.path.isfile("/.flatpak-info"):
            capture = "portal"
        else:
            capture = "kwin" if self.de == "kde" else ""
        selected_gpu = resolve_encoding_gpu(encoder, gpu_id)
        if gpu_id and not selected_gpu:
            self.logAppended.emit(
                "SUNSHINE",
                f"Selected encoding GPU {gpu_id} is unavailable; using Sunshine automatic selection",
            )
        elif selected_gpu:
            self.logAppended.emit(
                "SUNSHINE",
                f"Using encoding GPU {selected_gpu['label']}",
            )
        adapter_name = selected_gpu.get("render_node", "") if selected_gpu else ""
        sunshine_environment = None
        if selected_gpu and str(encoder).strip().lower() in ("nvidia", "nvenc"):
            cuda_index = selected_gpu.get("cuda_index", "")
            if cuda_index:
                sunshine_environment = {"CUDA_VISIBLE_DEVICES": str(cuda_index)}
        ok, message = sync_sunshine_stream_config(
            output_name,
            encoder,
            codec,
            native_pen_touch,
            instance=instance,
            capture=capture,
            adapter_name=adapter_name,
        )
        if not ok:
            self._set_status(message)
            self.logAppended.emit("SUNSHINE", f"ERROR: {message}")
            return False
        save_sunshine_config(
            {"stream_audio": "enabled" if audio else "disabled"}, instance=instance
        )
        start_kwargs = {
            "pipewire_node": pipewire_node,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "width": width,
            "height": height,
        }
        if sunshine_environment:
            start_kwargs["extra_environment"] = sunshine_environment
        ok, message = start_sunshine(instance, **start_kwargs)
        if not ok:
            self._set_status(message)
            self.logAppended.emit("SUNSHINE", f"ERROR: {message}")
            return False
        self.logAppended.emit("SUNSHINE", message)
        QTimer.singleShot(1500, self.sunshine_watchdog_timer.start)
        return True

    def _process_error(self, slot, generation, process):
        current_generation = self.generation if slot == "primary" else self.third_generation
        current_process = self.streamer if slot == "primary" else self.third_streamer
        if generation == current_generation and process is current_process:
            message = f"Virtual display process error: {process.errorString()}"
            self.logAppended.emit("DISPLAY", message)
            self._set_status(message)

    def _display_finished(self, slot, code, _status, generation, process):
        current_generation = self.generation if slot == "primary" else self.third_generation
        current_process = self.streamer if slot == "primary" else self.third_streamer
        if generation != current_generation or process is not current_process:
            return
        if slot == "primary":
            self.streamer = None
            self._set_primary_ready(False)
            if self.streaming:
                message = self.status if code else "Virtual display stopped unexpectedly"
                self.logAppended.emit("DISPLAY", f"Process exited with code {code}")
                self.stop()
                self._set_status(message)
        else:
            self.third_streamer = None
            if self.third_streaming:
                self.logAppended.emit("DISPLAY", f"Second display exited with code {code}")
                self.stop_third()

    def start_third(
        self,
        res,
        fps,
        encoder="Auto",
        codec="Auto",
        native_pen_touch=True,
        enable_audio=False,
        gpu_id="",
    ):
        if not self.streaming or not self.primary_ready:
            self._set_status("Start the primary display before adding another display")
            return
        if self.de not in ("kde", "gnome", "hyprland", "sway"):
            self._set_status("Additional displays require KDE, GNOME, Hyprland, or Sway")
            return
        if self.third_streaming:
            self.stop_third()
        self.third_width, self.third_height = sanitize_resolution(
            res, DEFAULT_SECONDARY_RESOLUTION
        )
        self.third_fps = sanitize_fps(fps)
        self.third_encoder = str(encoder or "Auto")
        self.third_gpu_id = normalize_pci_id(gpu_id)
        self.third_codec = str(codec or "Auto")
        self.third_native_pen_touch = bool(native_pen_touch)
        self.third_audio_enabled = bool(enable_audio)
        self.third_generation += 1
        self.third_event_buffer = ""
        self.third_streaming = True
        self.third_ready = False
        self.secondStreamChanged.emit(True)
        self._set_status("Creating the second virtual display…")
        self.third_streamer = self._start_display_process(
            "additional",
            self.third_width,
            self.third_height,
            self.third_fps,
            self.third_generation,
        )

    def stop_third(self):
        if not self.third_streaming and self.third_streamer is None:
            return
        self._save_gnome_virtual_layout()
        self.third_generation += 1
        process = self.third_streamer
        self.third_streamer = None
        if process is not None:
            try:
                if process.state() == QProcess.ProcessState.Running:
                    process.write(b"quit\n")
                    process.waitForBytesWritten(500)
            except Exception:
                pass
            stop_processes(process)
        stop_sunshine(instance=2)
        self.gnome_outputs.pop("additional", None)
        self.third_streaming = False
        self.third_ready = False
        self.secondStreamChanged.emit(False)
        self.logAppended.emit("DISPLAY", "Second virtual display stopped")

    def third_active(self):
        return self.third_streaming

    def active_configuration(self):
        config = {
            "version": 2,
            "primary": {
                "resolution": f"{self.width}x{self.height}",
                "fps": str(self.fps),
                "display_type": self.display_type,
                "sunshine_encoder": self.encoder,
                "sunshine_gpu": self.gpu_id,
                "sunshine_codec": self.codec,
                "sunshine_native_pen_touch": self.native_pen_touch,
                "enable_audio": self.audio_enabled,
            },
            "second": {"enabled": self.third_streaming},
        }
        if self.third_streaming:
            config["second"].update(
                resolution=f"{self.third_width}x{self.third_height}",
                fps=str(self.third_fps),
                sunshine_encoder=self.third_encoder,
                sunshine_gpu=self.third_gpu_id,
                sunshine_codec=self.third_codec,
                sunshine_native_pen_touch=self.third_native_pen_touch,
                enable_audio=self.third_audio_enabled,
            )
        return config

    def _start_pending_second(self, options):
        second = (options or {}).get("second")
        if not second or not second.get("enabled"):
            return
        QTimer.singleShot(
            0,
            lambda: self.start_third(
                second["resolution"],
                second["fps"],
                second.get("sunshine_encoder", "Auto"),
                second.get("sunshine_codec", "Auto"),
                second.get("sunshine_native_pen_touch", True),
                second.get("enable_audio", False),
                gpu_id=second.get("sunshine_gpu", ""),
            ),
        )

    def _check_sunshine_health(self):
        if not self.streaming:
            self.sunshine_watchdog_timer.stop()
            return
        alive, exit_code, error = check_sunshine_health(1)
        if not alive:
            message = "Sunshine instance 1 stopped unexpectedly"
            if exit_code is not None:
                message += f" (exit code {exit_code})"
            if error:
                message += f": {error}"
            app_log.error(message)
            self.logAppended.emit("SUNSHINE", f"ERROR: {message}")
            self._set_status(message)
            QTimer.singleShot(0, self.stop)
            return
        if self.third_streaming:
            alive, exit_code, error = check_sunshine_health(2)
            if not alive:
                message = "Sunshine instance 2 stopped unexpectedly"
                if exit_code is not None:
                    message += f" (exit code {exit_code})"
                if error:
                    message += f": {error}"
                self.logAppended.emit("SUNSHINE", f"ERROR: {message}")
                self._set_status(message)
                QTimer.singleShot(0, self.stop_third)

    def _should_track_gnome_virtual_layout(self):
        return self.de == "gnome" and self.streaming and bool(self.gnome_outputs)

    def _save_gnome_virtual_layout(self):
        if not self._should_track_gnome_virtual_layout():
            return False
        topology = "+".join(
            role for role in ("primary", "additional") if self.gnome_outputs.get(role)
        )
        return save_current_gnome_virtual_layout(
            topology, role_connectors=dict(self.gnome_outputs)
        )

    def _connect_gnome_display_config_signal(self):
        if self.gnome_display_config_connected or QDBusConnection is None:
            return
        try:
            bus = QDBusConnection.sessionBus()
            connected = bus.connect(
                GNOME_DISPLAY_CONFIG_SERVICE,
                GNOME_DISPLAY_CONFIG_PATH,
                GNOME_DISPLAY_CONFIG_IFACE,
                GNOME_DISPLAY_CONFIG_SIGNAL,
                self._gnome_monitors_changed_slot,
            )
        except Exception:
            return
        if connected:
            self.gnome_display_config_bus = bus
            self.gnome_display_config_connected = True

    def _disconnect_gnome_display_config_signal(self):
        if not self.gnome_display_config_connected:
            return
        try:
            self.gnome_display_config_bus.disconnect(
                GNOME_DISPLAY_CONFIG_SERVICE,
                GNOME_DISPLAY_CONFIG_PATH,
                GNOME_DISPLAY_CONFIG_IFACE,
                GNOME_DISPLAY_CONFIG_SIGNAL,
                self._gnome_monitors_changed_slot,
            )
        except Exception:
            pass
        self.gnome_display_config_bus = None
        self.gnome_display_config_connected = False

    @pyqtSlot()
    def _on_gnome_monitors_changed(self):
        if self._should_track_gnome_virtual_layout():
            self.gnome_layout_change_timer.start()

    def stop(self):
        if self._is_stopping:
            return
        self._is_stopping = True
        try:
            self._save_gnome_virtual_layout()
            self.gnome_layout_change_timer.stop()
            self._disconnect_gnome_display_config_signal()
            self.sunshine_watchdog_timer.stop()
            self.generation += 1
            if self.third_streaming or self.third_streamer is not None:
                self.stop_third()
            process = self.streamer
            self.streamer = None
            if process is not None:
                try:
                    if process.state() == QProcess.ProcessState.Running:
                        process.write(b"quit\n")
                        process.waitForBytesWritten(500)
                except Exception:
                    pass
                stop_processes(process)
            stop_sunshine()
            self.gnome_outputs.clear()
            self._set_primary_ready(False)
            self._set_streaming(False)
        finally:
            self._is_stopping = False
