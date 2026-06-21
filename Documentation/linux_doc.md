# Monitorize Linux Application

The Linux application creates or captures a display, encodes it as a low-latency H.264 stream, sends it to Android, and injects returned touch or stylus input into the desktop session.

KDE Plasma and Hyprland are the primary supported environments. GNOME support is experimental.

## Quick Start

Install the system packages listed in the repository root [README](../README.md), then create the Python environment:

```bash
cd linux
chmod +x install.sh
./install.sh
./venv/bin/python3 monitorize_gui.py
```

The desktop-specific GStreamer plugins, PipeWire portal, ADB, KScreen tools, and `/dev/uinput` permissions must also be available as described in the root README.

## Architecture

```text
PyQt6/QML GUI
    |
    +-- creates or requests a virtual display
    +-- starts a desktop-specific PipeWire capture process
    +-- optionally starts the TLS proxy
    +-- starts the touch daemon
    |
    v
PipeWire -> GStreamer H.264 encoder -> TCP video stream
                                            ^
Android input -> TCP/TLS or UDP -> touch daemon
```

Important components:

| Component | Responsibility |
| --- | --- |
| `monitorize_gui.py` | Application entry point. |
| `gui/streaming_controller.py` | Stream, virtual-display, TLS, input, and process lifecycle. |
| `gui/kde_virtual_monitor.py` | KDE version detection, legacy KRFB compatibility, and safe KScreen mode configuration. |
| `Streamer_kde.py` | KDE ScreenCast portal capture. |
| `portal_streamer.py` | Shared XDG ScreenCast portal session and PipeWire stream handling. |
| `pipeline_builder.py` | CPU, VA-API, or NVENC GStreamer pipeline launch. |
| `touch_daemon.py` and `input_bridge/` | Android input transport, dispatch, geometry, libei, and uinput handling. |
| `gui/discovery_service.py` | Zeroconf discovery and encrypted/plain capability advertisement. |

## Streaming Modes

### USB

The GUI configures these ADB reverse mappings:

```text
Android 127.0.0.1:7110 -> Linux 127.0.0.1:7112  video
Android 127.0.0.1:7111 -> Linux 127.0.0.1:7111  input
```

USB streaming is always plain because traffic remains inside the ADB transport.

### Wi-Fi

The Linux video service is advertised as `_monitorize._tcp.local.` through Zeroconf. The advertisement includes:

- whether encryption is enabled;
- the TLS certificate fingerprint when encryption is enabled;
- third-display availability and port information.

The desktop application's **Use encryption** setting is authoritative. Android no longer has a separate encryption switch.

## Wi-Fi Encryption and Pairing

### Encrypted desktop stream

When **Use encryption** is enabled:

1. The GStreamer video server binds to the local backend port.
2. The TLS proxy exposes the public Wi-Fi video port.
3. Zeroconf advertises `encrypted=1` and the certificate fingerprint.
4. Android detects that metadata and opens a TLS connection.
5. On the first connection, Android asks for the six-digit PIN shown by the Linux application.
6. After successful pairing, Android stores the pinned fingerprint and authentication token.
7. Later connections reuse those credentials and normally do not ask for the PIN again.

If the token is rejected or the fingerprint changes, Android clears the stale credentials and asks for pairing again.

Encrypted input starts only after video authentication succeeds, using the same trusted fingerprint and token.

### Plain desktop stream

When **Use encryption** is disabled:

- Zeroconf advertises `encrypted=0`;
- Android opens the normal TCP video connection;
- no pairing dialog is shown;
- Wi-Fi input uses the existing UDP input path.

### Manual Android IP connection

A host entered manually on Android is always treated as plain and never requests a PIN. Manual IP entry has no Zeroconf metadata, so it cannot safely determine whether the server expects TLS.

To connect to an encrypted desktop stream, select the discovered desktop entry instead of typing its IP address.

USB connections are also always plain.

## KDE Plasma Virtual Displays

Monitorize uses different Extend-mode paths before and after KDE Plasma 6.7 because the old `krfb-virtualmonitor` path is not reliable on KDE 6.7.

Mirror mode continues to use the normal portal source picker and does not create a virtual display.

### KDE Plasma 6.7 and newer

