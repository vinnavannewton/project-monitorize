<div align="center">
  <img src="linux/monitorize/assets/monitorize_desktop_logo.png" alt="Monitorize logo" width="160" />
  <h1>Monitorize</h1>
  <p><strong>A virtual display manager for Linux with built-in streaming.</strong></p>
  <p>
    <a href="https://github.com/vinnavannewton/project-monitorize/actions/workflows/desktop.yml"><img src="https://img.shields.io/github/actions/workflow/status/vinnavannewton/project-monitorize/desktop.yml?branch=main&label=Desktop%20CI" alt="Desktop CI" /></a>
    <a href="https://github.com/vinnavannewton/project-monitorize/actions/workflows/desktop-release.yml"><img src="https://img.shields.io/github/actions/workflow/status/vinnavannewton/project-monitorize/desktop-release.yml?label=Desktop%20CD" alt="Desktop CD" /></a>
    <a href="https://github.com/vinnavannewton/project-monitorize/releases/latest"><img src="https://img.shields.io/github/v/release/vinnavannewton/project-monitorize?sort=date&filter=monitorize-v*&display_name=tag&label=Latest%20release" alt="Latest release" /></a>
    <a href="https://github.com/vinnavannewton/project-monitorize/releases"><img src="https://img.shields.io/github/downloads/vinnavannewton/project-monitorize/total?label=Downloads" alt="Total release asset downloads" /></a>
  </p>
</div>

https://github.com/user-attachments/assets/14a21a68-011b-43bf-9f26-fc9ea731d80b

![Monitorize configuration and session screens](assets/Demo.png)

Monitorize creates and manages virtual displays on Linux using compositor-native outputs or [monitorize-vkms](https://github.com/vinnavannewton/monitorize-vkms). Use virtual displays on their own, or stream them to another device through built-in Sunshine integration.

For streaming, connect with [Moonlight](https://moonlight-stream.org/) on Android, Linux, Windows, macOS, iOS, or another supported device.

## Features

- Create and manage up to two virtual displays.

- Support compositor-native virtual displays and monitorize-vkms.

- Stream virtual displays or mirror an existing monitor through Sunshine.

- Configure streaming encoders, codecs, audio, touch, and stylus options via the integrated sunshine.

- Keeps Monitorize's Sunshine instances separate from your existing Sunshine setup.

## Supported desktops

This table shows whether each desktop environment supports via compositor-native virtual displays, [monitorize-vkms](https://github.com/vinnavannewton/monitorize-vkms), or both.

| Desktop Environment                    | Compositor | monitorize-vkms |
| -------------------------------------- |:----------:|:---------------:|
| KDE Plasma 6.7+                        | ✅          | ✅               |
| GNOME 50+                              | ✅          | ✅               |
| Hyprland (Aquamarine 0.15.1+ for VKMS) | ✅          | ✅               |
| Niri                                   | ❌          | ✅               |
| Cinnamon X11                           | ❌          | ✅               |
| COSMIC                                 | ❌          | ❌               |

## Installation

- [Fedora](https://github.com/vinnavannewton/project-monitorize/wiki/Fedora-installation)
- [Arch Linux](https://github.com/vinnavannewton/project-monitorize/wiki/Arch-installation)
- [Ubuntu](https://github.com/vinnavannewton/project-monitorize/wiki/Ubuntu-installation)
- [openSUSE Tumbleweed](https://github.com/vinnavannewton/project-monitorize/wiki/openSUSE-Tumbleweed-installation)
- [NixOS / Nix](https://github.com/vinnavannewton/project-monitorize/wiki/Nix-installation)

## Contributing

Want to contribute? See the [Contributing guide](https://github.com/vinnavannewton/project-monitorize/wiki/Contributing).

## Usage

For DEs with no compositor-native virtual display support, use via VKMS by installing [monitorize-vkms](https://github.com/vinnavannewton/monitorize-vkms).

Tip: If the stream is black or crashes, try changing the stream from auto to other options in monitorize based on what your hardware supports.

### Terminal and SSH

Native packages use `monitorize`. Source installs provide the same launcher
at `linux/venv/bin/monitorize`. An existing source checkout can also run
`venv/bin/python -m monitorize` from its `linux` directory. For Flatpak, use
`flatpak run com.vinnavan.Monitorize` with the same arguments.

```sh
monitorize setup --status
monitorize setup --input --firewall
"${EDITOR:-nano}" "$(monitorize config path)"
monitorize reload
monitorize start
monitorize status
```

For firewalld, use `setup --status` to review the network scope and
`setup --firewall --zone ZONE` to select a zone explicitly. Run administrator
actions from an SSH connection with a terminal, such as one opened with
`ssh -t`. Flatpak administrator actions require the matching Monitorize CLI
and setup helpers on the host. Source users can stage the matching host
helpers with `python3 scripts/stage-remote-login.py` from the checkout root
and follow the printed installation steps. An input grant can require a fresh
desktop login. `monitorize setup --status` reports that separately from
the saved grant.

Request a PIN in Moonlight, then run `monitorize pair` and enter it at the
prompt. `monitorize stop` stops hosting. `reload` validates edits and reports
changes that need a restart. Use `reload --restart` when you want to apply
those changes during a connected stream. Per-display encoder, codec, and
GPU selections use `streaming_customized`. Set it to `true` to select
values explicitly. Boot policy uses the authorized `boot` commands.

Desktop hosting uses the logged-in graphical session. To host before login on
a supported system, use `monitorize boot status` and `monitorize boot enable`
with the installed remote-login components.

## Star History

<a href="https://www.star-history.com/?repos=vinnavannewton%2FProjectMonitorize&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&theme=dark&legend=top-left&sealed_token=UrJIpYgjHe4fJj10B7GKSJo0AdhR5bGEFiNv9cpP4iJQEhNXknPXAq5aX7g6bk2y7UAMoa3xfVqKyRWSANVZobe2uVlMjvYXbCaeOmO5w6tdzEvCjnFejmzXxjtW2mIf4Yny6uOhs2jXSxC01ZMh5Q_Dd6dRD2NniWjkaltRE1SzVWfwyOP0rZwE5oCI" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&legend=top-left&sealed_token=UrJIpYgjHe4fJj10B7GKSJo0AdhR5bGEFiNv9cpP4iJQEhNXknPXAq5aX7g6bk2y7UAMoa3xfVqKyRWSANVZobe2uVlMjvYXbCaeOmO5w6tdzEvCjnFejmzXxjtW2mIf4Yny6uOhs2jXSxC01ZMh5Q_Dd6dRD2NniWjkaltRE1SzVWfwyOP0rZwE5oCI" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&legend=top-left&sealed_token=UrJIpYgjHe4fJj10B7GKSJo0AdhR5bGEFiNv9cpP4iJQEhNXknPXAq5aX7g6bk2y7UAMoa3xfVqKyRWSANVZobe2uVlMjvYXbCaeOmO5w6tdzEvCjnFejmzXxjtW2mIf4Yny6uOhs2jXSxC01ZMh5Q_Dd6dRD2NniWjkaltRE1SzVWfwyOP0rZwE5oCI" />
 </picture>
</a>
