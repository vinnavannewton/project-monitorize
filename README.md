<div align="center">
  <img src="linux/monitorize/assets/monitorize_desktop_logo.png" alt="Monitorize logo" width="160" />
  <h1>Monitorize</h1>
  <p><strong>Use any Moonlight-compatible device as an extra monitor for your Linux desktop.</strong></p>
</div>

https://github.com/user-attachments/assets/14a21a68-011b-43bf-9f26-fc9ea731d80b

Monitorize is a Linux host application for KDE Plasma, GNOME, and Hyprland. It creates and preserves compositor-native virtual displays, and streams using an isolated Sunshine server for each display.

Receiving is handled by the standard [Moonlight](https://moonlight-stream.org/) application on Android, Linux, Windows, macOS, iOS, and other supported clients.

## Features

- Extend or mirror a Linux desktop.

- Stream upto two virtual displays to any Moonlight-compatible device.

- All Sunshine features such as encoder, codec, audio, touch, and stylus configuration.

- KDE, GNOME, and Hyprland native virtual monitors.

- Doesn't interfere with existing user's sunshine.

## Supported desktops

- KDE Plasma 6.7+
- GNOME 50+
- Hyprland

### Virtual Display Backends

- **Compositor (Default)**: Uses compositor-native virtual display APIs (KDE KWin, GNOME Mutter, wlroots). If detection fails, Monitorize asks which supported desktop is running when this mode is used.
- **VKMS (Experimental)**: Creates kernel-level DRM virtual displays via the standalone [`monitorize-vkms`](https://github.com/vinnavannewton/monitorize-vkms) tool.

## Installation

- [Fedora](https://github.com/vinnavannewton/project-monitorize/wiki/Fedora-installation)
- [Arch Linux](https://github.com/vinnavannewton/project-monitorize/wiki/Arch-installation)
- [Ubuntu](https://github.com/vinnavannewton/project-monitorize/wiki/Ubuntu-installation)
- [openSUSE Tumbleweed](https://github.com/vinnavannewton/project-monitorize/wiki/openSUSE-Tumbleweed-installation)
- [NixOS / Nix](https://github.com/vinnavannewton/project-monitorize/wiki/Nix-installation)

## Contributing

Want to contribute? See the [Contributing guide](https://github.com/vinnavannewton/project-monitorize/wiki/Contributing).

## Usage

1. If the stream is black or crashes, try changing the stream from auto to other options in monitorize based on what your hardware supports.

## Star History

<a href="https://www.star-history.com/?repos=vinnavannewton%2FProjectMonitorize&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&theme=dark&legend=top-left&sealed_token=UrJIpYgjHe4fJj10B7GKSJo0AdhR5bGEFiNv9cpP4iJQEhNXknPXAq5aX7g6bk2y7UAMoa3xfVqKyRWSANVZobe2uVlMjvYXbCaeOmO5w6tdzEvCjnFejmzXxjtW2mIf4Yny6uOhs2jXSxC01ZMh5Q_Dd6dRD2NniWjkaltRE1SzVWfwyOP0rZwE5oCI" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&legend=top-left&sealed_token=UrJIpYgjHe4fJj10B7GKSJo0AdhR5bGEFiNv9cpP4iJQEhNXknPXAq5aX7g6bk2y7UAMoa3xfVqKyRWSANVZobe2uVlMjvYXbCaeOmO5w6tdzEvCjnFejmzXxjtW2mIf4Yny6uOhs2jXSxC01ZMh5Q_Dd6dRD2NniWjkaltRE1SzVWfwyOP0rZwE5oCI" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&legend=top-left&sealed_token=UrJIpYgjHe4fJj10B7GKSJo0AdhR5bGEFiNv9cpP4iJQEhNXknPXAq5aX7g6bk2y7UAMoa3xfVqKyRWSANVZobe2uVlMjvYXbCaeOmO5w6tdzEvCjnFejmzXxjtW2mIf4Yny6uOhs2jXSxC01ZMh5Q_Dd6dRD2NniWjkaltRE1SzVWfwyOP0rZwE5oCI" />
 </picture>
</a>
