"""Session-page orchestration: saved cards, live displays, and stream startup."""

import os
import copy

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from monitorize.config import app_log
from monitorize.config.settings import load_display_settings, load_second_display_settings, load_general_settings


class Session(QObject):
    changed = pyqtSignal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.client_runtime = None
        self.running = False
        self.start_requested = False
        self._driving = False
        self.runtime_configuration = None
        self.preset_configuration = None
        self.count = self._configured_display_count()
        controller.streamingChanged.connect(self._active_changed)
        controller.primaryReadyChanged.connect(self._changed)
        controller.secondStreamChanged.connect(self._changed)
        controller.statusChanged.connect(self._changed)
        controller.startFailed.connect(self._startup_failed)

    @property
    def active(self):
        return (self.controller.streaming or bool(self.client_runtime and self.client_runtime.active)
                or getattr(getattr(self.controller, "mirror_retry", None), "pending", False) is True)

    @property
    def ready(self):
        if self.client_runtime and self.client_runtime.active:
            return any(slot.status in ("waiting", "ready", "streaming", "reconnecting")
                       for slot in self.client_runtime.slots.values())
        return self.controller.streaming and self.controller.primary_ready

    @property
    def retiring(self):
        return bool(self.client_runtime and getattr(self.client_runtime, "retiring", False))

    def _client_changed(self):
        self.running = self.active
        self.changed.emit()

    def _runtime(self):
        if self.client_runtime is None:
            from monitorize.desktop.client_display_runtime import ClientDisplayRuntime
            self.client_runtime = ClientDisplayRuntime(self)
            self.client_runtime.changed.connect(self._client_changed)
            self.client_runtime.logAppended.connect(self.controller.logAppended)
        return self.client_runtime

    def has_client_cards(self):
        if self.controller.streaming_backend == "none" or self.configuration()["display_type"] != "Extend":
            return False
        return True

    def display_behavior(self):
        settings = self.preset_configuration if self.preset_configuration is not None else (self.runtime_configuration or load_general_settings())
        return settings.get('display_behavior', 'extend')

    def uses_managed_runtime(self):
        return self.has_client_cards() or (self.controller.streaming_backend != 'none' and self.mode == 'Extend' and self.display_behavior() != 'extend')

    def configured_snapshot(self):
        """Save configured intent, including a listener that has no display yet."""
        if self.preset_configuration:
            return copy.deepcopy(self.preset_configuration)
        primary, second = self._primary_settings(), self._second_settings()
        for raw in (primary, second):
            values = self._saved_values(raw)
            raw["resolution"], raw["fps"] = values["res"], values["fps"]
        return {"version": 2, "primary": primary, "display_behavior": self.display_behavior(),
                "second": {**second, "enabled": self.count == 2}}

    def _start_client_cards(self, listeners_only=False):
        if self.controller.streaming_backend == "none":
            self.controller._set_error("Client display settings require Sunshine.")
            return
        if self.controller.streaming:
            self.controller._set_error("Stop the current session before changing display settings.")
            return
        runtime = self._runtime()
        general = load_general_settings()
        configs = [(1, self.configuration())]
        if self.count == 2:
            configs.append((2, self.second_configuration()))
        for instance, config in configs:
            if instance in runtime.slots:
                continue
            config = {**config, "de": self.controller.de,
                      "display_behavior": self.display_behavior(),
                      "resolution": config["res"],
                      "client_restart_on_crash": True}
            if not runtime.start_slot(instance, config):
                self.controller._set_error(str(runtime.error))
                self.controller.startFailed.emit()
        self.running = bool(runtime.active)
        self.changed.emit()

    def _startup_failed(self):
        self.start_requested = False
        self.changed.emit()

    @property
    def busy(self):
        c = self.controller
        return bool(self.client_runtime and self.client_runtime.busy) or self.retiring or bool(c._vkms_retiring) or self.start_requested or (c.streaming and not c.primary_ready) or (
            c.third_streaming and not c.third_ready
        )

    @property
    def mode(self):
        if self.client_runtime and self.client_runtime.active:
            return "Extend"
        if self.controller.streaming:
            return self.controller.display_type
        return self.configuration()["display_type"]

    def _active_changed(self, active):
        if not active:
            self.running = False
            self.start_requested = False
        elif not self.controller.prepare_only:
            self.running = True
            self.count = 1 if self.controller.display_type == "Extend" else 0
        self._changed()

    def _changed(self, *_args):
        if self.controller.third_streaming:
            self.count = 2
        self.changed.emit()
        if self.start_requested:
            QTimer.singleShot(0, self._drive_start)

    def _primary_settings(self):
        return copy.deepcopy(self.runtime_configuration["primary"]) if self.runtime_configuration else load_display_settings()

    def _second_settings(self):
        return copy.deepcopy(self.runtime_configuration["second"]) if self.runtime_configuration else load_second_display_settings()

    def configuration(self):
        if self.preset_configuration:
            return self._preset_values(self.preset_configuration["primary"])
        return self._saved_values(self._primary_settings())

    @staticmethod
    def _saved_values(saved, *, virtual_display_creator=None):
        resolution_is_custom = saved.get("resolution", "1920x1080") == "Custom..."
        res = saved.get("resolution", "1920x1080")
        if resolution_is_custom:
            res = saved.get("custom_w", "1920") + "x" + saved.get("custom_h", "1080")
        else:
            res = res.split(" ")[0]
        fps = saved.get("fps", "60")
        if fps == "Custom...":
            fps = saved.get("custom_fps", "60")
        custom = saved.get("streaming_customized", False)
        return dict(
            res=res, fps=fps, display_type=saved.get("display_type", "Extend"),
            use_client_display_settings=bool(saved.get("use_client_display_settings", False)),
            virtual_display_creator=(
                virtual_display_creator or saved.get("virtual_display_creator", "native")
            ),
            encoder=saved.get("sunshine_encoder", "Auto") if custom else "Auto",
            codec=saved.get("sunshine_codec", "Auto") if custom else "Auto",
            capture=saved.get("sunshine_capture", "auto"),
            gpu_id=saved.get("sunshine_gpu", "") if custom else "",
            native_pen_touch=saved.get("sunshine_native_pen_touch", True),
            mirror_output=saved.get("mirror_output", ""),
            enable_audio=saved.get("enable_audio", False),
        )

    @staticmethod
    def _preset_values(saved):
        return dict(res=saved["resolution"], fps=saved["fps"],
                    use_client_display_settings=bool(saved.get("use_client_display_settings", False)),
                    display_type=saved.get("display_type", "Extend"),
                    virtual_display_creator=(
                        saved.get("virtual_display_creator", "native")
                    ),
                    encoder=saved.get("sunshine_encoder", "Auto"),
                    codec=saved.get("sunshine_codec", "Auto"),
                    capture=saved.get("sunshine_capture", "auto"),
                    gpu_id=saved.get("sunshine_gpu", ""),
                    native_pen_touch=saved.get("sunshine_native_pen_touch", True),
                    mirror_output=saved.get("mirror_output", ""),
                    enable_audio=saved.get("enable_audio", False))

    def second_configuration(self):
        if self.preset_configuration and self.preset_configuration.get("second", {}).get("enabled"):
            config = self._preset_values(self.preset_configuration["second"])
            config["virtual_display_creator"] = self.configuration()["virtual_display_creator"]
            return config
        primary = self.configuration()
        return self._saved_values(
            self._second_settings(),
            virtual_display_creator=primary["virtual_display_creator"],
        )

    def _configured_display_count(self):
        if self.configuration()["display_type"] != "Extend":
            return 0
        return 2 if self._second_settings().get("enabled", False) else 1

    def _prepare_primary(self):
        config = self.configuration()
        app_log.write(
            "DISPLAY",
            f"Session display request (primary): res={config.get('res')} fps={config.get('fps')} "
            f"display_type={config.get('display_type')} creator={config.get('virtual_display_creator')} "
            f"vkms={config.get('virtual_display_creator') == 'vkms'}",
        )
        config.pop("use_client_display_settings", None)
        self.controller.start(**config, options={"prepare_only": True})

    def _prepare_second(self):
        config = self.second_configuration()
        app_log.write(
            "DISPLAY",
            f"Session display request (second): res={config.get('res')} fps={config.get('fps')} "
            f"vkms={self.configuration().get('virtual_display_creator') == 'vkms'}",
        )
        config.pop("use_client_display_settings", None)
        config.pop("display_type")
        config.pop("mirror_output", None)
        config.pop("virtual_display_creator", None)
        self.controller.start_third(**config)

    @property
    def max_displays(self):
        return 2

    def configuration_changed(self):
        if not self.active:
            self.count = self._configured_display_count()
        self.changed.emit()

    def add(self):
        if self.mode != "Extend" or self.count >= self.max_displays or self.busy:
            return
        self.count += 1
        self.changed.emit()

    @property
    def pending_displays(self):
        if self.client_runtime and self.client_runtime.active:
            return len(self.client_runtime.slots) < self.count
        c = self.controller
        return self.mode == "Extend" and self.count > (
            int(c.streaming) + int(c.third_streaming)
        )

    def start(self, *, listeners_only=False):
        if self.busy:
            return
        guardian = getattr(self.client_runtime, 'guardian', None)
        owns_guardian = guardian and guardian.process and guardian.process.poll() is None
        if not self.active and not owns_guardian:
            from monitorize.streaming.recovery_guardian import recover_before_cleanup
            try:
                if not recover_before_cleanup():
                    raise RuntimeError('Previous display recovery is still running. Wait before starting another session.')
            except Exception as exc:
                self.controller._set_error(str(exc))
                self.controller.startFailed.emit()
                return
        if self.uses_managed_runtime():
            self._start_client_cards(listeners_only)
            return
        if listeners_only:
            return
        if self.running:
            if self.pending_displays:
                self._prepare_second()
            return
        if self.mode == "Mirror":
            config = self.configuration()
            config.pop("use_client_display_settings", None)
            self.controller.start(**config)
            return
        if not self.count:
            return
        if not self.controller.streaming:
            self._prepare_primary()
        self.start_requested = True
        self.changed.emit()
        self._drive_start()

    def _drive_start(self):
        c = self.controller
        if self._driving or not self.start_requested or not c.streaming or not c.primary_ready:
            return
        self._driving = True
        try:
            if self.count == 2 and not c.third_streaming:
                self._prepare_second()
                if not c.third_streaming:
                    self.start_requested = False
                    self.changed.emit()
                return
            if self.count == 2 and not c.third_ready:
                return
            c.pending_options = None
            self.start_requested = False
            if c.streaming_backend == "none":
                self.changed.emit()
                return
            for prefix in ("", "third_"):
                config = self.second_configuration() if prefix else self.configuration()
                for field, key in (("encoder", "encoder"), ("codec", "codec"),
                                   ("capture", "capture"),
                                   ("gpu_id", "gpu_id"), ("native_pen_touch", "native_pen_touch"),
                                   ("audio_enabled", "enable_audio")):
                    setattr(c, prefix + field, config[key])
            c.prepare_only = False
            self.running = True
            for slot in ("primary", "additional"):
                event = c.display_events.get(slot)
                if event:
                    c._display_ready(slot, event)
            self.changed.emit()
        finally:
            self._driving = False

    def stop(self):
        self.start_requested = False
        self.running = False
        if self.client_runtime:
            self.client_runtime.stop()
        self.controller.stop()
        self.controller._set_status("Stopping VKMS displays…" if self.controller._vkms_retiring else
                                    "Session stopped. Start to recreate your displays." if self.count else "Session stopped.")
        self.changed.emit()

    def remove(self, index):
        if index < 0 or index >= self.count or self.busy or (self.client_runtime and self.client_runtime.active):
            return False
        if index == 1 and self.controller.third_streaming:
            self.controller.stop_third()
        elif index == 0 and self.controller.streaming:
            self.stop()
        self.count -= 1
        if self.preset_configuration:
            if index == 0 and self.count:
                self.preset_configuration["primary"] = self.preset_configuration["second"]
            self.preset_configuration["second"] = {"enabled": False}
            if not self.count:
                self.preset_configuration = None
        self.changed.emit()
        return True

    def cards(self):
        c = self.controller
        if self.client_runtime and self.client_runtime.active:
            states = {"waiting": "Waiting for client", "preparing": "Preparing display…",
                      "ready": "Display ready — starting stream", "streaming": "Streaming",
                      "reconnecting": "Waiting for reconnect", "recovering": "Recovering host…",
                      "starting": "Starting host…", "stopping": "Stopping…", "error": "Error"}
            live = {card["instance"]: card for card in self.client_runtime.cards}
            result = []
            for instance in range(1, self.count + 1):
                card = live.get(instance, {})
                target = card.get("target", {})
                state = states.get(card.get("status"), "Not started")
                if target:
                    state += f" — {target['width']} × {target['height']} @ {target['fps']:g} Hz"
                result.append(dict(number=instance, title=f"Monitorize — Display {instance}",
                    mirror=False, state=state, live=card.get("status") == "streaming",
                    address=f"{c.local_ip}:{47989 if instance == 1 else 49089}"))
            return result
        if self.mode == "Mirror":
            configured_output = str(
                self.configuration().get("mirror_output") or ""
            )
            output_name = (
                str(c.mirror_output or configured_output)
                if c.streaming and c.display_type == "Mirror"
                else configured_output
            )
            live = bool(
                c.streaming
                and c.primary_ready
                and c.display_type == "Mirror"
            )
            return [{
                "number": 1,
                "title": output_name or "No monitor selected",
                "mirror": True,
                "state": "Ready for Moonlight" if live else "Not started",
                "live": live,
                "address": f"{c.local_ip}:47989",
            }]

        result = []
        for i in range(self.count):
            live = c.streaming if i == 0 else c.third_streaming
            ready = c.primary_ready if i == 0 else c.third_ready
            state = "Not started"
            if live:
                state = "Starting…" if not ready else ("Ready for Moonlight" if self.running else "Display ready")
            result.append({"number": i + 1,
                           "title": f"Virtual display {i + 1}",
                           "mirror": False, "state": state,
                           "live": bool(live and ready),
                           "address": f"{c.local_ip}:{47989 if i == 0 else 49089}"})
        return result
