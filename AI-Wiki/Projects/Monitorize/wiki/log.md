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
