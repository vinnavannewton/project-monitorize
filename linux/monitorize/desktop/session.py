"""Session-page orchestration: saved cards, live displays, and stream startup."""

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from monitorize.config.settings import load_display_settings


class Session(QObject):
    changed = pyqtSignal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.count = 0
        self.running = False
        self.start_requested = False
        self._driving = False
        self.preset_configuration = None
        controller.streamingChanged.connect(self._active_changed)
        controller.primaryReadyChanged.connect(self._changed)
        controller.secondStreamChanged.connect(self._changed)
        controller.statusChanged.connect(self._changed)
        controller.startFailed.connect(self._startup_failed)

    def _startup_failed(self):
        self.start_requested = False
        self.changed.emit()

    @property
    def busy(self):
        c = self.controller
        return self.start_requested or (c.streaming and not c.primary_ready) or (
            c.third_streaming and not c.third_ready
        )

    @property
    def mode(self):
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

    def configuration(self):
        if self.preset_configuration:
            return self._preset_values(self.preset_configuration["primary"])
        saved = load_display_settings()
        res = saved.get("resolution", "1920x1080")
        if res == "Custom...":
            res = saved.get("custom_w", "1920") + "x" + saved.get("custom_h", "1080")
        else:
            res = res.split(" ")[0]
        fps = saved.get("fps", "60")
        if fps == "Custom...":
            fps = saved.get("custom_fps", "60")
        custom = saved.get("streaming_customized", False)
        return dict(
            res=res, fps=fps, display_type=saved.get("display_type", "Extend"),
            encoder=saved.get("sunshine_encoder", "Auto") if custom else "Auto",
            codec=saved.get("sunshine_codec", "Auto") if custom else "Auto",
            gpu_id=saved.get("sunshine_gpu", "") if custom else "",
            native_pen_touch=saved.get("sunshine_native_pen_touch", True),
            mirror_output=saved.get("mirror_output", ""),
            enable_audio=saved.get("enable_audio", False),
        )

    @staticmethod
    def _preset_values(saved):
        return dict(res=saved["resolution"], fps=saved["fps"],
                    display_type=saved.get("display_type", "Extend"),
                    encoder=saved.get("sunshine_encoder", "Auto"),
                    codec=saved.get("sunshine_codec", "Auto"),
                    gpu_id=saved.get("sunshine_gpu", ""),
                    native_pen_touch=saved.get("sunshine_native_pen_touch", True),
                    mirror_output=saved.get("mirror_output", ""),
                    enable_audio=saved.get("enable_audio", False))

    def second_configuration(self):
        if self.preset_configuration and self.preset_configuration.get("second", {}).get("enabled"):
            return self._preset_values(self.preset_configuration["second"])
        return self.configuration()

    def _prepare_primary(self):
        self.controller.start(**self.configuration(), options={"prepare_only": True})

    def _prepare_second(self):
        config = self.second_configuration()
        config.pop("display_type")
        config.pop("mirror_output", None)
        self.controller.start_third(**config)

    def add(self):
        if self.mode != "Extend" or self.count >= 2 or self.busy:
            return
        self.count += 1
        self.changed.emit()

    @property
    def pending_displays(self):
        c = self.controller
        return self.mode == "Extend" and self.count > (
            int(c.streaming) + int(c.third_streaming)
        )

    def start(self):
        if self.busy:
            return
        if self.running:
            if self.pending_displays:
                self._prepare_second()
            return
        if self.mode == "Mirror":
            self.controller.start(**self.configuration())
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
        self.controller.stop()
        self.controller._set_status("Session stopped. Start to recreate your displays." if self.count else "Session stopped.")
        self.changed.emit()

    def remove(self, index):
        if index < 0 or index >= self.count or self.busy:
            return
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

    def cards(self):
        c = self.controller
        result = []
        for i in range(self.count):
            live = c.streaming if i == 0 else c.third_streaming
            ready = c.primary_ready if i == 0 else c.third_ready
            state = "Not started"
            if live:
                state = "Starting…" if not ready else ("Ready for Moonlight" if self.running else "Display ready")
            result.append({"number": i + 1, "state": state,
                           "live": bool(live and ready),
                           "address": f"{c.local_ip}:{47989 if i == 0 else 49089}"})
        return result
