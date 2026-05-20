<div align="center">
  <h1>🖥️ Monitorize</h1>
  <p><strong>Turn your Android tablet into a smooth, low-latency secondary monitor for Linux.</strong></p>

  <a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" /></a>
  <img src="https://img.shields.io/badge/Platform-Linux%20%7C%20Android-lightgrey" />
  <img src="https://img.shields.io/badge/Tech-Wayland%20%7C%20GStreamer%20%7C%20PyQt6-orange" />
  <img src="https://img.shields.io/badge/Status-Working%20%E2%9C%85-brightgreen" />
</div>

> **Project Status: Working & Actively Developed**
> Core pipeline is fully functional and tested on Fedora KDE (Wayland).
> Screen mirroring at native tablet resolutions and up to 120 FPS, using CPU `x264` encoding on Linux and hardware-accelerated H.264 decoding on Android over ADB (USB or Wi-Fi).

---

## 📖 Overview

**Monitorize** turns your Android tablet into a secondary monitor for your Linux desktop.

The pipeline is:

- Linux (Wayland) screen capture via PipeWire + portal
- CPU H.264 / IGPU encoding with GStreamer (`x264enc` tuned for low latency)
- Transport over ADB (USB or Wi-Fi TCP/IP)
- Hardware H.264 decode on Android using `MediaCodec`

### ✨ What You Get

- **Native resolution** on your tablet (e.g., 1280×800, 1920×1200, 2560×1600)
- **Configurable FPS** (30 / 60 / …) on both Linux and Android
- **USB Mode** for lowest latency and most stable quality
- **Wi-Fi Mode (Work In Progress)** using ADB over TCP/IP with lower bitrate tuned for wireless
- **PyQt6 desktop app** to guide you through: ADB, virtual display, streaming start/stop
- **Android app** with a simple UI: "Receive" and "Settings" (resolution/FPS)

---

## 🛠️ Requirements
### 📦 Dependencies (Must Do)
### Install PyQt6 package
```bash
pip install PyQt6
```
Before running Monitorize, install the required packages for your distro and desktop environment.
---
### 🐧 Fedora (DNF)

#### Step 1 — Enable RPM Fusion
Fedora does not ship `x264enc` by default due to patent restrictions. Enable RPM Fusion first:
```bash
bash -c 'sudo dnf install -y https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm'
```
#### Step 2 — Install Core Dependencies (all DEs)
```bash
sudo dnf install -y \
  gstreamer1 \
  gstreamer1-plugins-base \
  gstreamer1-plugins-bad-free \
  gstreamer1-plugins-bad-freeworld \
  gstreamer1-plugins-ugly \
  gstreamer1-plugin-libav \
  gstreamer1-plugin-pipewire \
  pipewire \
  pipewire-gstreamer \
  python3-dbus \
  python3-gobject \
  python3-pyqt6 \
  android-tools
```

#### Step 3 — Install snegg (libei Python bindings) for Touch/Pen Input (only needed for kde right now)

The touch daemon uses `snegg` — the official Python bindings for `libei`.
Do **not** use `pip install pyei` (that is a different, unrelated package).

```bash
# C build dependencies for snegg
sudo dnf install -y \
  libei \
  libei-devel \
  liboeffis \
  gcc \
  python3-devel \
  meson \
  ninja-build \
  pkg-config \
  git

# Install snegg from the upstream GitLab repo
python3 -m pip install --user \
  git+https://gitlab.freedesktop.org/whot/snegg
```

> **Note:** The Python module is `snegg`, not `ei`.
> Import it as `import snegg.ei` and `import snegg.oeffis` — not `import ei`.

#### Step 3 — Desktop-Specific (Fedora)

**KDE Plasma**
KDE requires `krfb` to create the virtual monitor output:
```bash
sudo dnf install -y krfb
```

**GNOME**
No extra packages needed. GNOME uses Mutter's built-in `RecordVirtual` D-Bus API which works out of the box.

**Hyprland**
Install the Hyprland XDG portal backend:
```bash
sudo dnf install -y \
  xdg-desktop-portal \
  xdg-desktop-portal-hyprland
```

---

### 🐧 Arch Linux (Pacman)

#### Step 1 — Install Core Dependencies (all DEs)
```bash
sudo pacman -S --needed \
  gstreamer \
  gst-plugins-base \
  gst-plugins-good \
  gst-plugins-bad \
  gst-plugins-ugly \
  gst-plugin-pipewire \
  pipewire \
  wireplumber \
  python-dbus \
  python-gobject \
  python-pyqt6 \
  x264 \
  android-tools
```

#### Step 2 — Install snegg (Arch)

```bash
# Build deps
sudo pacman -S --needed gcc python meson ninja pkg-config git libei

# Install snegg from upstream
python3 -m pip install --user \
  git+https://gitlab.freedesktop.org/whot/snegg
```

#### Step 2 — Desktop-Specific (Arch)

**KDE Plasma**
```bash
sudo pacman -S --needed krfb
```

