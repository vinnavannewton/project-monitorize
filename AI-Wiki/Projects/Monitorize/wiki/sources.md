# Monitorize Sources

Sources used to seed and maintain this wiki.

## Repository Docs

- `README.md` - product overview, platform support, dependencies, and install notes.
- `docs/project_structure.md` - platform and module layout.
- `docs/linux.md` - Linux runtime architecture, streaming modes, KDE virtual display behavior, encryption, presets, and desktop environment notes.

## Source Files Inspected

- `linux/monitorize/__main__.py` - Linux app entrypoint selection.
- `linux/monitorize/desktop/main_window.py` - full PyQt/QML window, tray behavior, desktop selection, and QML loading.
- `linux/monitorize/desktop/backend.py` - QML-facing facade and controller wiring.
- `linux/monitorize/desktop/streaming_controller.py` - primary stream, TLS proxy, display prep, input bridge, and preset capture lifecycle.
- `linux/monitorize/desktop/receiver_controller.py` - Linux receiver pipeline and encrypted receive flow.
- `linux/monitorize/desktop/usb_controller.py` - ADB device and reverse port setup.
- `linux/monitorize/desktop/discovery_service.py` - Zeroconf discovery and advertisement.
- `linux/monitorize/desktop/tray_agent.py` - lightweight tray entrypoint.
- `linux/monitorize/qml/main.qml` - root QML navigation and settings wiring.

## Raw Sources

No raw sources have been added yet.

Place original articles, notes, transcripts, screenshots, or source documents in `../raw/`. Codex should read them and integrate durable knowledge into `wiki/` without rewriting the raw files.