KDE 6.7+ uses the XDG ScreenCast portal's virtual-source type (`AvailableSourceTypes` bit `4`):

1. Monitorize snapshots the names of all connected and enabled KScreen outputs.
2. It opens the KDE ScreenCast portal with source type `4`.
3. In the KDE dialog, choose **Create virtual screen**.
4. After KDE creates the PipeWire stream, Monitorize searches for exactly one new connected and enabled `Virtual-*` output.
5. It rejects primary, priority-1, physical, existing, or ambiguous outputs.
6. It registers the requested resolution and refresh rate if KDE did not create that mode.
7. It activates the mode by KScreen mode ID and verifies that the requested mode became active.
8. Only after display preparation succeeds does the GStreamer pipeline and input path continue.

The custom mode registration uses:

```text
kscreen-doctor output.<VirtualName>.addCustomMode.<WIDTH>.<HEIGHT>.<FPS*1000>.full
```

The refresh value passed to `addCustomMode` is millihertz. For example, 60 Hz is `60000`.

The active mode is selected using the mode ID returned by `kscreen-doctor -j`. Selecting by mode ID avoids format differences in KDE's generated mode names.

The KDE 6.7+ path deliberately does **not** issue any scale or rotation command. This prevents Monitorize from accidentally resetting the primary monitor's fractional scale. Existing primary-display scale, virtual-display scale, rotation, and arrangement remain KWin/KScreen-owned.

If Monitorize cannot identify exactly one safe new virtual output, it does not modify any display. In particular, it must never apply a mode or scale command to outputs such as `eDP-1`, `DP-*`, or `HDMI-*`.

### KDE Plasma older than 6.7

KDE versions below 6.7 retain the legacy KRFB compatibility path:

1. Monitorize starts `krfb-virtualmonitor` with the stable name `monitorize`.
2. It waits for `Virtual-monitorize` to become visible.
3. It registers the requested custom mode.
4. It selects `<WIDTH>x<HEIGHT>@<FPS>`.
5. It applies scale `1.0` only to `Virtual-monitorize`.
6. The ScreenCast portal then captures that virtual display.

The legacy path keeps the KRFB process alive for the stream lifetime and stops only the tracked process during cleanup.

Some distributions install `org.kde.krfb.virtualmonitor.desktop` while KRFB registers as `org.kde.krfb-virtualmonitor`. Monitorize creates a user-local compatibility desktop entry when required and refreshes KDE's service cache.

### KDE path selection and overrides

Monitorize detects the KDE version using `kwin_wayland --version`, then falls back to `plasmashell --version`.

| Condition | Selected path |
| --- | --- |
| KDE `>= 6.7.0` | Portal-created virtual screen |
| KDE `< 6.7.0` | Legacy `krfb-virtualmonitor` |
| Version unknown and portal supports virtual sources | Portal-created virtual screen |
| Version unknown without virtual-source support | Legacy KRFB |

Development overrides:

| Variable | Effect |
| --- | --- |
| `MONITORIZE_KDE_FORCE_PORTAL=1` | Forces the portal-created virtual-screen path. |
| `MONITORIZE_KDE_USE_KRFB=1` | Forces legacy KRFB and logs a warning on KDE 6.7+. |

Forced KRFB on KDE 6.7+ may fail with messages such as:

```text
Failed to register with host portal
interface 'zkde_screencast_stream_unstable_v1' has no event 3
The Wayland connection experienced a fatal error
```

These errors indicate the incompatible legacy path; use the default portal flow instead.

## Other Desktop Environments

### Hyprland

- Extend mode creates a `HEADLESS-*` output with `hyprctl`.
- Monitorize configures its resolution, refresh rate, position, and scale.
- The output is removed when streaming stops.
- Input uses `/dev/uinput`.

### Sway

- Extend mode creates and configures a headless output.
- Mirror mode captures an existing output.
- Output geometry is passed to the input bridge for coordinate mapping.

### GNOME

- Extend mode uses Mutter's virtual-monitor ScreenCast support.
- Mirror mode captures an existing monitor.
- GNOME support remains experimental.

## GStreamer Pipeline

The GUI supports:

