# Fedora RPM build

## Release versions for distro packages

`pyproject.toml` is the canonical Monitorize version. Normal development does
not require version changes. When a release is ready, run
`./scripts/bump-version.py 0.33.1`, review `git diff`, then commit normally.
For another patch use `./scripts/bump-version.py 0.33.2`; for the next larger
release use `./scripts/bump-version.py 0.34`. The command updates Arch, Fedora,
Tumbleweed, all three DEB targets, and the main Nix package version, including
current RPM and DEB changelog entries. Nix's separate Sunshine version is not
the Monitorize release version and is left unchanged.

After the release commit reaches `main`, create and push a desktop tag (MAJOR.MINOR or MAJOR.MINOR.PATCH):

```bash
git tag monitorize-v0.34
git push origin monitorize-v0.34
```

The desktop release workflow requires the tag to match `pyproject.toml` and to
point to a commit on `main`. It builds the CUDA package for Arch, Fedora 44,
Tumbleweed, Ubuntu 24.04, and Ubuntu 26.04. Debian 13 remains available locally
but is temporarily disabled in CD. GitHub publishes the
release only after every package build and smoke test succeeds. The release
uploads the five enabled installable packages; build logs, source RPMs, caches, and debug
packages stay out of the release. GitHub also displays its automatic source
code archive links on every release.

The shared names and CD target switches live in `scripts/package-assets.py`.
Set Debian's `cd_enabled=True` to re-enable it; the matrix and expected asset
manifest expand automatically. Packages upload to a draft and are verified
before publication.

The Fedora binary is named `monitorize-<version>-fedora-44-x86_64.rpm`.
Installed package identity remains `monitorize`; debug and source RPMs retain
native filenames. Non-CUDA variants use the same download names in their
separate `no-cuda` output directories.

## Building

Run `./packaging/rpm/build.sh` from a clean, committed checkout with initialized
recursive submodules. The default builds Sunshine with CUDA support.

Use `./packaging/rpm/build.sh --no-cuda` to skip the CUDA installer and build
Sunshine without CUDA. Its packages, source RPM, and logs go under
`dist/rpm/fedora-44/no-cuda/`. The prepared dependency image is separate from
the CUDA build. After a normal non-CUDA build, use
`./packaging/rpm/build.sh --no-cuda --rebuild-offline` for an offline rebuild.
VA-API and Vulkan remain enabled.

Compilation uses all detected processors by default. Set
`MONITORIZE_BUILD_JOBS` to a positive integer to choose another job count.

VKMS is optional: install `monitorize-vkms` separately and complete its
kernel-module setup for preset or custom VKMS resolutions. Stock `vkms` alone
is no longer supported; compositor mode works without the helper.
Legacy stock VKMS outputs must be disabled using desktop display settings.

The app uses the host VKMS D-Bus service for up to two independently configured displays. Upgrade the host package and reboot after an older single-display installation.
