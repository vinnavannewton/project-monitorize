#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
readonly IMAGE=docker.io/library/archlinux:base
output_root="${PROJECT_ROOT}/dist/arch"

usage() {
    echo "Usage: $0 [--no-cuda] [--rebuild-offline]" >&2
}
die() {
    echo "Error: $*" >&2
    exit 1
}

offline=0
enable_cuda=1
while (( $# )); do
    case "$1" in
        --rebuild-offline) offline=1; shift ;;
        --no-cuda) enable_cuda=0; shift ;;
        --help) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
done
if (( ! enable_cuda )); then output_root="${output_root}/no-cuda"; fi
readonly OUTPUT_ROOT="${output_root}"
normal_command="./packaging/arch/build.sh"
if (( ! enable_cuda )); then normal_command+=' --no-cuda'; fi

source "${SCRIPT_DIR}/sources.conf"
for utility in git podman tar gzip awk sed sha256sum python3; do
    command -v "${utility}" >/dev/null 2>&1 || die "Missing command: ${utility}"
done
[[ "$(uname -m)" == x86_64 ]] || die 'The Arch builder currently supports x86_64 only.'
[[ "$(podman info --format '{{.Host.Security.Rootless}}')" == true ]] \
    || die 'Run this builder with rootless Podman.'

cpu_count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
[[ "${cpu_count}" =~ ^[1-9][0-9]*$ ]] || cpu_count=1
(( cpu_count > 2 )) && cpu_count=2
build_jobs="${MONITORIZE_BUILD_JOBS:-${cpu_count}}"
[[ "${build_jobs}" =~ ^[1-9][0-9]*$ ]] \
    || die 'MONITORIZE_BUILD_JOBS must be a positive integer.'

cd "${PROJECT_ROOT}"
for required_file in PKGBUILD dependencies.sh sources.conf container-prepare.sh container-build.sh \
    smoke-test.sh monitorize.install monitorize-wrapper; do
    git ls-files --error-unmatch "packaging/arch/${required_file}" >/dev/null 2>&1 \
        || die "Commit packaging/arch/${required_file} before building; the source snapshot uses HEAD."
done
[[ -z "$(git status --porcelain --untracked-files=no)" ]] \
    || die 'Commit or stash tracked changes before building; the source snapshot uses HEAD.'

