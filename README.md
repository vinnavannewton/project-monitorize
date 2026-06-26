<div align="center">
  <img src="linux/monitorize/assets/monitorize_desktop_logo.png" alt="Monitorize logo" width="160" />
  <h1>Monitorize</h1>
  <p><strong>Turn your Android, linux Laptop into a smooth, low-latency secondary monitor for your Linux Desktop .</strong></p>

<a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" /></a>
<img src="https://img.shields.io/badge/Platform-Linux%20%7C%20Android-lightgrey" />

</div>

> **Project Status: In beta & Actively Being Developed**
> Fully functional on KDE and Hyprland. Sway and GNOME support is experimental.

---

## Screenshots

<div align="center">
  <img src="screenshots/linux_frontpage.png" alt="Monitorize Linux front page" width="800" style="max-width: 100%;" />
</div>

---

## 📖 Overview

**Monitorize** turns your Android tablet, Laptop, PC into a secondary monitor for your Linux desktop.

Supported desktop environments are KDE Plasma, Hyprland. Sway and GNOME is experimental.

---

## 🛠️ Requirements:

| Android               | Desktop                                         |
| --------------------- | ----------------------------------------------- |
| Android 9+            | KDE (6.7+), Hyprland, Sway, Gnome(experimental) |
| Wi-Fi / USB Debugging | Tested on: Arch, fedora.                        |

---

### 📦 Dependencies (Must Do)

###### Before running Monitorize, install the required packages for your distro and desktop environment.

## <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/Fedora_icon_(2021).svg/960px-Fedora_icon_(2021).svg.png" height="28" alt="Fedora"> Fedora (DNF)

### Step 1 — Enable RPM Fusion:

```bash
bash -c 'sudo dnf install -y https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm https://download1.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm'
```

### Step 2 — Install Core Dependencies:

```bash
sudo dnf install -y --skip-unavailable \
  gstreamer1 \
  gstreamer1-plugins-base \
  gstreamer1-plugins-bad-free \
  gstreamer1-plugins-bad-freeworld \
  gstreamer1-plugins-ugly \
  gstreamer1-plugin-libav \
  pipewire \
  pipewire-gstreamer \
  python3-dbus \
  python3-gobject \
  android-tools
```

### Step 3 — Input Permission:

```bash
echo 'KERNEL=="uinput", MODE="0660", GROUP="input"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG input $USER
# Log out and back in for group change to take effect
```

### Step 4 — Desktop-Specific (Fedora):

### KDE Plasma:

KDE support requires Plasma 6.7+

```bash
sudo dnf install -y kscreen
```

---

### GNOME (Experimental):

No extra packages needed. However, you **must** disable hardware cursor rendering so the cursor is visible on the virtual monitor stream:

```bash
gsettings set org.gnome.mutter.wayland disable-hardware-cursor true
```

Log out and back in for the change to take effect. 

---

### Hyprland:

```bash
sudo dnf install -y \
  xdg-desktop-portal \
  xdg-desktop-portal-hyprland \
  xdg-desktop-portal-gtk \
  wlr-randr
```

---

### Sway:

```bash
sudo dnf install -y \
  xdg-desktop-portal \
  xdg-desktop-portal-wlr \
  xdg-desktop-portal-gtk
```

---

## <img src="https://upload.wikimedia.org/wikipedia/commons/thumb/1/13/Arch_Linux_%22Crystal%22_icon.svg/330px-Arch_Linux_%22Crystal%22_icon.svg.png" height="28" alt="Arch Linux"> Arch Linux (Pacman)

### Step 1 — Install Core Dependencies:

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
  x264 \
  android-tools
```

### Step 2 — Input Permission:

```bash
echo 'KERNEL=="uinput", MODE="0660", GROUP="input"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG input $USER
# Log out and back in for group change to take effect
```

### Step 3 — Desktop-Specific (Arch)

### KDE Plasma:

KDE support requires Plasma 6.7 and above

```bash
sudo pacman -S --needed kscreen
```

---

### GNOME (Experimental):

No extra packages needed. However, you **must** disable hardware cursor rendering so the cursor is visible on the virtual monitor stream:

```bash
gsettings set org.gnome.mutter.wayland disable-hardware-cursor true
```

Log out and back in for the change to take effect.

---

### Hyprland:

```bash
sudo pacman -S --needed \
  xdg-desktop-portal \
  xdg-desktop-portal-hyprland \
  xdg-desktop-portal-gtk \
  wlr-randr
