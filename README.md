<div align="center">
  <img src="linux/monitorize/assets/monitorize_desktop_logo.png" alt="Monitorize logo" width="160" />
  <h1>Monitorize</h1>
  <p><strong>Turn your Android, Linux laptop into a secondary monitor for your Linux desktop.</strong></p>

<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPL%20v3-blue.svg" /></a>
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

**Monitorize** turns your Android tablet, PC into a secondary monitor for your Linux desktop.

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

### Android:

**Install the APK from Android [Releases](https://github.com/vinnavannewton/project-monitorize/releaseslatest).**

Or build from source:

```bash
cd android
./gradlew installDebug
adb shell am start -n com.example.monitorize/.MainActivity
```

---

## Running the Application:

1. After starting the stream in the desktop application make sure you go to your display settings and configure the newly created virtual display.

2. When made changes to the virtual display's position, it might stop working, it's normal just restart the stream and the virtual monitor will spawn in the position it was set to.

---

## Contributing:

Please read the [Contribution Guide](https://github.com/vinnavannewton/project-monitorize/wiki/Contributing).

---

## Star History:

<a href="https://www.star-history.com/?repos=vinnavannewton%2Fproject-monitorize&type=timeline&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/project-monitorize&type=timeline&theme=dark&legend=top-left&sealed_token=rorqKrSf4SXCagrArSYq6EdpSyMY8dKOYaF3gc_52VeV2pLpUOfTA8P7utPV5-nnbs6KSGO-TKGZX3H27tgqi09UUtMtr7jYh10203VVPbqApAGw6t2gJM6iSBZ-mFVnx-4ct7WetN_yo_LxEp5bKIjev4z0_1UmM9-rk_l7d4AL1qGblSXPZLoQRBT5" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=vinnavannewton/project-monitorize&type=timeline&legend=top-left&sealed_token=rorqKrSf4SXCagrArSYq6EdpSyMY8dKOYaF3gc_52VeV2pLpUOfTA8P7utPV5-nnbs6KSGO-TKGZX3H27tgqi09UUtMtr7jYh10203VVPbqApAGw6t2gJM6iSBZ-mFVnx-4ct7WetN_yo_LxEp5bKIjev4z0_1UmM9-rk_l7d4AL1qGblSXPZLoQRBT5" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=vinnavannewton/project-monitorize&type=timeline&legend=top-left&sealed_token=rorqKrSf4SXCagrArSYq6EdpSyMY8dKOYaF3gc_52VeV2pLpUOfTA8P7utPV5-nnbs6KSGO-TKGZX3H27tgqi09UUtMtr7jYh10203VVPbqApAGw6t2gJM6iSBZ-mFVnx-4ct7WetN_yo_LxEp5bKIjev4z0_1UmM9-rk_l7d4AL1qGblSXPZLoQRBT5" />
 </picture>
</a>

---

## Support Monitorize

<div align="center">
  <a href="https://ko-fi.com/vinnavan">
    <img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Buy me a coffee on Ko-fi" />
  </a>
</div>

---

<div align="center">
  <sub>Expanding your productivity, one monitor at a time.</sub>
</div>
