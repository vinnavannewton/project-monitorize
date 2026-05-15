#!/usr/bin/env bash
# setup_uinput_permissions.sh
# Run this ONCE with sudo to allow the current user to access /dev/uinput
# without needing root on every launch.

set -e

USER_NAME="${SUDO_USER:-$USER}"
if [[ "$USER_NAME" == "root" ]]; then
    echo "ERROR: Do not run this as root directly. Use: sudo $0"
    exit 1
fi

echo "[setup] Adding $USER_NAME to 'input' group..."
usermod -aG input "$USER_NAME"

echo "[setup] Installing udev rule for /dev/uinput..."
cat > /etc/udev/rules.d/99-monitorize-uinput.rules <<'EOF'
KERNEL=="uinput", GROUP="input", MODE="0660", TAG+="uaccess"
EOF

echo "[setup] Reloading udev rules..."
udevadm control --reload-rules
udevadm trigger --name-match=uinput

echo ""
echo "Done. Please LOG OUT and LOG BACK IN for the group change to take effect."
echo "Then restart Monitorize."
