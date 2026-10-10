#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

usage() {
    echo "Usage: $0 --target {ubuntu-24.04|debian-trixie|ubuntu-26.04} [--no-cuda] [--rebuild-offline]" >&2
}
die() {
    echo "Error: $*" >&2
    exit 1
}

target=""
offline=0
enable_cuda=1
while (( $# )); do
    case "$1" in
        --target)
            (( $# >= 2 )) || { usage; exit 2; }
            target="$2"
            shift 2
            ;;
        --rebuild-offline)
            offline=1
            shift
            ;;
        --no-cuda)
            enable_cuda=0
            shift
            ;;
        --help) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
done
case "${target}" in
    ubuntu-24.04|debian-trixie|ubuntu-26.04) ;;
    *) usage; exit 2 ;;
esac

source "${SCRIPT_DIR}/common/sources.conf"
source "${SCRIPT_DIR}/${target}/target.conf"
readonly PACKAGE_DIR="${SCRIPT_DIR}/${target}"
output_root="${PROJECT_ROOT}/dist/deb/${target}"
if (( ! enable_cuda )); then output_root="${output_root}/no-cuda"; fi
readonly OUTPUT_ROOT="${output_root}"
readonly SUNSHINE_MK="${PACKAGE_DIR}/sunshine.mk"
normal_command="./packaging/deb/${target}/build.sh"
if (( ! enable_cuda )); then normal_command+=' --no-cuda'; fi

for command in git podman tar gzip awk sed sha256sum; do
    command -v "${command}" >/dev/null 2>&1 || die "Missing command: ${command}"
done
[[ "$(uname -m)" == x86_64 ]] || die "Only x86_64 hosts are supported for now."

metadata_value() {
    awk -F ' = ' -v key="$1" '$1 == key {print $2; exit}' "${SUNSHINE_MK}"
}

