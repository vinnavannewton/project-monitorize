<div align="center">
  <img src="linux/monitorize/assets/monitorize_desktop_logo.png" alt="Monitorize logo" width="160" />
  <h1>Monitorize</h1>
  <p><strong>Turn your Android, Linux laptop into a secondary monitor for your Linux desktop.</strong></p>

<a href="https://www.gnu.org/licenses/agpl-3.0"><img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" /></a>
<img src="https://img.shields.io/badge/Platform-Linux%20%7C%20Android-lightgrey" />

</div>

## Screenshots

<div align="center">
  <img src="screenshots/front_page.png" alt="Monitorize front page" width="800" style="max-width: 100%;" />
  <br />
  <br />
  <img src="screenshots/config_page.png" alt="Monitorize configuration page" width="800" style="max-width: 100%;" />
</div>

---

## 📖 Overview

**Monitorize** turns your Android tablet, Laptop, PC into a secondary monitor for your Linux desktop.

**Supported desktop environments are KDE Plasma, Hyprland and GNOME.**

---

## 🛠️ Requirements:

| Android               | Desktop                               |
| --------------------- | ------------------------------------- |
| Android 9+            | 🥇KDE (6.7+),🥇Hyprland,🥈GNOME (50+) |
| Wi-Fi / USB Debugging | Tested on: Arch, Fedora, NixOS.       |

---

## Installation:

### Desktop:

<table>
  <tr>
    <td><strong>Fedora</strong></td>
    <td><a href="https://github.com/vinnavannewton/project-monitorize/wiki/Fedora-installation">Fedora Installation</a></td>
  </tr>
  <tr>
    <td><strong>Arch Linux</strong></td>
    <td><a href="https://github.com/vinnavannewton/project-monitorize/wiki/Arch-installation">Arch Installation</a></td>
  </tr>
  <tr>
    <td><strong>Ubuntu / Debian</strong></td>
    <td><a href="https://github.com/vinnavannewton/project-monitorize/wiki/Ubuntu-Debian-installation">Ubuntu Debian Installation</a></td>
  </tr>
  <tr>
    <td><strong>NixOS / Nix</strong></td>
    <td><a href="https://github.com/vinnavannewton/project-monitorize/wiki/Nix-installation">Nix Installation</a></td>
  </tr>
</table>

Fedora Copr packages are published from desktop release tags such as `desktop-v0.2.7`:

```bash
sudo dnf copr enable vinnavannewton/monitorize
sudo dnf install monitorize
sudo usermod -aG monitorize "$USER"
```

Log out and back in after the `usermod` command to enable touch and stylus input.

### Android:

**Install the APK from Android releases such as `android-v0.2.7` in the [Releases](https://github.com/vinnavannewton/project-monitorize/releases) section.**

Or build from source:

```bash
cd android
./gradlew installDebug
adb shell am start -n com.example.monitorize/.MainActivity
```

---

## Running the Application:

1. After starting the stream in the desktop application make sure you go to your display settings and configure the newly created virtual display.

2. When made changes to the virtual display's position and applied, then the stream crashes, it's normal just restart the stream and the virtual monitor will spawn in the previous applied position.

### Notes:

- Match the resolution and FPS set in the Android app's settings to the desktop app settings.

---

## Contributing:

Please read the [Contribution Guide](https://github.com/vinnavannewton/project-monitorize/wiki/Contributing).

---

## 🗺️ Roadmap

- [x] Stable CPU encoder (Software encoder).

- [x] Stable vaapi encoder

- [x] Fix stream corruption.

- [x] desktop GUI.

- [x] Touch screen.

- [x] Stylus support with pressure.

- [x] Encrypted Wi-Fi mode.

- [x] Stable gnome.

- [x] Linux laptop as a viewer.

- [ ] Windows laptop as a viewer.

- [ ] Multi monitor setup.

- [ ] Stable nvidia encoder (waiting for driver 610.x which implemented proper DMA BUF).

- [ ] AppImage.

---

## Star History

<a href="https://www.star-history.com/?type=timeline&repos=vinnavannewton%2Fproject-monitorize">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/project-monitorize&type=timeline&theme=dark&legend=top-left&sealed_token=i7tImjFAdn6mJ7yyA6dCuwhVSy9r7uKrNKAkLLhC-1M3qLL9yJIwEQM0Fdf0M5QZGhCq_-7SEIzjL2sZDwbC0p39iYygcf1qunSnjYfURTQmwsVJGtyRRkZcv2apbaeXmn0rW6vGgif5DjDrhPsMAgM82DP2VRXDcxAkL2dDMvJj6fZocbabPGEMA_F6" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/project-monitorize&type=timeline&legend=top-left&sealed_token=i7tImjFAdn6mJ7yyA6dCuwhVSy9r7uKrNKAkLLhC-1M3qLL9yJIwEQM0Fdf0M5QZGhCq_-7SEIzjL2sZDwbC0p39iYygcf1qunSnjYfURTQmwsVJGtyRRkZcv2apbaeXmn0rW6vGgif5DjDrhPsMAgM82DP2VRXDcxAkL2dDMvJj6fZocbabPGEMA_F6" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=vinnavannewton/project-monitorize&type=timeline&legend=top-left&sealed_token=i7tImjFAdn6mJ7yyA6dCuwhVSy9r7uKrNKAkLLhC-1M3qLL9yJIwEQM0Fdf0M5QZGhCq_-7SEIzjL2sZDwbC0p39iYygcf1qunSnjYfURTQmwsVJGtyRRkZcv2apbaeXmn0rW6vGgif5DjDrhPsMAgM82DP2VRXDcxAkL2dDMvJj6fZocbabPGEMA_F6" />
 </picture>
</a>

<div align="center">
  <sub>Expanding your productivity, one monitor at a time.</sub>
</div>
