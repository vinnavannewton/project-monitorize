# Arch Linux x86_64 package

Build from a clean, committed checkout with initialized recursive submodules.
The builder uses rootless Podman and a current Arch container. It does not
install packages on the host.

```bash
git submodule update --init --recursive
./packaging/arch/build.sh
```

The result is `dist/arch/x86_64/monitorize-<version>-archlinux-x86_64.pkg.tar.zst`.
`dist/arch/build.log` and `dist/arch/build-manifest.txt` record the successful
build. A failed attempt writes `dist/arch/failed-build.log` and leaves the last
successful package in place.

The first normal build prepares an Arch dependency image, caches pacman
packages, checksummed FFmpeg and Boost archives, and npm packages. It compiles
the Python wheel, KDE helper, and bundled Sunshine with required CUDA support,
then installs and removes the package in a fresh Arch container. Compilation
uses all detected processors by default; set `MONITORIZE_BUILD_JOBS` to a
positive integer to change that.
The build uses the host UID/GID inside the container. If Arch already has an
account at that UID, preparation reuses it and puts build-home files under
`/work/home`.

Use `./packaging/arch/build.sh --no-cuda` to omit the CUDA toolkit and build
Sunshine with CUDA disabled. This variant writes to `dist/arch/no-cuda/` and
uses a separate prepared dependency image. VA-API and Vulkan remain enabled.
NVENC can still use RAM conversion and upload without Sunshine's compiled CUDA
kernels; the guest or host still needs compatible NVIDIA driver libraries.
Its offline form is `./packaging/arch/build.sh --no-cuda --rebuild-offline`;
run the normal non-CUDA build once to prepare that variant's image and caches.

For later source changes, reuse the prepared image and caches without any
container network access:

```bash
./packaging/arch/build.sh --rebuild-offline
```

Offline mode still recompiles the source. It fails if the prepared image or a
cached dependency is missing, and it skips the fresh install test because
that test updates an Arch container. A normal build refreshes the prepared
environment. The builder checks source pins and uses committed `HEAD`, so
commit any intended code changes before running it.

To test in an updated Arch VM with GPU passthrough, copy the built package in
and install it with `sudo pacman -U monitorize-*.pkg.tar.zst`. Check desktop
launch, first-run setup, Moonlight pairing, display capture, NVIDIA encoding,
audio/input, upgrade, and removal. Keep Arch fully updated before installing;
rolling Python and library updates can require a package rebuild. The CUDA
toolkit is needed by the build container; the installed desktop still needs
the appropriate GPU driver.

This recipe builds the local `monitorize` package from source. The later AUR
`monitorize-bin` recipe will download already compiled release files.

VKMS is optional in the native binary. Install the separate `monitorize-vkms`
package and complete its kernel-module setup to use either preset or custom VKMS
resolutions; stock `vkms` alone is no longer supported. The Monitorize package
does not bundle or load the module. Compositor mode works without it.
If upgrading from stock VKMS left an output active, disable it using your
desktop display settings; the new cleanup action does not manage stock outputs.

The app uses the host VKMS D-Bus service for up to two independently configured displays. Upgrade the host package and reboot after an older single-display installation.

For **Also start at boot (login screen)**, this package includes the system service
bundle and privileged setup helper. Install compatible monitorize-vkms host support,
select **Start Monitorize after login**, then enable the boot option and authorize
setup. If the checkbox is disabled, expand its options to see the exact prerequisite
and use **Refresh startup status** after repairing it. Missing Monitorize assets
require a package reinstall/upgrade; source staging scripts are not needed.
See [remote-login setup](../remote-login/README.md#current-installation-workflow).

Package checks run the host compatibility and source deployment regression tests,
including the private helper's copy of the compatibility checker. The fresh-install
smoke test also verifies that both packaged checkers match and accept VKMS `1.0`
as well as `1.0.0`. These checks do not replace a live login-screen streaming test.
