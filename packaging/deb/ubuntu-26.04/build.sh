#!/usr/bin/env bash

set -euo pipefail

readonly UBUNTU_VERSION=26.04
readonly IMAGE="ubuntu:${UBUNTU_VERSION}"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly DEBIAN_DIR="${SCRIPT_DIR}/debian"
readonly SUNSHINE_MK="${SCRIPT_DIR}/sunshine.mk"
readonly OUTPUT_ROOT="${PROJECT_ROOT}/dist/deb/ubuntu-${UBUNTU_VERSION}"

die() {
    echo "Error: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

metadata_value() {
    awk -F ' = ' -v key="$1" '$1 == key { print $2; exit }' "${SUNSHINE_MK}"
}

for command in git podman tar gzip awk sed sha256sum tee; do
    require_command "${command}"
done

[[ "$(uname -m)" == "x86_64" ]] || die "The Ubuntu DEB builder currently supports x86_64 hosts only."

cd "${PROJECT_ROOT}"

mapfile -t submodule_status < <(git submodule status --recursive)
(( ${#submodule_status[@]} > 0 )) || die "No initialized submodules were found. Run: git submodule update --init --recursive"

submodule_paths=()
for line in "${submodule_status[@]}"; do
    marker="${line:0:1}"
    [[ "${marker}" == " " ]] || die "Submodule is uninitialized, conflicted, or not at its pinned commit: ${line}"
    read -r _sha path _description <<< "${line:1}"
    [[ -n "${path}" ]] || die "Could not parse submodule status: ${line}"
    [[ -z "$(git -C "${path}" status --porcelain)" ]] || die "Submodule has uncommitted or untracked content: ${path}"
    submodule_paths+=("${path}")
done

[[ -z "$(git status --porcelain)" ]] || die "The working tree must be clean. Commit or stash tracked and untracked changes before building."

version="$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml | head -n 1)"
debian_version="$(sed -n 's/^monitorize (\([^)]*\)).*/\1/p' "${DEBIAN_DIR}/changelog" | head -n 1)"
[[ -n "${version}" && "${version}" == "${debian_version}" ]] || \
    die "pyproject.toml version (${version:-missing}) does not match debian/changelog (${debian_version:-missing})."

sunshine_commit="$(metadata_value SUNSHINE_COMMIT)"
actual_sunshine_commit="$(git -C external/sunshine rev-parse HEAD)"
[[ "${sunshine_commit}" == "${actual_sunshine_commit}" ]] || \
    die "The DEB packaging Sunshine commit does not match the pinned submodule commit."

ffmpeg_tag="$(metadata_value SUNSHINE_FFMPEG_TAG)"
ffmpeg_sha="$(metadata_value SUNSHINE_FFMPEG_SHA256)"
[[ -n "${ffmpeg_tag}" && -n "${ffmpeg_sha}" ]] || die "Missing Sunshine FFmpeg metadata."
actual_ffmpeg_tag="$(git -C external/sunshine/third-party/build-deps describe --tags --exact-match 2>/dev/null || true)"
[[ "${ffmpeg_tag}" == "${actual_ffmpeg_tag}" ]] || \
    die "The DEB packaging FFmpeg tag (${ffmpeg_tag}) does not match build-deps (${actual_ffmpeg_tag:-untagged})."

cpu_count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
[[ "${cpu_count}" =~ ^[1-9][0-9]*$ ]] || cpu_count=1
default_jobs="${cpu_count}"
(( default_jobs > 4 )) && default_jobs=4
build_jobs="${MONITORIZE_BUILD_JOBS:-${default_jobs}}"
[[ "${build_jobs}" =~ ^[1-9][0-9]*$ ]] || die "MONITORIZE_BUILD_JOBS must be a positive integer."

mkdir -p "${OUTPUT_ROOT}/amd64"
tmp_root="$(mktemp -d "${OUTPUT_ROOT}/.build.XXXXXX")"
cleanup() {
    podman unshare chmod -R u+rwX "${tmp_root}" 2>/dev/null || true
    podman unshare rm -rf "${tmp_root}" 2>/dev/null || rm -rf "${tmp_root}"
}
trap cleanup EXIT

source_name="monitorize-${version}"
stage_root="${tmp_root}/stage/${source_name}"
work_root="${tmp_root}/work"
mkdir -p "${stage_root}" "${work_root}"

git archive --format=tar HEAD | tar -xf - -C "${stage_root}"
for path in "${submodule_paths[@]}"; do
    mkdir -p "${stage_root}/${path}"
    git -C "${path}" archive --format=tar HEAD | tar -xf - -C "${stage_root}/${path}"
done
cp -a "${DEBIAN_DIR}" "${stage_root}/debian"

source_date_epoch="$(git show -s --format=%ct HEAD)"
source_archive="${work_root}/${source_name}.tar.gz"
tar --sort=name --mtime="@${source_date_epoch}" --owner=0 --group=0 --numeric-owner \
    -C "${tmp_root}/stage" -cf - "${source_name}" | gzip -n > "${source_archive}"

find "${OUTPUT_ROOT}" -type f -name '*.deb' -delete
build_log="${OUTPUT_ROOT}/build.log"
echo "Building Monitorize ${version} for Ubuntu ${UBUNTU_VERSION} AMD64 with ${build_jobs} job(s)…"
podman run --rm \
    --arch amd64 \
    --security-opt label=disable \
    --env DEBIAN_FRONTEND=noninteractive \
    --env "MONITORIZE_BUILD_JOBS=${build_jobs}" \
    --env "SOURCE_DATE_EPOCH=${source_date_epoch}" \
    --env "SUNSHINE_FFMPEG_TAG=${ffmpeg_tag}" \
    --env "SUNSHINE_FFMPEG_SHA256=${ffmpeg_sha}" \
    --volume "${OUTPUT_ROOT}:/artifacts" \
    --volume "${work_root}:/work" \
    "${IMAGE}" \
    bash -euxo pipefail -c '
        apt-get update
        apt-get install -y --no-install-recommends \
            build-essential cmake curl debhelper dh-python dpkg-dev fakeroot \
            gcc-14 g++-14 git glslang-tools libboost-filesystem-dev \
            libboost-locale-dev libboost-log-dev libboost-program-options-dev \
            libcap-dev libcurl4-openssl-dev libdrm-dev libevdev-dev libgbm-dev \
            libglib2.0-dev libminiupnpc-dev libnuma-dev libopus-dev \
            libpipewire-0.3-dev libpulse-dev libssl-dev libva-dev libvdpau-dev \
            libvulkan-dev libwayland-dev libx11-dev libxcb1-dev libxcb-shm0-dev \
            libxcb-xfixes0-dev libxcursor-dev libxfixes-dev libxi-dev libxinerama-dev \
            libxrandr-dev libxtst-dev \
            make nlohmann-json3-dev npm pkg-config python3-all python3-cairo \
            python3-dbus python3-dev python3-gi python3-jinja2 python3-pyqt6 \
            python3-pyqt6.qtquick pybuild-plugin-pyproject python3-setuptools python3-wheel \
            wayland-protocols desktop-file-utils
        tar -xzf /work/monitorize-*.tar.gz -C /work
        source_dir="$(find /work -mindepth 1 -maxdepth 1 -type d -name "monitorize-*" -print -quit)"
        test -n "${source_dir}"
        cd "${source_dir}"
        ffmpeg_archive="/work/Linux-x86_64-ffmpeg.tar.gz"
        curl --fail --location --retry 3 --output "${ffmpeg_archive}" "https://github.com/LizardByte/build-deps/releases/download/${SUNSHINE_FFMPEG_TAG}/Linux-x86_64-ffmpeg.tar.gz"
        echo "${SUNSHINE_FFMPEG_SHA256}  ${ffmpeg_archive}" | sha256sum --check --strict
        mkdir .ffmpeg-prepared
        tar -xzf "${ffmpeg_archive}" -C .ffmpeg-prepared --strip-components=1 --no-same-owner
        sed -i "s/find_package(Boost CONFIG \${BOOST_VERSION} EXACT /find_package(Boost CONFIG \${BOOST_VERSION} /" external/sunshine/cmake/dependencies/Boost_Sunshine.cmake
        dpkg-buildpackage -b -us -uc
        cp /work/monitorize_*.deb /artifacts/amd64/
    ' 2>&1 | tee "${build_log}"

mapfile -t main_debs < <(find "${OUTPUT_ROOT}/amd64" -maxdepth 1 -type f \
    -name "monitorize_${version}_amd64.deb" | sort)
(( ${#main_debs[@]} == 1 )) || die "Expected exactly one primary Monitorize DEB, found ${#main_debs[@]}."
main_deb="${main_debs[0]}"

echo "Smoke-testing $(basename "${main_deb}") in a fresh Ubuntu ${UBUNTU_VERSION} container…"
podman run --rm \
    --arch amd64 \
    --security-opt label=disable \
    --env DEBIAN_FRONTEND=noninteractive \
    --volume "${main_deb}:/tmp/monitorize.deb:ro" \
    "${IMAGE}" \
    bash -euxo pipefail -c '
        apt-get update
        apt-get install -y --no-install-recommends /tmp/monitorize.deb desktop-file-utils
        test ! -e /root/.config/monitorize
        dpkg -V monitorize
        getent group monitorize-input
        test -x /usr/bin/monitorize
        test -x /usr/bin/monitorize-kde-virtual-output
        test -x /usr/libexec/monitorize/sunshine
        test -x /usr/libexec/monitorize/monitorize-system-setup
        test -d /usr/share/monitorize/sunshine/assets/web
        test -f /usr/lib/firewalld/services/monitorize.xml
        test -f /usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize.system-setup.policy
        test -f /etc/ufw/applications.d/monitorize
        test -f /usr/lib/udev/rules.d/70-monitorize-uinput.rules
        test -f /usr/lib/modules-load.d/monitorize.conf
        test ! -e /usr/bin/sunshine
        test ! -e /usr/local/bin/sunshine
        desktop-file-validate /usr/share/applications/monitorize.desktop
        desktop-file-validate /usr/share/applications/monitorize-kde-virtual-output.desktop
        /usr/libexec/monitorize/sunshine --version
        MONITORIZE_SUNSHINE_BIN=/usr/libexec/monitorize/sunshine \
        MONITORIZE_SUNSHINE_ASSETS_DIR=/usr/share/monitorize/sunshine/assets \
        python3 - <<"PYTHON"
from PyQt6.QtQuickWidgets import QQuickWidget
from monitorize.desktop import main_window
from monitorize.platform.sunshine_service import get_sunshine_assets_dir, get_sunshine_candidates

command = get_sunshine_candidates()[0]
assert command[0] == "/usr/libexec/monitorize/sunshine"
assert get_sunshine_assets_dir(command[0]) == "/usr/share/monitorize/sunshine/assets"
assert QQuickWidget is not None
assert main_window is not None
PYTHON
        apt-get purge -y monitorize
        test ! -e /usr/bin/monitorize
        test ! -e /usr/bin/monitorize-kde-virtual-output
        test ! -e /usr/libexec/monitorize/sunshine
        test ! -e /usr/libexec/monitorize/monitorize-system-setup
        test ! -e /usr/share/monitorize
        test ! -e /usr/share/applications/monitorize.desktop
    '

echo "Ubuntu ${UBUNTU_VERSION} AMD64 DEB build and smoke test completed."
echo "Primary DEB: ${main_deb}"
