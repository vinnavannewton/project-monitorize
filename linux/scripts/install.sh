#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Monitorize — Linux desktop installer
#
# Creates a .desktop entry so Monitorize appears in the application
# menu on KDE, GNOME, Hyprland, and other freedesktop-compliant DEs.
#
# Usage:
#   cd linux/scripts
#   ./install.sh          # install
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

# XDG standard locations
DESKTOP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/192x192/apps"
ICON_DEST="${ICON_DIR}/${APP_ID}.png"

remove_legacy_udp_entries() {
    rm -f "${DESKTOP_DIR}/monitorize-udp.desktop"
    rm -f "${DESKTOP_DIR}/monitorize-udp-kde-virtual-output.desktop"
    rm -f "${ICON_DIR}/monitorize-udp.png"
}

desktop_quote() {
    local value="${1//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '"%s"' "${value}"
}

version_at_least() {
    [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
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
        command -v "${cc}" &>/dev/null && command -v "${cxx}" &>/dev/null || continue
        version="$("${cxx}" -dumpfullversion -dumpversion 2>/dev/null || "${cxx}" --version | head -n1 | grep -oE '[0-9]+' | head -n1)"
        [[ -n "${version}" ]] || continue
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
    [[ "${SUNSHINE_CCACHE}" == "on" ]] && echo "[Monitorize] ccache enabled"
}

configure_sunshine_build_fingerprint() {
    local commit patch_checksum vulkan
    commit="$(git -C "${SUNSHINE_SUBMODULE_DIR}" rev-parse HEAD)"
    patch_checksum="$(cksum "${SUNSHINE_STRICT_SELECTION_PATCH}" | awk '{print $1 ":" $2}')"
    vulkan="on"
    [[ " ${CMAKE_EXTRA_FLAGS[*]} " == *" -DSUNSHINE_ENABLE_VULKAN=OFF "* ]] && vulkan="off"
    SUNSHINE_BUILD_FINGERPRINT="commit=${commit}|type=Release|tray=off|tests=off|docs=off|cuda=auto|vulkan=${vulkan}|generator=${SUNSHINE_BUILD_GENERATOR}|ccache=${SUNSHINE_CCACHE}|cc=${SUNSHINE_CC}|cxx=${SUNSHINE_CXX}|strict-patch=${patch_checksum}"
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

    blocked_path="$(find "${node_modules}" -type d ! -writable -print -quit 2>/dev/null)"
    if [[ -n "${blocked_path}" ]]; then
        echo "Error: Sunshine's generated npm cache is not writable: ${blocked_path}" >&2
        echo "This is normally left behind by an earlier sudo or container build." >&2
        echo "Repair it with:" >&2
        echo "  sudo chown -R \"$(id -un)\":\"$(id -gn)\" \"${node_modules}\"" >&2
        echo "Then rerun this installer without sudo." >&2
        exit 1
    fi
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
        IFS= read -rsn1 key
        case "${key}" in
            $'\x1b')
                read -rsn2 key
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
}

# ── Uninstall ────────────────────────────────────────────────────────
FORCE_SUNSHINE_REBUILD=0
if [[ "${1:-}" == "--rebuild-sunshine" || "${MONITORIZE_REBUILD_SUNSHINE:-}" == "1" ]]; then
    FORCE_SUNSHINE_REBUILD=1
fi

if [[ "${1:-}" == "remove" || "${1:-}" == "uninstall" ]]; then
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
        gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true
    fi
    if command -v kbuildsycoca6 &>/dev/null; then
        kbuildsycoca6 2>/dev/null || true
    fi
    echo "✓ ${APP_NAME} has been removed from the application menu."
    exit 0
fi

# ── Pre-flight checks ────────────────────────────────────────────────
select_install_mode

if [[ ! -f "${ICON_SRC}" ]]; then
    echo "Error: Icon not found at ${ICON_SRC}" >&2
    exit 1
fi

if [[ ! -d "${PROJECT_DIR}/monitorize" ]]; then
    echo "Error: Python package not found at ${PROJECT_DIR}/monitorize" >&2
    exit 1
fi

require_command python3
require_command git

if [[ "${INSTALL_MODE}" == "complete" ]]; then
    require_command cmake
    require_command node
    require_command npm

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
    CMAKE_VERSION="$(cmake --version | head -n1 | awk '{print $3}')"
    if ! version_at_least "${CMAKE_VERSION}" "3.26"; then
        echo "Error: CMake newer than 3.25 is required; found ${CMAKE_VERSION}." >&2
        exit 1
    fi

    select_sunshine_compiler
    configure_build_jobs

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

if sunshine_build_is_current && (( ! FORCE_SUNSHINE_REBUILD )); then
    echo "[Monitorize] Sunshine build unchanged; reusing existing backend."
else
    echo "[Monitorize] Sunshine build jobs: ${BUILD_JOBS}"
    echo "Building bundled Sunshine with ${SUNSHINE_CXX} (-j${BUILD_JOBS})…"
    mkdir -p "${SUNSHINE_BUILD_DIR}"
    if ! cmake -B "${SUNSHINE_BUILD_DIR}" -S "${SUNSHINE_SUBMODULE_DIR}" \
             "${CMAKE_GENERATOR_FLAGS[@]}" \
             -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_C_COMPILER="${SUNSHINE_CC}" \
             -DCMAKE_CXX_COMPILER="${SUNSHINE_CXX}" \
             "${CMAKE_CCACHE_FLAGS[@]}" \
             -DSUNSHINE_ENABLE_TRAY=OFF -DBUILD_TESTS=OFF -DBUILD_DOCS=OFF \
             -DCUDA_FAIL_ON_MISSING=OFF \
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
SUNSHINE_PROFILE_DIR_1="${HOME}/.config/monitorize/sunshine-1"
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
SUNSHINE_PROFILE_DIR_2="${HOME}/.config/monitorize/sunshine-2"
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
Path=${PROJECT_DIR}
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
    gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" 2>/dev/null || true
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