**GNOME**
No extra packages needed. GNOME uses Mutter's built-in `RecordVirtual` D-Bus API out of the box.

**Hyprland**
```bash
sudo pacman -S --needed \
  xdg-desktop-portal \
  xdg-desktop-portal-hyprland \
  xdg-desktop-portal-gtk \
  wlr-randr
```

---

### 🐧 Debian / Ubuntu (APT)

#### Step 1 — Enable non-free repos (Debian only, skip on Ubuntu)
Debian restricts `gstreamer1.0-plugins-ugly` to the `non-free` component. Enable it first:
```bash
sudo apt install -y software-properties-common
sudo apt-add-repository non-free
sudo apt update
```

#### Step 2 — Install Core Dependencies (all DEs)
```bash
sudo apt install -y \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-pipewire \
  pipewire \
  wireplumber \
  python3-dbus \
  python3-gi \
  python3-pyqt6 \
  adb
```

#### Step 3 — Desktop-Specific (Debian / Ubuntu)

**KDE Plasma**
```bash
sudo apt install -y krfb
```

**GNOME**
No extra packages needed. GNOME uses Mutter's built-in `RecordVirtual` D-Bus API out of the box.

**Hyprland**
```bash
sudo apt install -y \
  xdg-desktop-portal \
  xdg-desktop-portal-hyprland
```
> **Note:** `xdg-desktop-portal-hyprland` may not be in older Debian/Ubuntu repos. If not found, build from source: [xdg-desktop-portal-hyprland](https://github.com/hyprwm/xdg-desktop-portal-hyprland)

---

## Running the Application

1. From the project repository, go into the `linux` directory and run `monitor_gui.py`.
2. enable usb debuuging and connect your android to your pc via usb.
3. in the desktop app click usb then click "i have connected"
4. Then open the android app and first configure settings use ur native resolution and fps for best experience
5. click receive on android app then click Start streaming on desktop app (order is important)
6. When the input access pop-up appears, allow it first.
7. From the second pop-up, select the **Tablet Virtual Display**.

> [!WARNING]
> The order matters: if you select the display first or click **Stream** before tapping **Receive** on Android, it will not work.

### Notes

- The resolution and FPS set in the Android app must match the desktop app.
- If the USB device is not detected, make sure `android-tools` is installed and run:
  ```bash
  adb devices
  ```
  to confirm the device is connected.



### Linux Host

| Requirement       | Notes                                              |
|-------------------|----------------------------------------------------|
| Wayland desktop   | KDE / GNOME / Hyprland tested                      |
| GStreamer + x264  | See distro steps above                             |
| PipeWire          | Required for screen capture                        |
| Python 3          | For scripts & GUI                                  |
| PyQt6             | `python3-pyqt6` / `python-pyqt6`                   |
| `adb`             | `android-tools` (Fedora/Arch) or `adb` (Debian)    |
| `krfb`            | KDE only — virtual monitor creation                |
| `snegg` + `libei` | Touch/pen(on RoadMap) input forwarding via libei portal. Install with `pip install --user git+https://gitlab.freedesktop.org/whot/snegg` |

### Android Tablet

| Requirement        | Notes                                               |
|--------------------|-----------------------------------------------------|
| Android 9+         | Tested on Samsung Galaxy Tab S7 FE                  |
| USB Debugging      | Enable in Developer Options                         |
| Monitorize app     | Built from `/android` or downloaded from Releases   |
| Decent USB cable   | True USB 3.x cable recommended for best USB mode    |
| 5GHz Wi-Fi (opt.)  | Recommended if using Wi-Fi ADB                      |

---

## 🚀 Getting Started

### 1. Clone, build and run (Desktop side)

```bash
git clone https://github.com/vinnavannewton/ProjectMonitorize.git
cd ProjectMonitorize
python3 monitorize_gui.py
```

### 2. Android side

Either:

- Build from source:
  ```bash
  cd android
  ./gradlew installDebug
  adb shell am start -n com.example.monitorize/.MainActivity
  ```

Or:

- Install the APK from the Releases section, then open **Monitorize**.

---

### Wi-Fi Mode (Work In Progress)

---


## 🗺️ Roadmap

- [x] Stable CPU-based H.264 pipeline (Linux → Android).
- [x] Fix TCP chunking / macroblock corruption.
- [x] desktop GUI.
- [ ] Touch screen and stylus support.
- [ ] Stable Wi-Fi mode.
- [ ] Flathub distribution.
- [ ] use your other laptop as second screen for your host laptop.
- [ ] Triple monitor setup attempt.
- [ ] multi monitor single desktop.

---

## Star History

<a href="https://www.star-history.com/?repos=vinnavannewton%2FProjectMonitorize&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&theme=dark&legend=bottom-right" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&legend=bottom-right" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&legend=bottom-right" />
 </picture>
</a>

## 📄 License

Licensed under the **GNU General Public License v3.0**. See `LICENSE` for details.

<div align="center">
  <sub>Built by Vinnavan · Expanding your productivity, one monitor at a time.</sub>
</div>
