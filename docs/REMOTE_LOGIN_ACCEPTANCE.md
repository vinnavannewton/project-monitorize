# Remote-login host implementation and acceptance record

Date: 2026-10-23. Code implemented and reviewed; live installation/boot acceptance pending.

## Source pair

- Host: `/home/vinnavan/.worktrees/remote-login-host`, branch `codex/remote-login-host-support`, host version 0.3.0.
- App: `/home/vinnavan/.worktrees/remote-login-app`, branch `codex/remote-login-host-support`, based on app commit `e317755`.
- The original host checkout's GNOME fix was preserved and committed in the host feature branch.
- The original checkouts and running service installation were not replaced.

## Implemented behavior

Protected mon1 ownership, fixed 1920x1080@60 disconnected boot reservation, verified
greeter activation, adoption before desktop-engine startup, desktop activation,
20-second app idle policy with host quiescence proof, disconnected return to greeter,
and authenticated release/recovery. mon2 stays available to ordinary creation.
GNOME activation selects the exact owned output even when another virtual output
offers the same mode. No host operation treats sysfs connected status as proof of
exact active mode or encoded-frame readiness.

All three required protocol values are 1. App readiness checks trusted installed
metadata, matching helper copies, module constants and engine support. Source
staging/deployment includes host VERSION and capabilities metadata. Native app
dependencies require optional monitorize-vkms >=0.3.0; packages own distinct files.
Source install/explicit rollback refuses native package-owned files; explicit
rollback also requires disabled enrollment and a released protected display.

## Verification performed

| Check | Result |
| --- | --- |
| Host full unittest suite | 212 tests, pass |
| App focused `test_remote_login*.py` suite | 167 tests, pass |
| App greeter adapter/context suites | 9 tests, pass |
| App GDM integration suite | 13 tests, pass |
| Cross-repository actual app callback → host activation/idle | Pass against the host feature worktree |
| Existing app VKMS/package-asset tests | 3 + 3 tests, pass |
| Arch host package | Built with makepkg; contents inspected with pacman/bsdtar |
| DKMS source content from the staged package | Compiles against 7.2.11-arch1-1 headers; no module installed/loaded |
| Reviewed source deployment bundle | `validate_bundle` passes; staged readiness returns `ready` with owner UID adjusted for user-owned staging |
| Full app suite, isolated configuration | 757 tests: 2 failures, 3 errors, 1 skipped |
| Original app baseline, isolated configuration | 746 tests: identical 2 failures, 3 errors, 1 skipped |
| DEB/RPM builds and package installation | Not run: matching build environments/toolchains unavailable |
| Native app binary rebuild/install | Not performed; existing package staging/metadata tested |
| Physical-screen and headless boot cycles | Not performed |

Full suites used separate temporary `XDG_CONFIG_HOME` directories so host log files
could not affect health tests. The worktree Sunshine submodule was initialized
from the local original checkout before the final full run.

The five original baseline failures are:

- `test_flatpak_patch_sequence.FlatpakPatchSequenceTests.test_manifest_patch_sequence_applies_to_pinned_source`: patch hunks fail against the pinned Sunshine source.
- `test_installer.SunshineOnlyPackagingTest.test_add_display_uses_the_same_two_card_limit_in_vkms_mode`: test expects `sessionBusy`, QML uses `applyingConfiguration`.
- `test_settings.SettingsTest.test_backend_card_round_trip_preserves_independent_listener_selection`: fixture lacks `applyingConfiguration`.
- `test_settings.SettingsTest.test_backend_saves_each_sunshine_instance_independently`: same fixture issue.
- `test_settings.SettingsTest.test_sunshine_choice_snapshot_writes_one_patch_and_skips_repeat`: same fixture issue.

These were reproduced in the original checkout and left outside this feature's
scope. A separate host-log-dependent Sunshine-health failure reproduced in both
checkouts without isolated configuration and disappears with isolation.

## Review findings resolved

