<div align="center">
  <img src="linux/monitorize/assets/monitorize_desktop_logo.png" alt="Monitorize logo" width="160" />
  <h1>Monitorize</h1>
  <p><strong>Use any Moonlight-compatible device as an extra monitor for your Linux desktop.</strong></p>
</div>

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

## Installation

- [Fedora](https://github.com/vinnavannewton/project-monitorize/wiki/Fedora-installation)
- [Arch Linux](https://github.com/vinnavannewton/project-monitorize/wiki/Arch-installation)
- [Ubuntu / Debian](https://github.com/vinnavannewton/project-monitorize/wiki/Ubuntu-Debian-installation)
- [openSUSE Tumbleweed](https://github.com/vinnavannewton/project-monitorize/wiki/openSUSE-Tumbleweed-installation)
- [NixOS / Nix](https://github.com/vinnavannewton/project-monitorize/wiki/Nix-installation)

Source installation uses the repository and all submodules:

```bash
git clone --recurse-submodules https://github.com/vinnavannewton/project-monitorize.git
cd project-monitorize
git submodule update --init --recursive
cd linux/scripts
chmod +x install.sh
./install.sh
```

Keep the clone in place after installing. The desktop entry and bundled Sunshine installation use files inside that checkout.

### Build a Fedora 44 RPM

On an x86_64 system with Git and rootless Podman, use a clean checkout with all
submodules initialized:

```bash
./packaging/rpm/build.sh
```

The command writes the binary RPM to `dist/rpm/fedora-44/x86_64/`, the SRPM to
`dist/rpm/fedora-44/source/`, and the full build log to
`dist/rpm/fedora-44/build.log`. It then test-installs the package in a fresh
Fedora 44 container. The installed package contains Monitorize and its pinned
Sunshine fork and does not depend on the source checkout.

Install the generated RPM and enable touch/input access and the Sunshine
firewall ports:

```bash
sudo dnf install ./dist/rpm/fedora-44/x86_64/monitorize-[0-9]*.fc44.x86_64.rpm
sudo usermod -aG monitorize-input "$USER"
sudo firewall-cmd --permanent --add-service=monitorize
sudo firewall-cmd --reload
```

Log out and back in after joining `monitorize-input`. For later local builds:

```bash
sudo dnf upgrade ./dist/rpm/fedora-44/x86_64/monitorize-[0-9]*.fc44.x86_64.rpm
sudo dnf reinstall ./dist/rpm/fedora-44/x86_64/monitorize-[0-9]*.fc44.x86_64.rpm
sudo dnf remove monitorize
```

Use `upgrade` for a newer version and `reinstall` when rebuilding the same
version. Uninstalling removes package-owned files but keeps
`~/.config/monitorize`.

The local RPM is unsigned and does not configure an update repository. Set
`MONITORIZE_BUILD_JOBS` to a positive integer to override the default build
limit of four jobs or the available CPU count, whichever is lower.
## Usage

1. Open Monitorize and click **Create a Virtual Display**.
2. Select resolution, refresh rate, and Extend to create virtual monitor or Mirror to just mirror primary monitor.
3. By default choose auto in encoder and codec unless it doesn't work then manually select based on your hardware compatibility then click start.
4. Open Moonlight on the receiving device and select the advertised host if not found add manaully using the ip shown in monitorize app.
5. Enter Moonlight's four-digit PIN in Monitorize when pairing.
6. When setting up two moonlight devices to use as lets say second and third monitor , as usual create the first virtual monitor, then click add display option to create second virtual monitor and start it, most of the time the second virtual monitor wont show in moonlight, in that case add it manually in moonlight using the ip along with respective port of that virtual monitor shown in the monitorize app. 
