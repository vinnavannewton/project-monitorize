# Project Structure

Monitorize is split by platform first, then by responsibility.

```text
android/                 Android tablet/phone client
linux/                   Linux desktop app
docs/                    Project documentation
packaging/               Future distributable package definitions
screenshots/             README and release screenshots
```

## Android

```text
android/app/src/main/java/com/example/monitorize/
├── discovery/           NSD discovery models and scanner
├── input/               Touch, stylus, and mouse event sender
├── security/            TLS socket helpers
├── streaming/           H.264 decoder and stream receiver
├── ui/                  Compose theme
└── MainActivity.kt      Android UI and app lifecycle
```

## Linux

```text
linux/
├── monitorize/
│   ├── config/          Settings, validation, logging, autostart
│   ├── desktop/         Qt/QML-facing backend and controllers
│   ├── input_bridge/    Touch, stylus, mouse daemon and protocol
│   ├── platform/        KDE/Sway/display/process helpers
│   ├── qml/             QML pages and reusable controls
│   ├── security/        TLS and encrypted UDP helpers
│   ├── streaming/       PipeWire, portal, and GStreamer launchers
│   └── assets/          Icons and UI image assets
├── tests/               Linux unit tests
└── scripts/             Installer and helper scripts
```

Run the Linux app from `linux/` with `python3 -m monitorize`.
