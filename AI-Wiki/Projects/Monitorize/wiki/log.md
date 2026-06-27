# Monitorize Wiki Log

## [2026-06-27] init | Codex-maintained Obsidian wiki

Initialized the project wiki structure for Monitorize.

- Created core wiki pages: [[overview]], [[architecture]], [[decisions]], [[open-questions]], [[glossary]], [[sources]], and [[index]].
- Added Codex maintenance instructions in the repository root and wiki root.
- Seeded the initial facts from `README.md`, `docs/project_structure.md`, and `docs/linux.md`.

## [2026-06-27] inspect | Linux desktop app

Inspected the Linux desktop app entrypoint, full window, QML root, backend facade, streaming lifecycle, receiver lifecycle, USB setup, Zeroconf discovery, and lightweight tray agent.

- Added [[desktop-app]] with source-derived notes.
- Updated [[architecture]], [[index]], [[sources]], and [[open-questions]].
- Noted a possible tray-agent issue: `tray_agent.py` references `os.path.join(...)` without importing `os`.

## [2026-06-27] config | Monitorize wiki maintainer skill

Created the personal Codex skill `monitorize-wiki-maintainer` under `/home/vinnavan/.codex/skills/monitorize-wiki-maintainer`.

- The skill triggers for work in `/home/vinnavan/user/MegaProjects/Monitorize-testing`.
- The skill instructs Codex to read [[index]], update durable project knowledge, and append this log after meaningful work.
- Updated repository `AGENTS.md` to prefer the skill when available.

## [2026-06-27] fix | GNOME virtual-display move reconnect

Implemented GNOME Extend controlled reconnect in the desktop streaming controller.

- The controller now listens for Mutter DisplayConfig `MonitorsChanged` while GNOME Extend streaming is active.
- After GStreamer is ready and a startup grace delay passes, monitor changes are debounced, the current virtual `x/y` is saved with retries, and the GNOME streamer/GStreamer/input path is relaunched on a new generation.
- Targeted `tests.test_gui_controllers` passed after adding coverage for signal connection, debounce/retry behavior, controlled reconnect, and stale crash-restart suppression.

## [2026-06-28] fix | GNOME full logical layout restore

Replaced the GNOME virtual-display move reconnect approach with passive layout saving plus full logical layout restore.

- Mutter source inspection showed left-side virtual placement cannot be represented as a virtual-only negative `x`; DisplayConfig validation requires non-negative coordinates and a layout normalized to min `x/y == 0`.
- Monitorize now saves a GNOME logical monitor layout snapshot and maps the saved virtual role to the current `Meta-*` connector after `RecordVirtual`.
- `ApplyMonitorsConfig` restoration moves all mapped logical monitors as needed while preserving modes, scale, transform, primary flag, monitor properties, and global layout properties.
- `MonitorsChanged` is used only to debounce a save while streaming; it no longer intentionally restarts the GNOME streamer.
- `python -m unittest tests.test_gui_controllers` and targeted `py_compile` checks passed.

## [2026-06-28] cleanup | GNOME layout workaround simplification

Removed obsolete GNOME layout workaround paths after the full logical layout restore proved working.

- `Streamer_gnome` no longer accepts or forwards saved `x/y` position arguments.
- `StreamingController` no longer loads GNOME layout settings just to append streamer arguments.
- GNOME settings now persist only the full logical layout snapshot; the separate top-level `x/y` values are ignored.
- `gnome_virtual_monitor.build_monitors_config()` now requires a saved full layout and no longer supports virtual-only x/y Apply payloads.
- `python -m unittest tests.test_gui_controllers` and targeted `py_compile` checks passed.

## [2026-06-28] docs | GNOME virtual layout workaround

Added [[gnome-virtual-layout]] as the canonical wiki page for the working GNOME virtual monitor placement workaround.

- Documented why virtual-only `x/y` restore fails under Mutter DisplayConfig validation.
- Documented the working flow: `RecordVirtual` with `modes`, wait for the new virtual connector, map saved full logical layout to the current state, apply through `ApplyMonitorsConfig`, then launch GStreamer.
- Documented what not to reintroduce: `RecordVirtual(position)`, saved `x/y` streamer args, virtual-only negative coordinates, and intentional reconnects on `MonitorsChanged`.
- Linked the page from [[index]] and [[desktop-app]].

## [2026-06-28] fix | GNOME scale persistence

Extended the GNOME full-layout workaround to persist scale accepted through GNOME Display Settings.

- GNOME saved layout entries now include logical monitor `scale`.
- `ApplyMonitorsConfig` restore uses saved scale only when the current mode reports that scale as supported.
- `Streamer_gnome` no longer accepts the dead scale CLI argument; it seeds the virtual monitor with mode `preferred-scale` when a saved virtual scale exists.
- `python -m unittest tests.test_gui_controllers` and targeted `py_compile` checks passed.
