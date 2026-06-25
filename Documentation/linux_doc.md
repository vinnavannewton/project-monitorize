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
| `gui/tray_agent.py` | Lightweight login tray that opens the full GUI only when needed. |
| `gui/streaming_controller.py` | Stream, virtual-display, TLS, input, and process lifecycle. |
| `gui/kde_virtual_monitor.py` | KDE portal virtual-output detection and safe KScreen mode configuration. |
| `Streamer_kde.py` | KDE ScreenCast portal capture. |
| `portal_streamer.py` | Shared XDG ScreenCast portal session and PipeWire stream handling. |
| `pipeline_builder.py` | CPU, VA-API, or NVENC GStreamer pipeline launch. |
| `touch_daemon.py` and `input_bridge/` | Android input transport, dispatch, geometry, and uinput handling. |
| `gui/discovery_service.py` | Zeroconf discovery and encrypted/plain capability advertisement. |

## Startup and Tray Behavior

The **Start Monitorize after login** setting writes an XDG autostart entry that launches `monitorize_gui.py --tray-agent`.

The tray agent keeps only a small Qt tray process alive at login. It shows **Show**, **Presets**, and **Quit**. Opening the app or launching a preset starts the normal GUI process on demand. Existing autostart entries that still pass bare `--start-in-tray` are also routed to the tray agent for compatibility.

When the full GUI is closed with **Minimize to tray on close** enabled, idle sessions return to the lightweight tray agent. Active streaming or receiving sessions keep the full GUI process alive so it can manage the stream and input processes.

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

## Saved Presets

The main page can show up to four saved stream presets. If none exist, it displays **No saved presets** without an add button.

To create a preset:

1. Start a Wi-Fi or USB stream with the required configuration.
2. Optionally start the additional KDE display.
3. Select **Save Preset** on the streaming page.
4. Enter a name.

A preset stores:

- Wi-Fi or USB mode;
- resolution, FPS, bitrate, display type, and encoder;
- Wi-Fi stream profile and encryption state;
- touch, stylus, and minimize-to-tray settings;
- the active additional KDE display and its stream configuration.

Preset cards on the main page launch their saved configuration when clicked. USB presets run the normal ADB readiness scan before starting. A saved additional display starts after the primary stream is ready and is skipped with a log message when the current desktop is not KDE.

Use a preset card's menu to rename or delete it. When four presets already exist, saving another requires replacing one of them. Preset launches do not overwrite the normal Wi-Fi, USB, or general defaults.

## KDE Plasma Virtual Displays

Monitorize supports KDE Plasma 6.7+ for Extend mode. KDE uses the XDG ScreenCast portal's virtual-source type (`AvailableSourceTypes` bit `4`) to create the virtual display.

Mirror mode continues to use the normal portal source picker and does not create a virtual display.

### KDE Extend Flow

1. Monitorize snapshots the names of all connected and enabled KScreen outputs.
2. It opens the KDE ScreenCast portal with source type `4`.
3. In the KDE dialog, choose **Create virtual screen**.
4. After KDE creates the PipeWire stream, Monitorize searches for exactly one new connected and enabled `Virtual-*` output.
5. It rejects primary, priority-1, physical, existing, or ambiguous outputs.
6. It registers the requested resolution and refresh rate if KDE did not create that mode.
7. It activates the mode by KScreen mode ID and verifies that the requested mode became active.
8. It restores the saved position and rotation for that virtual display slot.
9. Only after display preparation succeeds does the GStreamer pipeline and input path continue.

The custom mode registration uses:

```text
kscreen-doctor output.<VirtualName>.addCustomMode.<WIDTH>.<HEIGHT>.<FPS*1000>.full
```

The refresh value passed to `addCustomMode` is millihertz. For example, 60 Hz is `60000`.

The active mode is selected using the mode ID returned by `kscreen-doctor -j`. Selecting by mode ID avoids format differences in KDE's generated mode names.

Monitorize stores KDE virtual-display layout per slot:

- `primary`: the main Extend virtual display;
- `third`: the optional additional KDE display.

