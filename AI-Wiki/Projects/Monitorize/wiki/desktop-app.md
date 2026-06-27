# Monitorize Desktop App

This page captures the current Linux desktop app wiring from source inspection.

## Entrypoints

- `linux/monitorize/__main__.py` is the module entrypoint for `python3 -m monitorize`.
- The entrypoint launches the lightweight tray agent when `--tray-agent` is present, or when `--start-in-tray` is used without `--launch-preset`.
- Otherwise it launches the full desktop window from `monitorize.desktop.main_window`.

## Full Window

- `MonitorizeWindow` is a PyQt6 `QMainWindow`.
- It detects the desktop environment, falling back to a manual KDE/GNOME/Hyprland/Sway picker.
- It creates `MonitorizeBackend` and exposes it to QML as the `backend` context property.
- The QML root is `linux/monitorize/qml/main.qml`.
- The window owns the system tray icon for the full app and can quit back into the lightweight tray agent when idle.
- On startup it kills stale GStreamer, streamer, and TLS proxy processes that match Monitorize patterns.

## QML UI Flow

- `main.qml` uses a `StackView` with `MainMenuPage.qml` as the initial page.
- When `backend.isStreaming` becomes true, QML replaces the current page with `StreamingPage.qml`.
- When `backend.isReceiving` becomes true, QML replaces the current page with `ReceiverStreamingPage.qml`.
- Settings in `main.qml` call backend slots for general settings and autostart.
- Main menu actions route to USB setup, Wi-Fi setup, receiver setup, or preset launch.

## Backend Facade

`MonitorizeBackend` is the QML-facing facade. It owns and wires:

- `DiscoveryService` for Zeroconf browsing and advertisement.
- `UsbController` for ADB device scan and reverse port setup.
- `ReceiverController` for receiving a stream from another Monitorize host.
- `StreamingController` for primary stream, TLS proxy, input bridge, and third-display lifecycle.

The backend exposes PyQt properties and slots rather than putting lifecycle logic directly in QML.

## Streaming Controller

`StreamingController` owns the host-side stream lifecycle.

- `start()` sanitizes resolution, FPS, bitrate, display type, encoder, and encoder profile.
- Wi-Fi encryption starts `monitorize.security.tls_proxy` and makes the actual streamer bind to localhost.
- USB mode removes stale ADB reverse mappings for Wi-Fi ports before preparing the display.
- KDE Extend mode sets portal virtual-source environment variables and starts the KDE portal streamer.
- Hyprland and Sway Extend modes create headless outputs before launching the streamer.
- GNOME Extend mode tracks virtual layout and listens for Mutter DisplayConfig monitor changes. After the stream is ready, a GNOME virtual-display move is treated as a controlled reconnect: save the new virtual `x/y`, stop the stale streamer/GStreamer/input processes, and relaunch so the new `RecordVirtual` stream restores to the saved position before capture resumes.
- Input starts through `monitorize.input_bridge.touch_daemon` after the stream is ready enough for the desktop path.
- Active configurations can be saved as presets, including primary stream, general settings, Wi-Fi encryption/profile, and optional KDE third display.

## Receiver Controller

`ReceiverController` lets the Linux desktop receive another Monitorize stream.

- Plain streams launch a `gst-launch-1.0` TCP H.264 receive pipeline directly.
- Encrypted streams launch `monitorize.security.tls_receiver`, then pipe local decrypted video to GStreamer.
- Receiver credentials are saved per host after successful TLS authentication.
- Authentication failure clears stale credentials and emits a pairing-required signal back to QML.
- Hardware decoder mode requires a VA-API H.264 decoder; otherwise it uses software `avdec_h264`.

## USB Controller

`UsbController` performs the USB readiness flow:

- Runs `adb devices`.
- Sets `adb reverse tcp:7110 tcp:7112` for video.
- Sets `adb reverse tcp:7111 tcp:7111` for touch.
- Treats video reverse failure as fatal, but touch reverse failure as a warning so video can still work.

## Discovery Service

`DiscoveryService` uses Zeroconf service type `_monitorize._tcp.local.`.

- Browsing collects host name, IP, port, encryption flag, TLS fingerprint, third-display availability, and third-display port.
- Advertising publishes the local host on port `7110`.
- Encrypted advertisements include the TLS certificate fingerprint and `input_transport=udp-aesgcm-v1`.

## Note From Inspection

`linux/monitorize/desktop/tray_agent.py` references `os.path.join(...)` but does not import `os`. Verify this before relying on the lightweight tray agent.

## Sources

- `linux/monitorize/__main__.py`
- `linux/monitorize/desktop/main_window.py`
- `linux/monitorize/desktop/backend.py`
- `linux/monitorize/desktop/streaming_controller.py`
- `linux/monitorize/desktop/receiver_controller.py`
- `linux/monitorize/desktop/usb_controller.py`
- `linux/monitorize/desktop/discovery_service.py`
- `linux/monitorize/desktop/tray_agent.py`
- `linux/monitorize/qml/main.qml`