- Greeter idle used to probe the identity lock again while the host held it.
  Initial EngineGate idle authentication remains; callbacks now revalidate sender
  and session while the host independently holds the identity lock and checks
  cgroups. A test executes the actual daemon closure through this transaction.
- Explicit rollback could delete a formerly source-installed helper after native
  packages adopted it. Rollback now checks package ownership before changing any
  destination. Internal restoration after a failed current installation remains
  available. Tests cover package takeover, enabled enrollment, protected leases,
  and ordinary failed-install restoration.

## Reviewable artifacts

- Source bundle: `/home/vinnavan/.worktrees/remote-login-artifacts/reviewed-source-assets`.
- Host package: `/home/vinnavan/.worktrees/remote-login-host/packaging/arch/monitorize-vkms-0.3.0-1-x86_64.pkg.tar.zst`.
- Artifact hashes, git revisions and logs: `/home/vinnavan/.worktrees/remote-login-artifacts`.

The source bundle contains the currently built compatible Sunshine binary and
assets from the original app checkout, plus the reviewed app/host Python. It does
not contain a new kernel module, pairing secrets or enrollment state.

## Source installation test procedure (not executed)

Use this route only for the current source-managed host. For native package-owned
files, upgrade the native packages instead. Choose one route; do not overwrite
package-owned files with a source bundle.

1. Quit Monitorize from the tray, disconnect clients and stop Sunshine. If already
   enrolled, disable remote login and release its reservation first. Preserve pairing.
2. Stop `monitorize-vkms.service` and any running remote-login coordinator/experimental
   capture service. Confirm no Sunshine processes remain. This is a deliberate
   service change and has not been performed by the implementation work.
3. From the app feature worktree, install:

   ```sh
   sudo /usr/bin/python3 -I scripts/install-remote-login-assets.py \
     --stage /home/vinnavan/.worktrees/remote-login-artifacts/reviewed-source-assets
   ```

4. Save the exact rollback command/backup path printed by the installer. It replaces
   userspace and engine assets transactionally and does not enroll/start remote login.
5. Start the host D-Bus service. Launch the app from the feature worktree:

   ```sh
   MONITORIZE_SUNSHINE_BIN=/usr/libexec/monitorize/sunshine \
     PYTHONPATH=linux python3 -m monitorize
   ```

6. Confirm Settings readiness identifies all host protocols. Enable Remote login
   at boot with administrator authorization and confirm both system enrollment
   and the per-user controller report success. Do not infer live support from this.
7. Reboot deliberately with the local display available and perform the cycle below.

## Required live cycle

Record kernel, GNOME/GDM, app, host and Sunshine versions. Verify:

- Boot with no client: mon1 is reserved but disconnected; GDM remains running.
- Connect the existing paired Moonlight PC: exact mon1 activates at 1920x1080@60;
  actual encoded frames and input work.
- Sign in: expect disconnect, then reconnect to the same PC for the desktop.
- Disconnect for more than 20 seconds: the reserved VD disappears; reconnect restores
  the same reservation. Reconnect before expiry cancels removal.
- Lock/unlock does not trigger a greeter handoff. Logout returns to a disconnected
  greeter reservation, ready for an authenticated reconnect.
- Physical display layout is preserved; ordinary mon2 create/remove remains isolated.
- Coordinator/engine crashes, engine restart during idle, and stale session/token
  requests retain recoverable ownership and do not unplug another owner's output.
- Settings disable releases the reservation without deleting pairing. Desktop
  fallback and explicit asset rollback work after services/capture stop.

Only after that passes, repeat with no physical screen. If GDM cannot survive
zero outputs, mark headless mode unsupported and retain local recovery; do not
silently connect an always-on VD. Each other display manager needs its own cycle.

Do not collect key/certificate files or copy pairing identity into test reports.
Use system/user service journals for diagnostics. DEB/RPM and native app release
acceptance remain separate work on matching build/install environments.