For each slot, Monitorize saves `x,y` position and KScreen rotation before the portal virtual output disappears. On the next start, it restores them with:

```text
kscreen-doctor output.<VirtualName>.position.<X>,<Y>
kscreen-doctor output.<VirtualName>.rotation.<ROTATION>
```

If no saved position exists, the primary virtual display is placed to the right of the primary physical output. The third virtual display is placed to the right of the rightmost enabled output. Rotation is restored only when saved; otherwise KDE's current/default rotation is kept.

The KDE 6.7+ path deliberately does **not** issue any scale command. This prevents Monitorize from accidentally resetting the primary monitor's fractional scale. Existing primary-display scale remains KWin/KScreen-owned.

If Monitorize cannot identify exactly one safe new virtual output, it does not modify any display. In particular, it must never apply a mode, position, rotation, or scale command to outputs such as `eDP-1`, `DP-*`, or `HDMI-*`.

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
| `MONITORIZE_PORTAL_SOURCE_TYPE` | Internal KDE portal source type; KDE Extend uses `4`. |
| `MONITORIZE_VIRTUAL_SLOT` | Internal KDE layout slot; `primary` or `third`. |

## Input

Android sends normalized coordinates in the range `0..65535`. Linux maps them to current display geometry from:

- `kscreen-doctor -j` on KDE;
- `hyprctl monitors -j` on Hyprland;
- compositor-specific state on Sway or GNOME.

All supported Linux desktops use `/dev/uinput` for touch and stylus input. KDE, Hyprland, and Sway bind the virtual input device to the streamed output when the compositor exposes the needed mapping controls. GNOME remains experimental and uses desktop bounds from the compositor.

The input bridge releases active contacts when a client disconnects or UDP traffic becomes idle, preventing stuck touches after reconnects.

## Configuration

Settings are stored at:

```text
~/.config/monitorize/settings.ini
```

Wi-Fi settings include resolution, FPS, bitrate, display type, encoder, stream profile, and encryption choice.

Saved presets are stored in the same settings file. Pairing codes, authentication tokens, IP addresses, and runtime process state are not included in presets.

KDE virtual-display layout is stored per slot in the same settings file. The saved values include position and rotation only; scale is intentionally not stored or restored.

### Persistent application logs

The desktop application writes streamer, input, TLS, receiver, application-lifecycle, and uncaught Python exception messages to:

```text
~/.local/state/monitorize/monitorize.log
```

The file is written continuously, so recent output remains available after the application exits or crashes. It is created with user-only permissions and rotates at 2 MiB, retaining three backups:

```text
monitorize.log
monitorize.log.1
monitorize.log.2
monitorize.log.3
```

The on-screen log viewers remain selectable but do not display an editing context menu when right-clicked.

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
4. Move or rotate the virtual output in KDE display settings.
5. Stop streaming, start Extend again, and confirm position and rotation are restored.
6. If using the additional KDE display, move or rotate it separately and confirm it restores independently from the primary virtual display.
7. Confirm the primary output retains its original scale.
8. Stop streaming and confirm only the Monitorize virtual output disappears.

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

The KDE 6.7+ implementation must not issue `.scale.*` commands. It should configure only the uniquely detected new `Virtual-*` output and only its mode, position, and rotation.

Inspect logs and generated commands. No command should target the primary or a physical output such as `eDP-1`.

### KDE virtual display position or rotation is not restored

- Move or rotate the virtual display once while it exists, then stop streaming cleanly so Monitorize can save the layout before KDE removes the portal output.
- Confirm logs show a `Virtual-*` output name for the primary or third display.
- Check that `kscreen-doctor -j` reports `pos` and `rotation` for the virtual output while streaming is active.
- If the physical monitor layout changed, the saved absolute coordinates may no longer be useful; move the virtual display again and stop streaming to save the new layout.

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

### Application closes or crashes unexpectedly

Inspect the persistent log and its rotated backups:

```bash
tail -n 200 ~/.local/state/monitorize/monitorize.log
```

Look for `[APP]`, `[STREAMER]`, `[INPUT]`, `[TLS]`, or `[RECEIVER]` entries near the end of the file.
