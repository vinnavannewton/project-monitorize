# GNOME Input Mapping

GNOME input stays on Monitorize's uinput path.

Mutter does not expose a simple app command equivalent to KWin output binding, Hyprland `device:*:output`, or Sway `map_to_output`. Mutter's input mapper instead reads GNOME per-device settings keyed by input device vendor/product and maps touchscreens/tablets to monitor EDID tuples.

Monitorize therefore gives its uinput devices stable IDs:

- touch: vendor `0x4d5a`, product `0x1001`, name `Monitorize-Touch`
- stylus: vendor `0x4d5a`, product `0x1002`, name `Monitorize-Stylus`

Before creating those devices on GNOME, the input backend reads Mutter DisplayConfig, finds the current virtual monitor, extracts its `[vendor, product, serial]`, and writes:

- `/org/gnome/desktop/peripherals/touchscreens/4d5a:1001/` `output`
- `/org/gnome/desktop/peripherals/tablets/4d5a:1002/` `output`
- tablet `mapping = 'absolute'`

The mapping step is best-effort. If DisplayConfig or GSettings is unavailable, streaming and input startup continue with the existing uinput behavior.

When GNOME mapping succeeds, Monitorize sends uinput coordinates in the virtual monitor's local logical coordinate space. This avoids double-applying the virtual monitor's desktop offset because Mutter already maps the device to the selected output. If GNOME mapping fails, Monitorize keeps the older desktop-wide bounds fallback.
