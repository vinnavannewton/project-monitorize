# Monitorize Android Application

The Android application receives a raw H.264 stream from the Monitorize Linux host, decodes it directly to a `SurfaceView`, and sends normalized touch or stylus input back to Linux.

The UI is written with Jetpack Compose. Video decoding uses Android `MediaCodec`.

## Build

Requirements:

- Android SDK matching compile SDK 36
- Android 9 or newer (`minSdk 28`)
- JDK 17 or 21

Java 25 is not compatible with the current Gradle/Kotlin toolchain.

```bash
cd android
JAVA_HOME=/path/to/jdk-21 ./gradlew assembleDebug
```

Install and launch on a connected device:

```bash
./gradlew installDebug
adb shell am start -n com.example.monitorize/.MainActivity
```

## Architecture

```text
DeviceDiscovery
      |
      v
MainActivity / Compose UI
      |
      +-- StreamReceiver -> H264Decoder -> SurfaceView
      |
      +-- MotionEvent -> InputEventSender -> Linux touch daemon
```

### Main Components

| Component | Responsibility |
| --- | --- |
| `MainActivity.kt` | Application state, Compose screens, stream lifecycle, immersive mode, settings, and SurfaceView integration. |
| `DeviceDiscovery.kt` | Android NSD discovery and the local USB fallback entry. |
| `StreamReceiver.kt` | TCP connection, Annex B parsing, access-unit assembly, and decoder delivery. |
| `H264Decoder.kt` | Asynchronous MediaCodec configuration, frame queueing, rendering, and output-size reporting. |
| `InputEventSender.kt` | MotionEvent normalization, input packet serialization, and TCP/UDP transport. |
| `ui/theme/*` | Compose theme definitions. |

## Connection Modes

### USB

The Linux GUI creates ADB reverse mappings before streaming:

```text
Android 127.0.0.1:7110 -> Linux 127.0.0.1:7112  video
Android 127.0.0.1:7111 -> Linux 127.0.0.1:7111  input
```

The Android app always lists a USB fallback device at `127.0.0.1:7110`. If Linux sets the `debug.monitorize.pc_name` property, that value is used in the displayed USB device name.

### Wi-Fi

`DeviceDiscovery` uses Android `NsdManager` to browse `_monitorize._tcp.` services. A multicast lock remains active during discovery, and services are resolved sequentially to avoid overlapping Android NSD resolution calls.

The current implementation does not perform subnet scanning. If mDNS is unavailable, use the manual IP and port fields.

Wi-Fi endpoints:

| Purpose | Endpoint |
| --- | --- |
| Video | Linux host TCP port `7110` by default |
| Input | Linux host UDP port `video port + 3`, normally `7113` |

## Video Receive Pipeline

### TCP Receiver

`StreamReceiver`:

- Connects with a two-second timeout and retries once per second.
- Enables `TCP_NODELAY`.
- Requests a 1 MiB receive buffer.
- Attempts to set traffic class `0xC0`.
- Runs on a thread using `THREAD_PRIORITY_URGENT_DISPLAY`.
- Reads into a 4 MiB stream buffer using 128 KiB socket reads.

The incoming format is raw H.264 Annex B byte stream. The parser recognizes three-byte and four-byte start codes, groups NAL units into access units, identifies IDR frames, and uses first-slice parsing to find access-unit boundaries.

Access units are limited to 2 MiB. Oversized NAL units are dropped.

### MediaCodec Decoder

`H264Decoder` creates an asynchronous AVC decoder attached directly to the `SurfaceView` surface.

Configuration includes:

- `KEY_MAX_INPUT_SIZE = 2 MiB`
- adaptive maximum width and height based on the larger configured stream dimension
- `KEY_OPERATING_RATE = Short.MAX_VALUE`
- `KEY_PRIORITY = 0`
- `KEY_LOW_LATENCY = 1`
- constrained-baseline profile request where supported

The decoder uses:

- a dedicated `MonitorizeDecoder` `HandlerThread`
- three reusable 2 MiB frame buffers
- a three-item frame queue
- pending MediaCodec input-buffer tracking
- non-key-frame dropping under backpressure
- queue draining to preserve a newly received keyframe

## Live Rotation

The manifest uses `fullSensor` orientation and handles orientation and screen-size configuration changes without recreating the activity.

For KDE and Hyprland Extend streams:

1. Linux changes the encoded dimensions when the virtual display rotates.
2. MediaCodec reports the new output format.
3. `H264Decoder` calculates the visible width and height from crop values when present.
4. `MainActivity` updates the Compose aspect ratio.
5. The `SurfaceView` resizes while the active stream remains connected.

The adaptive decoder envelope uses a square maximum based on the configured larger dimension. For example, a configured `1280x800` stream can renegotiate to `800x1280`.

Physical tablet rotation and stream rotation are separate events: the virtual display must be portrait, and the tablet must be physically rotated for a portrait stream to fill a portrait device.

## Input Transport

`StreamSurface` forwards touch and hover `MotionEvent` objects to `InputEventSender`.

Coordinates are normalized against the current view dimensions:

