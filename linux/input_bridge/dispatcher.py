"""Backend-independent touch and stylus dispatch."""

import logging
import threading
import time

from .protocol import (
    ACTION_HOVER, ACTION_MOVE, ACTION_UP, PAYLOAD_SIZE, PEN_EXT_SIZE,
    PKT_PEN, PKT_PEN_EXT, PKT_TOUCH, unpack_packet,
)

log = logging.getLogger("TouchDaemon")


class InputDispatcher:
    def __init__(self, backend, stylus_only=False, suppression_seconds=5.0):
        self.backend = backend
        self.stylus_only = stylus_only
        self.suppression_seconds = suppression_seconds
        self.active_fingers = {}
        self.last_stylus_input = 0.0
        self.logged_dropped_pen = False
        self.lock = threading.RLock()

    @staticmethod
    def pen_touch_cid(cid):
        return 10005 + (cid % 5)

    def _finger_suppressed(self):
        return self.stylus_only or (
            self.last_stylus_input > 0
            and time.monotonic() - self.last_stylus_input < self.suppression_seconds
        )

    def _release_fingers_locked(self):
        items = list(self.active_fingers.items())
        for index, (cid, (x, y)) in enumerate(items):
            self.backend.inject_touch(ACTION_UP, cid, x, y, index == len(items) - 1)
        self.active_fingers.clear()

    def _release_fingers(self):
        with self.lock:
            self._release_fingers_locked()

    def release_all(self, reason=""):
        with self.lock:
            if reason:
                log.info("Releasing active input: %s", reason)
            self._release_fingers_locked()
            self.last_stylus_input = 0.0
            self.logged_dropped_pen = False
            release_backend = getattr(self.backend, "release_all", None)
            if callable(release_backend):
                release_backend()

    def dispatch_touch(self, action, cid, x, y, frame=True):
        with self.lock:
            if self._finger_suppressed():
                if cid in self.active_fingers:
                    old_x, old_y = self.active_fingers.pop(cid)
                    self.backend.inject_touch(
                        ACTION_UP, cid,
                        x if action == ACTION_UP else old_x,
                        y if action == ACTION_UP else old_y,
                        frame,
                    )
                return
            self.backend.inject_touch(action, cid, x, y, frame)
            if action in (0, ACTION_MOVE):
                self.active_fingers[cid] = (x, y)
            elif action == ACTION_UP:
                self.active_fingers.pop(cid, None)
            return

    def dispatch_pen(
        self, action, tool, cid, x, y, pressure, tilt_x, tilt_y,
        distance, buttons, flags, frame=True,
    ):
        with self.lock:
            self.last_stylus_input = time.monotonic()
            self._release_fingers_locked()
            if not self.backend.inject_pen(
                action, tool, x, y, pressure, tilt_x, tilt_y,
                distance, buttons, flags, frame,
            ) and action != ACTION_HOVER:
                self.backend.inject_touch(action, self.pen_touch_cid(cid), x, y, frame)

    def dispatch_packet(self, pkt_type, payload, frame=True):
        with self.lock:
            kind, values = unpack_packet(pkt_type, payload)
            if kind == "touch":
                action, _tool, cid, x, y, _pressure, _tx, _ty = values
                self.dispatch_touch(action, cid, x, y, frame)
                return True
            if kind == "pen":
                action, tool, cid, x, y, pressure, tilt_x, buttons = values
                self.dispatch_pen(
                    action, tool, cid, x, y, pressure,
                    max(-90, min(90, tilt_x)), 0, 0, buttons & 0xffff, 0, frame,
                )
                return True
            if kind == "pen_ext":
                self.dispatch_pen(*values, frame=frame)
                return True
        log.warning("Unknown or malformed packet type=0x%02x len=%d", pkt_type, len(payload))
        return False
