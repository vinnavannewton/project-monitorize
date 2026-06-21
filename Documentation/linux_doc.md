different steps and workings for kde version < 6.7 and kde>=6.7

normal discovery in android connect then desktop option encrypt or no encrypt is applied but if connected via typing ip address then its always gonna be unencrypted







# Monitorize Linux Application

The Linux application creates or captures a display, encodes it as a low-latency H.264 stream, sends it to an Android device, and injects returned touch or stylus input into the desktop session.

KDE Plasma and Hyprland are the primary supported environments. GNOME support is experimental.

## Quick Start

Install the system packages listed in the repository root [README](../README.md), then create the Python environment:

```bash
cd linux
chmod +x install.sh
./install.sh
./venv/bin/python3 monitorize_gui.py
```

`install.sh` installs the Python dependencies from `requirements.txt`:

- PyQt6
- zeroconf
- snegg
- evdev

The desktop-specific system dependencies, GStreamer plugins, PipeWire, ADB, and `/dev/uinput` permissions must be installed separately as described in the root README.

## Architecture

```text
PyQt6/QML GUI
    |
    +-- creates/configures a virtual display
    +-- starts a desktop-specific capture wrapper
    +-- starts the touch daemon
    |
    v
PipeWire source -> GStreamer H.264 encoder -> TCP video stream
                                              ^
Android input -> TCP or UDP -> touch_daemon.py
```

### Main Components

| Component | Responsibility |
| --- | --- |
| `monitorize_gui.py` | Application entry point. |
| `gui/main_window.py` | QML backend, process lifecycle, ADB setup, host discovery, receiver mode, and stream orchestration. |
| `gui/*.qml` | User interface for USB, Wi-Fi, streaming, receiver, and settings workflows. |
| `Streamer_kde.py` | KDE ScreenCast portal capture. The GUI creates extended displays with `krfb-virtualmonitor`. |
| `Streamer_hyprland.py` | Hyprland headless-output creation and ScreenCast portal capture. |
| `Streamer_gnome.py` | Experimental Mutter ScreenCast integration for virtual or mirrored displays. |
| `pipeline_builder.py` | Builds and launches CPU, VA-API, or NVENC GStreamer pipelines. |
| `touch_daemon.py` | Receives normalized Android input and injects it through libei or `/dev/uinput`. |
| `gui/settings.py` | Persists application settings in an INI file. |

## Streaming Modes

### USB

The GUI configures these ADB reverse mappings:

```text
Android 127.0.0.1:7110 -> Linux 127.0.0.1:7112  video
Android 127.0.0.1:7111 -> Linux 127.0.0.1:7111  input
```

The Linux video pipeline listens on `127.0.0.1:7112`. The touch daemon listens on `127.0.0.1:7111`.

### Wi-Fi

The Linux video pipeline listens on `0.0.0.0:7110` and advertises `_monitorize._tcp.local.` through Zeroconf. The touch daemon listens for UDP input on `0.0.0.0:7113`.

### Receiver Mode

The Linux application can also receive another Monitorize H.264 stream. It discovers hosts through Zeroconf or accepts a manual address, then launches:

```text
tcpclientsrc -> h264parse -> avdec_h264 -> videoconvert -> autovideosink
```

## Desktop Environment Behavior

### KDE Plasma

- Extend mode starts `krfb-virtualmonitor` with an output named `TabletDisplay`.
- The ScreenCast portal asks the user to select the source.
- Mirror mode skips virtual-monitor creation and captures a source selected through the portal.
- KDE can create a third-display stream named `TabletDisplay2` on port `7114`.
- Default touch injection uses libei through the XDG RemoteDesktop portal.
- Stylus-feature mode uses `/dev/uinput` and maps the generated devices to `Virtual-TabletDisplay`.

### Hyprland

- Extend mode creates a `HEADLESS-*` output using `hyprctl output create headless`.
- The GUI configures its resolution, refresh rate, position, and scale.
- The ScreenCast portal captures the selected headless output.
- The created output is removed when streaming stops.
- Input always uses `/dev/uinput` because the Hyprland portal does not provide the required RemoteDesktop input path.

### GNOME

- Extend mode uses Mutter's `RecordVirtual` API.
- Mirror mode uses `RecordMonitor` for the primary connector.
- The streamer is restarted if a display reconfiguration terminates the GNOME capture process.
- Default touch uses libei; optional stylus features use `/dev/uinput`.

GNOME is experimental and does not use the KDE/Hyprland live source-size rotation path.

## GStreamer Pipeline

`pipeline_builder.py` selects an encoder from the explicit GUI preference:

| Preference | Encoder |
| --- | --- |
| Software | `x264enc` |
| Intel/AMD VA-API | First available of `vah264enc`, `vah264lpenc`, or `vaapih264enc` |
| NVIDIA | `nvh264enc` |

All pipelines use:

