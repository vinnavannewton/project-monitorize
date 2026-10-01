#!/usr/bin/env bash
set -euo pipefail

die() { echo "Error: $*" >&2; exit 1; }
[[ "$(id -u)" == 0 ]] || die 'Dependency preparation requires container root.'
source /packaging/dependencies.sh

# Always update the base image and packages as one full Arch transaction.
pacman -Syu --noconfirm --needed \
    "${monitorize_runtime_deps[@]}" "${monitorize_build_deps[@]}"

build_uid="${MONITORIZE_BUILD_UID:?Missing build UID}"
build_gid="${MONITORIZE_BUILD_GID:?Missing build GID}"
[[ "${build_uid}" =~ ^[1-9][0-9]*$ && "${build_gid}" =~ ^[1-9][0-9]*$ ]] \
    || die 'Build UID and GID must be positive integers.'
if getent group "${build_gid}" >/dev/null; then
    build_group="$(getent group "${build_gid}" | cut -d: -f1)"
else
    build_group=monitorize-build
    groupadd --gid "${build_gid}" "${build_group}"
fi
if existing_user="$(getent passwd "${build_uid}")"; then
    echo "Reusing container account ${existing_user%%:*} for build UID ${build_uid}."
else
    useradd --uid "${build_uid}" --gid "${build_gid}" \
        --create-home --shell /bin/bash monitorize-build
fi
if [[ "${MONITORIZE_ENABLE_CUDA:-1}" == 1 ]]; then
    test -x /opt/cuda/bin/nvcc || die 'The Arch CUDA package did not provide /opt/cuda/bin/nvcc.'
fi
