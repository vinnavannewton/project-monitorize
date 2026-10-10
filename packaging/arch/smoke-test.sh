#!/usr/bin/env bash
set -euo pipefail

source /packaging/dependencies.sh

# One full upgrade keeps the fresh Arch image and repository state in sync.
pacman -Syu --noconfirm --needed desktop-file-utils libcap \
    "${monitorize_runtime_deps[@]}"
# The official Arch container excludes usr/share/doc/* via NoExtract. Remove
# that exclusion in this disposable test so pacman -Qkk can verify the package.
sed -i '/^[[:space:]]*NoExtract[[:space:]]*=.*usr\/share\/doc\/\*/d' /etc/pacman.conf
mkdir -p /root/.config/monitorize
printf 'preserve me\n' > /root/.config/monitorize/packaging-check

pacman -U --noconfirm /tmp/monitorize.pkg.tar.zst
pacman -Qi monitorize
pacman -Ql monitorize > /tmp/monitorize-file-list
pacman -Qkk monitorize
test -f /usr/share/doc/monitorize/README.md
getent group monitorize-input
getcap /usr/libexec/monitorize/sunshine | grep -q 'cap_sys_admin'
test -x /usr/bin/monitorize
test -x /usr/bin/monitorize-kde-virtual-output
test -x /usr/libexec/monitorize/sunshine
test -x /usr/libexec/monitorize/monitorize-system-setup
test -d /usr/share/monitorize/sunshine/assets/web
test -f /usr/lib/firewalld/services/monitorize.xml
test -f /etc/ufw/applications.d/monitorize
test -f /usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize.system-setup.policy
test -f /usr/lib/udev/rules.d/70-monitorize-uinput.rules
test -f /usr/lib/sysusers.d/monitorize.conf
test -f /usr/lib/modules-load.d/monitorize.conf
test ! -e /usr/bin/sunshine
test ! -e /usr/local/bin/sunshine
desktop-file-validate /usr/share/applications/monitorize.desktop
desktop-file-validate /usr/share/applications/monitorize-kde-virtual-output.desktop
helper_links="$(ldd /usr/bin/monitorize-kde-virtual-output)"
sunshine_links="$(ldd /usr/libexec/monitorize/sunshine)"
[[ "${helper_links}" != *'not found'* ]]
[[ "${sunshine_links}" != *'not found'* ]]

# CAP_SYS_ADMIN may be outside rootless Podman's bounding set. Clear it only in
# this disposable container to run non-GPU import/version checks.
setcap -r /usr/libexec/monitorize/sunshine
/usr/libexec/monitorize/sunshine --version
MONITORIZE_SUNSHINE_BIN=/usr/libexec/monitorize/sunshine \
MONITORIZE_SUNSHINE_ASSETS_DIR=/usr/share/monitorize/sunshine/assets \
QT_QPA_PLATFORM=offscreen python - <<'PYTHON'
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlComponent, QQmlEngine
from PyQt6.QtQuickWidgets import QQuickWidget
from monitorize.desktop import main_window
from monitorize.platform.sunshine_service import get_sunshine_assets_dir, get_sunshine_candidates
from monitorize.platform.utils import QML_DIR

command = get_sunshine_candidates()[0]
assert command[0] == '/usr/libexec/monitorize/sunshine'
assert get_sunshine_assets_dir(command[0]) == '/usr/share/monitorize/sunshine/assets'
assert QQuickWidget is not None and main_window is not None
app = QGuiApplication([])
engine = QQmlEngine()
component = QQmlComponent(engine, QUrl.fromLocalFile(f'{QML_DIR}/main.qml'))
assert not component.isError(), '\n'.join(error.toString() for error in component.errors())
PYTHON

# Reinstallation must reapply the capability to the packaged Sunshine binary.
pacman -U --noconfirm /tmp/monitorize.pkg.tar.zst
getcap /usr/libexec/monitorize/sunshine | grep -q 'cap_sys_admin'
pacman -R --noconfirm monitorize
test ! -e /usr/bin/monitorize
test ! -e /usr/bin/monitorize-kde-virtual-output
test ! -e /usr/libexec/monitorize/sunshine
test ! -e /usr/libexec/monitorize/monitorize-system-setup
test ! -e /usr/share/monitorize
test ! -e /usr/share/applications/monitorize.desktop
test -f /root/.config/monitorize/packaging-check