| Preference | Encoder |
| --- | --- |
| Software | `x264enc` |
| Intel/AMD VA-API | Available VA-API H.264 encoder |
| NVIDIA | `nvh264enc` |

Pipelines use PipeWire capture, a low-latency leaky queue, H.264 byte-stream output, and an unsynchronized TCP sink. If a preferred hardware encoder fails immediately, the launcher can fall back to the CPU encoder.

Useful environment variables:

| Variable | Purpose |
| --- | --- |
| `MONITORIZE_ENCODER` | Selects `cpu`, `vaapi`, or `nvidia`. |
| `MONITORIZE_STREAM_TYPE` | Selects the speed or stability stream profile. |
| `MONITORIZE_PRESERVE_SOURCE_SIZE` | Allows supported Extend sessions to renegotiate after display rotation. |
| `MONITORIZE_PORTAL_SOURCE_TYPE` | Internal KDE portal source type; KDE 6.7+ Extend uses `4`. |

## Input

Android sends normalized coordinates in the range `0..65535`. Linux maps them to current display geometry from:

- `kscreen-doctor -j` on KDE;
- `hyprctl monitors -j` on Hyprland;
- compositor-specific state on Sway or GNOME.

Backend selection:

- KDE/GNOME default: libei through the XDG RemoteDesktop portal;
- KDE/GNOME with stylus features: `/dev/uinput`;
- Hyprland and Sway: `/dev/uinput`.

The input bridge releases active contacts when a client disconnects or UDP traffic becomes idle, preventing stuck touches after reconnects.

## Configuration

Settings are stored at:

```text
~/.config/monitorize/settings.ini
```

Wi-Fi settings include resolution, FPS, bitrate, display type, encoder, stream profile, and encryption choice.

## Testing

Run the Linux controller, TLS, input, and compositor-support tests from `linux/`:

```bash
python3 -m unittest \
  test_gui_controllers.py \
  test_tls_proxy.py \
  test_input_bridge.py \
  test_sway_support.py
```

Check relevant Python syntax:

```bash
python3 -m py_compile \
  Streamer_kde.py \
  portal_streamer.py \
  gui/streaming_controller.py \
  gui/kde_virtual_monitor.py
```

Manual KDE 6.7+ validation:

1. Set a non-100% scale on the primary display.
2. Start an Extend stream and choose **Create virtual screen**.
3. Confirm the virtual output uses the resolution and FPS selected in Monitorize.
4. Confirm the primary output retains its original scale.
5. Stop streaming and confirm only the Monitorize virtual output disappears.

Manual encryption validation:

1. Enable desktop encryption and select the discovered desktop on Android; first connection must request the Linux PIN.
2. Reconnect; saved credentials should avoid another PIN prompt.
3. Disable desktop encryption; the discovered entry should connect without a PIN.
4. Enter the desktop IP manually; Android should use a plain connection.

## Troubleshooting

### KDE 6.7 virtual screen remains at its default resolution

- Confirm **Create virtual screen** was selected in the portal dialog.
- Check that `kscreen-doctor -j` reports exactly one new `Virtual-*` output.
- Inspect logs for custom-mode registration, mode activation, or verification errors.
- Confirm `kscreen-doctor` is installed and available in `PATH`.

### Primary KDE display scale changes

The KDE 6.7+ implementation must not issue `.scale.*` commands. It should configure only the uniquely detected new `Virtual-*` output and only its mode.

Inspect logs and generated commands. No command should target the primary or a physical output such as `eDP-1`.

### Android does not show a PIN

- Confirm **Use encryption** is enabled in the Linux Wi-Fi settings.
- Refresh Android discovery and select the discovered desktop entry.
- Do not use manual IP entry for encrypted connections; manual entries are intentionally plain.

### Android repeatedly asks for a PIN

- Confirm the six-digit code is current.
- Check whether the Linux TLS certificate or saved application data changed.
- A fingerprint change intentionally invalidates Android's saved credentials.

### Android cannot discover the host

- Confirm both devices are on the same network.
- Allow mDNS and TCP port `7110` through the firewall.
- Manual IP entry can be used only for a plain stream.

### USB connection is unavailable

- Confirm `adb devices` lists an authorized device.
- Run the GUI's USB setup again.
- Verify reverse mappings with `adb reverse --list`.
