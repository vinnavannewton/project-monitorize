#!/usr/bin/env bash

set -euo pipefail

readonly FEDORA_VERSION=44
readonly IMAGE="fedora:${FEDORA_VERSION}"
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
readonly SPEC_FILE="${SCRIPT_DIR}/monitorize.spec"
readonly SYSUSERS_FILE="${PROJECT_ROOT}/packaging/fedora/monitorize.sysusers"
readonly OUTPUT_ROOT="${PROJECT_ROOT}/dist/rpm/fedora-${FEDORA_VERSION}"

die() {
    echo "Error: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

spec_global() {
    local name="$1"
    awk -v name="${name}" '$1 == "%global" && $2 == name { print $3; exit }' "${SPEC_FILE}"
}

for command in git podman tar gzip awk sed sha256sum tee; do
    require_command "${command}"
done

[[ "$(uname -m)" == "x86_64" ]] || die "The Fedora RPM builder currently supports x86_64 hosts only."

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
spec_version="$(awk '$1 == "Version:" { print $2; exit }' "${SPEC_FILE}")"
[[ -n "${version}" && "${version}" == "${spec_version}" ]] || \
    die "pyproject.toml version (${version:-missing}) does not match the RPM spec (${spec_version:-missing})."

sunshine_commit="$(spec_global sunshine_commit)"
actual_sunshine_commit="$(git -C external/sunshine rev-parse HEAD)"
[[ "${sunshine_commit}" == "${actual_sunshine_commit}" ]] || \
    die "The RPM spec Sunshine commit does not match the pinned submodule commit."

ffmpeg_tag="$(spec_global sunshine_ffmpeg_tag)"
actual_ffmpeg_tag="$(git -C external/sunshine/third-party/build-deps describe --tags --exact-match 2>/dev/null || true)"
[[ "${ffmpeg_tag}" == "${actual_ffmpeg_tag}" ]] || \
    die "The RPM spec FFmpeg tag (${ffmpeg_tag}) does not match build-deps (${actual_ffmpeg_tag:-untagged})."

cpu_count="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
[[ "${cpu_count}" =~ ^[1-9][0-9]*$ ]] || cpu_count=1
default_jobs="${cpu_count}"
(( default_jobs > 4 )) && default_jobs=4
build_jobs="${MONITORIZE_BUILD_JOBS:-${default_jobs}}"
[[ "${build_jobs}" =~ ^[1-9][0-9]*$ ]] || die "MONITORIZE_BUILD_JOBS must be a positive integer."

mkdir -p "${OUTPUT_ROOT}"
tmp_root="$(mktemp -d "${OUTPUT_ROOT}/.build.XXXXXX")"
cleanup() {
    chmod -R u+rwX "${tmp_root}" 2>/dev/null || true
    rm -rf "${tmp_root}"
}
trap cleanup EXIT

source_name="monitorize-${version}"
stage_root="${tmp_root}/stage/${source_name}"
topdir="${tmp_root}/rpmbuild"
mkdir -p "${stage_root}" "${topdir}"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

git archive --format=tar HEAD | tar -xf - -C "${stage_root}"
for path in "${submodule_paths[@]}"; do
    mkdir -p "${stage_root}/${path}"
    git -C "${path}" archive --format=tar HEAD | tar -xf - -C "${stage_root}/${path}"
done

source_date_epoch="$(git show -s --format=%ct HEAD)"
source_archive="${topdir}/SOURCES/${source_name}.tar.gz"
tar --sort=name --mtime="@${source_date_epoch}" --owner=0 --group=0 --numeric-owner \
    -C "${tmp_root}/stage" -cf - "${source_name}" | gzip -n > "${source_archive}"

cp "${SPEC_FILE}" "${topdir}/SPECS/monitorize.spec"
cp "${SYSUSERS_FILE}" "${topdir}/SOURCES/monitorize.sysusers"

mkdir -p "${OUTPUT_ROOT}/x86_64" "${OUTPUT_ROOT}/source"
find "${OUTPUT_ROOT}" -type f -name '*.rpm' -delete
build_log="${OUTPUT_ROOT}/build.log"
echo "Building Monitorize ${version} for Fedora ${FEDORA_VERSION} x86_64 with ${build_jobs} job(s)…"
podman run --rm \
    --arch amd64 \
    --security-opt label=disable \
    --env "MONITORIZE_RPM_JOBS=${build_jobs}" \
    --volume "${OUTPUT_ROOT}:/artifacts" \
    --volume "${topdir}:/work" \
    "${IMAGE}" \
    bash -euxo pipefail -c '
        dnf -y --setopt=install_weak_deps=False install curl dnf-plugins-core rpm-build rpmlint
        dnf -y --setopt=install_weak_deps=False builddep /work/SPECS/monitorize.spec

        ffmpeg_url="$(rpmspec -P /work/SPECS/monitorize.spec | awk '\''$1 == "Source1:" && !found { value = $2; found = 1 } END { print value }'\'')"
        ffmpeg_sha="$(awk '\''$1 == "%global" && $2 == "sunshine_ffmpeg_sha256" { print $3; exit }'\'' /work/SPECS/monitorize.spec)"
        ffmpeg_archive="/work/SOURCES/$(basename "${ffmpeg_url}")"
        curl --fail --location --retry 3 --output "${ffmpeg_archive}" "${ffmpeg_url}"
        echo "${ffmpeg_sha}  ${ffmpeg_archive}" | sha256sum --check --strict

        export HOME=/tmp/monitorize-rpmbuild-home
        mkdir -p "${HOME}"
        rpmbuild -ba \
            --define "_topdir /work" \
            --define "_smp_build_ncpus ${MONITORIZE_RPM_JOBS}" \
            /work/SPECS/monitorize.spec
        rpmlint /work/SRPMS/*.src.rpm /work/RPMS/x86_64/*.rpm
        cp /work/RPMS/x86_64/*.rpm /artifacts/x86_64/
        cp /work/SRPMS/*.src.rpm /artifacts/source/
    ' 2>&1 | tee "${build_log}"

mapfile -t main_rpms < <(find "${OUTPUT_ROOT}/x86_64" -maxdepth 1 -type f \
    -name "monitorize-${version}-*.fc${FEDORA_VERSION}.x86_64.rpm" \
    ! -name '*-debuginfo-*' ! -name '*-debugsource-*' | sort)
(( ${#main_rpms[@]} == 1 )) || die "Expected exactly one primary Monitorize RPM, found ${#main_rpms[@]}."
main_rpm="${main_rpms[0]}"

echo "Smoke-testing $(basename "${main_rpm}") in a fresh Fedora ${FEDORA_VERSION} container…"
podman run --rm \
    --arch amd64 \
    --security-opt label=disable \
    --volume "${main_rpm}:/tmp/monitorize.rpm:ro" \
    "${IMAGE}" \
    bash -euxo pipefail -c '
        dnf -y --setopt=install_weak_deps=False install /tmp/monitorize.rpm desktop-file-utils
        test ! -e /root/.config/monitorize
        rpm -V monitorize
        getent group monitorize-input
        test -x /usr/bin/monitorize
        test -x /usr/bin/monitorize-kde-virtual-output
        test -x /usr/libexec/monitorize/sunshine
        test -x /usr/libexec/monitorize/monitorize-system-setup
        test -d /usr/share/monitorize/sunshine/assets/web
        test -f /usr/lib/firewalld/services/monitorize.xml
        test -f /usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize.system-setup.policy
        test -f /usr/lib/udev/rules.d/70-monitorize-uinput.rules
        test -f /usr/lib/sysusers.d/monitorize.conf
        test ! -e /usr/bin/sunshine
        test ! -e /usr/local/bin/sunshine
        desktop-file-validate /usr/share/applications/monitorize.desktop
        desktop-file-validate /usr/share/applications/monitorize-kde-virtual-output.desktop
        /usr/libexec/monitorize/sunshine --version
        MONITORIZE_SUNSHINE_BIN=/usr/libexec/monitorize/sunshine \
        MONITORIZE_SUNSHINE_ASSETS_DIR=/usr/share/monitorize/sunshine/assets \
        python3 - <<'\''PYTHON'\''
from PyQt6.QtQuickWidgets import QQuickWidget
from monitorize.desktop import main_window
from monitorize.platform.sunshine_service import get_sunshine_assets_dir, get_sunshine_candidates

command = get_sunshine_candidates()[0]
assert command[0] == "/usr/libexec/monitorize/sunshine"
assert get_sunshine_assets_dir(command[0]) == "/usr/share/monitorize/sunshine/assets"
assert QQuickWidget is not None
assert main_window is not None
PYTHON
        dnf -y remove monitorize
        test ! -e /usr/bin/monitorize
        test ! -e /usr/bin/monitorize-kde-virtual-output
        test ! -e /usr/libexec/monitorize/sunshine
        test ! -e /usr/libexec/monitorize/monitorize-system-setup
        test ! -e /usr/share/monitorize
        test ! -e /usr/share/applications/monitorize.desktop
    '

echo "Fedora ${FEDORA_VERSION} RPM build and smoke test completed."
echo "Primary RPM: ${main_rpm}"
echo "Source RPM: ${OUTPUT_ROOT}/source/"
