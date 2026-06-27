# GNOME Virtual Layout Workaround

This page defines the current working GNOME virtual monitor placement workaround in the Linux desktop app.

## Problem

Monitorize creates the GNOME virtual monitor through Mutter ScreenCast `RecordVirtual`. Mutter owns that virtual monitor as part of the ScreenCast session. When the session is recreated, the new virtual connector may have a new `Meta-*` connector name and GNOME will default the layout unless Monitorize restores it.

GNOME DisplayConfig cannot restore a left-side virtual monitor by applying only the virtual monitor's `x/y`. Mutter validates the whole logical monitor layout:

- logical monitor coordinates must be non-negative
- the layout must be normalized so minimum `x/y` is `0`
- monitors must form a valid non-overlapping adjacent layout

Therefore, a virtual monitor on the left is represented as:

- virtual logical monitor at `x=0`
- physical logical monitor shifted right by the virtual monitor width

The app must restore the full logical monitor layout, not just one monitor position.

## Current Working Flow

1. `Streamer_gnome` creates the virtual monitor with `RecordVirtual`.
2. `RecordVirtual` passes only `modes` and `cursor-mode`; it does not pass `position`.
3. After Mutter emits `PipeWireStreamAdded`, `Streamer_gnome` calls `restore_virtual_layout()` before starting GStreamer.
4. `restore_virtual_layout()` waits until the new virtual connector appears in `DisplayConfig.GetCurrentState()`.
5. The saved logical layout snapshot is mapped onto the current state:
   - physical monitors are matched by connector set, such as `eDP-1`
   - the saved virtual monitor role is mapped to the current virtual connector, such as `Meta-0` or `Meta-1`
6. `ApplyMonitorsConfig` is called with the whole logical monitor config, changing mapped logical monitor `x/y` positions and restoring saved scale when it is supported by the current mode.
7. GStreamer starts after the restore attempt, using the current PipeWire node.

If restore fails, streaming still starts. The workaround must not block the stream.

## Saved Data

GNOME layout state is stored in the GNOME-only settings group `gnome_virtual_primary`.

Only the full logical layout snapshot is canonical. The stored shape is a JSON list like:

```json
[
  {"connectors": ["eDP-1"], "x": 1920, "y": 0, "scale": 1.0, "virtual": false},
  {"connectors": ["Meta-0"], "x": 0, "y": 0, "scale": 1.25, "virtual": true}
]
```

Do not store or rely on a separate top-level `x/y` value for GNOME. KDE uses a different layout persistence path.

## Saving

`StreamingController` tracks GNOME layout only for GNOME Extend streaming.

It saves the current layout:

- periodically while streaming
- when Mutter DisplayConfig emits `MonitorsChanged`
- before GNOME stop or crash restart where possible

The save helper reads `org.gnome.Mutter.DisplayConfig.GetCurrentState()` and writes a snapshot only if a virtual physical connector is visible.

Virtual connectors are identified from physical monitor spec data containing `meta` or `virtual`.

## Restore Payload

`gnome_virtual_monitor.build_monitors_config()` requires a saved full logical layout. It intentionally does not support the old virtual-only `x/y` fallback.

The `ApplyMonitorsConfig` payload preserves current settings as much as possible:

- current selected mode IDs
- scale
- transform
- primary flag
- monitor list
- global `layout-mode`
- writable monitor properties: `color-mode`, `rgb-range`, `underscanning`

`GetCurrentState` exposes underscan as `is-underscanning`, but `ApplyMonitorsConfig` expects `underscanning`. The helper maps `is-underscanning` to `underscanning` and never passes read-only aliases such as `is-underscanning`, `enable_underscanning`, or `underscan`.

Saved scale is restored only if it is listed in the current mode's supported scales from `GetCurrentState()`. If any saved scale is missing or unsupported, Monitorize skips the restore attempt and still launches GStreamer.

`Streamer_gnome` also seeds the newly created virtual monitor by passing the saved virtual scale as mode `preferred-scale` inside `RecordVirtual`'s `modes` entry. It does not accept a separate scale command-line argument.

## Do Not Reintroduce

- Do not pass `position` to `RecordVirtual`.
- Do not append saved GNOME `x/y` arguments to `Streamer_gnome`.
- Do not append a GNOME scale argument to `Streamer_gnome`; use saved layout scale and `preferred-scale`.
- Do not use virtual-only negative `x/y` restore payloads.
- Do not apply arbitrary scale values; only use scales Mutter reports as supported for the current mode.
- Do not intentionally reconnect the GNOME streamer on every `MonitorsChanged`; the signal is only for passive save.
- Do not share KDE layout keys or behavior with GNOME.

## Source Files

- `linux/monitorize/platform/gnome_virtual_monitor.py`
- `linux/monitorize/streaming/Streamer_gnome.py`
- `linux/monitorize/desktop/streaming_controller.py`
- `linux/monitorize/config/settings.py`
- `linux/tests/test_gui_controllers.py`

Related pages: [[desktop-app]], [[decisions]], [[open-questions]].
