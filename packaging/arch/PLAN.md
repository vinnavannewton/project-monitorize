# Arch Linux native package implementation plan

Status: source implementation completed on 1 October 2026. Static and isolated
preflight checks passed; an Arch container build and GPU test are pending.
This remains the implementation contract and review checklist.

## 1. Goal and boundaries

Implement these commands on the developer's existing x86_64 Linux workstation:

```bash
./packaging/arch/build.sh
./packaging/arch/build.sh --rebuild-offline
MONITORIZE_BUILD_JOBS=4 ./packaging/arch/build.sh --rebuild-offline
```

Produce a native `monitorize-<version>-<pkgrel>-x86_64.pkg.tar.zst` containing the
Python application, compiled bundled Sunshine fork, KDE helper, assets, and
system integration. The developer compiles once; package users do not compile.

Defaults: package name `monitorize`, `pkgrel=1` initially, x86_64 only, current
stable Arch repositories, rootless Podman, all detected processors for
compilation, required CUDA support, and unsigned local artifacts for initial VM
testing.

Do not implement AUR publication, a binary release tarball, signing infrastructure,
ARM, CI binary builds, or a hosted pacman repository in this phase. Do not refactor
the working RPM/DEB builders. Do not commit or push without an explicit request.
Do not run long container preparations, downloads, or compilation: the user runs
those. The implementing agent performs short source checks only.

## 2. Read these sources before editing

- `packaging/rpm/build.sh`: source snapshot, prepared image, cache and offline flow.
- `packaging/rpm/monitorize.spec`: current native build and installed-file contract.
- `packaging/deb/build.sh` and `packaging/deb/common/rules.mk`: additional packaging
  and fresh-install checks. Use their behavior as reference, not literal package names.
- `pyproject.toml`, `linux/native/kde_virtual_output/build.sh`, and
  `packaging/common/monitorize-system-setup`.
- Current Sunshine `cmake/dependencies/Boost_Sunshine.cmake`, CUDA configuration,
  and npm lockfile under `external/sunshine`.
- `.github/workflows/desktop.yml` and existing packaging tests for short checks.

At this audit the project version is `0.33`, Sunshine gitlink is
`addd8c2f7126eb04a1f87f3d7b59833beacd69af`, and FFmpeg tag is
`v2026.724.203728`. Re-read the checkout at implementation time; never restore an
older pin from this document. Copy the corresponding SHA256 from current native
packaging and verify all pins match initialized recursive submodules.

## 3. File layout and ownership

Add these files under `packaging/arch/`:

| File | Responsibility |
| --- | --- |
| `build.sh` | Host CLI, preflight, source archive, image preparation, caches, logs, publication of local artifacts |
| `PKGBUILD` | Arch metadata and `prepare()`, `build()`, `check()`, `package()` |
| `dependencies.sh` | Shell arrays shared by PKGBUILD and image preparation; build, runtime, validation dependencies |
| `sources.conf` | Sunshine/FFmpeg pins and checksummed external source metadata |
| `container-prepare.sh` | Full pacman transaction and unprivileged build-user creation |
| `container-build.sh` | Cached sources, CUDA compiler probe and unprivileged makepkg |
| `smoke-test.sh` | Fresh installed-package checks and removal checks |
| `monitorize.install` | Idempotent install/upgrade capability and uinput setup |
| `monitorize-wrapper` | Private Sunshine executable/assets environment and Python entry point |
| `README.md` | Commands, prerequisites, cache behavior, outputs, VM testing and known limitations |

Keep scripts short enough to review; avoid placing hundreds of lines inside a
single quoted `podman ... bash -c` command. Copy auxiliary files into the isolated
makepkg workspace so its recipe never depends on host absolute paths. No tracked
`.SRCINFO` is needed for the local source builder; generate it with makepkg in the
prepared container as a validation artifact. The future AUR recipe is separate.

Reuse existing desktop entries, icon, sysusers file, udev rule, firewalld service,
polkit policy/helper and UFW profile. Their current Fedora/DEB directory names do
not make their contents distro-specific. Avoid moving shared files in this task.

## 4. Dependency and toolchain decisions

Use official Arch packages for the compiler and CUDA toolkit in the build image.
Do not assume `/usr/bin/gcc-14` exists. Use `/opt/cuda/bin/nvcc` after verifying the
package's actual path, with an explicitly selected compatible host compiler.
Prefer the repository compiler; if it is unsupported, investigate the official
compatibility compiler before choosing a version. Fail with the observed versions
and a clear diagnostic rather than disabling CUDA or using
`--allow-unsupported-compiler`.

