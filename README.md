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
> Screen mirroring at native tablet resolutions and up to 60 FPS, using CPU `x264` encoding on Linux and hardware-accelerated H.264 decoding on Android over ADB (USB or Wi-Fi).

---

## 📖 Overview

**Monitorize** turns your Android tablet into a high-performance secondary monitor for your Linux desktop.

The pipeline is:

- Linux (Wayland) screen capture via PipeWire + portal
- CPU H.264 encoding with GStreamer (`x264enc` tuned for low latency)
- Transport over ADB (USB or Wi-Fi TCP/IP)
- Hardware H.264 decode on Android using `MediaCodec`
- Fullscreen rendering to a `SurfaceView`

Think *Spacedesk* / *Duet Display*, but:

- Open-source
- Focused on Linux / Wayland
- Simple CPU-based encoder (no GPU driver drama)

### ✨ What You Get

- **Native resolution** on your tablet (e.g., 1280×800, 1920×1200, 2560×1600)
- **Configurable FPS** (30 / 60 / …) on both Linux and Android
- **USB Mode** for lowest latency and most stable quality
- **Wi-Fi Mode (Work In Progress)** using ADB over TCP/IP with lower bitrate tuned for wireless
- **PyQt6 desktop app** to guide you through: ADB, virtual display, streaming start/stop
- **Android app** with a simple UI: "Receive" and "Settings" (resolution/FPS)

---

## 🏗️ Architecture

```text
Linux (Wayland, e.g. Fedora KDE)
│
├─ PyQt6 GUI (monitorize_gui.py)
│    ├─ Lets you pick: USB / Wi-Fi mode
│    ├─ Lets you choose Resolution + FPS
│    └─ Runs ADB + virtual monitor + streamer scripts
│
├─ krfb-virtualmonitor
│    └─ Creates a virtual "TabletDisplay" output in KWin at chosen resolution
│
├─ PipeWire ScreenCast Portal
│    └─ Captures the "TabletDisplay" as a PipeWire stream
│
└─ GStreamer (Streamer_usb.py / monitorize_wifi.py)
     pipewiresrc (Wayland) →
     videorate (FPS cap) →
     videoconvert →
     x264enc (CPU, zerolatency, ultrafast) →
     h264parse (alignment=au) →
     tcpclientsink → localhost:7110
                                │
                        ADB forward tcp:7110
                                │
                        USB cable or Wi-Fi ADB
                                │
Android Tablet
│
├─ StreamReceiver.kt
│    └─ TCP ServerSocket on 7110
│       Reassembles Annex B frames from TCP stream
│
└─ H264Decoder.kt
     ├─ MediaCodec (hardware decode) in low-latency mode
     └─ Decodes to SurfaceView (full-screen, fixed-size)
```

---

## 🛠️ Requirements

### 📦 Dependencies (Must Do)

Before running Monitorize, install the required packages for your distro and desktop environment. Follow your distro section below in order.

---

### 🐧 Fedora (DNF)

#### Step 1 — Enable RPM Fusion
Fedora does not ship `x264enc` by default due to patent restrictions. Enable RPM Fusion first:
```bash
sudo dnf install -y \
  https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm \
  https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm
```

#### Step 2 — Install Core Dependencies (all DEs)
```bash
sudo dnf install -y \
  gstreamer1 \
  gstreamer1-plugins-base \
  gstreamer1-plugins-bad-free \
  gstreamer1-plugins-ugly \
  gstreamer1-plugins-ugly-free \
  gstreamer1-plugin-x264 \
  gstreamer1-plugin-pipewire \
  pipewire \
  pipewire-gstreamer \
  python3-dbus \
  python3-gobject \
  python3-pyqt6 \
  android-tools
```

#### Step 3 — Install snegg (libei Python bindings) for Touch/Pen Input

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
| `snegg` + `libei` | Touch/pen input forwarding via libei portal. Install with `pip install --user git+https://gitlab.freedesktop.org/whot/snegg` |

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

### 1. Clone and build

