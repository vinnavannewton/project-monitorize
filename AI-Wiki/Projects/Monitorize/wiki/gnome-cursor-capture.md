# GNOME Cursor Capture

Monitorize requests embedded cursor capture for GNOME ScreenCast streams.

- `linux/monitorize/streaming/Streamer_gnome.py` passes `cursor-mode = 1` to Mutter `RecordVirtual` and `RecordMonitor`.
- In Mutter, `META_STREAM_CURSOR_MODE_EMBEDDED` is value `1`.
- The Monitorize GStreamer sender consumes `pipewiresrc` video frames and encodes them; it does not currently composite PipeWire cursor metadata into the video.

## Mutter behavior

Mutter's monitor screencast source and virtual screencast source handle embedded cursors differently.

- `meta-stream-source-monitor.c` inhibits hardware cursors for embedded cursor mode.
- `meta-stream-source-virtual.c` does not install the same hardware cursor inhibitor for embedded cursor mode.
- If Mutter keeps the pointer on a hardware cursor plane, the virtual screencast framebuffer may not contain the cursor even though Monitorize requested embedded mode.

This explains why `MUTTER_DEBUG_DISABLE_HW_CURSORS=1` makes the cursor visible: Mutter is forced to draw the cursor through the composited/software path that the virtual stream captures.

## Practical options

The reliable fixes are:

- Patch Mutter so the virtual screencast source inhibits hardware cursors in embedded cursor mode, matching the monitor screencast source.
- Disable Mutter hardware cursors for the GNOME session.
- Add cursor metadata rendering to Monitorize before encoding, then use metadata cursor mode.

Do not switch GNOME virtual streaming to cursor metadata mode unless Monitorize also composites the metadata. The current sender pipeline encodes video frames only, so metadata mode can still produce an invisible cursor for receivers.

The README documents the current user workaround as adding this line to the bottom of `/etc/environment`, then logging out and back in:

```bash
MUTTER_DEBUG_DISABLE_HW_CURSORS=1
```