- `pipewiresrc` with timestamps and keepalive enabled
- A one-buffer leaky queue to favor recent frames
- H.264 byte-stream output aligned to access units
- `tcpserversink` with synchronization disabled and DSCP value 48

The environment passed to streamer processes controls internal pipeline behavior:

| Variable | Values | Purpose |
| --- | --- | --- |
| `MONITORIZE_ENCODER` | `cpu`, `vaapi`, `nvidia` | Chooses the requested encoder path. |
| `MONITORIZE_STREAM_TYPE` | `Speed`, `Stability` | Controls keyframe interval and intra-refresh behavior. |
| `MONITORIZE_PRESERVE_SOURCE_SIZE` | `1` or unset | Preserves PipeWire dimensions for live rotation. |

### Live Portrait Rotation

KDE and Hyprland Extend sessions set `MONITORIZE_PRESERVE_SOURCE_SIZE=1`. In this mode:

1. The virtual display changes from landscape to portrait or back.
2. PipeWire publishes the new frame dimensions.
3. Fixed width and height caps are omitted from the conversion stage.
4. The encoder renegotiates its H.264 output without intentionally closing the TCP stream.
5. The Android decoder reports the new output size and resizes its video surface.

Mirror mode and GNOME retain fixed configured dimensions. Runtime rotation also depends on the selected encoder supporting caps renegotiation.

## Input Protocol and Injection

Android sends normalized coordinates in the range `0..65535`. The daemon maps them to the current virtual monitor geometry discovered from:

- `kscreen-doctor -j` on KDE
- `hyprctl monitors -j` on Hyprland
- Mutter DisplayConfig D-Bus state on GNOME

Input transport:

| Mode | Transport | Linux endpoint |
| --- | --- | --- |
| USB | TCP | `127.0.0.1:7111` |
| Wi-Fi | UDP | `0.0.0.0:7113` |

Packet types:

| Type | Meaning |
| --- | --- |
| `0x03` | Touch packet |
| `0x04` | Legacy pen packet accepted for compatibility |
| `0x05` | Extended stylus/eraser packet with pressure, tilt, distance, buttons, and flags |

Backend selection:

- KDE/GNOME default: libei through `snegg` and the XDG RemoteDesktop portal
- KDE/GNOME with stylus features: `/dev/uinput`
- Hyprland: `/dev/uinput`

When stylus features are enabled while touch is disabled, the GUI starts the daemon with `--stylus-only`.

## Configuration

Settings are stored at:

```text
~/.config/monitorize/settings.ini
```

Groups:

- `usb`: resolution, FPS, bitrate, display type, and encoder
- `wifi`: USB fields plus stream type
- `general`: minimize-to-tray, touch enabled, and stylus features enabled
- `second_display`: KDE second-display settings
- `receiver`: manual receiver IP and port

## Standalone Commands

The GUI is the supported orchestration path. These commands are useful for debugging:

```bash
# Hyprland streamer
./venv/bin/python3 Streamer_hyprland.py 2560 1600 60 8000 usb

# Touch over USB using the desktop-default backend
./venv/bin/python3 touch_daemon.py 2560 1600

# Touch over Wi-Fi with stylus support
./venv/bin/python3 touch_daemon.py 2560 1600 --wifi --stylus-features
```

Streamer argument order:

```text
Streamer_*.py width height fps bitrate usb|wifi [desktop-specific arguments]
```

## Testing

Run the shared pipeline unit test from the repository root:

```bash
python3 -m unittest test_pipeline_builder.py
```

Check Python syntax:

```bash
python3 -m py_compile linux/pipeline_builder.py linux/gui/main_window.py
```

Hardware-dependent behavior requires manual testing:

- KDE and Hyprland Extend and Mirror modes
- USB and Wi-Fi streaming
- CPU, VA-API, and NVENC encoders
- landscape-to-portrait rotation
- touch corners, multitouch, hover, pressure, tilt, and eraser input

## Troubleshooting

### `/dev/uinput` permission denied

Create an input-group udev rule, add the user to the group, then log out and back in:

```bash
echo 'KERNEL=="uinput", MODE="0660", GROUP="input"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
sudo usermod -aG input "$USER"
```

### Missing GStreamer element

Use `gst-inspect-1.0 <element>` to confirm the selected encoder and required parser, conversion, PipeWire, and TCP elements are installed. Distribution-specific package commands are in the root README.

### KDE or Hyprland portrait stream remains cropped

- Confirm the stream uses Extend mode.
- Inspect the generated pipeline in the GUI log.
- The conversion caps must not contain the configured fixed width and height.
- Retry with the Software encoder if the selected hardware encoder cannot renegotiate dimensions.

### Android cannot discover the host

- Confirm both devices are on the same network.
- Allow mDNS and TCP port `7110` through the firewall.
- Use the Android manual connection field if multicast discovery is unavailable.

### USB connection is unavailable

- Confirm `adb devices` lists an authorized device.
- Run the GUI's USB setup again.
- Verify both reverse mappings with `adb reverse --list`.
