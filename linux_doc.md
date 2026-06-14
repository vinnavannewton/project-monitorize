# 🖥️ Monitorize Linux Desktop App Documentation

Welcome to the internal architectural and implementation documentation of the **Monitorize Linux Desktop App**. 

The Linux app acts as the streaming host and input consumer. It manages virtual displays, captures desktop frames, encodes them using hardware or software encoders, streams them over TCP to the Android client, and receives touch/stylus events to inject them back into the Wayland compositor.

---

## 🏗️ Architectural Overview

The desktop codebase is written in Python and uses a decoupled architecture:
* **Control Panel (GUI)**: Built with PyQt6 and QML, handling settings persistence, adb reverse routing, and process lifecycles.
* **Display Streamers**: Desktop environment-specific wrappers (KDE, Hyprland, GNOME) that interface with compositor APIs and portals to create virtual outputs and initialize PipeWire captures.
* **GStreamer Pipeline Builder**: Programmatically constructs optimized H.264 video encoding pipelines (VA-API, NVENC, or CPU).
* **Input Injection Daemon**: Receives network packets from the client and injects them back as virtual OS input events using `libei` (via `snegg`) or kernel-level `/dev/uinput` devices.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        MONITORIZE LINUX APP (GUI)                      │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ (Spawns QProcesses)
         ┌──────────────────────────┼──────────────────────────┐
         ▼                          ▼                          ▼
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│  Streamer KDE    │      │Streamer Hyprland │      │  Streamer GNOME  │
│(krfb-virtualmon) │      │ (hyprctl output) │      │ (Mutter D-Bus)   │
└────────┬─────────┘      └────────┬─────────┘      └────────┬─────────┘
         │                         │                         │
         └─────────────────────────┼─────────────────────────┘
                                   │
                                   ▼
                       ┌──────────────────────┐
                       │   pipeline_builder   │
                       │ (gst-launch H.264)   │
                       └───────────┬──────────┘
                                   │ (Streams raw H.264)
                                   ▼
                       ┌──────────────────────┐
                       │     touch_daemon     │ (Receives Android Input)
                       │   (libei / uinput)   │
                       └──────────────────────┘
