<div align="center">
  <h1>🖥️ Monitorize</h1>
  <p><strong>Turn your Android tablet into a smooth, low-latency secondary monitor for Linux — over USB.</strong></p>

  [![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
  [![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20Android-lightgrey)](#)
  [![Tech](https://img.shields.io/badge/Tech-Wayland%20%7C%20GStreamer%20%7C%20ADB-orange)](#)
  [![Status](https://img.shields.io/badge/Status-Working%20%E2%9C%85-brightgreen)](#)
</div>

> **Project Status: Working & Actively Developed** — Core pipeline is fully functional and tested on fedora kde with some caveats. Screen mirroring at native-android-res @ native fps with hardware-accelerated H.264 decode on Android via ADB over USB.

---

## 📖 Overview

**Monitorize** transforms your Android tablet into a high-performance secondary monitor for your Linux desktop. It uses a PipeWire screen capture → GStreamer H.264 encode → ADB USB tunnel → Android `MediaCodec` hardware decode pipeline to deliver smooth, low-latency video with no Wi-Fi dependency.

Think *Spacedesk* or *Duet Display*, but open-source and built for Linux/Wayland power users.

### ✅ What Works Right Now (Only works on kde for now)

- **60fps, 1280×800** hardware-decoded stream on Samsung Galaxy Tab S7 FE
- **~100ms latency** over USB ADB tunnel
- **Crystal-clear output** — no pink artifacts, no chroma corruption
- **Smooth mouse cursor** — minimal ghosting or trails
- Fully hardware-accelerated decode via Android `MediaCodec`
- No Wi-Fi — runs entirely over USB

---

## 🏗️ Architecture

```
Fedora KDE (Wayland)
│
├─ krfb-virtualmonitor ──► Creates virtual "TabletDisplay" output in KWin
│
├─ PipeWire ScreenCast Portal ──► Captures the virtual display as a PipeWire stream
│
└─ GStreamer Pipeline:
     pipewiresrc → videorate → videoconvert → videoscale
     → x264enc (zerolatency, ultrafast, 15 Mbps)
     → h264parse → tcpclientsink → 127.0.0.1:7110
                                          │
                               ADB forward tcp:7110
                                          │
                               USB Cable (not Wi-Fi)
                                          │
Android (Samsung Tab S7 FE)
│
├─ ServerSocket:7110 ──► Receives raw H.264 Annex B byte stream
│
└─ MediaCodec (hardware decoder)
     → SurfaceView (fullscreen)
```

---

## 🛠️ Prerequisites

### Linux Host
| Requirement | Notes |
|-------------|-------|
| Fedora 44 / KDE Plasma 6 | Any modern Wayland compositor should work |
| `krfb` | Virtual monitor creation — `sudo dnf install krfb` |
| `gstreamer1-plugins-bad-free` | PipeWire source, x264 encoder |
| `gstreamer1-plugins-ugly-free` | x264enc plugin |
| `android-tools` | ADB — `sudo dnf install android-tools` |
| `python3-dbus` | For the fallback script |
| `python3-gobject` | GLib mainloop |

### Android Tablet
| Requirement | Notes |
|-------------|-------|
| Android 9+ | Tested on Samsung Galaxy Tab S7 FE (One UI 6) |
| USB Debugging enabled | Developer Options → USB Debugging |
| USB cable | High-quality USB-C cable recommended |

---

## 🚀 Setup & Run

### Step 1 — Create the virtual monitor (Linux)

```bash
krfb-virtualmonitor --resolution 1280x800 --name TabletDisplay --password test123 --port 5900
```

Leave this running. Open **KDE System Settings → Display & Monitor** and position `TabletDisplay` as an extended display.

### Step 2 — Connect tablet and set up ADB

```bash
# Connect tablet via USB, allow USB debugging prompt on tablet
adb devices          # should show your device as "device" (not unauthorized)
adb forward tcp:7110 tcp:7110
```

### Step 3 — Build and install the Android app (Or Download it from releases)

```bash
cd android/
./gradlew installDebug
adb shell am start -n com.example.monitorize/.MainActivity
```

The app will show **"Waiting for connection…"** — keep it open.

### Step 4 — Start streaming

```bash
python3 linux/monitorize_fallback.py
```

In the KDE screencast picker that appears, select **TabletDisplay**.

The tablet should start behaving as your second monitor.

---

## 📁 Project Structure

```
Monitorize/
├── android/                        # Android app (Kotlin + Compose)
│   └── app/src/main/java/com/example/monitorize/
│       ├── MainActivity.kt         # App entry, surface setup
│       ├── StreamReceiver.kt       # TCP socket → raw H.264 byte reader
│       ├── H264Decoder.kt          # MediaCodec hardware decoder
│       └── StreamScreen.kt         # SurfaceView composable
│
└── linux/
    ├── monitorize_fallback.py      # ✅ Main launcher (PipeWire portal)
    └── monitorize.sh               # Alternative (wf-recorder pipeline)
```

---

## ⚙️ Encoder Configuration

The GStreamer pipeline is tuned for minimum latency over USB:

| Parameter | Value | Reason |
|-----------|-------|--------|
| `speed-preset` | `ultrafast` | Minimum encode latency |
| `tune` | `zerolatency` | No frame buffering in encoder |
| `bitrate` | `15000 kbps` | High quality at 1280×800@60fps |
| `key-int-max` | `30` | IDR every 500ms for error recovery |
| `bframes` | `0` | No B-frames — zero reorder delay |
| `ref` | `1` | Single reference frame |
| `rc-lookahead` | `0` | No lookahead — encode immediately |
| `vbv-bufsize` | `1000` | ~67ms VBV buffer (low latency) |
| `queue` | `1 buffer, leaky` | Drop old frames, show latest |

---



## 🗺️ Roadmap

- [ ] Touch input forwarding (tap on tablet → mouse click on host)
- [ ] Auto-detect resolution from SPS NAL unit
- [ ] Auto-start on USB connect (Android foreground service)
- [ ] Resolution/FPS selection UI on Android

---

## 📄 License

Licensed under the **GNU General Public License v3.0**. See `LICENSE` for details.

---

<div align="center">
  <sub>Built by Vinnavan | Expanding your productivity, one monitor at a time.</sub>
</div>
