from zeroconf import Zeroconf, ServiceInfo, ServiceBrowser, ServiceListener
import socket
import time

zc = Zeroconf()
hostname = socket.gethostname()
desc = {'name': hostname, 'port': 7110}
info = ServiceInfo(
    "_monitorize._tcp.local.",
    f"{hostname}._monitorize._tcp.local.",
    addresses=[socket.inet_aton("127.0.0.1")],
    port=7110,
    properties=desc,
    server=f"{hostname}.local.",
)
zc.register_service(info)

class MyListener(ServiceListener):
    def add_service(self, zc, type_, name):
        print(f"Service {name} added")
        info = zc.get_service_info(type_, name)
        if info:
            print(f"Info: {info}")
            print(f"Addresses: {info.addresses}")
            print(f"Properties: {info.properties}")

browser = ServiceBrowser(zc, "_monitorize._tcp.local.", MyListener())
time.sleep(3)
zc.close()
