"""Monitorize host discovery and stream advertisement."""

import socket

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from monitorize.config.validation import sanitize_fps, sanitize_port, valid_port


class DiscoveryService(QObject):
    devicesChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.devices = []
        self.browser = None
        self.discovery_zc = None
        self.advertisement_zc = None
        self.advertisements = []
        self.advertisement_state = None
        self.service_names = {}

    @staticmethod
    def _prop(props, key, default=b""):
        value = props.get(key, default)
        return value if isinstance(value, bytes) else str(value).encode()

    @staticmethod
    def _decode(value, encoding="utf-8"):
        return value.decode(encoding, errors="replace")

    @staticmethod
    def _ipv4(addresses):
        for address in addresses:
            if len(address) == 4:
                return socket.inet_ntoa(address)
        return ""

    @staticmethod
    def _safe_port(value, default=7114):
        try:
            port = int(value)
        except (TypeError, ValueError):
            return default
        return sanitize_port(port, default) if valid_port(port) else default

    def start(self):
        self.stop_browsing()
        self.devices = []
        self.service_names = {}
        self.devicesChanged.emit()
        try:
            from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
            service = self

            class Listener(ServiceListener):
                def add_service(self, zc, type_, name):
                    info = zc.get_service_info(type_, name)
                    if not info or not info.addresses:
                        return
                    props = info.properties
                    ip = service._ipv4(info.addresses)
                    if not ip or not valid_port(info.port):
                        return
                    values = (
                        service._decode(service._prop(props, b"name", b"Unknown")),
                        ip,
                        int(info.port),
                        service._prop(props, b"encrypted", b"0") == b"1",
                        service._decode(service._prop(props, b"fingerprint"), "ascii"),
                        None if b"third_available" not in props
                        else service._prop(props, b"third_available") == b"1",
                        service._safe_port(service._prop(props, b"third_port", b"7114")),
                        name,
                    )
                    QTimer.singleShot(0, lambda: service.add_device(*values))

                def update_service(self, zc, type_, name):
                    self.add_service(zc, type_, name)

                def remove_service(self, zc, type_, name):
                    QTimer.singleShot(0, lambda: service.remove_device(name))

            self.discovery_zc = Zeroconf()
            self.browser = ServiceBrowser(
                self.discovery_zc,
                "_monitorize._tcp.local.",
                Listener(),
            )
        except Exception as exc:
            print(f"[Receiver] Discovery failed: {exc}")
            self.stop_browsing()

    def add_device(
        self, name, ip, port, encrypted=False, fingerprint="",
        third_available=False, third_port=7114, service_name=None,
    ):
        if not ip or not valid_port(port):
            return
        port = sanitize_port(port)
        third_port = sanitize_port(third_port, 7114)
        existing = None
        if service_name and service_name in self.service_names:
            old_ip, old_port = self.service_names[service_name]
            existing = next((
                device for device in self.devices
                if device.get("ip") == old_ip and device.get("port") == old_port
            ), None)
        data = {
            "name": name,
            "ip": ip,
            "port": port,
            "encrypted": encrypted,
            "fingerprint": fingerprint,
            "thirdAvailable": third_available,
            "thirdPort": third_port,
        }
        existing = existing or next((
            device for device in self.devices
            if device.get("ip") == ip and device.get("port") == port
        ), None)
        if existing:
            existing.update(data)
        else:
            self.devices.append(data)
        if service_name:
            self.service_names[service_name] = (ip, port)
        self.devicesChanged.emit()

    def remove_device(self, service_name):
        target = self.service_names.pop(service_name, None)
        if not target:
            return
        ip, port = target
        before = len(self.devices)
        self.devices = [
            device for device in self.devices
            if not (device.get("ip") == ip and device.get("port") == port)
        ]
        if len(self.devices) != before:
            self.devicesChanged.emit()

    def stop_browsing(self):
        if self.browser is not None:
            self.browser.cancel()
            self.browser = None
        if self.discovery_zc is not None:
            try:
                self.discovery_zc.close()
            except Exception:
                pass
            self.discovery_zc = None

    def advertise(self, ip, encrypted, third_available, fps=60, third_fps=None):
        try:
            from zeroconf import ServiceInfo, Zeroconf
            hostname = socket.gethostname()
            fingerprint = ""
            if encrypted:
                from monitorize.security.tls_proxy import certificate_fingerprint
                fingerprint = certificate_fingerprint()

            def properties(name, port, stream_fps):
                values = {
                    "name": name,
                    "port": port,
                    "fps": str(sanitize_fps(stream_fps)),
                    "encrypted": "1" if encrypted else "0",
                }
                if encrypted:
                    values["fingerprint"] = fingerprint
                    values["input_transport"] = "udp-aesgcm-v1"
                return values

            primary_properties = properties(
                f"{hostname} — First Virtual Monitor", 7110, fps
            )
            primary_properties.update({
                "encrypted": "1" if encrypted else "0",
                "third_available": "1" if third_available else "0",
                "third_port": "7114",
            })
            advertised = [
                ("First Virtual Monitor", 7110, primary_properties),
            ]
            if third_available:
                advertised.append((
                    "Second Virtual Monitor",
                    7114,
                    properties(
                        f"{hostname} — Second Virtual Monitor",
                        7114,
                        fps if third_fps is None else third_fps,
                    ),
                ))
            state = (
                ip,
                tuple(
                    (label, port, tuple(sorted(values.items())))
                    for label, port, values in advertised
                ),
            )
            if self.advertisement_zc is not None and state == self.advertisement_state:
                return
            self.stop_advertising()
            self.advertisement_zc = Zeroconf()
            instance_host = hostname[:36]
            for label, port, values in advertised:
                info = ServiceInfo(
                    "_monitorize._tcp.local.",
                    f"{instance_host} {label}._monitorize._tcp.local.",
                    addresses=[socket.inet_aton(ip)],
                    port=port,
                    properties=values,
                    server=f"{hostname}.local.",
                )
                self.advertisement_zc.register_service(info)
                self.advertisements.append(info)
            self.advertisement_state = state
        except Exception as exc:
            print("Zeroconf registration/update failed:", exc)
            self.stop_advertising()

    def stop_advertising(self):
        if self.advertisement_zc is None:
            return
        for advertisement in self.advertisements:
            try:
                self.advertisement_zc.unregister_service(advertisement)
            except Exception:
                pass
        try:
            self.advertisement_zc.close()
        except Exception:
            pass
        self.advertisement_zc = None
        self.advertisements = []
        self.advertisement_state = None

    def close(self):
        self.stop_browsing()
        self.stop_advertising()
