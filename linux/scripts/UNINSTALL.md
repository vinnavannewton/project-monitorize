# Uninstalling a source installation

From this directory, run as your normal desktop user:

```bash
./uninstall.sh
```

Use the arrow keys and Enter to choose one of two modes:

- **Uninstall — keep settings and pairing:** remove the installed source
  environment, bundled build artifacts, launchers, icons, autostart/controller
  entries and source-installed system assets. Disable remote login and release
  its reserved display. Keep app preferences, presets, Sunshine profiles,
  certificates, paired clients, logs and saved system data for a later reinstall.
- **Uninstall — remove all data:** perform the same removal, then erase the
  invoking user's Monitorize configuration, data, logs, cache, pairing keys and
  saved application system state, including source setup/deployment backups.
  Moonlight clients will need pairing again after reinstalling.

For a terminal without interactive input, explicitly select the mode:

```bash
./uninstall.sh --keep-data
./uninstall.sh --remove-all
```

`install.sh` only installs. Its former `remove`/`uninstall` arguments are no
longer accepted.

The uninstaller requests a normal graceful application shutdown so display-layout
restoration runs before removing files. Resolve any unapplied-changes prompt in
Monitorize. Older installed versions may need **Quit** from the tray first.
Active Monitorize streams, including remote login, are stopped by uninstalling.
Run locally if disconnecting your stream would lose access to the machine.
System cleanup requests Polkit authorization when applicable; do not run the
entire script with `sudo`.

Cleanup covers the current XDG roots and legacy hardcoded locations such as
`~/.config/monitorize` and `~/.local/state/monitorize`. It does not search other
users' homes or guess former XDG roots or arbitrary custom `MONITORIZE_LOG_FILE`
/ `SUNSHINE_CONFIG_DIR` paths. Move custom profiles into the normal Monitorize
paths before uninstalling if they should be erased too. An unresolved layout
recovery journal or failed privileged cleanup produces an error and retains the
records needed to retry. Fix the reported cause and rerun; repeated runs are safe.

The source checkout and personal changes, independently installed Sunshine and
its profiles, the separate `monitorize-vkms` installation, GPU drivers, CUDA,
Python, Qt and other shared prerequisites are preserved. Input group membership
is preserved when the standalone VKMS host is installed. Otherwise remove-all
also removes this user's `monitorize-input` membership and deletes the group if
unused. System journal/package-manager history is managed by the OS and is not
wiped. Package-owned system assets are refused; manage those through their
package manager. This script is for source installations.
