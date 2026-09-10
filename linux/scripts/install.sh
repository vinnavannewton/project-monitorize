#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Monitorize — Linux desktop installer
#
# Creates a .desktop entry so Monitorize appears in the application
# menu on KDE, GNOME, Hyprland, and other freedesktop-compliant DEs.
#
# Usage:
#   ./install.sh          # interactive install
#   ./install.sh --complete
#   ./install.sh --partial
#   ./install.sh --complete --cuda=auto  # auto, on, or off
#   ./install.sh --rebuild-sunshine  # clean and rebuild Sunshine
#   ./install.sh remove   # uninstall
# ──────────────────────────────────────────────────────────────────────

set -euo pipefail

APP_NAME="Monitorize"
APP_ID="monitorize"
DESKTOP_FILE="${APP_ID}.desktop"

# Resolve paths relative to this script (linux/scripts directory)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ICON_SRC="${PROJECT_DIR}/monitorize/assets/monitorize_desktop_logo.png"
VENV_DIR="${PROJECT_DIR}/venv"
HELPER_NAME="monitorize-kde-virtual-output"
HELPER_BUILD="${PROJECT_DIR}/native/kde_virtual_output/build.sh"
HELPER_PATH="${VENV_DIR}/bin/${HELPER_NAME}"
HELPER_DESKTOP_FILE="${APP_ID}-kde-virtual-output.desktop"
REPOSITORY_DIR="$(cd "${PROJECT_DIR}/.." && pwd)"
SUNSHINE_SUBMODULE_DIR="${REPOSITORY_DIR}/external/sunshine"
SUNSHINE_BUILD_DIR="${SUNSHINE_SUBMODULE_DIR}/build"
SUNSHINE_BUILD_BIN="${SUNSHINE_BUILD_DIR}/sunshine"
SUNSHINE_BUILD_ASSETS="${SUNSHINE_BUILD_DIR}/assets"
SUNSHINE_BUILD_STAMP="${SUNSHINE_BUILD_DIR}/.monitorize-built-fingerprint"
SUNSHINE_VENV_BIN="${VENV_DIR}/bin/sunshine"
SUNSHINE_VENV_ASSETS="${VENV_DIR}/share/monitorize/sunshine/assets"
SUNSHINE_STRICT_SELECTION_PATCH="${REPOSITORY_DIR}/packaging/sunshine-strict-selection.patch"
SUNSHINE_PORTAL_TOKEN_PATCH="${REPOSITORY_DIR}/packaging/sunshine-portal-token-scope.patch"

# XDG standard locations
CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"
DESKTOP_DIR="${DATA_HOME}/applications"
ICON_CACHE_DIR="${DATA_HOME}/icons/hicolor"
ICON_DIR="${ICON_CACHE_DIR}/192x192/apps"
ICON_DEST="${ICON_DIR}/${APP_ID}.png"

remove_legacy_udp_entries() {
    rm -f "${DESKTOP_DIR}/monitorize-udp.desktop"
    rm -f "${DESKTOP_DIR}/monitorize-udp-kde-virtual-output.desktop"
    rm -f "${ICON_DIR}/monitorize-udp.png"
}

desktop_quote() {
    local value="$1"
    local escaped=""
    local char index

    for (( index = 0; index < ${#value}; index++ )); do
        char="${value:index:1}"
        case "${char}" in
            \\) escaped+="\\\\\\\\" ;;
            '"') escaped+='\\"' ;;
            '`') escaped+='\\`' ;;
            '$') escaped+='\\$' ;;
            '%') escaped+='%%' ;;
            *) escaped+="${char}" ;;
        esac
    done
    printf '"%s"' "${escaped}"
}

desktop_string_escape() {
    local value="$1"
    local escaped=""
    local char index

    for (( index = 0; index < ${#value}; index++ )); do
        char="${value:index:1}"
        case "${char}" in
            ' ') escaped+='\s' ;;
            $'\n') escaped+='\n' ;;
            $'\t') escaped+='\t' ;;
            $'\r') escaped+='\r' ;;
            \\) escaped+="\\\\" ;;
            *) escaped+="${char}" ;;
        esac
    done
    printf '%s' "${escaped}"
}

