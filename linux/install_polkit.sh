#!/bin/bash
set -e
POLICY_DIR="/usr/share/polkit-1/actions"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
sudo cp "$SCRIPT_DIR/com.monitorize.touch.policy" "$POLICY_DIR/"
sudo chmod 644 "$POLICY_DIR/com.monitorize.touch.policy"
echo "Polkit policy installed. pkexec will now show a proper password dialog."
