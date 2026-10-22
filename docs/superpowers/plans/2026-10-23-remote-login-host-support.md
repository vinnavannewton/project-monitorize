# Remote-login Host Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This document is a proposed plan, not authorization to deploy or reboot.

**Goal:** Implement the missing VKMS remote-login lifecycle and deliver it with compatible source and native binary installations.

**Architecture:** Extend the existing host lease/locking model with a protected mon1 reservation and an installed `boot_display` facade matching the app's existing calls. Keep authentication/handoff in Monitorize and physical display mutations in the host. Deliver host and app service files through distinct package ownership, with runtime protocol checks before enrollment.

**Tech Stack:** Python, unittest, systemd/logind, D-Bus, Polkit, DRM/configfs, existing Sunshine fork, DKMS, Arch/DEB/RPM packaging.

**Spec:** [Proposed design](../specs/2026-10-23-remote-login-host-support.md).

## Execution status (2026-10-23)

Tasks 1–5 are implemented and verified in the host/app feature worktrees. Tasks
6–8 have delivery/readiness/guard implementations, a built and inspected Arch host
package, and passing focused tests; clean cross-distro builds and native app
package installation remain pending. Task 9 has cross-repository simulation and
baseline comparisons, but physical-screen/headless boot acceptance is pending.
See `docs/REMOTE_LOGIN_ACCEPTANCE.md` for exact evidence, limitations and artifacts.

Review fixes: greeter idle callbacks avoid re-probing the host-held identity lock;
explicit source rollback checks current native package ownership and disabled/
released state. Added desktop `adopt` before engine startup because prepare occurs
after the engine acquires identity ownership. `activate_greeter` returns its worker's
verified mode result. Native changes require disabled/released enrollment.

## Global Constraints

- Proposed first compatible host release: `monitorize-vkms 0.3.0`.
- Required protocol values: `BOOT_DISPLAY_PROTOCOL = 1`, `GREETER_ON_DEMAND_PROTOCOL = 1`, `IDLE_DISPLAY_PROTOCOL = 1`.
- Reserve `mon1` at fixed `1920x1080@60`; leave `mon2` available to ordinary streaming.
- Boot reservation programs EDID but leaves the output disconnected.
- The app owns the existing 20-second idle timer. The host owns validated mutations.
- Login uses disconnect-and-reconnect to the same paired Moonlight PC.
- No forced module unload, display-manager restart, automatic enrollment, or pairing reset.
- Preserve the uncommitted GNOME session-discovery fix and its tests.
- Implementation is Python/userspace first; no kernel change is assumed.

## Review Focus

- Power loss or service crash between configfs and lease writes must leave recoverable ownership (Tasks 1–3).
- Greeter/session replacement during activation must reject stale requests and retain cleanup responsibility (Tasks 3–4).
- An engine restarting during idle/recovery must prevent unplugging an in-use reservation (Task 4).
- Package upgrade/downgrade must preserve pairing and reject incompatible active ownership (Tasks 6–8).
- A zero-output GDM startup failure must be reported as unsupported, never silently force an always-connected display (Task 9).

## Repositories and file boundaries

`H` = `/home/vinnavan/monitorize-vkms`; `A` = `/home/vinnavan/Projects/Monitorize`.
Run commands in the explicitly named repository. Existing app tests use
`PYTHONPATH=linux python3 -m unittest discover -s linux/tests -p '<pattern>'`.

New host modules:

- `lease.py`: bounded trusted lease persistence and protected-state validation.
- `boot_display.py`: installed public facade and systemd entry point.
- `boot_policy.py`: pure transition/ownership validation.
- `boot_runtime.py`: configfs, DRM, compositor and installed-context effects.
- `protocols.py`: capability constants and manifest representation.

Keep systemd unit ownership in A, where the units already exist. Do not create a
second copy of the boot-display unit in the host package. Reuse existing helper
primitives rather than rewriting ordinary create/remove.

### Task 1: Protected lease persistence and transition policy

**Files:** H create `monitorize_vkms/lease.py`, `monitorize_vkms/boot_policy.py`, `tests/test_boot_policy.py`, `tests/test_boot_lease.py`; modify `monitorize_vkms/helper.py`.

