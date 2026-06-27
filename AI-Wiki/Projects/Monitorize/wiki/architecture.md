# Monitorize Architecture

Monitorize is split by platform first, then by responsibility.

## Repository Shape

- `android/`: Android client.
- `linux/`: Linux desktop app.
- `docs/`: project documentation.
- `packaging/`: package definitions.
- `screenshots/`: README and release screenshots.

## Linux App

The Linux app creates or captures a display, encodes it as H.264, streams it to Android, and injects returned input into the desktop session.

See [[desktop-app]] for the current desktop entrypoint and controller wiring.

Important areas:

- `linux/monitorize/desktop/`: PyQt6/QML-facing backend, controllers, tray agent, discovery, and stream lifecycle.
- `linux/monitorize/qml/`: QML pages and reusable UI controls.
- `linux/monitorize/streaming/`: PipeWire, XDG portal, KDE/Hyprland/GNOME streamers, and GStreamer pipeline building.
- `linux/monitorize/platform/`: display, KDE virtual monitor, GNOME virtual monitor, process, and desktop helpers.
- `linux/monitorize/input_bridge/`: Android input transport, geometry mapping, dispatch, protocol, and uinput handling.
- `linux/monitorize/security/`: TLS proxy, TLS receiver, and secure UDP helpers.
- `linux/monitorize/config/`: settings, validation, logging, and autostart.

Run the Linux app from `linux/` with:

```bash
python3 -m monitorize
```

## Android App

The Android client handles discovery, receiving and decoding the H.264 stream, sending input events, TLS socket helpers, and Compose UI.

Known areas from repository docs:

- `discovery/`: NSD discovery models and scanner.
- `input/`: touch, stylus, and mouse event sender.
- `security/`: TLS socket helpers.
- `streaming/`: H.264 decoder and stream receiver.
- `ui/`: Compose theme.
- `MainActivity.kt`: Android UI and app lifecycle.

## Runtime Flow

```text
PyQt6/QML GUI
  -> create or request virtual display
  -> start desktop-specific PipeWire capture
  -> optionally start TLS proxy
  -> start input daemon
  -> PipeWire to GStreamer H.264 encoder to TCP video stream
  -> Android returns input to Linux input bridge
```

## Desktop Environment Notes

- KDE Plasma 6.7+ supports Extend mode through the XDG ScreenCast portal virtual-source type.
- Hyprland Extend mode creates a `HEADLESS-*` output with `hyprctl`.
- Sway Extend mode creates and configures a headless output.
- GNOME uses Mutter virtual-monitor ScreenCast support and remains experimental.

## Sources

- `docs/project_structure.md`
- `docs/linux.md`
