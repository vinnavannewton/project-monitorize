# AMD64 DEB builds

Build each distribution in its own rootless Podman container from a clean,
committed checkout with initialized recursive submodules:

```bash
git submodule update --init --recursive
./packaging/deb/ubuntu-24.04/build.sh
./packaging/deb/debian-trixie/build.sh
./packaging/deb/ubuntu-26.04/build.sh
```

Each target writes packages to `dist/deb/<target>/amd64/` and a transcript to
`dist/deb/<target>/build.log`. A failed attempt leaves
`dist/deb/<target>/failed-build.log`. The normal build prepares a target-specific
dependency image, caches checksummed CUDA, Boost, FFmpeg, and (on Ubuntu 24.04)
Node archives, and runs a fresh-install/purge smoke test. It does not install
anything on the host.

Pass `--no-cuda` to any target's `build.sh` to skip the NVIDIA toolkit installer
and build Sunshine without CUDA. For example,
`./packaging/deb/ubuntu-24.04/build.sh --no-cuda` writes to
`dist/deb/ubuntu-24.04/no-cuda/`. Use `--no-cuda --rebuild-offline` after a
normal non-CUDA build; its prepared image is separate from the CUDA build.
VA-API and Vulkan remain enabled.

After one normal build has prepared that target's image and caches, repeat the
binary build without refreshing distribution repositories or downloading build
sources:

```bash
./packaging/deb/ubuntu-24.04/build.sh --rebuild-offline
./packaging/deb/debian-trixie/build.sh --rebuild-offline
./packaging/deb/ubuntu-26.04/build.sh --rebuild-offline
```

Offline mode uses `--network=none` and fails if a required cached source or
npm package is absent. A normal build is required after changing dependency
metadata. Set `MONITORIZE_BUILD_JOBS` to a positive integer to change the
default maximum of two compile jobs. These containers validate package
installation and basic runtime imports; run desktop, capture, and NVIDIA
encoding checks in a VM with GPU passthrough.

VKMS is optional in these native binaries. Install `monitorize-vkms` separately
and complete its kernel-module setup for preset or custom VKMS resolutions.
Stock `vkms` alone is no longer supported; compositor mode needs no VKMS helper.
Legacy stock VKMS outputs must be disabled using desktop display settings.