**Interfaces:** `read_lease(state: Path, slot: str) -> dict | None`,
`write_lease(state: Path, slot: str, lease: dict) -> None`,
`remove_lease(state: Path, slot: str) -> None` in lease.py; existing helper lease
functions delegate without changing signatures. `validate_transition(lease: dict,
operation: str, *, uid: int, session: str, boot_id: str, generation: str) -> dict`
in boot_policy.py returns a proposed lease, performs no I/O, and rejects invalid transitions.

- [ ] Write failing tests: v1 ordinary leases remain readable; protected schema v2 validates exact types; stale boot/session/generation and unknown state fail; same operation is idempotent; unsafe ownership/symlinks/oversized JSON fail before mutation; interrupted atomic replacement retains prior valid file.
- [ ] Run H `python3 -m unittest discover -s tests -p 'test_boot_*.py'`; confirm intended failures.
- [ ] Implement bounded reads (64 KiB maximum), trusted state-directory checks, atomic replace, and the spec's state transitions. Keep protected data in `primary.json` and reject accidental use for mon2.
- [ ] Run new tests and H `python3 -m unittest discover -s tests -p test_helper.py`; require all passing before proceeding.
- [ ] Review diff and commit only task files: `feat: add protected remote-login lease policy`.

### Task 2: Reserve a disconnected boot output

**Files:** H create `monitorize_vkms/boot_display.py`, `monitorize_vkms/boot_runtime.py`, `tests/test_boot_display.py`; modify `monitorize_vkms/drm.py` only as needed for exact enabled-output evidence.

**Interfaces:** facade `boot_output_status(state: Path) -> dict | None`;
runtime `reserve_boot_output(*, state: Path, connector: Path, uid: int,
boot_id: str, sessions: list[dict]) -> dict`; facade `main() -> int` for
`python3 -I /usr/lib/monitorize-vkms/monitorize_vkms/boot_display.py`.

- [ ] Write failing tests for disabled/missing settings no-op, malformed/unsafe settings, existing ordinary/foreign leases, active graphical sessions, fixed mon1 EDID, disconnected reservation, repeated startup, and interrupted writes. Assert no mon2/physical-output changes.
- [ ] Run H `python3 -m unittest discover -s tests -p test_boot_display.py`; verify failures.
- [ ] Implement root-only installed entry point with fixed trusted import path. Validate bounded `/etc/monitorize/remote-login.json` schema/account using the subset defined in the design, without importing app source. Acquire lifecycle lock; persist ownership before side effects; retain pending recovery state on partial failure.
- [ ] Add exact DRM identity/enabled/mode observation; reserve may have no DRM EDID identity until connected. Never manufacture a connector name or report active from `status=connected` alone.
- [ ] Run tests and existing EDID/DRM/bootstrap tests; commit `feat: reserve disconnected remote-login boot output`.

### Task 3: Authenticated on-demand greeter activation

**Files:** H modify `monitorize_vkms/boot_display.py`, `monitorize_vkms/boot_runtime.py`; create `tests/test_boot_greeter.py`. A modify `linux/monitorize/remote_login/daemon.py` only if contract alignment is required; extend `linux/tests/test_remote_login_greeter_display.py` and existing GDM tests.

**Interfaces:** facade
`reconnect_reserved(*, state: Path, generation: str, boot_id: str, connector: Path) -> dict`;
`greeter_operation_installed(operation: str, *, context_check: Callable[[], object], activate: Callable[[dict, float], object]) -> dict`.
Greeter operations are `prepare` and `idle`; app `check` stays a gate-only operation.
Returned lease includes `connector`; activation callback receives DRM `name`,
`card`, `connector_id` and remaining timeout seconds, matching daemon.py.

- [ ] Write failing tests for prepare→active, idle→reserved, repeated calls, exact mode mismatch, connector/card replacement, wrong/missing greeter, no authenticated engine, expired generation, greeter change inside activation callback, and hotplug timeout. Assert unsafe cases never report success.
- [ ] Run H `python3 -m unittest discover -s tests -p test_boot_greeter.py`; verify failures.
- [ ] Implement bounded prepare with a 10-second total deadline, invoking the existing worker with at most the remaining deadline (8-second worker maximum). Revalidate callbacks before mutation and after activation. Retain pending lease after ambiguous partial activation.
- [ ] Reuse app GDM socket/UID/session/trusted executable/Mutter-owner verification. Add a targeted fix only if actual greeter user-service layout cannot satisfy it; never relax ownership globally or treat the desktop metadata fallback as greeter authentication.
- [ ] Run H boot tests; run A `PYTHONPATH=linux python3 -m unittest discover -s linux/tests -p 'test_*gdm*.py'` and `PYTHONPATH=linux python3 -m unittest discover -s linux/tests -p test_remote_login_greeter_display.py`; commit per repository.