cd "${PROJECT_ROOT}"
mapfile -t submodule_status < <(git submodule status --recursive)
(( ${#submodule_status[@]} > 0 )) || die "Initialize submodules with git submodule update --init --recursive."
submodule_paths=()
for line in "${submodule_status[@]}"; do
    [[ "${line:0:1}" == ' ' ]] || die "Submodule is not at its pinned commit: ${line}"
    read -r _sha path _description <<< "${line:1}"
    [[ -n "${path}" ]] || die "Could not parse submodule status: ${line}"
    [[ -z "$(git -C "${path}" status --porcelain)" ]] || die "Submodule has local changes: ${path}"
    submodule_paths+=("${path}")
done
[[ -z "$(git status --porcelain)" ]] || die "Commit or stash local changes before building; the source archive uses HEAD."

version="$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml | head -n 1)"
debian_version="$(sed -n 's/^monitorize (\([^)]*\)).*/\1/p' "${PACKAGE_DIR}/debian/changelog" | head -n 1)"
[[ -n "${version}" && "${version}" == "${debian_version}" ]] || die "Project and DEB versions differ."
sunshine_commit="$(metadata_value SUNSHINE_COMMIT)"
[[ "${sunshine_commit}" == "$(git -C external/sunshine rev-parse HEAD)" ]] || die "Sunshine pin differs from the submodule."
ffmpeg_tag="$(metadata_value SUNSHINE_FFMPEG_TAG)"
ffmpeg_sha="$(metadata_value SUNSHINE_FFMPEG_SHA256)"
[[ -n "${ffmpeg_tag}" && -n "${ffmpeg_sha}" ]] || die "Missing FFmpeg source metadata."
[[ "${ffmpeg_tag}" == "$(git -C external/sunshine/third-party/build-deps describe --tags --exact-match 2>/dev/null || true)" ]] || die "FFmpeg tag differs from build-deps."

cpu_count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
[[ "${cpu_count}" =~ ^[1-9][0-9]*$ ]] || cpu_count=1
build_jobs="${MONITORIZE_BUILD_JOBS:-${cpu_count}}"
[[ "${build_jobs}" =~ ^[1-9][0-9]*$ ]] || die "MONITORIZE_BUILD_JOBS must be a positive integer."

dependency_hash="$( { printf 'cuda=%s\n' "${enable_cuda}"; sha256sum "${PACKAGE_DIR}/debian/control" "${PACKAGE_DIR}/target.conf"; } | sha256sum | awk '{print substr($1,1,16)}')"
deps_image="localhost/monitorize-deb-builddeps:${target}-${dependency_hash}"
if (( offline )); then
    podman image exists "${deps_image}" || die "Missing prepared image ${deps_image}; run ${normal_command} first."
fi

mkdir -p "${OUTPUT_ROOT}/cache/sources" "${OUTPUT_ROOT}/cache/npm" "${OUTPUT_ROOT}/amd64"
tmp_root="$(mktemp -d "${OUTPUT_ROOT}/.build.XXXXXX")"
build_log="${tmp_root}/build.log"
deps_container=""
cleanup() {
    local status=$?
    if [[ -n "${deps_container}" ]]; then
        podman rm -f "${deps_container}" >/dev/null 2>&1 || true
    fi
    if (( status != 0 )) && [[ -f "${build_log}" ]]; then
        cp "${build_log}" "${OUTPUT_ROOT}/failed-build.log" || true
    fi
    podman unshare chmod -R u+rwX "${tmp_root}" 2>/dev/null || true
    podman unshare rm -rf "${tmp_root}" 2>/dev/null || rm -rf "${tmp_root}"
}
trap cleanup EXIT

source_name="monitorize-${version}"
stage_root="${tmp_root}/stage/${source_name}"
work_root="${tmp_root}/work"
artifact_root="${tmp_root}/artifacts"
mkdir -p "${stage_root}" "${work_root}" "${artifact_root}/amd64"
git archive --format=tar HEAD | tar -xf - -C "${stage_root}"
for path in "${submodule_paths[@]}"; do
    mkdir -p "${stage_root}/${path}"
    git -C "${path}" archive --format=tar HEAD | tar -xf - -C "${stage_root}/${path}"
done
cp -a "${PACKAGE_DIR}/debian" "${stage_root}/debian"
source_date_epoch="$(git show -s --format=%ct HEAD)"
tar --sort=name --mtime="@${source_date_epoch}" --owner=0 --group=0 --numeric-owner \
    -C "${tmp_root}/stage" -cf - "${source_name}" | gzip -n > "${work_root}/${source_name}.tar.gz"

if (( ! offline )); then
    deps_container="monitorize-deb-${target}-deps-$$"
    podman run --name "${deps_container}" --arch amd64 --security-opt label=disable \
        --env DEBIAN_FRONTEND=noninteractive \
        --env "MONITORIZE_DEB_TARGET=${target}" \
        --env "MONITORIZE_ENABLE_CUDA=${enable_cuda}" "${IMAGE}" bash -euxo pipefail -c '
            apt-get update
            cuda_deps=()
            if [[ "${MONITORIZE_ENABLE_CUDA}" == 1 ]]; then
                cuda_deps=(aria2 cpio gcc-14 g++-14 rpm2cpio)
            fi
            qt_deps=(qt6-wayland)
            if [[ "${MONITORIZE_DEB_TARGET}" == ubuntu-24.04 ]]; then
                qt_deps+=(qml6-module-qtquick-templates qml6-module-qtquick-window libqt6svg6)
            else
                qt_deps+=(qt6-svg-plugins)
            fi
            apt-get install -y --no-install-recommends \
                build-essential cmake curl debhelper desktop-file-utils \
                dh-python dpkg-dev fakeroot git glslang-tools \
                libboost-filesystem-dev libboost-locale-dev libboost-log-dev \
                libboost-program-options-dev libcap-dev libcap2-bin libcurl4-openssl-dev \
                libdrm-dev libevdev-dev libgbm-dev libglib2.0-dev libminiupnpc-dev \
                libnuma-dev libopus-dev libpipewire-0.3-dev libpulse-dev libssl-dev \
                libva-dev libvdpau-dev libvulkan-dev libwayland-dev libx11-dev \
                libxcb1-dev libxcb-shm0-dev libxcb-xfixes0-dev libxcursor-dev \
                libxfixes-dev libxi-dev libxinerama-dev libxrandr-dev libxtst-dev \
                make nlohmann-json3-dev npm patch pkg-config python3-all \
                python3-cairo python3-dbus python3-dev python3-gi python3-jinja2 \
                python3-pyqt6 python3-pyqt6.qtquick pybuild-plugin-pyproject \
                python3-setuptools python3-wheel qml6-module-qtquick \
                qml6-module-qtquick-controls qml6-module-qtquick-layouts \
                qml6-module-qtqml-workerscript \
                wayland-protocols xz-utils "${cuda_deps[@]}" "${qt_deps[@]}"
        ' 2>&1 | tee "${build_log}"
    podman commit "${deps_container}" "${deps_image}" >/dev/null
    podman rm "${deps_container}" >/dev/null
    deps_container=""
fi

echo "Building Monitorize ${version} for ${DISTRO_LABEL} AMD64 with ${build_jobs} job(s)…"
run_options=(--rm --pull=never --arch amd64 --security-opt label=disable)
if (( offline )); then
    run_options+=(--network=none --env MONITORIZE_OFFLINE=1 --env npm_config_offline=true)
fi
podman run "${run_options[@]}" \
    --env "MONITORIZE_BUILD_JOBS=${build_jobs}" \
    --env "MONITORIZE_ENABLE_CUDA=${enable_cuda}" \
    --env "SOURCE_DATE_EPOCH=${source_date_epoch}" \
    --env "MONITORIZE_VERSION=${version}" \
    --env "SUNSHINE_FFMPEG_TAG=${ffmpeg_tag}" \
    --env "SUNSHINE_FFMPEG_SHA256=${ffmpeg_sha}" \
    --env "CUDA_VERSION=${CUDA_VERSION}" --env "CUDA_BUILD=${CUDA_BUILD}" \
    --env "CUDA_SHA256=${CUDA_SHA256}" \
    --env "BOOST_VERSION=${BOOST_VERSION}" --env "BOOST_SHA256=${BOOST_SHA256}" \
    --env "NODE_VERSION=${NODE_VERSION}" --env "NODE_SHA256=${NODE_SHA256}" \
    --env "USE_PINNED_NODE=${USE_PINNED_NODE}" \
    --env "NEEDS_XML2_COMPAT=${NEEDS_XML2_COMPAT}" \
    --env "LIBXML2_COMPAT_URL=${LIBXML2_COMPAT_URL}" \
    --env "LIBXML2_COMPAT_SHA256=${LIBXML2_COMPAT_SHA256}" \
    --env npm_config_cache=/npm-cache \
    --env MONITORIZE_CUDA_ROOT=/work/cuda \
    --volume "${work_root}:/work" \
    --volume "${artifact_root}:/artifacts" \
    --volume "${OUTPUT_ROOT}/cache/sources:/source-cache" \
    --volume "${OUTPUT_ROOT}/cache/npm:/npm-cache" \
    "${deps_image}" bash -euxo pipefail -c '
        cache_source() {
            local url="$1" sha="$2" archive
            archive="/source-cache/$(basename "${url}")"
            if ! echo "${sha}  ${archive}" | sha256sum --check --strict --status >/dev/null; then
                if [[ "${MONITORIZE_OFFLINE:-0}" == 1 ]]; then
                    echo "Missing cached source: ${archive}. Run a normal build first." >&2
                    exit 1
                fi
                curl --fail --location --retry 3 --output "${archive}.part" "${url}"
                echo "${sha}  ${archive}.part" | sha256sum --check --strict --status >/dev/null
                mv "${archive}.part" "${archive}"
            fi
            echo "${archive}"
        }
        tar -xzf /work/monitorize-*.tar.gz -C /work
        cd "/work/monitorize-${MONITORIZE_VERSION}"
        ffmpeg_archive="$(cache_source "https://github.com/LizardByte/build-deps/releases/download/${SUNSHINE_FFMPEG_TAG}/Linux-x86_64-ffmpeg.tar.gz" "${SUNSHINE_FFMPEG_SHA256}")"
        mkdir .ffmpeg-prepared
        tar -xzf "${ffmpeg_archive}" -C .ffmpeg-prepared --strip-components=1 --no-same-owner
        boost_archive="$(cache_source "https://github.com/boostorg/boost/releases/download/boost-${BOOST_VERSION}/boost-${BOOST_VERSION}-cmake.tar.xz" "${BOOST_SHA256}")"
        mkdir .boost-prepared
        tar -xJf "${boost_archive}" -C .boost-prepared --strip-components=1 --no-same-owner
        sed -i "s/find_package(Boost CONFIG \${BOOST_VERSION} EXACT /find_package(Boost CONFIG \${BOOST_VERSION} /" external/sunshine/cmake/dependencies/Boost_Sunshine.cmake

        if [[ "${USE_PINNED_NODE}" == 1 ]]; then
            node_archive="$(cache_source "https://nodejs.org/download/release/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" "${NODE_SHA256}")"
            mkdir -p /work/node
            tar -xJf "${node_archive}" -C /work/node --strip-components=1 --no-same-owner
            export PATH="/work/node/bin:${PATH}"
        fi
        node --version
        node -e '\''const [major,minor]=process.versions.node.split(".").map(Number); if (!(major>20 || major===20 && minor>=19)) process.exit(1)'\''

        if [[ "${MONITORIZE_ENABLE_CUDA}" == 1 ]]; then
        cuda_url="https://developer.download.nvidia.com/compute/cuda/${CUDA_VERSION}/local_installers/cuda_${CUDA_VERSION}_${CUDA_BUILD}_linux.run"
        cuda_archive="/source-cache/$(basename "${cuda_url}")"
        if ! echo "${CUDA_SHA256}  ${cuda_archive}" | sha256sum --check --strict --status; then
            if [[ "${MONITORIZE_OFFLINE:-0}" == 1 ]]; then
                echo "Missing cached CUDA installer: ${cuda_archive}. Run a normal build first." >&2
                exit 1
            fi
            aria2c --continue=true --max-connection-per-server=8 --split=8 --min-split-size=1M \
                --file-allocation=none --max-tries=3 --retry-wait=5 \
                --dir=/source-cache --out="$(basename "${cuda_archive}").part" "${cuda_url}"
            echo "${CUDA_SHA256}  ${cuda_archive}.part" | sha256sum --check --strict
            mv "${cuda_archive}.part" "${cuda_archive}"
        fi
        if [[ "${NEEDS_XML2_COMPAT}" == 1 ]]; then
            compat_archive="$(cache_source "${LIBXML2_COMPAT_URL}" "${LIBXML2_COMPAT_SHA256}")"
            mkdir -p /work/cuda-installer-compat
            cd /work/cuda-installer-compat
            rpm2cpio "${compat_archive}" | cpio -idm --quiet '\''./usr/lib64/libxml2.so.2*'\''
            test -e usr/lib64/libxml2.so.2
            export LD_LIBRARY_PATH="/work/cuda-installer-compat/usr/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
            cd "/work/monitorize-${MONITORIZE_VERSION}"
        fi
        bash "${cuda_archive}" --silent --toolkit --toolkitpath=/work/cuda \
            --no-drm --no-man-page --no-opengl-libs --override
        unset LD_LIBRARY_PATH
        patch -p2 --directory=/work/cuda < external/sunshine/packaging/linux/patches/x86_64/cuda-13-math_functions.patch
        patch -p1 --directory=/work/cuda < packaging/common/cuda-13-iec-60559-noexcept.patch
        test -x /work/cuda/bin/nvcc
        fi

        export DEBIAN_FRONTEND=noninteractive
        dpkg-buildpackage -b -us -uc
        cp /work/monitorize_*.deb /artifacts/amd64/
    ' 2>&1 | tee -a "${build_log}"

mapfile -t main_debs < <(find "${artifact_root}/amd64" -maxdepth 1 -type f -name "monitorize_${version}_amd64.deb" | sort)
(( ${#main_debs[@]} == 1 )) || die "Expected one primary ${target} DEB, found ${#main_debs[@]}."
main_deb="${main_debs[0]}"
if (( ! offline )); then
    echo "Smoke-testing $(basename "${main_deb}") in fresh ${DISTRO_LABEL}…"
    podman run --rm --arch amd64 --security-opt label=disable \
        --env DEBIAN_FRONTEND=noninteractive \
        --volume "${main_deb}:/tmp/monitorize.deb:ro" "${IMAGE}" \
        bash -euxo pipefail -c '
            apt-get update
            apt-get install -y --no-install-recommends /tmp/monitorize.deb desktop-file-utils
            dpkg -V monitorize
            getent group monitorize-input
            getcap /usr/libexec/monitorize/sunshine | grep -q cap_sys_admin
            test -x /usr/bin/monitorize
            test -x /usr/bin/monitorize-kde-virtual-output
            test -x /usr/libexec/monitorize/sunshine
            test -d /usr/share/monitorize/sunshine/assets/web
            desktop-file-validate /usr/share/applications/monitorize.desktop
            desktop-file-validate /usr/share/applications/monitorize-kde-virtual-output.desktop
            /usr/libexec/monitorize/sunshine --version
            QT_QPA_PLATFORM=offscreen python3 - <<'\''PYTHON'\''
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine
from monitorize.platform.utils import QML_DIR

app = QGuiApplication([])
engine = QQmlEngine()
component = QQmlComponent(engine, QUrl.fromLocalFile(f"{QML_DIR}/main.qml"))
assert not component.isError(), "\n".join(error.toString() for error in component.errors())
PYTHON
            apt-get purge -y monitorize
            test ! -e /usr/bin/monitorize
            test ! -e /usr/libexec/monitorize/sunshine
        ' 2>&1 | tee -a "${build_log}"
fi

cp "${artifact_root}/amd64/"*.deb "${OUTPUT_ROOT}/amd64/"
cp "${build_log}" "${OUTPUT_ROOT}/build.log"
printf 'source_commit=%s\ncuda_enabled=%s\n' "$(git rev-parse HEAD)" "${enable_cuda}" \
    > "${OUTPUT_ROOT}/build-manifest.txt"
echo "Completed ${DISTRO_LABEL} AMD64 DEB: ${OUTPUT_ROOT}/amd64/$(basename "${main_deb}")"
