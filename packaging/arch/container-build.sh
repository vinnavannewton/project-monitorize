#!/usr/bin/env bash
set -euo pipefail

die() { echo "Error: $*" >&2; exit 1; }

[[ $# == 0 ]] || die 'Build container does not accept arguments.'

[[ "$(id -u)" != 0 ]] || die 'makepkg must run as the unprivileged build user.'
[[ "$(id -u)" == "${MONITORIZE_BUILD_UID:?}" ]] || die 'Unexpected build UID.'
if [[ "${MONITORIZE_ENABLE_CUDA:-1}" == 1 ]]; then
    [[ -x /opt/cuda/bin/nvcc ]] || die 'Missing cached CUDA toolkit; rerun the normal build.'
fi
[[ "${HOME:-}" == /work/home ]] || die 'Build home must be /work/home.'
mkdir -p "${HOME}"

cache_source() {
    local url="$1" sha="$2" name="$3" archive
    archive="/source-cache/${name}"
    if ! printf '%s  %s\n' "${sha}" "${archive}" | sha256sum --check --strict --status; then
        [[ "${MONITORIZE_OFFLINE:-0}" != 1 ]] \
            || die "Missing or invalid cached ${name}; run ./packaging/arch/build.sh first."
        curl --fail --location --retry 3 --output "${archive}.part" "${url}"
        printf '%s  %s\n' "${sha}" "${archive}.part" | sha256sum --check --strict --status
        mv "${archive}.part" "${archive}"
    fi
    printf '%s\n' "${archive}"
}

ffmpeg_url="https://github.com/LizardByte/build-deps/releases/download/${SUNSHINE_FFMPEG_TAG}/Linux-x86_64-ffmpeg.tar.gz"
boost_url="https://github.com/boostorg/boost/releases/download/boost-${BOOST_VERSION}/boost-${BOOST_VERSION}-cmake.tar.xz"
ffmpeg_path="$(cache_source "${ffmpeg_url}" "${SUNSHINE_FFMPEG_SHA256}" "${MONITORIZE_FFMPEG_CACHE_NAME}")"
boost_path="$(cache_source "${boost_url}" "${BOOST_SHA256}" "${MONITORIZE_BOOST_CACHE_NAME}")"
cp "${ffmpeg_path}" /work/makepkg/Linux-x86_64-ffmpeg.tar.gz
cp "${boost_path}" "/work/makepkg/boost-${BOOST_VERSION}-cmake.tar.xz"

node --version
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (!(major === 20 && minor >= 19 || major === 22 && minor >= 12 || major > 22)) process.exit(1)'
if [[ "${MONITORIZE_ENABLE_CUDA:-1}" == 1 ]]; then
/opt/cuda/bin/nvcc --version

probe_root="$(mktemp -d /work/cuda-probe.XXXXXX)"
trap 'rm -rf "${probe_root}"' EXIT
cat > "${probe_root}/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.26)
project(monitorize_cuda_probe LANGUAGES CUDA CXX)
add_executable(monitorize_cuda_probe main.cu)
set_target_properties(monitorize_cuda_probe PROPERTIES CUDA_ARCHITECTURES 75)
CMAKE
cat > "${probe_root}/main.cu" <<'CUDA'
__global__ void monitorize_kernel() {}
int main() { monitorize_kernel<<<1, 1>>>(); return 0; }
CUDA

host_cc=''
host_cxx=''
for candidate in gcc gcc-15; do
    if [[ "${candidate}" == gcc ]]; then candidate_cxx=g++; else candidate_cxx=g++-15; fi
    command -v "${candidate}" >/dev/null 2>&1 || continue
    command -v "${candidate_cxx}" >/dev/null 2>&1 || continue
    echo "Probing CUDA with $(${candidate} --version | head -n 1)"
    if cmake -S "${probe_root}" -B "${probe_root}/build-${candidate}" \
        -DCMAKE_CUDA_COMPILER=/opt/cuda/bin/nvcc \
        -DCMAKE_CUDA_HOST_COMPILER="$(command -v "${candidate}")" \
        -DCMAKE_CUDA_FLAGS=-Xcompiler=-fPIC \
        -DCMAKE_CUDA_ARCHITECTURES=75 \
        && cmake --build "${probe_root}/build-${candidate}" --parallel 1; then
        host_cc="$(command -v "${candidate}")"
        host_cxx="$(command -v "${candidate_cxx}")"
        break
    fi
done
[[ -n "${host_cc}" ]] || die 'CUDA compile/link probe failed for system GCC and gcc15; inspect build.log.'
export MONITORIZE_CUDA_HOST_CC="${host_cc}"
export MONITORIZE_CUDA_HOST_CXX="${host_cxx}"
else
    host_cc="$(command -v gcc)"
    host_cxx="$(command -v g++)"
fi
export CC="${host_cc}" CXX="${host_cxx}"

cd /work/makepkg
makepkg --printsrcinfo > /artifacts/.SRCINFO
makepkg --force --clean
mapfile -t package_names < <(makepkg --packagelist)
[[ "${#package_names[@]}" == 1 ]] || die 'Expected one Arch package; disable unexpected debug packages.'
package_file="${package_names[0]}"
[[ -f "${package_file}" ]] || die "Missing makepkg output: ${package_file}"
cp "${package_file}" /artifacts/
namcap "/artifacts/$(basename "${package_file}")"

{
    printf 'source_commit=%s\n' "${MONITORIZE_SOURCE_COMMIT}"
    printf 'sunshine_commit=%s\n' "${MONITORIZE_SUNSHINE_COMMIT}"
    printf 'build_deps_image=%s\n' "${MONITORIZE_DEPS_IMAGE_ID}"
    printf 'source_sha256=%s\n' "${MONITORIZE_SOURCE_SHA256}"
    printf 'ffmpeg_sha256=%s\n' "${SUNSHINE_FFMPEG_SHA256}"
    printf 'boost_sha256=%s\n' "${BOOST_SHA256}"
    printf 'cuda_enabled=%s\n' "${MONITORIZE_ENABLE_CUDA:-1}"
    printf 'cuda_host_cc=%s\n' "${host_cc}"
    printf 'cuda_host_cxx=%s\n' "${host_cxx}"
    printf 'python_version=%s\n' "$(python --version)"
    printf 'node_version=%s\n' "$(node --version)"
    if [[ "${MONITORIZE_ENABLE_CUDA:-1}" == 1 ]]; then
        printf 'cuda_version=%s\n' "$(/opt/cuda/bin/nvcc --version | tail -n 1)"
        pacman -Q cuda gcc15
    fi
    pacman -Q gcc boost boost-libs nodejs npm python
} > /artifacts/build-manifest.txt