### Task 4: Desktop handoff, idle disconnect and release

**Files:** H modify boot modules and session/compositor worker integration in `monitorize_vkms/helper.py`; create `tests/test_boot_desktop.py`, `tests/test_boot_recovery.py`. A extend `linux/tests/test_remote_login_idle.py`, `linux/tests/test_remote_login_engine_gate.py`, `linux/tests/test_remote_login_enrollment.py`.

**Interfaces:** facade
`desktop_operation_installed(uid: int, session: str, operation: str, *, engines_stopped: bool = False) -> dict` for `prepare`, `idle`, `release`;
`return_to_greeter(*, state: Path, generation: str, boot_id: str, sessions: list[dict], engines_stopped: bool, connector: Path) -> dict`.
Runtime `verify_quiescent(uid: int) -> None` independently checks fixed engine
cgroups and nonblocking authoritative identity lock; caller boolean is only intent.

- [ ] Write failing tests for greeter→desktop ownership, local login without connecting, desktop reconnect at fixed mode, 20-second idle scheduling/cancellation in A, sole-virtual-output idle, logout→disconnected greeter reservation, lock≠logout, and safe release. Include engine restart during quiescence checks, stale descriptors, PID reuse, session change, and failed worker cleanup.
- [ ] Run focused tests in H and A; confirm failures before implementation.
- [ ] Implement independent enrollment/logind/boot/generation validation and quiescence checks. Use existing worker/layout logic, with an explicit protected-output-only path permitting sole-output idle. Never weaken ordinary last-monitor safeguards.
- [ ] Validate lock ordering with a concurrent prepare/remove test and a service-stop timeout test. Retain lease on failure. Revalidate engine/session ownership after effects; the coordinator keeps encoded-frame capture readiness separate from host activation.
- [ ] Run both focused suites and existing host GNOME/session-discovery tests; commit per repository.

### Task 5: Ordinary CLI coexistence and capability reporting

**Files:** H modify `monitorize_vkms/helper.py`, `monitorize_vkms/cli.py`, `monitorize_vkms/dbus_service.py`, `scripts/verify-install.sh`; create `monitorize_vkms/protocols.py`, `monitorize_vkms/remote_login_client.py`, `tests/test_boot_coexistence.py`, `tests/test_protocols.py`; extend `tests/test_cli.py`.

**Interfaces:** `protocols.capabilities() -> dict` returns
`{"boot_display": 1, "greeter_on_demand": 1, "idle_display": 1}` only after Tasks 1–4 pass.
Helper capability response adds `remote_login_protocols` without changing API v1.
`remote_login_client.remove_reserved() -> dict` calls the existing app coordinator
`io.github.vinnavannewton.MonitorizeRemoteLogin1.RemoveDisplay` as the actual user.

- [ ] Write failing tests: automatic create skips protected mon1 and uses mon2; explicit create/destroy cannot overwrite it; foreground stale cleanup cannot release it; destroy-all reports partial results accurately; explicit CLI remove invokes authenticated coordinator; absent coordinator does not bypass ownership. Capability works before topology readiness; ordinary operation works without app installation.
- [ ] Run focused tests and verify intended failures.
- [ ] Add protected guards before every mutation, including disconnected-connector cleanup. Keep app D-Bus calls outside the host lifecycle lock. Keep ordinary D-Bus destroy callers fail-closed with an actionable managed-display error; only user CLI routes the authenticated removal.
- [ ] Add read-only doctor distinction between ordinary display health and remote-login readiness. Preserve kernel-only verification semantics while reporting optional host protocols separately.
- [ ] Run H full suite `python3 -m unittest discover -s tests`; commit `feat: expose remote-login capabilities and protect reserved output`.

### Task 6: Host release, installation and native packages

**Files:** H modify `VERSION`, `dkms.conf`, `install.sh`, `uninstall.sh`, `README.md`; create `scripts/stage-host-assets.py`, `packaging/arch/PKGBUILD`, `packaging/arch/monitorize-vkms.install`, `packaging/deb/debian/{control,rules,postinst,prerm,postrm}`, `packaging/rpm/monitorize-vkms.spec`, `tests/test_host_delivery.py`.