version_at_least() {
    [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

node_version_supported() {
    local version="$1"
    if version_at_least "${version}" "22.12.0"; then
        return 0
    fi
    if version_at_least "${version}" "20.19.0" && ! version_at_least "${version}" "21.0.0"; then
        return 0
    fi
    return 1
}

require_command() {
    if ! command -v "$1" &>/dev/null; then
        echo "Error: '$1' is required. Install the dependencies listed in the project wiki and try again." >&2
        exit 1
    fi
}

select_sunshine_compiler() {
    local cc_cxx cc cxx version
    for cc_cxx in "gcc-14:g++-14" "gcc:g++" "clang:clang++"; do
        cc="${cc_cxx%%:*}"
        cxx="${cc_cxx##*:}"
        if ! command -v "${cc}" &>/dev/null || ! command -v "${cxx}" &>/dev/null; then
            continue
        fi
        if ! version="$("${cxx}" -dumpfullversion -dumpversion 2>/dev/null)" || [[ -z "${version}" ]]; then
            if ! version="$("${cxx}" --version 2>/dev/null | awk '
                !found && match($0, /[0-9]+/) {
                    print substr($0, RSTART, RLENGTH)
                    found=1
                }
                END { if (!found) exit 1 }
            ')"; then
                continue
            fi
        fi
        if [[ "${cc}" == clang* ]]; then
            version_at_least "${version}" "17" || continue
        else
            version_at_least "${version}" "14" || continue
        fi
        SUNSHINE_CC="$(command -v "${cc}")"
        SUNSHINE_CXX="$(command -v "${cxx}")"
        return 0
    done
    echo "Error: Sunshine requires GCC 14+ or Clang 17+. Install a supported compiler and try again." >&2
    exit 1
}

configure_build_jobs() {
    local detected
    detected="$(nproc 2>/dev/null || echo 1)"
    [[ "${detected}" =~ ^[1-9][0-9]*$ ]] || detected=1
    if [[ -n "${MONITORIZE_BUILD_JOBS:-}" ]]; then
        if [[ ! "${MONITORIZE_BUILD_JOBS}" =~ ^[1-9][0-9]*$ ]]; then
            echo "Error: MONITORIZE_BUILD_JOBS must be a positive integer." >&2
            exit 1
        fi
        BUILD_JOBS="${MONITORIZE_BUILD_JOBS}"
    elif (( detected > 8 )); then
        BUILD_JOBS=8
    else
        BUILD_JOBS="${detected}"
    fi
}

nvidia_gpu_present() {
    local output vendor class vendor_path device_dir
    local pci_root="${MONITORIZE_PCI_SYSFS_ROOT:-/sys/bus/pci/devices}"

    # A working driver is the strongest signal and also covers containers where
    # PCI sysfs may not be mounted into the namespace.
    if command -v nvidia-smi &>/dev/null; then
        if output="$(nvidia-smi --query-gpu=uuid --format=csv,noheader,nounits 2>/dev/null)" &&
                [[ -n "${output//[[:space:]]/}" ]]; then
            return 0
        fi
    fi

    # Fall back to PCI sysfs so Auto still recognizes an NVIDIA display device
    # before its kernel driver is loaded. Class 0x03 covers VGA, 3D, and display
    # controllers without introducing an lspci dependency.
    for vendor_path in "${pci_root}"/*/vendor; do
        [[ -r "${vendor_path}" ]] || continue
        if ! IFS= read -r vendor < "${vendor_path}"; then
            continue
        fi
        [[ "${vendor,,}" == "0x10de" ]] || continue
        device_dir="${vendor_path%/vendor}"
        if ! IFS= read -r class < "${device_dir}/class"; then
            continue
        fi
        if [[ "${class,,}" == 0x03* ]]; then
            return 0
        fi
    done
    return 1
}

find_cuda_compiler_hint() {
    local candidate root
    for candidate in "${CUDACXX:-}" /opt/cuda/bin/nvcc; do
        if [[ -n "${candidate}" && -x "${candidate}" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    for root in "${CUDA_HOME:-}" "${CUDA_PATH:-}"; do
        [[ -n "${root}" ]] || continue
        candidate="${root%/}/bin/nvcc"
        if [[ -x "${candidate}" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    if command -v nvcc &>/dev/null; then
        command -v nvcc
        return 0
    fi
    return 1
}

probe_cuda_toolchain() {
    local probe_dir probe_log compiler_hint cache_compiler
    local -a probe_args

    SUNSHINE_CUDA_COMPILER=""
    SUNSHINE_CUDA_VERSION=""
    SUNSHINE_CUDA_PROBE_ERROR=""

    if ! probe_dir="$(mktemp -d "${TMPDIR:-/tmp}/monitorize-cuda-probe.XXXXXX")"; then
        SUNSHINE_CUDA_PROBE_ERROR="Could not create a temporary CUDA probe directory."
        return 1
    fi
    probe_log="${probe_dir}/configure.log"
    # The dollar expressions below belong to the generated CMake project.
    # shellcheck disable=SC2016
    printf '%s\n' \
        'cmake_minimum_required(VERSION 3.26)' \
        'project(MonitorizeCudaProbe LANGUAGES CUDA)' \
        'if(CMAKE_CUDA_COMPILER_VERSION VERSION_LESS 12.0)' \
        '  message(FATAL_ERROR "Sunshine requires CUDA 12.0 or newer")' \
        'endif()' \
        'file(WRITE "${CMAKE_BINARY_DIR}/cuda-version.txt" "${CMAKE_CUDA_COMPILER_VERSION}")' \
        > "${probe_dir}/CMakeLists.txt"

    probe_args=(
        -S "${probe_dir}"
        -B "${probe_dir}/build"
        -DCMAKE_CUDA_HOST_COMPILER="${SUNSHINE_CC}"
    )
    if compiler_hint="$(find_cuda_compiler_hint)"; then
        probe_args+=("-DCMAKE_CUDA_COMPILER=${compiler_hint}")
    fi

    if ! cmake "${probe_args[@]}" > "${probe_log}" 2>&1; then
        SUNSHINE_CUDA_PROBE_ERROR="$(tail -n 30 "${probe_log}" 2>/dev/null || true)"
        [[ -n "${SUNSHINE_CUDA_PROBE_ERROR}" ]] || SUNSHINE_CUDA_PROBE_ERROR="CMake could not configure a CUDA 12+ compiler."
        rm -rf -- "${probe_dir}"
        return 1
    fi

    if [[ -r "${probe_dir}/build/cuda-version.txt" ]]; then
        IFS= read -r SUNSHINE_CUDA_VERSION < "${probe_dir}/build/cuda-version.txt" || true
    fi
    if [[ -r "${probe_dir}/build/CMakeCache.txt" ]]; then
        cache_compiler="$(sed -n 's/^CMAKE_CUDA_COMPILER:[^=]*=//p' "${probe_dir}/build/CMakeCache.txt" | head -n1)"
        SUNSHINE_CUDA_COMPILER="${cache_compiler}"
    fi
    [[ -n "${SUNSHINE_CUDA_COMPILER}" ]] || SUNSHINE_CUDA_COMPILER="${compiler_hint:-cmake-auto}"
    [[ -n "${SUNSHINE_CUDA_VERSION}" ]] || SUNSHINE_CUDA_VERSION="detected"
    rm -rf -- "${probe_dir}"
    return 0
}

configure_sunshine_cuda() {
    SUNSHINE_CUDA_CMAKE_FLAGS=()
    SUNSHINE_CUDA_ENABLED="off"
    SUNSHINE_CUDA_COMPILER="none"
    SUNSHINE_CUDA_VERSION="none"

    case "${CUDA_POLICY}" in
        off)
            echo "[Monitorize] CUDA: disabled by installer policy"
            ;;
        on)
            echo "[Monitorize] CUDA: validating required CUDA 12+ toolchain…"
            if ! probe_cuda_toolchain; then
                echo "Error: --cuda=on requires a working CUDA 12+ compiler with ${SUNSHINE_CC} as its host compiler." >&2
                echo "CUDA probe output:" >&2
                printf '%s\n' "${SUNSHINE_CUDA_PROBE_ERROR}" >&2
                return 1
            fi
            SUNSHINE_CUDA_ENABLED="on"
            ;;
        auto)
            if ! nvidia_gpu_present; then
                echo "[Monitorize] CUDA Auto: no NVIDIA display GPU detected; building Sunshine without CUDA."
            else
                echo "[Monitorize] CUDA Auto: NVIDIA GPU detected; validating the CUDA 12+ toolchain…"
                if probe_cuda_toolchain; then
                    SUNSHINE_CUDA_ENABLED="on"
                else
                    echo "Warning: NVIDIA hardware was found, but its CUDA 12+ build toolchain is unusable; building without CUDA." >&2
                    echo "Rerun with --cuda=on to make the CUDA probe strict and show its diagnostics." >&2
                    SUNSHINE_CUDA_COMPILER="none"
                    SUNSHINE_CUDA_VERSION="none"
                fi
            fi
            ;;
        *)
            echo "Error: Invalid CUDA policy '${CUDA_POLICY}'; expected auto, on, or off." >&2
            return 2
            ;;
    esac

    if [[ "${SUNSHINE_CUDA_ENABLED}" == "on" ]]; then
        SUNSHINE_CUDA_CMAKE_FLAGS=(
            -DSUNSHINE_ENABLE_CUDA=ON
            -DCUDA_FAIL_ON_MISSING=ON
            -DCMAKE_CUDA_HOST_COMPILER="${SUNSHINE_CC}"
        )
        if [[ "${SUNSHINE_CUDA_COMPILER}" != "cmake-auto" ]]; then
            SUNSHINE_CUDA_CMAKE_FLAGS+=("-DCMAKE_CUDA_COMPILER=${SUNSHINE_CUDA_COMPILER}")
        fi
        echo "[Monitorize] CUDA enabled (${SUNSHINE_CUDA_COMPILER}, version ${SUNSHINE_CUDA_VERSION})"
    else
        SUNSHINE_CUDA_CMAKE_FLAGS=(
            -DSUNSHINE_ENABLE_CUDA=OFF
            -DCUDA_FAIL_ON_MISSING=OFF
        )
    fi
    return 0
}

detect_git_jobs() {
    local jobs
    if command -v nproc &>/dev/null; then
        jobs="$(nproc)"
    elif command -v getconf &>/dev/null; then
        jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
    else
        jobs=4
    fi
    [[ "${jobs}" =~ ^[0-9]+$ ]] || jobs=4
    (( jobs < 1 )) && jobs=1
    (( jobs > 8 )) && jobs=8
    printf '%s\n' "${jobs}"
}

update_sunshine_submodules() {
    local jobs="$1"

    if ! git -C "${REPOSITORY_DIR}" submodule sync --recursive; then
        echo "Error: Could not synchronize Sunshine submodule URLs." >&2
        return 1
    fi
    echo "[Monitorize] Updating Sunshine submodules (shallow, parallel: ${jobs} jobs)…"
    if git -C "${REPOSITORY_DIR}" submodule update --init --recursive --depth 1 --jobs "${jobs}" external/sunshine; then
        echo "[Monitorize] Sunshine submodules updated successfully."
        return 0
    fi

    echo "Warning: shallow Sunshine submodule checkout failed; retrying with full history." >&2
    if git -C "${REPOSITORY_DIR}" submodule update --init --recursive --jobs "${jobs}" external/sunshine; then
        echo "[Monitorize] Sunshine submodules updated successfully using fallback."
        return 0
    fi
    return 1
}

configure_sunshine_build_tools() {
    local existing_generator
    CMAKE_GENERATOR_FLAGS=()
    CMAKE_CCACHE_FLAGS=()
    SUNSHINE_BUILD_GENERATOR="default"
    SUNSHINE_CCACHE="off"

    if [[ -f "${SUNSHINE_BUILD_DIR}/CMakeCache.txt" ]]; then
        existing_generator="$(sed -n 's/^CMAKE_GENERATOR:INTERNAL=//p' "${SUNSHINE_BUILD_DIR}/CMakeCache.txt" | head -n1)"
        if [[ "${existing_generator}" == "Ninja" ]]; then
            if command -v ninja &>/dev/null; then
                CMAKE_GENERATOR_FLAGS=(-G Ninja)
                SUNSHINE_BUILD_GENERATOR="Ninja"
            else
                echo "Warning: the existing Sunshine build needs Ninja, which is unavailable; rebuilding with CMake's default generator." >&2
                rm -rf "${SUNSHINE_BUILD_DIR}"
            fi
        fi
    elif command -v ninja &>/dev/null; then
        CMAKE_GENERATOR_FLAGS=(-G Ninja)
        SUNSHINE_BUILD_GENERATOR="Ninja"
    fi

    if command -v ccache &>/dev/null; then
        CMAKE_CCACHE_FLAGS=(
            -DCMAKE_C_COMPILER_LAUNCHER=ccache
            -DCMAKE_CXX_COMPILER_LAUNCHER=ccache
        )
        SUNSHINE_CCACHE="on"
    else
        # Clear a launcher retained in an existing CMake cache so a removed
        # ccache never makes a normal rebuild fail.
        CMAKE_CCACHE_FLAGS=(
            -DCMAKE_C_COMPILER_LAUNCHER=
            -DCMAKE_CXX_COMPILER_LAUNCHER=
        )
    fi

    echo "[Monitorize] Sunshine build generator: ${SUNSHINE_BUILD_GENERATOR}"
    if [[ "${SUNSHINE_CCACHE}" == "on" ]]; then
        echo "[Monitorize] ccache enabled"
    fi
    return 0
}

configure_sunshine_build_fingerprint() {
    local commit patch_checksum portal_patch_checksum vulkan
    commit="$(git -C "${SUNSHINE_SUBMODULE_DIR}" rev-parse HEAD)"
    patch_checksum="$(cksum "${SUNSHINE_STRICT_SELECTION_PATCH}" | awk '{print $1 ":" $2}')"
    portal_patch_checksum="$(cksum "${SUNSHINE_PORTAL_TOKEN_PATCH}" | awk '{print $1 ":" $2}')"
    vulkan="on"
    if [[ " ${CMAKE_EXTRA_FLAGS[*]} " == *" -DSUNSHINE_ENABLE_VULKAN=OFF "* ]]; then
        vulkan="off"
    fi
    SUNSHINE_BUILD_FINGERPRINT="commit=${commit}|type=Release|tray=off|tests=off|docs=off|cuda-policy=${CUDA_POLICY}|cuda=${SUNSHINE_CUDA_ENABLED}|cuda-compiler=${SUNSHINE_CUDA_COMPILER}|cuda-version=${SUNSHINE_CUDA_VERSION}|vulkan=${vulkan}|generator=${SUNSHINE_BUILD_GENERATOR}|ccache=${SUNSHINE_CCACHE}|cc=${SUNSHINE_CC}|cxx=${SUNSHINE_CXX}|strict-patch=${patch_checksum}|portal-token-patch=${portal_patch_checksum}"
    return 0
}

sunshine_build_is_current() {
    [[ -f "${SUNSHINE_BUILD_STAMP}" ]] &&
        [[ -x "${SUNSHINE_BUILD_BIN}" ]] &&
        [[ -f "${SUNSHINE_BUILD_ASSETS}/web/index.html" ]] &&
        [[ "$(<"${SUNSHINE_BUILD_STAMP}")" == "${SUNSHINE_BUILD_FINGERPRINT}" ]]
}

check_sunshine_node_modules_permissions() {
    local node_modules blocked_path
    node_modules="${SUNSHINE_SUBMODULE_DIR}/node_modules"
    [[ -d "${node_modules}" ]] || return 0

    if ! blocked_path="$(find "${node_modules}" ! -writable -print -quit 2>/dev/null)"; then
        echo "Error: Could not inspect Sunshine's generated npm cache permissions." >&2
        exit 1
    fi
    if [[ -n "${blocked_path}" ]]; then
        echo "Error: Sunshine's generated npm cache is not writable: ${blocked_path}" >&2
        echo "This is normally left behind by an earlier sudo or container build." >&2
        echo "Repair it with:" >&2
        echo "  sudo chown -R \"$(id -un)\":\"$(id -gn)\" \"${node_modules}\"" >&2
        echo "Then rerun this installer without sudo." >&2
        exit 1
    fi
    return 0
}

select_install_mode() {
    # Pure bash TUI menu: arrow-key navigable install mode selector.
    local options=("Complete Install  (Recommended)|Virtual monitor creation + Sunshine streaming"
                   "Partial Install|Virtual monitor creation only")
    local selected=0
    local key

    # Draw the menu frame and options.
    draw_menu() {
        local i label desc
        printf '\033[?25l'  # hide cursor
        printf '\n'
        printf '  ╭──────────────────────────────────────────────────────────╮\n'
        printf '  │  Monitorize — Choose Installation Type                   │\n'
        printf '  ├──────────────────────────────────────────────────────────┤\n'
        for i in "${!options[@]}"; do
            label="${options[$i]%%|*}"
            desc="${options[$i]##*|}"
            if (( i == selected )); then
                printf '  │  \033[38;5;111m› %-53s\033[0m │\n' "${label}"
            else
                printf '  │    %-53s │\n' "${label}"
            fi
            printf '  │    \033[2m%-53s\033[0m │\n' "${desc}"
            if (( i < ${#options[@]} - 1 )); then
                printf '  │                                                          │\n'
            fi
        done
        printf '  ╰──────────────────────── ↑↓ Navigate · Enter Select ──────╯\n'
        printf '\033[?25h'  # show cursor
    }

    # Move cursor up to overwrite the previous menu.
    clear_menu() {
        local lines=$(( 4 + ${#options[@]} * 2 + (${#options[@]} - 1) + 1 ))
        printf '\033[%dA' "${lines}"
    }

    draw_menu

    while true; do
        if ! IFS= read -rsn1 key; then
            echo "Error: Installation mode was not selected because no input was available." >&2
            return 1
        fi
        case "${key}" in
            $'\x1b')
                if ! IFS= read -rsn2 -t 1 key; then
                    key=""
                fi
                case "${key}" in
                    '[A') if (( selected > 0 )); then selected=$((selected - 1)); fi ;;
                    '[B') if (( selected < ${#options[@]} - 1 )); then selected=$((selected + 1)); fi ;;
                esac
                clear_menu
                draw_menu
                ;;
            '')
                # Enter key
                break
                ;;
        esac
    done
    printf '\n'

    if (( selected == 0 )); then
        INSTALL_MODE="complete"
        echo "Selected: Complete Install (virtual monitor + Sunshine streaming)"
    else
        INSTALL_MODE="partial"
        echo "Selected: Partial Install (virtual monitor creation only)"
    fi
    return 0
}

select_cuda_policy() {
    local options=("Auto  (Recommended)|Require an NVIDIA GPU and working CUDA 12+ compiler"
                   "On|Require CUDA; stop with diagnostics if unavailable"
                   "Off|Build Sunshine without CUDA support")
    local selected=0
    local key

    draw_cuda_menu() {
        local i label desc
        printf '\033[?25l'
        printf '\n'
        printf '  ╭──────────────────────────────────────────────────────────╮\n'
        printf '  │  Bundled Sunshine — CUDA Build Policy                    │\n'
        printf '  ├──────────────────────────────────────────────────────────┤\n'
        for i in "${!options[@]}"; do
            label="${options[$i]%%|*}"
            desc="${options[$i]##*|}"
            if (( i == selected )); then
                printf '  │  \033[38;5;111m› %-53s\033[0m │\n' "${label}"
            else
                printf '  │    %-53s │\n' "${label}"
            fi
            printf '  │    \033[2m%-53s\033[0m │\n' "${desc}"
            if (( i < ${#options[@]} - 1 )); then
                printf '  │                                                          │\n'
            fi
        done
        printf '  ╰──────────────────────── ↑↓ Navigate · Enter Select ──────╯\n'
        printf '\033[?25h'
    }

    clear_cuda_menu() {
        local lines=$(( 4 + ${#options[@]} * 2 + (${#options[@]} - 1) + 1 ))
        printf '\033[%dA' "${lines}"
    }

    draw_cuda_menu
    while true; do
        if ! IFS= read -rsn1 key; then
            printf '\033[?25h'
            echo "Error: CUDA policy was not selected because no input was available." >&2
            return 1
        fi
        case "${key}" in
            $'\x1b')
                if ! IFS= read -rsn2 -t 1 key; then
                    key=""
                fi
                case "${key}" in
                    '[A') if (( selected > 0 )); then selected=$((selected - 1)); fi ;;
                    '[B') if (( selected < ${#options[@]} - 1 )); then selected=$((selected + 1)); fi ;;
                esac
                clear_cuda_menu
                draw_cuda_menu
                ;;
            '') break ;;
        esac
    done
    printf '\n'

    case "${selected}" in
        0) CUDA_POLICY="auto" ;;
        1) CUDA_POLICY="on" ;;
        2) CUDA_POLICY="off" ;;
    esac
    echo "Selected: Sunshine CUDA ${CUDA_POLICY}"
    return 0
}

print_usage() {
    cat <<'EOF'
Usage: ./install.sh [--complete | --partial | --rebuild-sunshine] [--cuda=POLICY]
       ./install.sh remove

With no arguments, an interactive menu selects the installation mode.
  --complete          Install virtual-display support and bundled Sunshine.
  --partial           Install virtual-display support only.
  --rebuild-sunshine  Force a clean bundled Sunshine build (complete mode).
  --cuda=POLICY       Sunshine CUDA policy: auto (default), on, or off.
                      This option implies complete mode. --cuda POLICY also works.
  remove, uninstall   Remove the per-user source installation.

MONITORIZE_CUDA=auto|on|off provides the same policy noninteractively.
EOF
}

request_install_mode() {
    local requested="$1"
    if [[ -n "${INSTALL_MODE}" && "${INSTALL_MODE}" != "${requested}" ]]; then
        echo "Error: --complete and --partial cannot be used together." >&2
        return 2
    fi
    INSTALL_MODE="${requested}"
    return 0
}

set_cuda_policy() {
    local requested="${1,,}"
    case "${requested}" in
        auto|on|off) ;;
        *)
            echo "Error: Invalid CUDA policy '$1'; expected auto, on, or off." >&2
            return 2
            ;;
    esac
    if (( CUDA_POLICY_EXPLICIT )) && [[ "${CUDA_POLICY}" != "${requested}" ]]; then
        echo "Error: conflicting CUDA policies '${CUDA_POLICY}' and '${requested}'." >&2
        return 2
    fi
    CUDA_POLICY="${requested}"
    CUDA_POLICY_EXPLICIT=1
    request_install_mode "complete"
}

# ── Uninstall ────────────────────────────────────────────────────────
FORCE_SUNSHINE_REBUILD=0
INSTALL_MODE=""
INSTALL_ACTION="install"
CUDA_POLICY="auto"
CUDA_POLICY_EXPLICIT=0

while (( $# > 0 )); do
    argument="$1"
    shift
    case "${argument}" in
        --complete)
            request_install_mode "complete"
            ;;
        --partial)
            request_install_mode "partial"
            ;;
        --rebuild-sunshine)
            FORCE_SUNSHINE_REBUILD=1
            request_install_mode "complete"
            ;;
        --cuda=*)
            set_cuda_policy "${argument#--cuda=}"
            ;;
        --cuda)
            if (( $# == 0 )); then
                echo "Error: --cuda requires auto, on, or off." >&2
                exit 2
            fi
            set_cuda_policy "$1"
            shift
            ;;
        remove|uninstall)
            INSTALL_ACTION="remove"
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo "Error: Unknown installer argument: ${argument}" >&2
            print_usage >&2
            exit 2
            ;;
    esac
done

if [[ "${INSTALL_ACTION}" == "install" && -n "${MONITORIZE_CUDA:-}" ]]; then
    set_cuda_policy "${MONITORIZE_CUDA}"
fi

if [[ "${INSTALL_ACTION}" == "install" && "${MONITORIZE_REBUILD_SUNSHINE:-}" == "1" ]]; then
    FORCE_SUNSHINE_REBUILD=1
    request_install_mode "complete"
fi

if [[ "${INSTALL_ACTION}" == "remove" ]]; then
    if [[ -n "${INSTALL_MODE}" || "${FORCE_SUNSHINE_REBUILD}" == "1" ]]; then
        echo "Error: remove cannot be combined with an installation option." >&2
        exit 2
    fi
    echo "Removing ${APP_NAME} desktop entry…"
    rm -f "${DESKTOP_DIR}/${DESKTOP_FILE}"
    rm -f "${DESKTOP_DIR}/${HELPER_DESKTOP_FILE}"
    rm -f "${ICON_DEST}"
    remove_legacy_udp_entries
    rm -rf "${PROJECT_DIR}/venv"
    find "${PROJECT_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    # Refresh desktop database if available
    if command -v update-desktop-database &>/dev/null; then
        update-desktop-database "${DESKTOP_DIR}" 2>/dev/null || true
    fi
    if command -v gtk-update-icon-cache &>/dev/null; then
        gtk-update-icon-cache -f -t "${ICON_CACHE_DIR}" 2>/dev/null || true
    fi
    if command -v kbuildsycoca6 &>/dev/null; then
        kbuildsycoca6 2>/dev/null || true
    fi
    echo "✓ ${APP_NAME} has been removed from the application menu."
    exit 0
fi

# ── Pre-flight checks ────────────────────────────────────────────────
if [[ -z "${INSTALL_MODE}" ]]; then
    select_install_mode
    if [[ "${INSTALL_MODE}" == "complete" ]]; then
        select_cuda_policy
    fi
elif [[ "${INSTALL_MODE}" == "complete" ]]; then
    echo "Selected: Complete Install (virtual monitor + Sunshine streaming)"
    echo "Selected: Sunshine CUDA ${CUDA_POLICY}"
else
    echo "Selected: Partial Install (virtual monitor creation only)"
fi

if [[ ! -f "${ICON_SRC}" ]]; then
    echo "Error: Icon not found at ${ICON_SRC}" >&2
    exit 1
fi

if [[ ! -d "${PROJECT_DIR}/monitorize" ]]; then
    echo "Error: Python package not found at ${PROJECT_DIR}/monitorize" >&2
    exit 1
fi

require_command python3

if [[ "${INSTALL_MODE}" == "complete" ]]; then
    require_command git
    require_command cmake
    require_command node
    require_command npm
    require_command patch
    require_command cksum

    for required_patch in "${SUNSHINE_STRICT_SELECTION_PATCH}" "${SUNSHINE_PORTAL_TOKEN_PATCH}"; do
        if [[ ! -f "${required_patch}" ]]; then
            echo "Error: Sunshine patch not found at ${required_patch}." >&2
            exit 1
        fi
    done

    if ! command -v vainfo &>/dev/null; then
        echo "Warning: 'vainfo' is not installed; multi-GPU VA-API selection will be unavailable." >&2
        echo "Install vainfo (Ubuntu) or libva-utils (Arch/Fedora/openSUSE) to enable it." >&2
    fi
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
if ! version_at_least "${PYTHON_VERSION}" "3.11"; then
    echo "Error: Python 3.11+ is required; found ${PYTHON_VERSION}." >&2
    exit 1
fi

if [[ "${INSTALL_MODE}" == "complete" ]]; then
    CMAKE_VERSION="$(cmake --version | awk 'NR == 1 { print $3 }')"
    if ! version_at_least "${CMAKE_VERSION}" "3.26"; then
        echo "Error: CMake newer than 3.25 is required; found ${CMAKE_VERSION}." >&2
        exit 1
    fi

    if ! NODE_VERSION="$(node -p 'process.versions.node' 2>/dev/null)" || [[ -z "${NODE_VERSION}" ]]; then
        echo "Error: Could not determine the installed Node.js version." >&2
        exit 1
    fi
    if ! node_version_supported "${NODE_VERSION}"; then
        echo "Error: Sunshine requires Node.js 20.19+ (20.x) or 22.12+; found ${NODE_VERSION}." >&2
        exit 1
    fi

    select_sunshine_compiler
    configure_build_jobs
    configure_sunshine_cuda

    if git -C "${REPOSITORY_DIR}" rev-parse --is-inside-work-tree &>/dev/null; then
        GIT_JOBS="$(detect_git_jobs)"
        if ! update_sunshine_submodules "${GIT_JOBS}"; then
            echo "Error: Sunshine submodule initialization failed. Fix the Git error above and retry." >&2
            exit 1
        fi
    fi
    if ! git -C "${SUNSHINE_SUBMODULE_DIR}" rev-parse --verify HEAD &>/dev/null; then
        echo "Error: Sunshine submodule checkout is invalid or incomplete. Run the installer again to repair it." >&2
        exit 1
    fi

    for required_path in CMakeLists.txt package.json package-lock.json third-party/moonlight-common-c/CMakeLists.txt; do
        if [[ ! -e "${SUNSHINE_SUBMODULE_DIR}/${required_path}" ]]; then
            echo "Error: Sunshine submodule is incomplete (missing ${required_path})." >&2
            echo "Run: git submodule update --init --recursive" >&2
            exit 1
        fi
    done
    if ! grep -q "MONITORIZE_STRICT_SELECTION_FAILED" "${SUNSHINE_SUBMODULE_DIR}/src/video.cpp"; then
        echo "Applying Monitorize strict Sunshine encoder and codec selection patch…"
        if ! patch --batch --forward -d "${SUNSHINE_SUBMODULE_DIR}" -p1 < "${SUNSHINE_STRICT_SELECTION_PATCH}"; then
            echo "Error: Could not apply the Monitorize Sunshine strict-selection patch." >&2
            exit 1
        fi
    fi
    if ! grep -q "SUNSHINE_PORTAL_TOKEN_SCOPE" "${SUNSHINE_SUBMODULE_DIR}/src/platform/linux/portalgrab.cpp"; then
        echo "Applying Monitorize portal restore-token scope patch…"
        if ! patch --batch --forward -d "${SUNSHINE_SUBMODULE_DIR}" -p1 < "${SUNSHINE_PORTAL_TOKEN_PATCH}"; then
            echo "Error: Could not apply the Monitorize portal restore-token scope patch." >&2
            exit 1
        fi
    fi
    check_sunshine_node_modules_permissions
fi

# ── Setup Virtual Environment ────────────────────────────────────────
echo "Setting up Python virtual environment at ${VENV_DIR}…"

# Check if python3-venv is available
if ! python3 -c "import venv" &>/dev/null; then
    echo "Error: The Python 'venv' module is not installed." >&2
    if command -v apt-get &>/dev/null; then
        echo "Please install it by running:  sudo apt install python3-venv" >&2
    elif command -v dnf &>/dev/null; then
        echo "Please install it by running:  sudo dnf install python3-virtualenv" >&2
    elif command -v pacman &>/dev/null; then
        echo "Please install it by running:  sudo pacman -S python-virtualenv" >&2
    else
        echo "Please install the python virtual environment package for your distribution." >&2
    fi
    exit 1
fi

# Create venv with --system-site-packages so it can access the system's dbus-python
python3 -m venv --system-site-packages "${VENV_DIR}"

echo "Installing/updating Python dependencies inside the virtual environment…"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${PROJECT_DIR}/requirements.txt"
echo "✓ Virtual environment dependencies installed"

# Build the small Wayland client that owns native KWin virtual outputs.
if ! "${HELPER_BUILD}" "${HELPER_PATH}"; then
    echo "Error: Could not build the KDE virtual-output helper." >&2
    if command -v dnf &>/dev/null; then
        echo "Install its build tools with: sudo dnf install gcc pkgconf-pkg-config wayland-devel wayland-utils" >&2
    elif command -v apt-get &>/dev/null; then
        echo "Install its build tools with: sudo apt install build-essential pkg-config libwayland-dev wayland-protocols" >&2
    elif command -v pacman &>/dev/null; then
        echo "Install its build tools with: sudo pacman -S gcc pkgconf wayland" >&2
    fi
    exit 1
fi
echo "✓ KDE virtual-output helper installed to ${HELPER_PATH}"

if [[ "${INSTALL_MODE}" == "complete" ]]; then
# ── Build and install the project-local Sunshine backend ─────────────
CMAKE_EXTRA_FLAGS=()
if ! command -v glslc &>/dev/null && ! command -v glslangValidator &>/dev/null; then
    CMAKE_EXTRA_FLAGS+=("-DSUNSHINE_ENABLE_VULKAN=OFF")
    echo "Warning: Vulkan shader tools were not found; building Sunshine without Vulkan encoding." >&2
fi

if (( FORCE_SUNSHINE_REBUILD )); then
    echo "[Monitorize] Forcing a clean Sunshine rebuild…"
    rm -rf "${SUNSHINE_BUILD_DIR}"
fi
configure_sunshine_build_tools
configure_sunshine_build_fingerprint
CMAKE_FRESH_FLAGS=()
if [[ -f "${SUNSHINE_BUILD_DIR}/CMakeCache.txt" ]] && ! sunshine_build_is_current; then
    # CMake compilers and generators are sticky cache entries. A policy,
    # compiler, or other fingerprint change must not inherit stale CUDA state.
    CMAKE_FRESH_FLAGS=(--fresh)
    echo "[Monitorize] Sunshine build configuration changed; refreshing its CMake cache."
fi

if sunshine_build_is_current && (( ! FORCE_SUNSHINE_REBUILD )); then
    echo "[Monitorize] Sunshine build unchanged; reusing existing backend."
else
    echo "[Monitorize] Sunshine build jobs: ${BUILD_JOBS}"
    echo "Building bundled Sunshine with ${SUNSHINE_CXX} (-j${BUILD_JOBS})…"
    mkdir -p "${SUNSHINE_BUILD_DIR}"
    if ! cmake -B "${SUNSHINE_BUILD_DIR}" -S "${SUNSHINE_SUBMODULE_DIR}" \
             "${CMAKE_FRESH_FLAGS[@]}" \
             "${CMAKE_GENERATOR_FLAGS[@]}" \
             -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_C_COMPILER="${SUNSHINE_CC}" \
             -DCMAKE_CXX_COMPILER="${SUNSHINE_CXX}" \
             "${CMAKE_CCACHE_FLAGS[@]}" \
             -DSUNSHINE_ENABLE_TRAY=OFF -DBUILD_TESTS=OFF -DBUILD_DOCS=OFF \
             "${SUNSHINE_CUDA_CMAKE_FLAGS[@]}" \
             -DPython_EXECUTABLE="${VENV_DIR}/bin/python3" -DGLAD_SKIP_PIP_INSTALL=ON \
             "${CMAKE_EXTRA_FLAGS[@]}"; then
        echo "Error: Sunshine configuration failed. Check the missing dependency above and retry." >&2
        exit 1
    fi
    if ! cmake --build "${SUNSHINE_BUILD_DIR}" -j"${BUILD_JOBS}"; then
        echo "Error: Sunshine compilation failed. Check the compiler output above and retry." >&2
        exit 1
    fi
    if [[ ! -x "${SUNSHINE_BUILD_BIN}" || ! -f "${SUNSHINE_BUILD_ASSETS}/web/index.html" ]]; then
        echo "Error: Sunshine build completed without the required binary or web assets." >&2
        exit 1
    fi
    printf '%s\n' "${SUNSHINE_BUILD_FINGERPRINT}" > "${SUNSHINE_BUILD_STAMP}"
fi

install -m 0755 "${SUNSHINE_BUILD_BIN}" "${SUNSHINE_VENV_BIN}"
rm -rf "${SUNSHINE_VENV_ASSETS}"
mkdir -p "${SUNSHINE_VENV_ASSETS}"
cp -aL "${SUNSHINE_BUILD_ASSETS}/." "${SUNSHINE_VENV_ASSETS}/"
echo "✓ Bundled Sunshine installed inside ${VENV_DIR}"

normalize_sunshine_config() {
    local config_path="$1"
    sed -i \
        -e '/^[[:space:]]*origin_pin_allowed[[:space:]]*=/d' \
        -e 's/^[[:space:]]*origin_web_ui_allowed[[:space:]]*=.*/origin_web_ui_allowed = lan/' \
        "${config_path}"
    if ! grep -q '^[[:space:]]*origin_web_ui_allowed[[:space:]]*=' "${config_path}"; then
        printf '\norigin_web_ui_allowed = lan\n' >> "${config_path}"
    fi
}

# Hostname for advertised Sunshine device entries (Option A)
HOST_NAME="$(hostname 2>/dev/null | cut -d. -f1 || echo "Monitorize")"
if [[ -z "${HOST_NAME}" ]]; then
    HOST_NAME="Monitorize"
fi

# Instance 1 (Primary Display - Port 47989)
SUNSHINE_PROFILE_DIR_1="${CONFIG_HOME}/monitorize/sunshine-1"
mkdir -p "${SUNSHINE_PROFILE_DIR_1}"
SUNSHINE_CONF_1="${SUNSHINE_PROFILE_DIR_1}/sunshine.conf"
if [[ ! -f "${SUNSHINE_CONF_1}" ]]; then
    cat > "${SUNSHINE_CONF_1}" <<EOF
# Sunshine configuration isolated for Monitorize Display 1
sunshine_name = ${HOST_NAME} Monitor 1
port = 47989
system_tray = disabled
origin_web_ui_allowed = lan
encoder = 
EOF
    echo "✓ Isolated Sunshine profile 1 (${HOST_NAME} Monitor 1) initialized at ${SUNSHINE_PROFILE_DIR_1}"
fi
normalize_sunshine_config "${SUNSHINE_CONF_1}"
SUNSHINE_APPS_1="${SUNSHINE_PROFILE_DIR_1}/apps.json"
if [[ ! -f "${SUNSHINE_APPS_1}" ]]; then
    cat > "${SUNSHINE_APPS_1}" <<EOF
{
    "apps": [
        {
            "image-path": "desktop.png",
            "name": "Desktop"
        }
    ],
    "env": {
        "PATH": "\$(PATH):\$(HOME)/.local/bin"
    }
}
EOF
fi

# Instance 2 (Secondary / Additional Display - Port 49089)
SUNSHINE_PROFILE_DIR_2="${CONFIG_HOME}/monitorize/sunshine-2"
mkdir -p "${SUNSHINE_PROFILE_DIR_2}"
SUNSHINE_CONF_2="${SUNSHINE_PROFILE_DIR_2}/sunshine.conf"
if [[ ! -f "${SUNSHINE_CONF_2}" ]]; then
    cat > "${SUNSHINE_CONF_2}" <<EOF
# Sunshine configuration isolated for Monitorize Display 2
sunshine_name = ${HOST_NAME} Monitor 2
port = 49089
system_tray = disabled
origin_web_ui_allowed = lan
encoder = 
EOF
    echo "✓ Isolated Sunshine profile 2 (${HOST_NAME} Monitor 2) initialized at ${SUNSHINE_PROFILE_DIR_2}"
fi
normalize_sunshine_config "${SUNSHINE_CONF_2}"
SUNSHINE_APPS_2="${SUNSHINE_PROFILE_DIR_2}/apps.json"
if [[ ! -f "${SUNSHINE_APPS_2}" ]]; then
    cat > "${SUNSHINE_APPS_2}" <<EOF
{
    "apps": [
        {
            "image-path": "desktop.png",
            "name": "Desktop"
        }
    ],
    "env": {
        "PATH": "\$(PATH):\$(HOME)/.local/bin"
    }
}
EOF
fi
fi  # end INSTALL_MODE == complete

# ── Install icon ─────────────────────────────────────────────────────
mkdir -p "${ICON_DIR}"
cp "${ICON_SRC}" "${ICON_DEST}"
echo "✓ Icon installed to ${ICON_DEST}"

# ── Create .desktop file ─────────────────────────────────────────────
mkdir -p "${DESKTOP_DIR}"
remove_legacy_udp_entries
EXEC_PY="$(desktop_quote "${VENV_DIR}/bin/python3")"
WORKING_DIR="$(desktop_string_escape "${PROJECT_DIR}")"

cat > "${DESKTOP_DIR}/${DESKTOP_FILE}" <<EOF
[Desktop Entry]
Type=Application
Name=${APP_NAME}
Comment=Create Sunshine virtual displays for Moonlight clients
Exec=${EXEC_PY} -m monitorize
Icon=${APP_ID}
Terminal=false
Categories=Utility;System;
Keywords=monitor;display;moonlight;sunshine;screen;extend;mirror;streaming;
StartupNotify=true
Path=${WORKING_DIR}
EOF

chmod +x "${DESKTOP_DIR}/${DESKTOP_FILE}"
echo "✓ Desktop entry created at ${DESKTOP_DIR}/${DESKTOP_FILE}"

# KWin exposes its virtual-output protocol only to executables whose desktop
# entry explicitly requests it. Exec must be the helper's exact absolute path.
HELPER_EXEC="$(desktop_quote "${HELPER_PATH}")"
cat > "${DESKTOP_DIR}/${HELPER_DESKTOP_FILE}" <<EOF
[Desktop Entry]
Type=Application
Name=Monitorize KDE Virtual Output
Exec=${HELPER_EXEC}
NoDisplay=true
Terminal=false
X-KDE-Wayland-Interfaces=zkde_screencast_unstable_v1
EOF
echo "✓ KWin virtual-output permission registered"

# ── Refresh desktop database ─────────────────────────────────────────
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "${DESKTOP_DIR}" 2>/dev/null || true
    echo "✓ Desktop database updated"
fi

if command -v kbuildsycoca6 &>/dev/null; then
    kbuildsycoca6 2>/dev/null || true
    echo "✓ KDE service cache updated"
fi

# Refresh icon cache so DEs pick up the new icon immediately
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t "${ICON_CACHE_DIR}" 2>/dev/null || true
    echo "✓ Icon cache updated"
fi

# ── Post-install validation ──────────────────────────────────────────
if ! "${VENV_DIR}/bin/python3" -c 'import PyQt6, dbus, gi'; then
    echo "Error: An installed Python dependency could not be imported." >&2
    echo "Install the distro packages listed in the wiki, then rerun this installer." >&2
    exit 1
fi

if [[ "${INSTALL_MODE}" == "complete" ]]; then
    for required_path in \
        "${SUNSHINE_VENV_BIN}" \
        "${SUNSHINE_VENV_ASSETS}/web/index.html" \
        "${SUNSHINE_CONF_1}" \
        "${SUNSHINE_CONF_2}" \
        "${DESKTOP_DIR}/${DESKTOP_FILE}"; do
        if [[ ! -e "${required_path}" ]]; then
            echo "Error: Post-install validation failed; missing ${required_path}." >&2
            exit 1
        fi
    done
    echo "✓ Python, Sunshine, assets, profiles, and desktop entry validated"
else
    if [[ ! -e "${DESKTOP_DIR}/${DESKTOP_FILE}" ]]; then
        echo "Error: Post-install validation failed; missing ${DESKTOP_DIR}/${DESKTOP_FILE}." >&2
        exit 1
    fi
    echo "✓ Python and desktop entry validated"
fi

if [[ ! -e /dev/uinput || ! -r /dev/uinput || ! -w /dev/uinput ]]; then
    echo "Warning: /dev/uinput is not accessible to this user." >&2
    echo "Touch/input needs the monitorize-input setup from the wiki and a new login session." >&2
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ "${INSTALL_MODE}" == "complete" ]]; then
    echo "  ${APP_NAME} has been installed!  (Complete Install)"
    echo "  It should now appear in your application menu."
    echo ""
    echo "  Sunshine streaming backend and KDE virtual-display"
    echo "  support are installed and ready."
    echo "  Sunshine CUDA: ${SUNSHINE_CUDA_ENABLED} (policy: ${CUDA_POLICY})."
else
    echo "  ${APP_NAME} has been installed!  (Partial Install)"
    echo "  It should now appear in your application menu."
    echo ""
    echo "  Virtual monitor creation is ready. Sunshine streaming"
    echo "  was not installed — use your own streaming backend."
    echo ""
    echo "  To upgrade to a complete install, rerun:  ./install.sh"
fi
echo ""
echo "  Keep this source folder at: ${REPOSITORY_DIR}"
echo "  Moving or deleting it will break the installed launcher."
echo ""
echo "  To uninstall:  ./install.sh remove"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
