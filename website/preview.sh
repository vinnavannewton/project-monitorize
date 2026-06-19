#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-4173}"

printf 'Project Monitorize website preview:\n'
printf '  http://%s:%s/\n\n' "$HOST" "$PORT"
printf 'Press Ctrl+C to stop.\n'

python3 -m http.server "$PORT" --bind "$HOST"
