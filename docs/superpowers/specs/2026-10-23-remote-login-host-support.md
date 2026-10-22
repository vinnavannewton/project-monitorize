# Remote-login host support: proposed design

Status: userspace implementation is on reviewed feature branches; deployment and live acceptance are pending.

## Intended result

Make the existing Monitorize remote-login implementation usable with a compatible
Monitorize VKMS host installation, through source deployment and native binary
packages. Keep ordinary virtual-display creation working. First acceptance target:
this Arch VM with GNOME/GDM Wayland. Other display managers retain experimental
status until separately tested.

## Observed baseline

- Host checkout: `/home/vinnavan/monitorize-vkms`, version `0.2.4`.
- App checkout: `/home/vinnavan/Projects/Monitorize`, version `0.33.4`.
- The host has two persistent connectors, EDID support, locked lifecycle operations,
  root-owned lease files, session workers and a Polkit-gated D-Bus service.
- The host has no `boot_display.py` or boot/idle/on-demand protocol constants.
- The app already imports `boot_output_status`, `return_to_greeter`,
  `reconnect_reserved`, `desktop_operation_installed`, and
  `greeter_operation_installed` from that missing module.
- The app already stages service units and performs protocol checks during enrollment.
  Its source staging check omits the idle protocol that deployment requires.
- Native app recipes stage the private service bundle, but VKMS dependencies have
  no compatible version floor. There are no native host-package recipes here yet.
- Source GNOME session-discovery changes from the preceding fix are uncommitted.
  Preserve them and their regression tests throughout implementation.

## Constraints and ownership

- Proposed first compatible host release: `monitorize-vkms 0.3.0`.
- Required protocol values: `BOOT_DISPLAY_PROTOCOL = 1`,
  `GREETER_ON_DEMAND_PROTOCOL = 1`, `IDLE_DISPLAY_PROTOCOL = 1`.
- Protocol constants advertise implemented behavior; adding constants alone is not a fix.
- Reserve `mon1` at fixed `1920x1080@60`; leave `mon2` available to ordinary streaming.
- Boot reservation programs EDID but leaves the output disconnected.
- An authenticated managed-engine launch may connect and activate the reservation.
- The app owns the existing 20-second idle timer. The host owns validated mutations.
- Login uses disconnect-and-reconnect to the same paired Moonlight PC.
- Host code owns configfs, lease identity, DRM verification and compositor workers.
  Monitorize owns enrollment, pairing identity, engine authentication and handoff.
- The app package owns its boot-display systemd unit; the host package owns the
  executable module referenced by that unit. Neither package duplicates the other's files.
- Only root-installed trusted code performs root operations. Session metadata is a
  hint; it never authorizes a caller, grants DRM ownership, or selects executable code.
- No forced module unload, display-manager restart, automatic enrollment, or pairing reset.
- No kernel patch is planned: first verify the current configfs/DRM implementation
  meets these contracts; treat any discovered kernel requirement as a scope change.

## Host state model

Extend the existing `primary.json` lease; do not introduce competing ownership files.
Add `schema_version=2`, `protected=true`, `owner_kind="remote-login"`, enrolled `uid`,
`phase` (`greeter` or `desktop`), `session`, `boot_id`, `generation`, `mode`,
`edid_sha256`, and verified connector/card/object identity when available.
Existing unprotected lease files remain readable by the ordinary lifecycle.

Transitions: absent → reserved → pending → active → reserved. Release removes the
reservation only after authorized recovery or disablement. `reserved` means EDID
retained and connector disconnected; `pending` retains recovery responsibility
while activation is incomplete. `active` requires the exact output enabled at the
fixed mode, not merely a connected sysfs status. Capture readiness additionally
requires the app's existing Sunshine encoded-frame health event.

Every mutation uses the existing lifecycle lock and rechecks boot ID, generation,
connector identity and current session. Boot reservation refuses existing leases,
connected foreign outputs, or an already running graphical session. Identical
repeated operations are idempotent. Partial failures retain a recognizable lease
and never adopt or unplug another owner's output.

The installed boot entry point reads bounded root-owned enrollment settings,
verifies the account and `enabled=true`, and reserves only mon1 before the display
manager starts. Missing/disabled settings produce a successful no-op. Malformed
settings fail without writes. The host reads the minimal settings fields itself;
it does not import application code from an arbitrary Python path.

Desktop idle is permitted to disconnect the final virtual output only for the
protected managed reservation after engine capture and identity ownership have
been released. Ordinary remove retains its existing last-screen protection.
Logout transfers the disconnected reservation back to greeter ownership after
user sessions and previous engines have exited. Locking is not logout.

## Authorization and recovery

The existing app coordinator authenticates bus sender, managed engine PID, epoch,
session and identity ownership. Host installed wrappers independently verify root,
enrollment, current logind session and lease ownership. A caller-supplied
`engines_stopped=True` is not sufficient proof of quiescence.

Host recovery verifies the fixed managed engine units' cgroups have no capture
processes and acquires the authoritative identity lock. Keep ordering consistent:
app coordinator serializes requests; host acquires lifecycle lock; verification
never waits for app D-Bus work while holding that lock. Identity-lock probes are
nonblocking. No lock is held across a stop/wait cycle.

Ordinary create skips protected mon1 during automatic slot selection. Ordinary
destroy refuses direct changes to that reservation. Explicit CLI remove routes a
protected reservation through the coordinator's authenticated `RemoveDisplay`;
an unavailable coordinator yields an actionable recovery error, never a bypass.
Administrator recovery uses the existing app enrollment remove/fallback path and
the same host checks. Account/session mismatch, stale generation, reused PID,
greeter replacement and unexpected engine restart all refuse mutation.

## Compatibility and distribution

Expose an additive `remote_login_protocols` capability object with `boot_display`,
`greeter_on_demand`, and `idle_display` integer values, plus a root-installed JSON
manifest for non-privileged readiness checks. Keep the existing D-Bus API version 1
and old capability fields. Verify manifest against installed module constants at
enrollment; a manifest alone is not authorization or proof of working behavior.

App readiness distinguishes missing service assets, old host support, incomplete
host installation, incompatible Sunshine, and unsupported/untested display manager.
Ordinary desktop streaming remains available when remote login is unavailable.

Native app packages include app assets. Native host packages include host Python,
matching executable copies, bootstrap, D-Bus/Polkit files and DKMS sources. The app
keeps VKMS an optional versioned dependency (`>=0.3.0`) because other streaming
backends do not need it; enrollment always enforces all three runtime protocols.
No package install/upgrade enables remote login automatically.

Source staging validates all prerequisites before creating output. On an unmanaged
source-installed host it can update host Python transactionally. On a package-owned
host it must require a package upgrade rather than overwrite package-owned files.
A portable binary uses the same companion native host/app-service packages; this
plan does not add an arbitrary self-elevating portable installer.

## Acceptance

Unit/integration checks must cover all transitions, failure rollback, stale ownership,
ordinary create/remove, package ownership and source deployment. Actual acceptance
requires physical-screen GNOME/GDM boot → greeter connect → login → reconnect →
idle disconnect → reconnect → logout, followed by a separate headless run. Record
versions, modes, output identity and service health without identity secrets.
GDM failing to start with zero outputs is an explicit unsupported result requiring
a follow-up design; do not silently introduce an always-connected fallback.

Preserve pairing and enrollment during upgrade/rollback. Reject downgrade/removal
while enabled enrollment or active protected ownership depends on the host APIs;
disable and drain first. Preserve the current fixed-mode and second-instance
limitations in user-visible support claims until separately validated.
