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
HELPER_DESKTOP_FILE="${HELPER_NAME}.desktop"

# XDG standard locations
DESKTOP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/192x192/apps"
ICON_DEST="${ICON_DIR}/${APP_ID}.png"

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
    rm -f "${ICON_DEST}"
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

# ── Install icon ─────────────────────────────────────────────────────
mkdir -p "${ICON_DIR}"
cp "${ICON_SRC}" "${ICON_DEST}"
echo "✓ Icon installed to ${ICON_DEST}"

# ── Create .desktop file ─────────────────────────────────────────────
mkdir -p "${DESKTOP_DIR}"
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
StartupWMClass=monitorize
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