This differs intentionally from the RPM/DEB NVIDIA runfile setup. Pacman's package
cache and the prepared image cache the toolkit. Do not download a second CUDA
runfile, import an openSUSE compatibility RPM, or blindly apply the CUDA 13.1
header patch to a newer Arch toolkit. A minimal CUDA CMake compile/link probe,
including the host PIC flags, must pass before the expensive Sunshine build. This
probe needs no GPU. Any compatibility patch must be justified against the actual
toolkit and applied only to a version-matched staged copy.

Starting dependency mapping (verify package names and actual linkage during
implementation; this is not a final dependency closure):

- Python runtime: `python`, `python-pyqt6`, `qt6-declarative`, `python-jinja`,
  `python-dbus`, `python-gobject`, `python-cairo`.
- Native runtime candidates: `gcc-libs`, `glibc`, `libcap`, `curl`, `openssl`,
  `libdrm`, `libevdev`, `libva`, `libvdpau`, `libpulse`, `pipewire`, `libx11`,
  `libxcb`, `libxcursor`, `libxfixes`, `libxi`, `libxinerama`, `libxrandr`,
  `libxtst`, `wayland`, `mesa`, `vulkan-icd-loader`, `numactl`, `opus`,
  `miniupnpc`, `glib2` and any further libraries required by the current fork.
- System integration: `polkit`, `systemd`, `shadow`, `kmod`, `iproute2`,
  `libva-utils`, `xdg-desktop-portal`, and Avahi if required by current native behavior.
- Build tools: `base-devel`, `cmake`, `git`, `pkgconf`, `cuda`, `nodejs`, `npm`,
  `python-build`, `python-installer`, `python-setuptools`, `python-wheel`,
  `python-jinja`, `wayland-protocols`, `nlohmann-json`, and shader tooling required
  by the actual CMake checks (`glslc`/`glslangValidator` provider packages).
- Validation tools: `namcap`, `desktop-file-utils`, `file`, and `binutils`.
- Optional runtime integration: `firewalld` or `ufw`, appropriate desktop portal
  backend, and GPU driver/VA-API/Vulkan providers. Do not force NVIDIA drivers on
  AMD/Intel users or install/enable a firewall automatically.

Use normal Arch Node/npm if their versions satisfy the current lockfile engines.
For Boost, use the same checksummed 1.89 source fallback mechanism as the native
builders, with `FETCHCONTENT_SOURCE_DIR_BOOST` pointing to an extracted cache.
Prefer a compatible system Boost when available; if using the source fallback,
build it statically and inspect the resulting ELF to ensure no private shared
Boost library was omitted. Declare system Boost runtime dependencies if linked
dynamically. Do not let CMake fetch uncached dependencies unexpectedly.

Verify every direct and dynamically loaded dependency; `namcap` alone cannot infer
all Python imports, Qt plugins or GPU libraries. The CUDA toolkit is build-only
unless the final ELF proves a runtime component is required. Users still need the
appropriate working GPU driver. Do not claim a universal NVIDIA generation support
matrix without testing the chosen toolkit and hardware.

## 5. Host script and source snapshot

1. Parse `--rebuild-offline` and `--help`; reject unknown arguments with exit 2.
2. Require x86_64, Git, rootless Podman and archive/checksum utilities. Validate
   `MONITORIZE_BUILD_JOBS` as a positive integer; default to the detected CPU
   count.
3. Require committed tracked source changes and initialized, clean, correctly
   pinned recursive submodules. Ignore unrelated untracked host files as the RPM
   builder does; they are not included in the archive. Explain that new packaging
   files must be committed before a real build, but do not commit them automatically.
4. Read the project version and validate PKGBUILD/source metadata against it.
5. Archive HEAD and every recursive submodule into a temporary source tree; never
   patch or build directly inside the developer checkout.
6. Create a deterministic tarball using the commit time as `SOURCE_DATE_EPOCH`,
   stable ordering, numeric ownership and deterministic compression. Calculate its
   checksum and inject it into the staged recipe rather than using `SKIP`.
7. Use a trap to preserve failure logs, remove only this invocation's temporary
   containers/files, and preserve caches and previous successful packages.

## 6. Normal and offline environments

Normal mode prepares a container from a fully qualified official Arch image such
as `docker.io/library/archlinux:base`. Perform a full `pacman -Syu` transaction
together with dependency installation; never implement an unsupported partial
upgrade using a standalone database refresh followed by selected upgrades.

Mount a persistent pacman package cache during preparation. Install dependencies
as container root, create an ordinary build user, and commit a dependency image
only after preparation succeeds. Compile with makepkg as that ordinary user.
Rootless Podman's container root is still UID 0 to makepkg and is not sufficient.
Handle writable source/npm/output directory ownership explicitly; do not grant
world-writable permissions or modify host source ownership.

Derive the prepared-image key from dependency arrays, image/toolchain settings,
Python build requirements and the preparation script. Do not include ordinary
application source changes or the Sunshine commit in that key. Record resolved
image ID, installed package versions, compiler/CUDA/Python versions, source commits
and external hashes in a build manifest. Offline mode reuses that environment;
it does not promise that old Arch dependencies remain compatible with a newly
updated end-user system.

