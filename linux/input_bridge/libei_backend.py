"""libei input backend using the XDG RemoteDesktop portal."""

import logging
import os
import select
import threading
import time

from .protocol import ACTION_DOWN, ACTION_HOVER, ACTION_MOVE, ACTION_UP

log = logging.getLogger("TouchDaemon")

try:
    import snegg.ei as ei
    import snegg.oeffis as oeffis
except ImportError:
    ei = oeffis = None


class LibeiBackend:
    def __init__(self, geometry, shutdown):
        self.geometry = geometry
        self.shutdown = shutdown
        self.ctx = None
        self.touch = None
        self.pen = None
        self.io_fd = None
        self.active = {}
        self.lock = threading.Lock()
        self._libei = ei.libei if ei else None
        if self._libei:
            self._libei.event_get_type.restype = __import__("ctypes").c_uint32

    def setup(self, _stylus_features=False):
        if not ei or not oeffis:
            raise RuntimeError("snegg not installed — libei backend unavailable")
        eis_fd = self._portal_fd()
        if eis_fd is None:
            raise RuntimeError("RemoteDesktop portal denied or timed out")
        self.io_fd = os.fdopen(eis_fd, "rb", buffering=0)
        self.ctx = ei.Sender.create_for_fd(self.io_fd, name="Virtual-TabletDisplay")
        self._discover_devices()
        if not self.touch:
            raise RuntimeError("libei exposed no touch or absolute pointer device")
        threading.Thread(target=self._dispatch_loop, daemon=True).start()

    def _portal_fd(self):
        device_sets = (
            oeffis.DeviceType.TOUCHSCREEN | oeffis.DeviceType.POINTER,
            oeffis.DeviceType.ALL_DEVICES,
            oeffis.DeviceType.POINTER,
        )
        for devices in device_sets:
            try:
                request = oeffis.Oeffis.create(devices=devices)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and not self.shutdown.is_set():
                    if select.select([request.fd.fileno()], [], [], 1)[0] and request.dispatch():
                        return request.eis_fd
            except oeffis.SessionClosedError:
                return None
            except Exception as exc:
                log.warning("RemoteDesktop portal attempt failed: %s", exc)
        return None

    def _discover_devices(self):
        connected = False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not self.shutdown.is_set():
            if self.ctx.fd in select.select([self.ctx.fd], [], [], 0.1)[0]:
                self.ctx.dispatch()
                for event in self.ctx.events:
                    event_type = int(self._libei.event_get_type(event._cobject))
                    if event_type == 1:
                        connected = True
                    elif event_type == 3:
                        event.seat.bind(event.seat.capabilities)
                    elif event_type == 5:
                        dev = event.device
                        caps = dev.capabilities if dev else ()
                        if dev and ei.DeviceCapability.TOUCH in caps:
                            self.touch = dev
                            dev.start_emulating()
                        elif dev and ei.DeviceCapability.POINTER_ABSOLUTE in caps:
                            self.pen = dev
                            dev.start_emulating()
                    elif event_type == 2:
                        return
            if connected and (self.touch or self.pen):
                break
        if not self.touch and self.pen:
            self.touch, self.pen = self.pen, None

    def _dispatch_loop(self):
        while not self.shutdown.is_set() and self.ctx:
            with self.lock:
                if self.ctx.fd in select.select([self.ctx.fd], [], [], 0.05)[0]:
                    self.ctx.dispatch()
                    for event in self.ctx.events:
                        if int(self._libei.event_get_type(event._cobject)) == 2:
                            self.shutdown.set()
                            return
                else:
                    self.ctx.dispatch()

    def _scale(self, device, x, y):
        target_x, target_y, _, _ = self.geometry.virtual_rect()
        best = device.regions[0] if device.regions else None
        rx, ry, rw, rh = 0.0, 0.0, self.geometry.screen_w, self.geometry.screen_h
        for region in device.regions:
            region_x = float(self._libei.region_get_x(region._cobject))
            region_y = float(self._libei.region_get_y(region._cobject))
            if abs(region_x - target_x) < 5 and abs(region_y - target_y) < 5:
                best = region
                break
        if best:
            rx = float(self._libei.region_get_x(best._cobject))
            ry = float(self._libei.region_get_y(best._cobject))
            rw, rh = best.dimension
        return rx + x / 65535 * rw, ry + y / 65535 * rh

    def inject_touch(self, action, cid, x, y, frame=True):
        if not self.touch:
            return
        px, py = self._scale(self.touch, x, y)
        is_touch = ei.DeviceCapability.TOUCH in self.touch.capabilities
        with self.lock:
            if is_touch:
                if action == ACTION_DOWN:
                    contact = self.touch.touch_new()
                    self.active[cid] = contact
                    contact.down(px, py)
                elif action == ACTION_MOVE and cid in self.active:
                    self.active[cid].motion(px, py)
                elif action == ACTION_UP:
                    contact = self.active.pop(cid, None)
                    if contact:
                        contact.up()
            else:
                if action == ACTION_DOWN:
                    self.touch.pointer_motion_absolute(px, py)
                    self.touch.button_button(0x110, True)
                    self.active[cid] = True
                elif action == ACTION_MOVE:
                    self.touch.pointer_motion_absolute(px, py)
                elif action == ACTION_UP:
                    self.active.pop(cid, None)
                    self.touch.pointer_motion_absolute(px, py)
                    self.touch.button_button(0x110, False)
            if frame:
                self.touch.frame()
                self.ctx.dispatch()

    def inject_pen(
        self, action, tool, x, y, _pressure, _tilt_x, _tilt_y,
        _distance, buttons, _flags, frame=True,
    ):
        device = self.pen or self.touch
        if not device:
            return False
        px, py = self._scale(device, x, y)
        secondary = bool(buttons & 32) or tool == 2
        button = 0x111 if secondary else 0x110
        with self.lock:
            device.pointer_motion_absolute(px, py)
            if action == ACTION_DOWN:
                device.button_button(button, True)
            elif action == ACTION_UP:
                device.button_button(button, False)
                device.button_button(0x110 if secondary else 0x111, False)
            if frame:
                device.frame()
                self.ctx.dispatch()
        return action in (ACTION_DOWN, ACTION_MOVE, ACTION_UP, ACTION_HOVER)

    def close(self):
        if self.touch:
            try:
                self.touch.stop_emulating()
            except Exception:
                pass
        if self.io_fd:
            self.io_fd.close()

