# 📱 Monitorize Android Client Documentation

Welcome to the internal architectural and implementation documentation of the **Monitorize Android App**. 

This client is designed to turn any modern Android device (optimized for tablets, compatible with phones) into a high-performance, low-latency secondary display for Linux systems running KDE or Hyprland. It leverages hardware-accelerated video decoding and a custom low-overhead protocol to pipe touch/stylus inputs back to the host.

---

## 🏗️ Architectural Overview

The application is built in Kotlin and utilizes a modern Android stack:
* **UI Engine**: Jetpack Compose for declarative, smooth UI state transitions.
* **Decoding Pipeline**: `MediaCodec` APIs utilizing hardware-accelerated H.264/AVC decoding.
* **Networking**: Direct TCP socket streaming for video, alongside dual TCP/UDP socket channels for input feedback.
* **Concurrency**: Kotlin Coroutines and Channels for high-performance, non-blocking asynchronous operations.

```
┌────────────────────────────────────────────────────────────────────────┐
│                          MONITORIZE ANDROID APP                        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         ▼                          ▼                          ▼
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ Device Discovery │      │  Stream Receiver │      │   Input Sender   │
│ (mDNS & Subnet)  │      │   (TCP Sockets)  │      │   (UDP & TCP)    │
└──────────────────┘      └─────────┬────────┘      └──────────┬───────┘
                                    │                          │
                                    ▼                          ▼
                          ┌──────────────────┐      ┌──────────────────┐
                          │  H264 Decoder    │      │  OS MotionEvent  │
                          │   (MediaCodec)   │      │    Serializer    │
                          └─────────┬────────┘      └──────────────────┘
                                    │
                                    ▼
                          ┌──────────────────┐
                          │  StreamSurface   │
                          │   (SurfaceView)  │
                          └──────────────────┘
```

---

## 🔍 Subsystem Details

### 1. Device Discovery (`DeviceDiscovery.kt`)

The discovery subsystem is responsible for finding compatible Monitorize desktop servers on the local network or over USB. It implements a multi-tiered discovery pipeline:

#### A. mDNS / Network Service Discovery (NSD)
* Resolves services of type `_monitorize._tcp.` using Android's native `NsdManager`.
* **Multicast Lock**: Acquires `WifiManager.MulticastLock` on startup to permit reception of multicast packets.
* **Sequential Resolution**: To bypass a known Android NSD bug where concurrent service resolutions fail, the resolver queues incoming `NsdServiceInfo` instances into a coroutine `Channel` and processes them sequentially with a `CompletableDeferred` lock.
* **Friendly Name Extraction**: Checks TXT records for keys `"fn"`, `"model"`, or `"name"` to display friendly hostnames rather than plain network names.

#### B. Parallel Subnet Scanning
* Actively handles scenarios where mDNS/multicast is blocked or disabled by local router configurations.
* Resolves the local IP address (with custom fallback for offline Wi-Fi access points).
* Launches concurrent coroutines to scan the `/24` subnet. It checks target ports in parallel:
  * **Port 7110**: Monitorize (Primary)
  * **Port 1714**: KDE Connect
  * **Port 22**: SSH
  * **Port 80**: HTTP
  * **Port 53**: DNS
* If a port is open, the device is immediately listed as a fallback `"WiFi Device"`, while a background task attempts to resolve its network hostname via `InetAddress.getByName()`.

#### C. USB Mode Fallback
* USB connection is assumed via ADB forwarding (`adb reverse tcp:7110 tcp:7110`).
* Registers `127.0.0.1:7110` as a local device under `"Local PC (USB)"`.
* Inspects the custom Android system property `debug.monitorize.pc_name` using `getprop` to fetch the actual PC name when connected via USB.

---

### 2. Stream Receiver (`StreamReceiver.kt`)

The `StreamReceiver` establishes the socket connections, handles low-latency OS socket tuning, and parses incoming streams.

* **Network Tuning**:
  * Disables Nagle's algorithm with `tcpNoDelay = true`.
  * Sets the TCP receive buffer size to `1MB` (`receiveBufferSize = 1024 * 1024`).
  * Applies **Traffic Class / Quality of Service (QoS)**: Configures the socket's traffic class to `0xC0` (DSCP CS6 / Voice Class) to enforce high-priority routing at the Android OS kernel and local Wi-Fi chipset driver levels.
* **Annex B Video Stream Parsing**:
  * Reads raw network bytes into a circular-style `4MB` data buffer.
  * Implements a fast, zero-copy start-code parser (`findStartCode`) searching for H.264 Annex B start sequences (`0x00000001` or `0x000001`).
  * When a complete NAL unit is framed between start codes, it extracts the chunk and immediately pipes it to the hardware decoder.

---

### 3. H.264 Video Decoder (`H264Decoder.kt`)