`--rebuild-offline` must:

- Check that the required prepared image and cached sources exist before compiling.
- Use `--pull=never --network=none` on every container it starts.
- Never refresh pacman repositories, install missing packages, initialize/fetch
  submodules or fall back to an online build.
- Use cached, checksum-validated FFmpeg/Boost and npm packages with npm offline
  mode. Keep source caches keyed by version or checksum so releases cannot collide.
- Fail clearly for a missing/incompatible image, archive or npm cache entry, with
  the normal build command to populate it. Check what can be checked up front;
  npm may report individual missing entries during its offline install.
- Recompile/package fresh sources using existing dependencies. It is not an
  incremental-object build or a promise to skip compilation.

Use atomic `.part` downloads/checksum verification/rename for external sources.
Keep npm cache separate from source archives; do not cache node_modules as the
offline contract. A failed build can still leave useful prepared dependencies.

## 7. PKGBUILD implementation

Use `pkgname=monitorize`, `arch=('x86_64')`, the project version and license, and
the existing project URL. Do not set `epoch` or `replaces` without a migration
reason. This local source-building recipe is not the eventual AUR `-bin` recipe.

`prepare()`: extract the staged source/archive inputs, verify their layout, apply
`packaging/sunshine-strict-selection.patch` exactly once, and configure prepared
FFmpeg/Boost paths. Check patch applicability against the pinned fork. Do not
reintroduce the obsolete portal-token patch.

`build()`:

1. Build the Python wheel with `python -m build --wheel --no-isolation`.
2. Build the KDE helper using its existing script, passing Arch compiler and
   hardening flags through the script's supported `CFLAGS_EXTRA`/`LDFLAGS` inputs.
3. Configure and build Sunshine using current native recipe options: required CUDA,
   DRM, KWin, portal, VA-API, Vulkan, Wayland and X11 enabled; docs, upstream tests
   and tray disabled; GLAD pip installation disabled; private executable/assets
   paths. Pass the current commit through the same version environment variables.
4. Use prepared FFmpeg and npm cache, explicit CUDA host compiler and PIC settings,
   and the requested job count. Preserve CMake failure status; do not mask errors
   with a subsequent successful command.

`check()`: validate desktop files, Python imports, bundled runtime path resolution,
helper/Sunshine existence and ELF linkage, web assets and Sunshine version. Add a
Qt offscreen component-load check with actionable QML errors. These checks do not
require a GPU, host D-Bus or a running compositor. Do not run an unrelated giant
test suite requiring desktop services during makepkg.

`package()`: install the wheel with `python -m installer --destdir="$pkgdir"`,
then install the wrapper over its generated entry point and stage the native
files manually. Never use a privileged source installer or an upstream install
target that adds unwanted global Sunshine services/files. Do not install build
caches, CUDA toolkit, node_modules, source trees or temporary paths into the package.

Record the Python major/minor used for site-packages. Set matching Python minor
bounds in generated package runtime metadata so a package installed into an old
Python directory does not silently become unusable after an Arch Python upgrade.
Document that such upgrades require a rebuild/pkgrel bump.

## 8. Installed-file and lifecycle contract

Preserve the existing application paths:

```text
/usr/bin/monitorize
/usr/bin/monitorize-kde-virtual-output
/usr/libexec/monitorize/sunshine
/usr/libexec/monitorize/monitorize-system-setup
/usr/share/monitorize/sunshine/assets/
/usr/share/applications/monitorize.desktop
/usr/share/applications/monitorize-kde-virtual-output.desktop
```

Also install Python modules/QML/assets, icon, licenses, polkit policy, firewalld
service, UFW profile, sysusers configuration for `monitorize-input`, udev rule,
and modules-load configuration for `uinput`. Verify Arch policy and pacman file
ownership for these paths; do not change application paths casually for this target.

Use Arch's existing systemd/pacman hooks for sysusers where appropriate; make group
creation reliably idempotent. The install script reapplies
`cap_sys_admin,cap_sys_nice+p` to private Sunshine after install and upgrade. Do
not depend on package archive xattrs preserving capabilities. Reload udev rules
and attempt uinput loading on installed systems, tolerating unavailable live
udev/kernel services in containers. Report real capability failures clearly.

Never add the installing user to groups, enable firewall ports/services, or modify
home directories automatically. Keep the existing explicit polkit setup flow.
Removal keeps user configuration and does not delete shared system groups or
unload kernel modules. Upgrade must preserve settings and restore capabilities.

## 9. Artifacts and validation

Use this output layout:

