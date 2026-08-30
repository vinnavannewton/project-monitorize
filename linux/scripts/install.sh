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
SUNSHINE_VENV_BIN="${VENV_DIR}/bin/sunshine"
SUNSHINE_VENV_ASSETS="${VENV_DIR}/share/monitorize/sunshine/assets"

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
    if [[ -n "${MONITORIZE_BUILD_JOBS:-}" ]]; then
        if [[ ! "${MONITORIZE_BUILD_JOBS}" =~ ^[1-9][0-9]*$ ]]; then
            echo "Error: MONITORIZE_BUILD_JOBS must be a positive integer." >&2
            exit 1
        fi
        BUILD_JOBS="${MONITORIZE_BUILD_JOBS}"
    elif (( detected > 4 )); then
        BUILD_JOBS=4
    else
        BUILD_JOBS="${detected}"
    fi
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

# ── Uninstall ────────────────────────────────────────────────────────
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
require_command cmake
require_command node
require_command npm

if ! command -v vainfo &>/dev/null; then
    echo "Warning: 'vainfo' is not installed; multi-GPU VA-API selection will be unavailable." >&2
    echo "Install vainfo (Ubuntu) or libva-utils (Arch/Fedora/openSUSE) to enable it." >&2
fi

PYTHON_VERSION="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
if ! version_at_least "${PYTHON_VERSION}" "3.11"; then
    echo "Error: Python 3.11+ is required; found ${PYTHON_VERSION}." >&2
    exit 1
fi

CMAKE_VERSION="$(cmake --version | head -n1 | awk '{print $3}')"
if ! version_at_least "${CMAKE_VERSION}" "3.26"; then
    echo "Error: CMake newer than 3.25 is required; found ${CMAKE_VERSION}." >&2
    exit 1
fi

select_sunshine_compiler
configure_build_jobs

if [[ -d "${REPOSITORY_DIR}/.git" ]]; then
    echo "Initializing the bundled Sunshine submodule…"
    if ! git -C "${REPOSITORY_DIR}" submodule update --init --recursive external/sunshine; then
        echo "Error: Sunshine submodule initialization failed. Fix the Git error above and retry." >&2
        exit 1
    fi
fi

for required_path in CMakeLists.txt package.json package-lock.json third-party/moonlight-common-c/CMakeLists.txt; do
    if [[ ! -e "${SUNSHINE_SUBMODULE_DIR}/${required_path}" ]]; then
        echo "Error: Sunshine submodule is incomplete (missing ${required_path})." >&2
        echo "Run: git submodule update --init --recursive" >&2
        exit 1
    fi
done
check_sunshine_node_modules_permissions

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

# ── Build and install the project-local Sunshine backend ─────────────
echo "Building bundled Sunshine with ${SUNSHINE_CXX} (-j${BUILD_JOBS})…"
CMAKE_EXTRA_FLAGS=()
if ! command -v glslc &>/dev/null && ! command -v glslangValidator &>/dev/null; then
    CMAKE_EXTRA_FLAGS+=("-DSUNSHINE_ENABLE_VULKAN=OFF")
    echo "Warning: Vulkan shader tools were not found; building Sunshine without Vulkan encoding." >&2
fi

mkdir -p "${SUNSHINE_BUILD_DIR}"
if ! cmake -B "${SUNSHINE_BUILD_DIR}" -S "${SUNSHINE_SUBMODULE_DIR}" \
         -DCMAKE_BUILD_TYPE=Release \
         -DCMAKE_C_COMPILER="${SUNSHINE_CC}" \
         -DCMAKE_CXX_COMPILER="${SUNSHINE_CXX}" \
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
if [[ ! -x "${SUNSHINE_BUILD_BIN}" || ! -d "${SUNSHINE_BUILD_ASSETS}/web" ]]; then
    echo "Error: Sunshine build completed without the required binary or web assets." >&2
    exit 1
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

if [[ ! -e /dev/uinput || ! -r /dev/uinput || ! -w /dev/uinput ]]; then
    echo "Warning: /dev/uinput is not accessible to this user." >&2
    echo "Streaming will work, but touch/input needs the monitorize-input setup from the wiki and a new login session." >&2
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ${APP_NAME} has been installed!"
echo "  It should now appear in your application menu."
echo ""
echo "  KDE native virtual-display support is installed and authorized."
echo ""
echo "  Keep this source folder at: ${REPOSITORY_DIR}"
echo "  Moving or deleting it will break the installed launcher."
echo ""
echo "  To uninstall:  ./install.sh remove"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