**Interfaces:** `stage_host_assets(destination: Path) -> None` in staging script;
installed `/usr/share/monitorize-vkms/capabilities.json` schema version 1 contains
host version and `remote_login_protocols`. Source installer and native recipes
consume the same userspace manifest. Host owns no app remote-login unit.

- [ ] Write failing staging tests for package contents, root-readable modes under restrictive umask, matching helper/package copies and manifest, install/rollback targets, absent credentials/config enrollment, no automatic remote-login enablement, and rejection of downgrade/removal while dependent enrollment is enabled.
- [ ] Run H `python3 -m unittest discover -s tests -p test_host_delivery.py`; verify failures.
- [ ] Implement common userspace staging; package DKMS source/module metadata separately. Install boot modules and manifest atomically with matching helper copies. Release version 0.3.0; keep VERSION/dkms.conf consistent. Preserve prior ordinary leases and the GNOME fix.
- [ ] Add package-manager lifecycle hooks for DKMS build/index updates and host bootstrap. Block incompatible removal/downgrade before deleting files; never forcibly unload the running GPU. Source rollback restores all userspace copies/manifest together.
- [ ] Build Arch package in a clean Arch build environment; inspect `pacman -Qlp <artifact>`. Build DEB/RPM in their matching disposable environments; inspect `dpkg-deb --contents <artifact>` and `rpm -qpl <artifact>`. Test install/upgrade/removal with remote login disabled, then assert enabled-enrollment removal is blocked. Record artifacts and commands; do not claim cross-distro validation from manifest tests alone.
- [ ] Run H full suite and shell syntax checks; commit `build: package compatible VKMS remote-login host support`.

### Task 7: App readiness, enrollment and source deployment

**Files:** A create `linux/monitorize/platform/remote_login_host.py`; modify `linux/monitorize/platform/system_setup.py`, `linux/monitorize/qml/RemoteLoginSetupPage.qml`, `linux/monitorize/remote_login/enrollment.py`, `scripts/stage-remote-login.py`, `scripts/install-remote-login-assets.py`; extend `linux/tests/test_remote_login_delivery.py`, `linux/tests/test_remote_login_deployment.py`, `linux/tests/test_remote_login_readiness.py`, `linux/tests/test_remote_login_enrollment.py`.

**Interfaces:** `remote_login_host.host_readiness() -> dict` returns `available: bool`,
`reason: str`, `message: str`, `protocols: dict`. Reasons: `ready`, `missing_assets`,
`host_missing`, `host_incompatible`, `host_incomplete`, `engine_incompatible`.
Root enrollment validates actual installed modules/paths in addition to the manifest.

- [ ] Write failing tests for every readiness reason, mixed-version helper copies, absent/unsafe/malformed manifest, wrong protocol, old engine, and missing GDM service update. Assert ordinary streaming is available and failed enrollment writes no identity/enablement state.
- [ ] Run focused tests; verify failures.
- [ ] Implement shared capability requirements and clear install/upgrade instructions. Preserve display-manager experimental status. Avoid importing host implementation into the unprivileged GUI or requesting authentication just to display readiness.
- [ ] Make source staging validate all three protocols before creating output; fix fixtures currently missing the idle constant. Validate bundle again before root mutation. Detect package-owned host paths via the host package manager; reject `--vkms-source` replacement on managed installs and request package upgrade. Permit coherent transactional host-Python updates on source-managed hosts.
- [ ] Test failed installation restores files/modes/capabilities/manifest together, preserves pairing and enrollment, and never enables services. Test missing protocol fails without creating output or backup transaction.
- [ ] Run A remote-login delivery/deployment/readiness/enrollment suites; commit `fix: gate remote-login setup on complete host support`.

### Task 8: App binary distribution and upgrade policy

**Files:** A modify `packaging/arch/PKGBUILD`, `packaging/arch/monitorize.install`, `packaging/rpm/monitorize.spec`, `packaging/tumbleweed/monitorize.spec`, `packaging/deb/common/rules.mk`, each `packaging/deb/{ubuntu-24.04,ubuntu-26.04,debian-trixie}/debian/control` and corresponding maintainer scripts; extend `linux/tests/test_remote_login_delivery.py`, `linux/tests/test_vkms_packaging.py`; update `packaging/remote-login/README.md`.