```text
dist/arch/x86_64/monitorize-<version>-<pkgrel>-x86_64.pkg.tar.zst
dist/arch/build.log
dist/arch/failed-build.log
dist/arch/build-manifest.txt
dist/arch/cache/pacman/
dist/arch/cache/sources/
dist/arch/cache/npm/
```

Build into temporary output first. Select the primary package using makepkg's
package list/metadata, not a glob that accidentally selects a debug package.
Publish successful artifacts atomically and keep the previous successful build
when a new build or smoke test fails. Handle optional debug packages explicitly.

Normal mode then starts a fresh runtime container without the source tree or
build caches. Fully update its packages and install the local artifact with
`pacman -U`, resolving only declared runtime dependencies. Validate:

- `pacman -Qi/-Ql`, missing files and required metadata; interpret `-Qkk` output
  with expected install-script capability changes rather than ignoring it wholesale.
- Required paths/group, absence of a global `/usr/bin/sunshine`, Python imports,
  QML component load, dynamic linkage, desktop entries and private asset resolution.
- Sunshine capabilities after installation and after reinstall/upgrade.
- Removal leaves no package-owned application files and preserves seeded user settings.
- `namcap` results: fix errors; document reasoned exceptions for warnings.

Container caveat: a binary carrying `cap_sys_admin` may fail to execute when that
capability is outside rootless Podman's bounding set. First verify installed
capabilities, then, if necessary, clear them only in the disposable test container
for the version/import checks. Never alter the released artifact or weaken the
installed desktop contract to make a container check pass.

Offline mode performs makepkg/package/source checks but skips fresh runtime
dependency installation. Clearly report the fresh-install smoke test as skipped;
do not claim it passed. Do not introduce networking to complete that test.

## 10. Short checks the implementing agent should run

- `bash -n` on the new scripts, PKGBUILD and install script; ShellCheck if available.
- `git diff --check`; verify executable modes and current source pins.
- Strict-selection patch dry-run against the current fork.
- Focused tests for CLI errors/help, job validation and source/version consistency.
- Mock Podman/download tools to verify offline mode never invokes image pulls,
  repository updates or network downloads, and that missing caches fail clearly.
- Test preservation of existing successful artifacts and failure-log publication
  using tiny fixtures; never trigger a real native build from a unit test.
- Validate makepkg metadata with `--printsrcinfo` only if an appropriate environment
  is already available. Otherwise leave it as a clearly named user-build check.

Extend the existing desktop source-check workflow only with short Arch static
checks. Do not make CI download CUDA or build Sunshine in this phase. Do not
claim static checks prove package installation, ELF compatibility or encoding.

## 11. User-run acceptance and handoff

After committing when explicitly authorized, give the user the exact normal
build command and expected output/log paths. The user then:

1. Runs the normal build and shares any failure log.
2. Runs `--rebuild-offline` and verifies no download/repository-refresh activity.
3. Copies the package to an updated Arch VM and installs it with `pacman -U`.
4. Checks launch, first-run setup, Moonlight pairing, selected desktop's virtual
   display/capture path, NVIDIA hardware encoding, audio/input, stop/restart,
   reinstall/upgrade, and removal with configuration preservation.

Use the user's existing NVIDIA GPU passthrough; do not require physical Arch
hardware for this first validation. An Arch VM test does not certify every Arch
derivative, compositor, GPU or driver combination.

Final implementation report must distinguish completed source work, short checks
actually run, and pending user builds/VM tests. No automatic Git commits, release
uploads or AUR submissions.

## 12. Later AUR phase

After the native package passes VM testing, export its compiled payload as a
versioned Arch binary archive and create a separate `monitorize-bin` PKGBUILD with
versioned `provides=('monitorize=...')` and `conflicts=('monitorize')`. That recipe
downloads and packages prebuilt files; it does not compile Sunshine. Preserve the
same runtime dependencies and install/upgrade behavior. Reuse compiled outputs;
another heavy compilation is not needed just to create the binary archive.

Before release, review Python minor paths, shared-library ABI requirements,
checksums and licenses against the exact exported payload. Publish artifacts and
AUR metadata only when the user explicitly requests that later phase.

## References checked for this plan

- [makepkg manual](https://man.archlinux.org/man/core/pacman/makepkg.8.en)
- [PKGBUILD manual](https://man.archlinux.org/man/PKGBUILD.5.en)
- [Arch Python packaging](https://wiki.archlinux.org/index.php/Python_package_guidelines)
- [Arch CUDA package](https://archlinux.org/packages/extra/x86_64/cuda/)
- [Arch PyQt6 package](https://archlinux.org/packages/extra/x86_64/python-pyqt6/)
- [Arch libxml2-legacy files](https://archlinux.org/packages/extra/x86_64/libxml2-legacy/files/)

Arch package versions change. These references support the design, not a frozen
promise that today's compiler/toolkit combination will remain available forever.
