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
RTP_SENDER_NAME="monitorize-rtp-sender"
RTP_SENDER_BUILD="${PROJECT_DIR}/native/rtp_sender/build.sh"
RTP_SENDER_PATH="${VENV_DIR}/bin/${RTP_SENDER_NAME}"

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

# ── Uninstall ────────────────────────────────────────────────────────
if [[ "${1:-}" == "remove" || "${1:-}" == "uninstall" ]]; then
    echo "Removing ${APP_NAME} desktop entry…"
    rm -f "${DESKTOP_DIR}/${DESKTOP_FILE}"
    rm -f "${DESKTOP_DIR}/${HELPER_DESKTOP_FILE}"
    rm -f "${DESKTOP_DIR}/dev.lizardbyte.app.Sunshine*.desktop"
    rm -f "${ICON_DEST}"
    remove_legacy_udp_entries
    if command -v sudo &>/dev/null; then
        sudo rm -f /usr/local/share/applications/dev.lizardbyte.app.Sunshine*.desktop 2>/dev/null || true
    fi
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

# Check for python3
if ! command -v python3 &>/dev/null; then
    echo "Error: python3 is not installed." >&2
    exit 1
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

if ! "${RTP_SENDER_BUILD}" "${RTP_SENDER_PATH}"; then
    echo "Error: Could not build the deterministic RTP sender." >&2
    echo "Install a C compiler (gcc or clang) and re-run the installer." >&2
    exit 1
fi
echo "✓ Deterministic RTP sender installed to ${RTP_SENDER_PATH}"

# ── Setup Isolated Sunshine Submodule & Profile ──────────────────────
SUNSHINE_SUBMODULE_DIR="${PROJECT_DIR}/../external/sunshine"
SUNSHINE_BUILD_BIN="${SUNSHINE_SUBMODULE_DIR}/build/sunshine"
SUNSHINE_VENV_BIN="${VENV_DIR}/bin/sunshine"

if [[ -f "${SUNSHINE_BUILD_BIN}" ]]; then
    echo "Installing bundled Sunshine binary to ${SUNSHINE_VENV_BIN}…"
    cp -f "${SUNSHINE_BUILD_BIN}" "${SUNSHINE_VENV_BIN}"
    chmod +x "${SUNSHINE_VENV_BIN}"
    echo "✓ Bundled Sunshine binary installed to ${SUNSHINE_VENV_BIN}"
    echo "Installing Sunshine assets to /usr/local/assets…"
    if command -v sudo &>/dev/null; then
        sudo cmake --install "${SUNSHINE_SUBMODULE_DIR}/build"
        # Sunshine is an embedded headless backend for Monitorize. Remove standalone desktop launchers so it never pollutes the application menu.
        sudo rm -f /usr/local/share/applications/dev.lizardbyte.app.Sunshine*.desktop 2>/dev/null || true
        if command -v update-desktop-database &>/dev/null; then
            sudo update-desktop-database /usr/local/share/applications 2>/dev/null || true
        fi
    else
        cmake --install "${SUNSHINE_SUBMODULE_DIR}/build"
        rm -f /usr/local/share/applications/dev.lizardbyte.app.Sunshine*.desktop 2>/dev/null || true
    fi
    echo "✓ Sunshine assets installed"
