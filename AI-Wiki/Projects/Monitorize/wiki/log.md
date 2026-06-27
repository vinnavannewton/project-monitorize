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
