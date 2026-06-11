# Monitorize — Linux Desktop Application

Streams your Linux desktop to an Android tablet over USB or Wi-Fi, turning it into a secondary monitor with touch input support.

## File Structure

```
linux/
├── monitorize_gui.py          # Entry point — run this to launch the app
├── pipeline_builder.py        # GStreamer pipeline construction + HW encoder detection
├── touch_daemon.py            # Relays Android touch/pen events to the Linux desktop
├── install.sh                 # Desktop installer (creates .desktop entry + venv)
├── requirements.txt           # Python dependencies for the virtual environment
│
├── Streamer_kde.py            # KDE Plasma streamer (freedesktop portal)
├── Streamer_gnome.py          # GNOME streamer (Mutter ScreenCast D-Bus API)
├── Streamer_hyprland.py       # Hyprland streamer (xdg-desktop-portal-hyprland)
│
├── gui/                       # PyQt6 + QML interface package
│   ├── __init__.py            # Package init — exports MonitorizeWindow
│   ├── main_window.py         # QML backend bridge (signals, slots, process mgmt)
│   ├── settings.py            # Persistent settings via QSettings (~/.config/monitorize/)
│   ├── utils.py               # Helpers (DE detection, IP lookup, tray icon)
│   ├── main.qml               # Root QML — StackView orchestrator
│   ├── MainMenuPage.qml       # Home screen with USB / Wi-Fi mode selection
│   ├── UsbStep1Page.qml       # USB mode — ADB device scan
│   ├── UsbStep2Page.qml       # USB mode — streaming configuration
│   ├── WifiPage.qml           # Wi-Fi mode — streaming configuration
│   ├── StreamingPage.qml      # Live streaming view with log output
│   ├── CustomButton.qml       # Reusable styled button component
│   ├── CustomCheckBox.qml     # Reusable styled checkbox component
│   ├── CustomComboBox.qml     # Reusable styled dropdown component
│   └── CustomTextField.qml    # Reusable styled text input component
│
└── assets/
    ├── monitorize-icon.png    # Application icon (192×192)
    ├── svg/                   # SVG logos for DE badges and mode cards
    └── tray/                  # System tray icon variants
```

## How It Works

1. **`monitorize_gui.py`** launches a PyQt6 window that renders a QML interface via `QQuickWidget`.
2. The user selects USB or Wi-Fi mode and configures resolution, FPS, and encoder settings.
3. **`main_window.py`** spawns the correct DE-specific streamer script as a subprocess.
4. Each streamer script uses D-Bus APIs to capture a screen (or create a virtual monitor), then pipes the video through a GStreamer H.264 encoding pipeline to a TCP socket.
5. The Android app connects to the TCP stream and decodes the video in real time.
6. **`touch_daemon.py`** runs in parallel, receiving touch coordinates from the Android app and injecting them into the Linux input stack.

## Supported Desktop Environments

| DE | Virtual Monitor | Mirror | Touch Input |
|----|----------------|--------|-------------|
| KDE Plasma | ✅ (krfb-virtualmonitor) | ✅ | ✅ |
| GNOME | ✅ (Mutter RecordVirtual) | ✅ | ✅ |
| Hyprland | ✅ (hyprctl headless) | ✅ | ✅ (uinput) |

## Installation

```bash
chmod +x install.sh
./install.sh          # Install (creates venv + .desktop entry)
./install.sh remove   # Uninstall
```

## Configuration

Settings are stored in `~/.config/monitorize/settings.ini` and persist across sessions.
