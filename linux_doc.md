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

The GUI manages the application state and interacts with QML elements using PyQt6 properties, slots, and signals.

* **Settings Persistence (`gui/settings.py`)**: Stores settings in `~/.config/monitorize/settings.ini` using `QSettings`. Supports profile configurations for both USB and Wi-Fi streaming.
  * `general/minimize_to_tray`: Keep the app running in the tray when streaming.
  * `general/enable_touch`: Start or skip the input bridge.
  * `general/enable_stylus_features`: On KDE/GNOME/Hyprland, opt into uinput stylus features instead of the compatibility-only path.
  * `general/stylus_only`: Drop finger-touch packets and accept only stylus/eraser packets.
* **Theme and Controls**:
  * `gui/Theme.qml` defines the shared modern dark palette, text colors, border colors, `controlRadius`, `cardRadius`, and `hoverScale`.
  * Shared controls (`CustomButton`, `CustomTextField`, `CustomComboBox`, `CustomCheckBox`) consume these tokens so palette and shape changes stay centralized.
  * The streaming page's KDE `Add Display` action uses `assets/svg/display-add.svg` instead of an emoji glyph so it renders consistently across desktop fonts.
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

### 2. Desktop Streamers & Virtual Display Setup (`Streamer_*.py`)

Each desktop environment manages virtual monitor configuration differently depending on its Wayland architecture:

#### A. KDE Plasma (`Streamer_kde.py`)
* Leverages `krfb-virtualmonitor` to configure a virtual monitor named `TabletDisplay`.
* The system establishes a local VNC loop on port 5900.
* The wrapper queries the Freedesktop ScreenCast portal (`org.freedesktop.portal.ScreenCast`) to gain PipeWire credentials for `TabletDisplay`.
* The PipeWire file descriptor (retrieved via `OpenPipeWireRemote`) and node ID are passed to start the GStreamer capture.

#### B. Hyprland (`Streamer_hyprland.py`)
* Invokes `hyprctl output create headless` to request a headless monitor output.
* Dynamically sets up logical size and positioning using `hyprctl keyword monitor <name>,<res>@<fps>,auto,1`.
* Obtains PipeWire capture sources through the standard Freedesktop ScreenCast portal backed by `xdg-desktop-portal-hyprland`.
* During exit, calls `hyprctl output remove <name>` to cleanly delete the headless monitor.

#### C. GNOME (`Streamer_gnome.py`)
* Communicates directly with Mutter's private D-Bus API (`org.gnome.Mutter.ScreenCast`).
* Invokes `RecordVirtual` with logical configurations (resolution struct, logical positioning offset, frame rate, and cursor mode).
* Captures the `PipeWireStreamAdded` signal on the screen recording interface to capture the node ID.

---

### 3. GStreamer Pipeline Builder (`pipeline_builder.py`)

This utility builds optimized `gst-launch-1.0` shell pipelines based on selected preferences and hardware capability:

* **Hardware Video Encoders**:
  * **Intel/AMD VA-API**: Uses `vah264enc`, `vah264lpenc`, or `vaapih264enc` (excluding NVIDIA cards). Configured with constant quantization parameters (`qpi=20`, `qpp=22`), disabled B-frames (`b-frames=0`), and low latency options (`target-usage=7`).
  * **NVIDIA NVENC**: Uses `nvh264enc` with low-latency overrides (`zerolatency=true`, `rc-mode=cbr`, `preset=p1`, `bframes=0`).
* **Software Video Encoder (CPU)**:
  * Falls back to `x264enc` optimized for real-time delivery (`tune=zerolatency`, `speed-preset=ultrafast`, `sliced-threads=false`, `threads=1`).
* **Optimized GStreamer Elements & Filters**:
  * `pipewiresrc do-timestamp=true keepalive-time=1000`: Synchronizes GStreamer clocks with PipeWire timestamp metadata, keeping the PipeWire buffer stream alive if no new frames are pushed.
  * `always-copy=true/false`: Configured to `false` for VAAPI pipelines to prevent copying video memory back to host memory prior to encoding.
  * `tcpserversink sync=false sync-method=2 recover-policy=2 buffers-max=10 qos-dscp=48`:
    * `sync=false`: Prevents the GStreamer pipeline clock from synchronizing with the video sink, avoiding network latency delays.
    * `sync-method=2`: Sends frames starting immediately from the latest keyframe, ensuring newly connected client devices connect instantly without waiting for a GOP cycle.
    * `recover-policy=2`: Implements a leaky downstream policy, dropping older unconsumed packets instead of stalling encoder operations when network congestion occurs.
    * `qos-dscp=48`: Flags output IP packets with DSCP CS6 (Voice/Control class) to prioritize packets on network switches and router queues.

---

### 4. Input Translation & Injection (`touch_daemon.py`)

The touch daemon runs in the background, listening for incoming event payloads from the Android app, mapping the coordinates, and injecting them into the host.

* **Network Receivers**:
  * **TCP (USB)**: Listens on port `7111` for ADB-forwarded touch input.
  * **UDP (Wi-Fi)**: Listens on port `7113` for direct, low-overhead Wi-Fi packet datagrams.
* **Packet Protocol**:
  * Touch packets keep the legacy `0x03` packet type with a 13-byte payload and 18-byte framed message.
  * Legacy pen packets `0x04` remain accepted for compatibility.
  * Extended pen packets `0x05` use a 19-byte payload and carry pressure, X/Y tilt, hover distance, Android button state, and cancel/hover flags.
  * Palm rejection is delegated to Android/tablet input. The daemon treats cancel flags and `ACTION_CANCEL` as release/cancel events to avoid stuck stylus or touch state.
  * Any stylus/eraser packet suppresses finger-touch packets for 5 seconds and releases active finger contacts. With `stylus_only`, finger-touch packets are always dropped.
