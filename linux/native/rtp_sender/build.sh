#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 OUTPUT" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="$1"
mkdir -p "$(dirname "${OUTPUT}")"

read -r -a BUILD_CFLAGS <<< "${RPM_OPT_FLAGS:-${CFLAGS_EXTRA:-}}"
read -r -a BUILD_LDFLAGS <<< "${RPM_LD_FLAGS:-${LDFLAGS:-}}"
cc -std=c11 -O2 -Wall -Wextra \
    -pthread \
    -I"${SCRIPT_DIR}/nanors" -I"${SCRIPT_DIR}/nanors/deps/obl" \
    "${SCRIPT_DIR}/monitorize-rtp-sender.c" \
    "${SCRIPT_DIR}/nanors/rs.c" \
    "${SCRIPT_DIR}/nanors/deps/obl/oblas_common.c" \
    "${SCRIPT_DIR}/nanors/deps/obl/oblas_lite.c" \
    "${BUILD_CFLAGS[@]}" "${BUILD_LDFLAGS[@]}" -o "${OUTPUT}"
