#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 OUTPUT" >&2
    exit 2
fi

for command in wayland-scanner cc pkg-config; do
    if ! command -v "${command}" >/dev/null 2>&1; then
        echo "Missing KDE helper build dependency: ${command}" >&2
        exit 1
    fi
done

if ! pkg-config --exists wayland-client; then
    echo "Missing Wayland client development files" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="$1"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

XML="${SCRIPT_DIR}/zkde-screencast-unstable-v1.xml"
HEADER="${TMP_DIR}/zkde-screencast-unstable-v1-client-protocol.h"
CODE="${TMP_DIR}/zkde-screencast-unstable-v1-protocol.c"

wayland-scanner client-header "${XML}" "${HEADER}"
wayland-scanner private-code "${XML}" "${CODE}"
mkdir -p "$(dirname "${OUTPUT}")"

read -r -a CFLAGS <<< "$(pkg-config --cflags wayland-client)"
read -r -a LIBS <<< "$(pkg-config --libs wayland-client)"
cc -std=c11 -O2 -Wall -Wextra -Werror -I"${TMP_DIR}" \
    "${SCRIPT_DIR}/monitorize-kde-virtual-output.c" "${CODE}" \
    "${CFLAGS[@]}" "${LIBS[@]}" -o "${OUTPUT}"
