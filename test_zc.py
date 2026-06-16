from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
import time

class MyListener(ServiceListener):
    def add_service(self, zc, type_, name):
        print(f"Service {name} added")
        info = zc.get_service_info(type_, name)
        if info:
            print(f"Info: {info}")

    def remove_service(self, zc, type_, name):
        print(f"Service {name} removed")

    def update_service(self, zc, type_, name):
        print(f"Service {name} updated")

zc = Zeroconf()
browser = ServiceBrowser(zc, "_monitorize._tcp.local.", MyListener())
time.sleep(3)
zc.close()