```text
normalized_x = x / view_width  * 65535
normalized_y = y / view_height * 65535
```

This keeps touch aligned when the video surface changes between landscape and portrait.

Transport:

| Mode | Transport | Destination |
| --- | --- | --- |
| USB | TCP | `127.0.0.1:(video port + 1)`, normally `7111` |
| Wi-Fi | UDP | Linux host `(video port + 3)`, normally `7113` |

The sender uses a 256-item coroutine channel with `DROP_OLDEST` overflow behavior.

## Input Packet Format

Every frame starts with a four-byte big-endian payload length followed by a one-byte packet type.

### Touch packet

Packet type `0x03`, 13-byte payload, 18-byte total frame:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | Payload length: `13` |
| 4 | 1 | Packet type: `0x03` |
| 5 | 1 | Action |
| 6 | 1 | Tool type |
| 7 | 1 | Contact ID |
| 8 | 2 | Normalized X |
| 10 | 2 | Normalized Y |
| 12 | 2 | Pressure |
| 14 | 2 | Reserved tilt field, currently zero |
| 16 | 2 | Android button-state bitmask |

### Extended pen packet

Packet type `0x05`, 19-byte payload, 24-byte total frame:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | Payload length: `19` |
| 4 | 1 | Packet type: `0x05` |
| 5 | 1 | Action |
| 6 | 1 | Tool: `1` stylus, `2` eraser |
| 7 | 1 | Contact ID |
| 8 | 2 | Normalized X |
| 10 | 2 | Normalized Y |
| 12 | 2 | Pressure |
| 14 | 2 | X tilt in degrees |
| 16 | 2 | Y tilt in degrees |
| 18 | 2 | Normalized hover distance, `0..1024` |
| 20 | 2 | Android button-state bitmask |
| 22 | 2 | Cancel and hover flags |

Actions:

| Value | Meaning |
| --- | --- |
| `0` | Down |
| `1` | Move |
| `2` | Up or cancel |
| `3` | Hover |

Pen flags:

| Bit | Meaning |
| --- | --- |
| `0` | Canceled |
| `1` | Hover exit |
| `2` | Hover enter |

`ACTION_MOVE` sends all active pointers. `ACTION_CANCEL` sends an up/cancel packet for each active pointer.

## UI and Lifecycle

`MainActivity` provides:

- a device-selection home screen
- stream resolution settings
- a receive screen containing the SurfaceView
- immersive system-bar hiding
- display-cutout support
- screen-on behavior during use
- a low-latency or high-performance Wi-Fi lock during streaming

Resolution settings are stored in `monitorize_prefs` and default to `1280x800`. Native, 0.75x, 0.5x, and custom profiles are available.

Changing the saved resolution relaunches `MainActivity` with `FLAG_ACTIVITY_NEW_TASK | FLAG_ACTIVITY_CLEAR_TASK`. Stream teardown closes the receiver, decoder, input sender, and Wi-Fi lock.

If a connected stream ends after video has been received, the app reports disconnection and returns through the same clean activity relaunch path.

## Permissions and Manifest Behavior

The application requests:

- Internet access
- wake lock
- Wi-Fi and network state
- multicast-state changes

`MainActivity` is configured with:

```xml
android:screenOrientation="fullSensor"
android:configChanges="orientation|screenSize|keyboardHidden"
android:keepScreenOn="true"
```

## Testing

Compile the Android application:

```bash
cd android
JAVA_HOME=/path/to/jdk-21 ./gradlew :app:compileDebugKotlin
```

Manual device tests should cover:

- USB and Wi-Fi discovery and connection
- stream reconnect and disconnect behavior
- landscape-to-portrait-to-landscape changes
- physical tablet rotation
- touch alignment at all four corners
- multitouch, stylus, eraser, pressure, tilt, hover, and buttons
- resolution changes and activity relaunch

## Troubleshooting

### Gradle fails with `IllegalArgumentException: 25.x`

Run Gradle with JDK 17 or 21. The current Kotlin tooling cannot parse Java 25 version strings.

### Wi-Fi host is not discovered

- Confirm Linux advertises `_monitorize._tcp.local.`.
- Confirm Android and Linux are on the same network.
- Check multicast filtering or client isolation on the access point.
- Enter the host IP and port manually.

### USB device connects but no video appears

- Confirm the Linux GUI completed ADB setup.
- Run `adb reverse --list`.
- Verify the Android video endpoint maps `tcp:7110` to Linux `tcp:7112`.
- Verify the Android stream resolution matches the Linux stream configuration before testing rotation.

### Portrait video is cropped or keeps the landscape aspect ratio

- Use KDE or Hyprland Extend mode.
- Confirm Linux is preserving the PipeWire source dimensions.
- Confirm the Android app was rebuilt with adaptive MediaCodec sizing and `fullSensor`.
- Retry with the Linux Software encoder if the hardware encoder does not renegotiate dimensions.

### Touch is offset

- Confirm the video fills the same SurfaceView receiving touch.
- Confirm Linux reports the correct virtual-monitor bounds.
- Test the four corners after every rotation.