mapfile -t submodule_status < <(git submodule status --recursive)
(( ${#submodule_status[@]} > 0 )) || die 'Initialize recursive submodules before building.'
submodule_paths=()
for line in "${submodule_status[@]}"; do
    [[ "${line:0:1}" == ' ' ]] || die "Submodule is not at its pinned commit: ${line}"
    read -r _sha path _description <<< "${line:1}"
    [[ -n "${path}" ]] || die "Could not parse submodule status: ${line}"
    [[ -z "$(git -C "${path}" status --porcelain)" ]] \
        || die "Submodule has local changes: ${path}"
    submodule_paths+=("${path}")
done

version="$(sed -n 's/^version = "\([^"]*\)"/\1/p' pyproject.toml | head -n 1)"
pkgver="$(sed -n 's/^pkgver=//p' "${SCRIPT_DIR}/PKGBUILD" | head -n 1)"
pkgrel="$(sed -n 's/^pkgrel=//p' "${SCRIPT_DIR}/PKGBUILD" | head -n 1)"
[[ -n "${version}" && "${version}" == "${pkgver}" ]] \
    || die "Project version (${version:-missing}) and Arch pkgver (${pkgver:-missing}) differ."
[[ "${pkgrel}" =~ ^[1-9][0-9]*$ ]] || die 'PKGBUILD pkgrel must be a positive integer.'
[[ "${SUNSHINE_COMMIT}" == "$(git -C external/sunshine rev-parse HEAD)" ]] \
    || die 'Arch Sunshine pin differs from the checked-out submodule.'
[[ "${SUNSHINE_FFMPEG_TAG}" == "$(git -C external/sunshine/third-party/build-deps describe --tags --exact-match 2>/dev/null || true)" ]] \
    || die 'Arch FFmpeg release tag differs from pinned build-deps.'
[[ "${BOOST_VERSION}" == "$(sed -n 's/^_boost_version=//p' "${SCRIPT_DIR}/PKGBUILD" | head -n 1)" ]] \
    || die 'Boost version differs between sources.conf and PKGBUILD.'
for hash in "${SUNSHINE_FFMPEG_SHA256}" "${BOOST_SHA256}"; do
    [[ "${hash}" =~ ^[0-9a-f]{64}$ ]] || die 'External source SHA256 must be 64 lowercase hex digits.'
done

dependency_hash="$( { printf '%s\n' "${IMAGE}" "cuda=${enable_cuda}"; sha256sum "${SCRIPT_DIR}/dependencies.sh" "${SCRIPT_DIR}/container-prepare.sh" | awk '{print $1}'; } \
    | sha256sum | awk '{print substr($1,1,16)}')"
deps_image="localhost/monitorize-arch-builddeps:${dependency_hash}"
ffmpeg_cache_name="Linux-x86_64-ffmpeg-${SUNSHINE_FFMPEG_TAG}-${SUNSHINE_FFMPEG_SHA256:0:12}.tar.gz"
boost_cache_name="boost-${BOOST_VERSION}-${BOOST_SHA256:0:12}-cmake.tar.xz"
if (( offline )); then
    podman image exists "${deps_image}" \
        || die "Missing prepared image ${deps_image}; run ${normal_command} first."
    for source_check in \
        "${SUNSHINE_FFMPEG_SHA256}|${OUTPUT_ROOT}/cache/sources/${ffmpeg_cache_name}" \
        "${BOOST_SHA256}|${OUTPUT_ROOT}/cache/sources/${boost_cache_name}"; do
        expected_hash="${source_check%%|*}"
        source_path="${source_check#*|}"
        printf '%s  %s\n' "${expected_hash}" "${source_path}" \
            | sha256sum --check --strict --status \
            || die "Missing or invalid cached $(basename "${source_path}"); run ${normal_command} first."
    done
fi

mkdir -p "${OUTPUT_ROOT}/x86_64" "${OUTPUT_ROOT}/cache/pacman" \
    "${OUTPUT_ROOT}/cache/sources" "${OUTPUT_ROOT}/cache/npm"
tmp_root="$(mktemp -d "${OUTPUT_ROOT}/.build.XXXXXX")"
build_log="${tmp_root}/build.log"
deps_container=''
cleanup() {
    local result=$?
    if [[ -n "${deps_container}" ]]; then
        podman rm -f "${deps_container}" >/dev/null 2>&1 || true
    fi
    if (( result != 0 )) && [[ -f "${build_log}" ]]; then
        cp "${build_log}" "${OUTPUT_ROOT}/failed-build.log" || true
    fi
    podman unshare chmod -R u+rwX "${tmp_root}" 2>/dev/null || true
    podman unshare rm -rf "${tmp_root}" 2>/dev/null || rm -rf "${tmp_root}"
}
trap cleanup EXIT

stage_root="${tmp_root}/stage/monitorize-${version}"
work_root="${tmp_root}/work"
recipe_root="${work_root}/makepkg"
artifact_root="${tmp_root}/artifacts"
mkdir -p "${stage_root}" "${recipe_root}" "${artifact_root}"
: > "${build_log}"
git archive --format=tar HEAD | tar -xf - -C "${stage_root}"
for path in "${submodule_paths[@]}"; do
    mkdir -p "${stage_root}/${path}"
    git -C "${path}" archive --format=tar HEAD | tar -xf - -C "${stage_root}/${path}"
done

source_commit="$(git rev-parse HEAD)"
source_date_epoch="$(git show -s --format=%ct HEAD)"
tar --sort=name --mtime="@${source_date_epoch}" --owner=0 --group=0 \
    --numeric-owner -C "${tmp_root}/stage" -cf - "monitorize-${version}" \
    | gzip -n > "${recipe_root}/monitorize-${version}.tar.gz"
source_sha="$(sha256sum "${recipe_root}/monitorize-${version}.tar.gz" | awk '{print $1}')"

for helper in PKGBUILD dependencies.sh monitorize.install monitorize-wrapper; do
    cp "${SCRIPT_DIR}/${helper}" "${recipe_root}/${helper}"
done
sed -i \
    -e "s/__MONITORIZE_SOURCE_SHA256__/${source_sha}/" \
    -e "s/__SUNSHINE_FFMPEG_SHA256__/${SUNSHINE_FFMPEG_SHA256}/" \
    -e "s/__BOOST_SHA256__/${BOOST_SHA256}/" \
    "${recipe_root}/PKGBUILD"

build_uid="$(id -u)"
build_gid="$(id -g)"
[[ "${build_uid}" =~ ^[1-9][0-9]*$ && "${build_gid}" =~ ^[1-9][0-9]*$ ]] \
    || die 'Build as a regular host user with positive UID and GID.'

if (( ! offline )); then
    deps_container="monitorize-arch-builddeps-$$"
    podman run --name "${deps_container}" --arch amd64 \
        --userns=keep-id --user 0:0 --security-opt label=disable \
        --env "MONITORIZE_BUILD_UID=${build_uid}" \
        --env "MONITORIZE_BUILD_GID=${build_gid}" \
        --env "MONITORIZE_ENABLE_CUDA=${enable_cuda}" \
        --volume "${SCRIPT_DIR}:/packaging:ro" \
        --volume "${OUTPUT_ROOT}/cache/pacman:/var/cache/pacman/pkg:U" \
        "${IMAGE}" /bin/bash /packaging/container-prepare.sh \
        2>&1 | tee -a "${build_log}"
    podman commit "${deps_container}" "${deps_image}" >/dev/null
    podman rm "${deps_container}" >/dev/null
    deps_container=''
fi

deps_image_id="$(podman image inspect --format '{{.Id}}' "${deps_image}")"
echo "Building Monitorize ${version}-${pkgrel} for Arch x86_64 with ${build_jobs} job(s)…"
run_options=(--rm --pull=never --arch amd64 --userns=keep-id \
    --user "${build_uid}:${build_gid}" --security-opt label=disable)
if (( offline )); then
    run_options+=(--network=none --env MONITORIZE_OFFLINE=1 --env npm_config_offline=true)
fi
podman run "${run_options[@]}" \
    --env "MONITORIZE_BUILD_UID=${build_uid}" \
    --env "MONITORIZE_BUILD_JOBS=${build_jobs}" \
    --env "MONITORIZE_ENABLE_CUDA=${enable_cuda}" \
    --env HOME=/work/home \
    --env "MONITORIZE_SOURCE_COMMIT=${source_commit}" \
    --env "MONITORIZE_SOURCE_SHA256=${source_sha}" \
    --env "MONITORIZE_SUNSHINE_COMMIT=${SUNSHINE_COMMIT}" \
    --env "MONITORIZE_DEPS_IMAGE_ID=${deps_image_id}" \
    --env "SUNSHINE_FFMPEG_TAG=${SUNSHINE_FFMPEG_TAG}" \
    --env "SUNSHINE_FFMPEG_SHA256=${SUNSHINE_FFMPEG_SHA256}" \
    --env "BOOST_VERSION=${BOOST_VERSION}" \
    --env "BOOST_SHA256=${BOOST_SHA256}" \
    --env "MONITORIZE_FFMPEG_CACHE_NAME=${ffmpeg_cache_name}" \
    --env "MONITORIZE_BOOST_CACHE_NAME=${boost_cache_name}" \
    --env "SOURCE_DATE_EPOCH=${source_date_epoch}" \
    --env npm_config_cache=/npm-cache \
    --volume "${SCRIPT_DIR}:/packaging:ro" \
    --volume "${work_root}:/work" \
    --volume "${artifact_root}:/artifacts" \
    --volume "${OUTPUT_ROOT}/cache/sources:/source-cache" \
    --volume "${OUTPUT_ROOT}/cache/npm:/npm-cache" \
    "${deps_image}" /bin/bash /packaging/container-build.sh \
    2>&1 | tee -a "${build_log}"

main_package="${artifact_root}/monitorize-${version}-${pkgrel}-x86_64.pkg.tar.zst"
[[ -f "${main_package}" ]] || die "Expected Arch package missing: ${main_package}"
if (( ! offline )); then
    echo "Smoke-testing $(basename "${main_package}") in a fresh Arch container…"
    podman run --rm --pull=never --arch amd64 --cap-add SETFCAP \
        --security-opt label=disable \
        --volume "${main_package}:/tmp/monitorize.pkg.tar.zst:ro" \
        --volume "${SCRIPT_DIR}:/packaging:ro" \
        "${IMAGE}" /bin/bash /packaging/smoke-test.sh \
        2>&1 | tee -a "${build_log}"
else
    echo 'Offline rebuild complete; fresh-install smoke test skipped (network disabled).' \
        | tee -a "${build_log}"
fi

published_package="${OUTPUT_ROOT}/x86_64/$(basename "${main_package}")"
cp "${main_package}" "${published_package}.part"
mv "${published_package}.part" "${published_package}"
cp "${artifact_root}/build-manifest.txt" "${OUTPUT_ROOT}/build-manifest.txt"
cp "${build_log}" "${OUTPUT_ROOT}/build.log"
echo "Completed Arch x86_64 package: ${published_package}"
