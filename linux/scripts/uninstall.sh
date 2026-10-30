#!/usr/bin/env bash
# Monitorize source uninstaller. Run as the desktop user, without sudo.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"

usage() {
    cat <<'EOF'
Usage: ./uninstall.sh [--keep-data | --remove-all]

Without arguments, use Up/Down and Enter to choose:
  Uninstall — keep settings and pairing
  Uninstall — remove all data

Run as your desktop user, without sudo. System cleanup requests Polkit
administrator authorization only when source-installed system assets exist.
The source checkout, standalone Sunshine, monitorize-vkms and shared build
prerequisites are retained. See linux/scripts/UNINSTALL.md for the scope.
EOF
}

mode=""
if (( $# > 1 )); then usage >&2; exit 2; fi
case "${1:-}" in
    --keep-data) mode=keep ;;
    --remove-all) mode=all ;;
    -h|--help) usage; exit 0 ;;
    '') ;;
    *) usage >&2; exit 2 ;;
esac
if (( EUID == 0 )); then
    echo 'Run ./uninstall.sh as your desktop user, without sudo.' >&2
    exit 1
fi
if [[ -z "${mode}" ]]; then
    if [[ ! -t 0 || ! -t 1 ]]; then
        echo 'An interactive terminal is required; alternatively use --keep-data or --remove-all.' >&2
        exit 2
    fi
    selected=0
    draw_menu() {
        printf '\n  Monitorize — Uninstall\n\n'
        local index
        local -a labels=('Uninstall — keep settings and pairing' 'Uninstall — remove all data')
        for index in 0 1; do
            if (( index == selected )); then printf '  \033[38;5;111m› %s\033[0m\n' "${labels[index]}"
            else printf '    %s\n' "${labels[index]}"; fi
        done
        printf '\n  ↑↓ Navigate · Enter Select · Ctrl+C Cancel\n'
    }
    trap 'printf "\033[?25h"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    printf '\033[?25l'
    draw_menu
    while true; do
        if ! IFS= read -rsn1 key; then exit 1; fi
        case "${key}" in
            $'\x1b')
                if ! IFS= read -rsn2 -t 1 key; then key=""; fi
                case "${key}" in '[A') selected=0 ;; '[B') selected=1 ;; esac
                printf '\033[7A'; draw_menu ;;
            '') break ;;
        esac
    done
    printf '\033[?25h\n'
    if (( selected == 0 )); then mode=keep; else mode=all; fi
fi
command -v python3 >/dev/null || { echo 'Python 3 is required to uninstall Monitorize.' >&2; exit 1; }
# The backend uses only stdlib; remove the source venv after all cleanup.
exec python3 -B "${SCRIPT_DIR}/../../scripts/uninstall-source.py" "${mode}"
