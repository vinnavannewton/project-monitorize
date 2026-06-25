"""Input daemon orchestration."""

import logging
import signal
import threading
import time

from .dispatcher import InputDispatcher
from .geometry import Geometry, detect_de
from .libei_backend import LibeiBackend
from .transport import run_tcp_server, run_udp_server
from .uinput_backend import UInputBackend

log = logging.getLogger("TouchDaemon")


class InputDaemon:
    def __init__(
        self, width, height, wifi=False, stylus_features=False,
        stylus_only=False, de=None, udp_host="0.0.0.0", udp_port=7113,
    ):
        self.shutdown = threading.Event()
        self.de = de or detect_de()
        self.wifi = wifi
        self.udp_host = udp_host
        self.udp_port = udp_port
        self.stylus_features = stylus_features and self.de in (
            "kde", "gnome", "hyprland", "sway"
        )
        self.geometry = Geometry(self.de, width, height)
        backend_type = UInputBackend if (
            self.de in ("hyprland", "sway") or self.stylus_features
        ) else LibeiBackend
        self.backend = backend_type(self.geometry, self.shutdown)
        self.dispatcher = InputDispatcher(self.backend, stylus_only)

    def run(self):
        signal.signal(signal.SIGINT, self.close)
        signal.signal(signal.SIGTERM, self.close)
        if not self._setup_backend():
            return
        transport = run_udp_server if self.wifi else run_tcp_server
        args = (
            (self.dispatcher, self.shutdown, self.geometry, self.udp_host, self.udp_port)
            if self.wifi else (self.dispatcher, self.shutdown)
        )
        threading.Thread(target=transport, args=args, daemon=True).start()
        while not self.shutdown.is_set():
            time.sleep(0.5)

    def _setup_backend(self):
        try:
            self.backend.setup(self.stylus_features)
            return True
        except Exception as exc:
            log.error("%s", exc)
            self.shutdown.set()
            self.backend.close()
            return False

    def close(self, *_args):
        self.shutdown.set()
        self.backend.close()
