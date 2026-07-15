import sys
import os
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication
from PyQt6.QtQuick import QQuickView
from monitorize.desktop.backend import MonitorizeBackend
from monitorize.platform.utils import detect_desktop_environment, QML_DIR

def test():
    app = QApplication(sys.argv + ["-platform", "offscreen"])
    de = detect_desktop_environment() or "gnome"
    backend = MonitorizeBackend(de)
    
    view = QQuickView()
    view.rootContext().setContextProperty("backend", backend)
    
    
    wifi_url = QUrl.fromLocalFile(os.path.join(QML_DIR, "WifiPage.qml"))
    
    from PyQt6.QtQml import QQmlComponent
    comp = QQmlComponent(view.engine(), wifi_url)
    if comp.isError():
        print("WifiPage errors:")
        for err in comp.errors():
            print(err.toString())
    else:
        print("WifiPage loaded successfully!")
        
    usb_url = QUrl.fromLocalFile(os.path.join(QML_DIR, "UsbStep1Page.qml"))
    comp_usb = QQmlComponent(view.engine(), usb_url)
    if comp_usb.isError():
        print("UsbStep1Page errors:")
        for err in comp_usb.errors():
            print(err.toString())
    else:
        print("UsbStep1Page loaded successfully!")

    
    backend._recent_usb_devices = [{"serial": "12345", "name": "USB Device 1", "online": True}]
    backend._recent_wifi_devices = [{"ip": "192.168.1.100", "name": "Wifi Device 1", "online": False}]

    from PyQt6.QtQml import QQmlExpression
    expr1 = QQmlExpression(view.engine().rootContext(), None, "backend.recentUsbDevices")
    val1 = expr1.evaluate()
    print("QML recentUsbDevices type:", type(val1), "val:", val1)
    
    expr2 = QQmlExpression(view.engine().rootContext(), None, "backend.recentWifiDevices")
    val2 = expr2.evaluate()
    print("QML recentWifiDevices type:", type(val2), "val:", val2)
    
    
    expr3 = QQmlExpression(view.engine().rootContext(), None, "backend.recentWifiDevices.length")
    val3 = expr3.evaluate()
    print("QML recentWifiDevices.length:", val3)

if __name__ == "__main__":
    test()