```bash
git clone https://github.com/vinnavannewton/ProjectMonitorize.git
cd ProjectMonitorize
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

In the app:

1. Open **Settings**.
2. Choose your tablet's resolution (e.g., `2560x1600`).
3. Choose FPS (e.g., `60`).
4. Go back and tap **Receive Stream**. The app will wait for a connection.

### 3. Linux desktop app (PyQt GUI)

From the repo root:

```bash
cd linux
python3 monitorize_gui.py
```

The GUI will guide you through:

1. Choose **USB Mode** (Wi-Fi Mode is still experimental).
2. Connect your tablet via USB and click **"I have connected it"**.
   - The app runs `adb devices` and `adb forward tcp:7110 tcp:7110`.
3. Select **Resolution** and **FPS** in the GUI.
   - These must match the Android app's Settings.
4. Click **"Start Streaming"**.
   - The app starts `krfb-virtualmonitor` with the chosen resolution.
   - Shows a 3…2…1 countdown while the virtual screen is created.
   - Then runs `Streamer_usb.py` with the selected resolution/FPS.
5. When the ScreenCast popup appears, **select "TabletDisplay"** and click **Share**.

Your tablet should now show your Linux desktop as a second monitor.

---

## 🔧 USB vs Wi-Fi Modes

### USB Mode (Recommended)

- Lowest latency.
- More stable bandwidth.
- Just needs:
  ```bash
  adb devices
  adb forward tcp:7110 tcp:7110
  ```

### Wi-Fi Mode (Experimental)

Wi-Fi mode uses **ADB over TCP/IP** (still TCP, but over wireless):

1. With USB connected once:
   ```bash
   adb tcpip 5555
   ```
2. Disconnect USB. Find tablet IP (e.g., `192.168.1.15`).
3. Connect over Wi-Fi:
   ```bash
   adb connect 192.168.1.15:5555
   adb forward tcp:7110 tcp:7110
   ```
4. In the GUI, choose **Wi-Fi Mode**, then start streaming.
   - `monitorize_wifi.py` uses **lower bitrate** and **more frequent keyframes** so Wi-Fi dropouts don't trash the frame.

> Wi-Fi is great for browsing/code, but USB will always feel snappier for fast mouse-heavy work.

---

## ⚙️ Internals & Latency Tuning

Key encoder settings (CPU `x264enc`) in the scripts:

- `tune=zerolatency` — no internal frame buffering.
- `speed-preset=ultrafast` — trade compression for speed.
- `bitrate=` — configurable; higher = better quality, more bandwidth.
- `key-int-max=15–30` — frequent IDR frames for quick recovery if packets are dropped.
- `bframes=0` — no B-frames; avoids reordering delay.

On Android:

- `MediaCodec` is configured with `LOW_LATENCY` flags (where available).
- A dedicated decode thread pulls frames from a small queue, always preferring the most recent frame over backlog.
- TCP chunks are reassembled into full Annex B Access Units by scanning for `0x00000001` start codes before feeding into `MediaCodec`.

This combination is why you no longer see "random colored pixels" or corruption on static wallpaper scenes: the decoder never receives partial frames anymore.

---

## 🧱 Project Layout

```text
ProjectMonitorize/
├── README.md
├── android/
│   └── app/src/main/java/com/example/monitorize/
│       ├── MainActivity.kt
│       ├── StreamReceiver.kt
│       ├── H264Decoder.kt
│       └── StreamScreen.kt
└── linux/
    ├── monitorize_gui.py        # PyQt6 desktop controller
    ├── Streamer_usb.py          # USB mode streamer (CPU x264enc)
    └── monitorize_wifi.py       # Wi-Fi mode streamer (CPU x264enc, lower bitrate)
```

---

## 🗺️ Roadmap

- [x] Stable CPU-based H.264 pipeline (Linux → Android).
- [x] Fix TCP chunking / macroblock corruption.
- [x] Dynamic resolution & FPS (Linux + Android).
- [x] PyQt6 desktop GUI with wizard flow and countdown.
- [x] Touch/pen input forwarding via libei + XDG RemoteDesktop portal (`touch_daemon.py`).
- [ ] Polished Wi-Fi mode (potential UDP/RTP option).
- [ ] Flatpak packaging & Flathub distribution.

---

## Star History

<a href="https://www.star-history.com/?type=date&repos=vinnavannewton%2FProjectMonitorize">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&legend=top-left" />
 </picture>
</a>

## 📄 License

Licensed under the **GNU General Public License v3.0**. See `LICENSE` for details.

<div align="center">
  <sub>Built by Vinnavan · Expanding your productivity, one monitor at a time.</sub>
</div>