else
    # Auto-initialize submodule if folder is empty or not checked out
    if [[ ! -f "${SUNSHINE_SUBMODULE_DIR}/CMakeLists.txt" ]] && command -v git &>/dev/null && [[ -d "${PROJECT_DIR}/../.git" ]]; then
        NPROC="$(nproc 2>/dev/null || echo 4)"
        echo "Fetching Sunshine submodule in parallel (${NPROC} jobs, shallow)…"
        git -C "${PROJECT_DIR}/.." submodule update --init --recursive --depth 1 --jobs "${NPROC}" external/sunshine 2>/dev/null || true
    fi

    if [[ -f "${SUNSHINE_SUBMODULE_DIR}/CMakeLists.txt" ]]; then
        if command -v cmake &>/dev/null; then
            NPROC="$(nproc 2>/dev/null || echo 2)"
            echo "Building isolated Sunshine from submodule at ${SUNSHINE_SUBMODULE_DIR} (-j${NPROC})…"
            CMAKE_EXTRA_FLAGS=()
            if ! command -v glslc &>/dev/null && ! command -v glslangValidator &>/dev/null; then
                CMAKE_EXTRA_FLAGS+=("-DSUNSHINE_ENABLE_VULKAN=OFF")
            fi

            mkdir -p "${SUNSHINE_SUBMODULE_DIR}/build"
            if cmake -B "${SUNSHINE_SUBMODULE_DIR}/build" -S "${SUNSHINE_SUBMODULE_DIR}" \
                     -DCMAKE_BUILD_TYPE=Release -DSUNSHINE_ENABLE_TRAY=OFF -DBUILD_TESTS=OFF -DBUILD_DOCS=OFF \
                     -DCUDA_FAIL_ON_MISSING=OFF \
                     -DPython_EXECUTABLE="${VENV_DIR}/bin/python3" -DGLAD_SKIP_PIP_INSTALL=ON \
                     "${CMAKE_EXTRA_FLAGS[@]}" && \
               cmake --build "${SUNSHINE_SUBMODULE_DIR}/build" -j"${NPROC}"; then
                cp -f "${SUNSHINE_BUILD_BIN}" "${SUNSHINE_VENV_BIN}"
                chmod +x "${SUNSHINE_VENV_BIN}"
                echo "✓ Isolated Sunshine compiled and installed to ${SUNSHINE_VENV_BIN}"
                echo "Installing Sunshine assets to /usr/local/assets…"
                if command -v sudo &>/dev/null; then
                    sudo cmake --install "${SUNSHINE_SUBMODULE_DIR}/build"
                    sudo rm -f /usr/local/share/applications/dev.lizardbyte.app.Sunshine*.desktop 2>/dev/null || true
                    if command -v update-desktop-database &>/dev/null; then
                        sudo update-desktop-database /usr/local/share/applications 2>/dev/null || true
                    fi
                else
                    cmake --install "${SUNSHINE_SUBMODULE_DIR}/build"
                    rm -f /usr/local/share/applications/dev.lizardbyte.app.Sunshine*.desktop 2>/dev/null || true
                fi
                echo "✓ Sunshine assets installed"
            else
                echo "Note: Sunshine submodule compilation failed. To install missing build dependencies, run:"
                if command -v dnf &>/dev/null; then
                    echo "  sudo dnf install -y --skip-unavailable openssl-devel opus-devel pipewire-devel glib2-devel wayland-protocols-devel mesa-libgbm-devel libdrm-devel libva-devel libvdpau-devel pulseaudio-libs-devel libcap-devel libevdev-devel libcurl-devel miniupnpc-devel boost-devel numactl-devel glslang libX11-devel libXfixes-devel libXrandr-devel libXtst-devel libXi-devel"
                elif command -v apt-get &>/dev/null; then
                    echo "  sudo apt install -y libssl-dev libopus-dev libpipewire-0.3-dev libglib2.0-dev libwayland-dev wayland-protocols libgbm-dev libdrm-dev libva-dev libvdpau-dev libpulse-dev libcap-dev libevdev-dev libcurl4-openssl-dev libminiupnpc-dev libboost-all-dev libnuma-dev glslang-tools libx11-dev libxfixes-dev libxrandr-dev libxtst-dev libxi-dev"
                elif command -v pacman &>/dev/null; then
                    echo "  sudo pacman -S --needed base-devel openssl opus pipewire glib2 wayland wayland-protocols mesa libdrm libva libvdpau libpulse libcap libevdev curl miniupnpc boost numactl glslang libx11 libxfixes libxrandr libxtst libxi"
                fi
            fi
        else
            echo "Note: 'cmake' was not found. Install cmake and build tools to compile the bundled Sunshine submodule."
        fi
    fi
fi

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
origin_pin_allowed = pc,lan,wan
encoder = 
EOF
    echo "✓ Isolated Sunshine profile 1 (${HOST_NAME} Monitor 1) initialized at ${SUNSHINE_PROFILE_DIR_1}"
fi
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
origin_pin_allowed = pc,lan,wan
encoder = 
EOF
    echo "✓ Isolated Sunshine profile 2 (${HOST_NAME} Monitor 2) initialized at ${SUNSHINE_PROFILE_DIR_2}"
fi
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
Comment=Linux to Android Display Bridge — extend or mirror your desktop to a tablet
Exec=${EXEC_PY} -m monitorize
Icon=${APP_ID}
Terminal=false
Categories=Utility;System;
Keywords=monitor;display;tablet;android;screen;extend;mirror;streaming;
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

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ${APP_NAME} has been installed!"
echo "  It should now appear in your application menu."
echo ""
echo "  KDE native virtual-display support is installed and authorized."
echo ""
echo "  To uninstall:  ./install.sh remove"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