The decoder wrapper interfaces directly with Android's MediaCodec API to parse H.264 streams and push decoded frames to the display surface.

* **Low-Latency Configurations**:
  * Sets `KEY_LOW_LATENCY = 1` and `KEY_PRIORITY = 0` (Real-Time Priority).
  * Forces `KEY_OPERATING_RATE = Short.MAX_VALUE` to tell the GPU decoder to run at maximum possible frequency.
  * Requests `CodecProfileLevel.AVCProfileConstrainedBaseline` to minimize decoding reference frame delays.
* **Asynchronous Decoding Pipeline**:
  * Uses an asynchronous callback model (`MediaCodec.Callback`) bound to a high-priority background thread `HandlerThread("MonitorizeDecoder")` to guarantee that GC or main UI thread pauses never block frame ingestion.
* **Custom Frame Ring Buffer**:
  * To avoid frequent heap allocations and Garbage Collector (GC) latency spikes, the decoder manages a fixed pool of `FrameChunk` structures (size: `10` buffers, each pre-allocated to `2MB`).
  * If the input queue fills up, buffers are recycled immediately rather than re-allocated.

---

### 4. Input Event Sender (`InputEventSender.kt`)

Input feedback routes user touch, mouse, and stylus interactions from the Android screen back to the Linux host compositor.

* **Dual Protocol Transmitters**:
  * **UDP Mode (Wi-Fi)**: Binds to port `7113` for low-overhead, frame-independent event delivery.
  * **TCP Mode (USB)**: Connects to localhost port `7111` for guaranteed, ordered transmission over the USB tunnel.
* **18-Byte Event Framing Protocol**:
  Each event is packed into an 18-byte structure:

  | Byte Range | Type / Format | Field Description |
  | :--- | :--- | :--- |
  | **0 - 3** | Big-Endian Int | Packet length header (always `0x0000000d` / 13 bytes payload) |
  | **4** | Byte | Packet Type: `0x03` (Touch) \| `0x04` (Stylus/Eraser) |
  | **5** | Byte | Action: `0` (Down) \| `1` (Move) \| `2` (Up/Cancel) \| `3` (Hover) |
  | **6** | Byte | Tool Type: `0` (Finger/Generic) \| `1` (Stylus) \| `2` (Eraser) |
  | **7** | Byte | Contact Pointer ID (mapped modulo 256) |
  | **8 - 9** | Big-Endian Short | Mapped X coordinate (`0` to `65535` relative to view width) |
  | **10 - 11** | Big-Endian Short | Mapped Y coordinate (`0` to `65535` relative to view height) |
  | **12 - 13** | Big-Endian Short | Mapped Pressure value (`0` to `65535`) |
  | **14 - 15** | Big-Endian Short | Mapped Tilt Angle (`-9000` to `9000`) |
  | **16 - 17** | Big-Endian Short | Button States bitmask |

* **Zero-Allocation Pooling**:
  Uses a `ByteArrayPool` that caches byte array buffers of size 18, recycling them upon socket writes, completely eliminating memory allocation overhead during fast touch gestures.

---

### 5. UI and Immersive Layouts (`MainActivity.kt`)

* **Immersive Mode**: Hides system navigation and status bars using `WindowInsetsControllerCompat`. Automatically reapplies immersive styling on window focus changes.
* **Notch/Cutout Support**: Configures `layoutInDisplayCutoutMode` to `LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES` to utilize the full width of devices with camera cutouts.
* **Power Management & Wi-Fi Lock**:
  * Keeps the device display powered on with `FLAG_KEEP_SCREEN_ON`.
  * Acquires a high-performance, low-latency Wi-Fi lock (`WIFI_MODE_FULL_LOW_LATENCY` for Q+, fallback to `WIFI_MODE_FULL_HIGH_PERF`) when active streams are running. This forces the Android Wi-Fi driver to disable power-saving states that would otherwise introduce jitter and packet latency.
* **Device Profiling**:
  Automatically calculates screen scale profiles (Native, Medium 0.75x, Low 0.5x, Custom) based on screen DPI and resolution to provide clean resolution matching options.

---

## 🛠️ Stream View Porting Note (`StreamScreen.kt` vs `MainActivity.kt`)

* **`StreamScreen.kt`**: Contains a standard compose-wrapped `SurfaceView` template setting `setZOrderOnTop(false)`.
* **Active View**: The operational stream rendering is performed by `StreamSurface()` defined in `MainActivity.kt` (lines 803-837). This component manages pointer touch / hover listeners directly and routes raw events directly to the `InputEventSender`.

---

## 🚀 Building and Running

### Prerequisites
* Android SDK 28+ (Android 9.0+)
* Gradle 8.0+

### Command Line Build
To assemble and install the debug version of the application directly on your connected tablet/phone:
```bash
cd android
./gradlew installDebug
# Launch the activity
adb shell am start -n com.example.monitorize/.MainActivity
```