**Interfaces:** app packages keep their current service bundle and optional versioned
VKMS dependency >=0.3.0; runtime protocol checks are authoritative. Portable builds
point to companion native packages; no root service runs from portable/user-writable assets.

- [ ] Write failing metadata/manifest tests: all native builders include identical app assets; compatible optional host dependency in Arch/DEB/RPM; no app package owns host Python/helpers; package hooks do not enroll/start remote login; install does not overwrite pairing/settings. Include app downgrade compatibility handling while enrolled.
- [ ] Run focused tests; verify failures.
- [ ] Implement dependency metadata, service reload policy and explicit drain/update instructions for active installations. Source installer remains a development route; packaged installs use package-manager ownership and coordinated compatible upgrades.
- [ ] Build/inspect actual app artifacts in matching build environments. Test normal streaming without VKMS, old VKMS readiness error, compatible install/enrollment, paired upgrade, failed upgrade, and rollback to a compatible pair. Treat each distro as a separate validation result.
- [ ] Document portable companion installation and required administrator enrollment. Update stale source paths/examples in remote-login README; keep unsupported/headless limitations explicit. Commit `build: deliver versioned remote-login service dependencies`.

### Task 9: Cross-repository integration and staged live acceptance

**Files:** H create `tests/test_boot_app_contract.py`; A create `linux/tests/test_remote_login_host_contract.py`, `docs/REMOTE_LOGIN_ACCEPTANCE.md`; update `packaging/remote-login/README.md` with measured results only.

**Interfaces:** tests load the host facade from the chosen H checkout and exercise
the app daemon/enrollment calls with temporary trusted filesystem and mocked
logind/DRM/engine evidence. Unit tests never mutate the real host or restart services.

- [ ] Add cross-repository tests that execute real host facade bodies rather than only assert importability/constants. Cover disconnected boot reservation → authenticated greeter activation → desktop ownership → idle → logout → greeter reservation → disable/release.
- [ ] Run H full suite and A `PYTHONPATH=linux python3 -m unittest discover -s linux/tests -p 'test_remote_login*.py'`. Run broader A tests once; diagnose failures and distinguish pre-existing failures using a clean baseline. Do not report a passing full suite without a completed passing run.
- [ ] Validate real configfs assumptions in a disposable VM: EDID persists disconnected, connector object identity can be recovered, exact mode activation is observable, and idle permits reconnect. Escalate any missing kernel behavior as a design change before modifying kernel code.
- [ ] Prepare a concrete deployment/rollback manifest and confirm tests, files and capabilities before live installation. Source deployment must include the compatible H checkout only on source-managed hosts. Stop engines before replacement; retain installer backup paths. Enrollment and reboot are separate deliberate actions.
- [ ] With a physical display available, record Arch/GNOME/GDM/kernel/engine/host/app versions. Reboot without a client; assert no active VD. Connect to greeter, sign in, reconnect to the same paired PC, verify encoded video/input/audio, disconnect >20 seconds, reconnect, lock/unlock, logout, and reconnect to greeter. Verify physical layout and mon2 isolation.
- [ ] Inject coordinator crash, engine crash/restart and stale session during preparation. Assert no stale-owner activation, pairing loss, foreign-output unplug or unrecoverable blank screen. Exercise Settings disable and desktop fallback, plus compatible asset rollback.
- [ ] Run a separate headless boot cycle only after the physical-screen cycle passes. If GDM cannot remain running with zero outputs, record unsupported status and stop headless acceptance; retain explicit consent-based recovery. Other display managers require their own recorded acceptance.
- [ ] Review acceptance evidence and commit docs/tests. Release remote-login support only for configurations that passed; leave untested configurations experimental.

## Completion and handoff

- [ ] Verify each design requirement maps to a task and each public signature matches current app call sites.
- [ ] Review host and app diffs, package ownership, protocol manifests and preserved GNOME regression tests.
- [ ] Record unit, integration, package-build and live results separately; identify unavailable build environments explicitly.
- [ ] Keep pairing/enrollment intact and provide exact compatible rollback paths.

Recommended execution: native, sequential implementation in this session, with a
whole-change review before deployment. Host and app tasks share lifecycle interfaces;
finish and test each contract before its consumers. Review this proposed plan before
starting implementation. No implementation or deployment was performed by writing it.
