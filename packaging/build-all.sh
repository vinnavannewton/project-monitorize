#!/usr/bin/env bash
# Run enabled native x86_64/amd64 package builds sequentially.
set -euo pipefail

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
list_only=false
case "${1:-}" in
    '') ;;
    --list) list_only=true; shift ;;
    --help|-h)
        echo "Usage: $0 [--list]"
        echo 'Build enabled 64-bit native packages sequentially using existing defaults.'
        echo 'Failures are summarized; remaining targets still run. Ctrl+C stops the queue.'
        exit 0 ;;
    *) echo "Usage: $0 [--list]" >&2; exit 2 ;;
esac
(( $# == 0 )) || { echo 'Unexpected arguments.' >&2; exit 2; }

cd -- "$PROJECT_ROOT"
# Read the canonical enablement flags; do not modify them or any build settings.
queue="$(python3 - <<'PY'
import runpy
specs = runpy.run_path('scripts/package-assets.py')['TARGETS']
paths = {
    'fedora-44': 'packaging/rpm/build.sh',
    'tumbleweed': 'packaging/tumbleweed/build.sh',
    'arch': 'packaging/arch/build.sh',
    'ubuntu-24.04': 'packaging/deb/ubuntu-24.04/build.sh',
    'ubuntu-26.04': 'packaging/deb/ubuntu-26.04/build.sh',
    'debian-trixie': 'packaging/deb/debian-trixie/build.sh',
}
for target, spec in specs.items():
    if spec.cd_enabled and spec.arch in ('x86_64', 'amd64') and target not in paths:
        raise SystemExit(f'Enabled target has no queue command: {target}')
for target, path in paths.items():
    spec = specs.get(target)
    if spec and spec.cd_enabled and spec.arch in ('x86_64', 'amd64'):
        print(f'{target}\t{path}')
PY
)"
[[ -n "$queue" ]] || { echo 'No enabled 64-bit package targets.' >&2; exit 1; }
if "$list_only"; then
    printf '%s\n' "$queue"
    exit 0
fi
[[ "$(uname -m)" == x86_64 ]] || { echo 'These builders require an x86_64 host.' >&2; exit 1; }

trap 'echo "Build queue interrupted." >&2; exit 130' INT
trap 'echo "Build queue terminated." >&2; exit 143' TERM
results=()
failed=0
while IFS=$'\t' read -r target script; do
    printf '\nStarting %s: ./%s\n' "$target" "$script"
    if "$PROJECT_ROOT/$script"; then
        results+=("PASS  $target")
    else
        status=$?
        if (( status == 130 || status == 143 )); then exit "$status"; fi
        results+=("FAIL  $target (exit $status)")
        failed=1
    fi
done <<< "$queue"
printf '\nBuild queue finished:\n'
printf '%s\n' "${results[@]}"
exit "$failed"
