# Monitorize Overview

Monitorize turns an Android tablet, phone, laptop, or PC into a secondary monitor for a Linux desktop.

## Current Status

- Beta and actively developed.
- Primary Linux desktop support: KDE Plasma and Hyprland.
- Experimental desktop support: Sway and GNOME.
- Linux app streams a low-latency H.264 display feed.
- Android client receives the video stream and sends touch, stylus, or mouse input back to Linux.

## Main Capabilities

- USB streaming through ADB reverse port mappings.
- Wi-Fi streaming with Zeroconf discovery.
- Optional encrypted Wi-Fi stream using TLS pairing.
- KDE Plasma virtual display support for Extend mode.
- Saved stream presets.
- Login tray behavior for launching the full GUI on demand.

## Related Pages

- See [[architecture]] for system structure.
- See [[decisions]] for durable design choices.
- See [[open-questions]] for unresolved project questions.

## Sources

- `README.md`
- `docs/project_structure.md`
- `docs/linux.md`