```

---

## 🔍 Subsystem Details

### 1. Control Panel & GUI Backend (`gui/main_window.py` & QML Pages)

The GUI manages the application state and interacts with QML elements using PySide6/PyQt6 properties, slots, and signals.

* **Settings Persistence (`gui/settings.py`)**: Stores settings in `~/.config/monitorize/settings.ini` using `QSettings`. Supports profile configurations for both USB and Wi-Fi streaming.
* **ADB Management & Port Forwarding**:
  * Scans for connected devices via `adb devices`.
  * Configures reverse port tunnels using:
    * `adb reverse tcp:7110 tcp:7112` (for USB mode video routing)
    * `adb reverse tcp:7111 tcp:7111` (for USB mode touch data routing)
* **Receiver Mode**: 
  * Integrates a Zeroconf mDNS browser (`_monitorize._tcp.local.`) to detect other Monitorize hosts.
  * Spawns a `tcpclientsrc` GStreamer pipeline to connect, decode, and display streams from other hosts in a window.
* **Power Management Inhibition**:
  * Prevents sleep/idle states during active sessions by invoking system inhibitors (`systemd-inhibit` and KDE D-Bus `org.freedesktop.ScreenSaver.Inhibit`).

---

### 2. Desktop Streamers (`Streamer_*.py`)

Each supported desktop environment has a dedicated Python wrapper to set up the virtual monitor and capture session:

#### A. KDE Plasma (`Streamer_kde.py`)
* Leverages `krfb-virtualmonitor` to create a virtual monitor named `TabletDisplay`.
* Requests screencast credentials from the Freedesktop ScreenCast portal (`org.freedesktop.portal.ScreenCast`).
* Extracts the underlying PipeWire stream node ID and file descriptor (via `OpenPipeWireRemote`), then spawns GStreamer.

#### B. Hyprland (`Streamer_hyprland.py`)
* Uses `hyprctl output create headless` to spawn a headless display on demand.
* Configures resolution and layout placement using `hyprctl keyword monitor`.
* Obtains PipeWire screencast streams via `xdg-desktop-portal-hyprland`.
* Destroys the virtual output dynamically during cleanup to restore the display layout.

#### C. GNOME (`Streamer_gnome.py`)
* Interacts directly with Mutter's private D-Bus API (`org.gnome.Mutter.ScreenCast`).
* Invokes `RecordVirtual` with logical configurations (logical resolution and position coordinates).
* Listens to the `PipeWireStreamAdded` signal on the screen recording D-Bus interface to obtain the target PipeWire node.

---

### 3. GStreamer Pipeline Builder (`pipeline_builder.py`)

This utility builds optimized `gst-launch-1.0` shell pipelines based on selected preferences and hardware capability:

* **Hardware Video Encoders**:
  * **Intel/AMD VA-API**: Uses `vah264enc`, `vah264lpenc`, or `vaapih264enc` (excluding NVIDIA cards). Configured with constant quantization parameters (`qpi=20`, `qpp=22`), disabled B-frames (`b-frames=0`), and low latency options (`target-usage=7`).
  * **NVIDIA NVENC**: Uses `nvh264enc` with low-latency overrides (`zerolatency=true`, `rc-mode=cbr`, `preset=p1`, `bframes=0`).
* **Software Video Encoder (CPU)**:
  * Falls back to `x264enc` optimized for real-time delivery (`tune=zerolatency`, `speed-preset=ultrafast`, `sliced-threads=false`, `threads=1`).
* **Stream Optimization Modes**:
  * **Speed**: Minimizes latency by reducing GOP (Group of Pictures) keyframe spacing dynamically based on current FPS (`key-int-max = max(fps // 2, 15)`).
  * **Stability**: Uses constant small keyframe intervals (`15`) and H.264 intra-refresh (`intra-refresh=true`) to make streams resilient to Wi-Fi packet drops.
* **Transmission Sink**:
  * Streams raw H.264 video via `tcpserversink` with `sync=false` and `sync-method=2` (serves clients starting from the most recent keyframe).
  * Quality of Service: Sets the socket's DSCP IP header bits to `48` (`qos-dscp=48` / network control priority).

---

### 4. Touch Input Injection Daemon (`touch_daemon.py`)

The touch daemon runs in the background, listening for incoming event payloads from the Android app, mapping the coordinates, and injecting them into the host.

* **Network Receivers**:
  * **TCP (USB)**: Listens on port `7111` for ADB-forwarded touch input.
  * **UDP (Wi-Fi)**: Listens on port `7113` for direct, low-overhead Wi-Fi packet datagrams.
* **Injectors**:
  * **KDE & GNOME (Wayland)**:
    * Implements standard Wayland input emulation using `libei` (via `snegg` & `oeffis`).
    * Communicates with the `org.freedesktop.portal.RemoteDesktop` D-Bus API to request input control.
    * Dynamically creates virtual touchscreen and relative absolute pointer devices.
  * **Hyprland**:
    * Bypasses the RemoteDesktop portal (unsupported by `xdg-desktop-portal-hyprland`) by writing directly to `/dev/uinput` using `evdev` to create a virtual touchscreen device named `Monitorize-Touch`.
    * Utilizes `hyprctl keyword device:monitorize-touch:output` to map touch coordinates to the corresponding virtual headless monitor.
* **Coordinate Mapping & Scaling**:
  * Detects current virtual monitor positions and scaling by parsing output states from compositor tools:
    * `kscreen-doctor -j` on KDE.
    * `hyprctl monitors -j` on Hyprland.
    * Mutter's D-Bus `DisplayConfig` properties on GNOME.
  * Converts the normalized coordinate payload (`0-65535`) sent from Android into absolute pixel positions matching the compositor's layout.

---

## 🚀 Building & Running

### Dependencies
Ensure the system-level packages specified in the root `README.md` are installed. Then configure the python virtual environment:
```bash
cd linux
chmod +x install.sh
./install.sh
```

### Standalone Command Line Execution
To run the GUI launcher manually:
```bash
./venv/bin/python3 monitorize_gui.py
```

To run the streamers in standalone mode (using defaults):
```bash
# Example for Hyprland
./venv/bin/python3 Streamer_hyprland.py 2560 1600 60 8000 usb
```