```

---

### Sway:

```bash
sudo pacman -S --needed \
  xdg-desktop-portal \
  xdg-desktop-portal-wlr \
  xdg-desktop-portal-gtk
```

## <img src="https://upload.wikimedia.org/wikipedia/commons/4/4a/Debian-OpenLogo.svg" height="28" alt="Debian"> Debian / Ubuntu (APT)

### Step 1 — Enable non-free repos (Debian only, skip on Ubuntu):

```bash
sudo apt install -y software-properties-common
sudo apt-add-repository non-free
sudo apt update
```

### Step 2 — Install Core Dependencies:

```bash
sudo apt install -y \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-pipewire \
  gstreamer1.0-vaapi \
  intel-media-va-driver \
  mesa-va-drivers \
  pipewire \
  wireplumber \
  python3-dbus \
  python3-gi \
  adb \
  python3-pip \
  python3-venv \
  qt6-base-dev \
  libxkbcommon0 \
  psmisc \
  liboeffis1 \
  liboeffis-dev
```

### Step 3 — Input Permission:

```bash
echo 'KERNEL=="uinput", MODE="0660", GROUP="input"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG input $USER
# Log out and back in for group change to take effect
```

### Step 4 — Desktop-Specific (Debian / Ubuntu)

### ~~KDE Plasma~~:

KDE support requires Plasma 6.7+ which isnt avalable yet on debian stable and ubuntu

```bash
sudo apt install -y kscreen
```

### ---

### GNOME (Experimental):

No extra packages needed. However, you **must** disable hardware cursor rendering so the cursor is visible on the virtual monitor stream:

```bash
gsettings set org.gnome.mutter.wayland disable-hardware-cursor true
```

Log out and back in for the change to take effect.

---

### Hyprland:

```bash
sudo apt install -y \
  xdg-desktop-portal \
  xdg-desktop-portal-hyprland \
  xdg-desktop-portal-gtk \
  wlr-randr
```

---

### Sway:

```bash
sudo apt install -y \
  xdg-desktop-portal \
  xdg-desktop-portal-wlr \
  xdg-desktop-portal-gtk
```

---

## Running the Application:

1.After starting the stream in the desktop application make sure you go to your display settings and configure the newly created virtual display.

2.When made changes to the virtual display's position or anything sometimes the stream crashes, it's normal just start the stream again (This won't work in gnome though).

---

### Notes:

- Match the resolution and FPS set in the Android settings app to the desktop app settings.

- If the USB device is not detected, make sure `android-tools` is installed and run:
  
  to confirm the device is connected.

---

## 🚀 Getting Started

### 1. Clone and install (Desktop side)

```bash
git clone https://github.com/vinnavannewton/ProjectMonitorize.git
cd ProjectMonitorize/linux
cd scripts
chmod +x install.sh
./install.sh
```

Or run manually:

```bash
./venv/bin/python3 -m monitorize
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

- Install the APK from the Releases section.

---

## 🗺️ Roadmap

- [x] Stable CPU encoder (Software encoder).

- [x] Stable vaapi encoder

- [x] Fix stream corruption.

- [x] desktop GUI.

- [x] Touch screen.

- [x] Stylus support with pressure.

- [x] Encrypted Wi-Fi mode.

- [x] Sway DE (beta).
- [x] use laptop as second screen.
- [x] triple monitor setup.
- [ ] Stable nvidia encoder (waiting for driver 610.x which implemented proper DMA BUF).
- [ ] Stable gnome.

- [ ] Flathub distribution.

---

## Star History

<a href="https://www.star-history.com/?type=date&repos=vinnavannewton%2FProjectMonitorize">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=vinnavannewton/ProjectMonitorize&type=date&legend=top-left" />
 </picture>
</a>

<div align="center">
  <sub>Expanding your productivity, one monitor at a time.</sub>
</div>

## ❤️ Support

If **Project Monitorize** helped you, consider supporting the development.

**UPI ID:** `8660721754@upi`

<a href="upi://pay?pa=8660721754@upi&pn=Vinnavan%20Newton&cu=INR">
  <img src="https://img.shields.io/badge/Support%20via-UPI-6f42c1?style=for-the-badge" alt="Support via UPI">
</a>