* **Injectors**:
  * **KDE & GNOME (Wayland, default)**:
    * Implements standard Wayland input emulation using `libei` (via `snegg` & `oeffis`).
    * Communicates with the `org.freedesktop.portal.RemoteDesktop` D-Bus API to request input control.
    * Dynamically creates virtual touchscreen and relative absolute pointer devices.
  * **KDE & GNOME Stylus Features (optional)**:
    * Uses `/dev/uinput` instead of libei to create `Monitorize-Touch` and `Monitorize-Stylus`.
    * Exposes stylus pressure, X/Y tilt, distance, eraser, hover, and stylus buttons to drawing apps.
    * On KDE, binds the uinput devices to `Virtual-TabletDisplay` through KWin's input-device `outputName` property.
    * Uses uinput only in stylus-feature mode. If uinput permissions are missing or KDE cannot bind the touch device to the virtual output, input stops instead of opening the RemoteDesktop portal or emulating pen events as mouse input.
  * **Hyprland**:
    * Bypasses the RemoteDesktop portal (unsupported by `xdg-desktop-portal-hyprland`) by writing directly to `/dev/uinput` using `evdev` to create a virtual touchscreen device named `Monitorize-Touch`.
    * When stylus features are enabled, also creates `Monitorize-Stylus` with pressure, tilt, distance, eraser, hover, and stylus buttons.
    * Utilizes `hyprctl keyword device:...:output` to map uinput devices to the corresponding virtual headless monitor.
* **Coordinate Mapping & Scaling**:
  * Android touch events transmit normalized coordinates ranging from `0` to `65535` (`COORD_MAX`).
  * `touch_daemon.py` maps these values to physical coordinate offsets depending on the current desktop compositor layout:
    1. **KDE**: Parses `kscreen-doctor -j` to query logical offsets `(x, y, w, h)` of `Virtual-TabletDisplay`.
       * In optional uinput stylus mode, KDE uinput devices are sized to the virtual display and mapped to `Virtual-TabletDisplay` through KWin.
    2. **Hyprland**: Parses `hyprctl monitors -j` to query coordinates and DPI scaling of the `HEADLESS-N` output.
    3. **GNOME**: Queries the logical display matrix using the Mutter `DisplayConfig` D-Bus interface.
       * In optional uinput stylus mode, GNOME uinput devices are sized to full logical desktop bounds and events are offset into the Mutter virtual monitor.
  * Coordinate Transformation:
    $$\text{Host X} = \text{Offset X} + \left(\frac{\text{Android X}}{65535}\right) \times \text{Logical Width}$$
    $$\text{Host Y} = \text{Offset Y} + \left(\frac{\text{Android Y}}{65535}\right) \times \text{Logical Height}$$

---

## 🛠️ Troubleshooting Guide

### A. uinput Touch/Stylus Injection Fails (Permission Issues)
* **Symptom**: `touch_daemon.py` reports `PermissionError: Cannot open /dev/uinput`.
* **Fix**: Ensure udev permission rules are loaded and your user belongs to the `input` group:
  ```bash
  echo 'KERNEL=="uinput", MODE="0660", GROUP="input"' | sudo tee /etc/udev/rules.d/99-uinput.rules
  sudo udevadm control --reload-rules && sudo udevadm trigger
  sudo usermod -aG input $USER
  # Log out and log back in for changes to apply
  ```

### B. Missing GStreamer Plugins (Black Screens/Codec Crashes)
* **Symptom**: GStreamer launcher fails with `no element "x264enc"` or `no element "vah264enc"`.
* **Fix**: Install the missing plugins on your host distribution:
  * **Fedora**: Enable RPM Fusion and install `gstreamer1-plugins-ugly`, `gstreamer1-plugins-bad-freeworld`, and `gstreamer1-plugin-libav`.
  * **Arch**: Install `gst-plugins-good`, `gst-plugins-bad`, and `gst-plugins-ugly`.
  * **Debian/Ubuntu**: Install `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-bad`, and `gstreamer1.0-plugins-ugly`.

### C. Touch Emulation Fails on KDE/GNOME (Portal Denied)
* **Symptom**: Log reports `Portal session closed/denied by user` or `Portal timed out`.
* **Fix**: KDE/GNOME default input uses the XDG RemoteDesktop portal. If the system shows an input-emulation permission prompt, approve it. If no prompt appears, verify that `xdg-desktop-portal-gtk` or a matching desktop portal is running in your user session.

### D. Wi-Fi Device Discovery Fails (mDNS / Multicast Blocking)
* **Symptom**: Desktop app does not show up on the Android app, or manual IP connection fails.
* **Fix**: Some home network routers block multicast DNS (mDNS) traffic. The Android client will perform a sequential parallel subnet scan on ports `7110` / `1714` to bypass this, but you should also verify that firewalls (such as `firewalld` or `ufw`) on the Linux host allow TCP traffic on port `7110` and `7111`.

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

To run the input bridge manually:
```bash
# Default input path for the current desktop environment
./venv/bin/python3 touch_daemon.py 2560 1600

# Optional pressure/tilt stylus path on KDE, GNOME, or Hyprland
./venv/bin/python3 touch_daemon.py 2560 1600 --stylus-features
```
