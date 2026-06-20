"""Monitorize host discovery and stream advertisement."""

import socket

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class DiscoveryService(QObject):
    devicesChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.devices = []
        self.browser = None
        self.discovery_zc = None
        self.advertisement_zc = None
        self.advertisement = None

    def start(self):
        self.stop_browsing()
        self.devices = []
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
                    values = (
                        props.get(b"name", b"Unknown").decode("utf-8", errors="replace"),
                        socket.inet_ntoa(info.addresses[0]),
                        info.port,
                        props.get(b"encrypted", b"0") == b"1",
                        props.get(b"fingerprint", b"").decode("ascii", errors="ignore"),
                        None if b"third_available" not in props else props[b"third_available"] == b"1",
                        int(props.get(b"third_port", b"7114")),
                    )
                    QTimer.singleShot(0, lambda: service.add_device(*values))

                def update_service(self, zc, type_, name):
                    self.add_service(zc, type_, name)

                def remove_service(self, zc, type_, name):
                    pass

            self.discovery_zc = Zeroconf()
            self.browser = ServiceBrowser(
                self.discovery_zc,
                "_monitorize._tcp.local.",
                Listener(),
            )
        except Exception as exc:
            print(f"[Receiver] Discovery failed: {exc}")

    def add_device(
        self, name, ip, port, encrypted=False, fingerprint="",
        third_available=False, third_port=7114,
    ):
        data = {
            "name": name,
            "ip": ip,
            "port": port,
            "encrypted": encrypted,
            "fingerprint": fingerprint,
            "thirdAvailable": third_available,
            "thirdPort": third_port,
        }
        existing = next((
            device for device in self.devices
            if device.get("ip") == ip and device.get("port") == port
        ), None)
        if existing:
            existing.update(data)
        else:
            self.devices.append(data)
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

    def advertise(self, ip, encrypted, third_available):
        try:
            from zeroconf import ServiceInfo, Zeroconf
            self.stop_advertising()
            hostname = socket.gethostname()
            properties = {
                "name": hostname,
                "port": 7110,
                "encrypted": "1" if encrypted else "0",
                "third_available": "1" if third_available else "0",
                "third_port": "7114",
            }
            if encrypted:
                from tls_proxy import certificate_fingerprint
                properties["fingerprint"] = certificate_fingerprint()
            self.advertisement_zc = Zeroconf()
            self.advertisement = ServiceInfo(
                "_monitorize._tcp.local.",
                f"{hostname}._monitorize._tcp.local.",
                addresses=[socket.inet_aton(ip)],
                port=7110,
                properties=properties,
                server=f"{hostname}.local.",
            )
            self.advertisement_zc.register_service(self.advertisement)
        except Exception as exc:
            print("Zeroconf registration/update failed:", exc)

    def stop_advertising(self):
        if self.advertisement_zc is None:
            return
        if self.advertisement is not None:
            try:
                self.advertisement_zc.unregister_service(self.advertisement)
            except Exception:
                pass
        try:
            self.advertisement_zc.close()
        except Exception:
            pass
        self.advertisement_zc = None
        self.advertisement = None

    def close(self):
        self.stop_browsing()
        self.stop_advertising()

