Name:           monitorize
Version:        0.2.8
Release:        1%{?dist}
Summary:        Linux to Android display bridge

License:        AGPL-3.0-only
URL:            https://github.com/vinnavannewton/project-monitorize
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  desktop-file-utils
BuildRequires:  gcc
BuildRequires:  pkgconf-pkg-config
BuildRequires:  python3-devel
BuildRequires:  python3-wheel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  systemd-rpm-macros
BuildRequires:  wayland-devel

Requires:       android-tools
Requires:       dbus-tools
Requires:       firewalld
Requires:       gstreamer1
Requires:       gstreamer1-plugin-libav
Requires:       gstreamer1-plugin-openh264
Requires:       gstreamer1-plugins-bad-free
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
Requires:       gstreamer1-plugins-ugly
Requires:       iproute
Requires:       iputils
Requires:       kmod
Requires:       openssl
Requires:       pipewire
Requires:       pipewire-gstreamer
Requires:       procps-ng
Requires:       python3-cryptography
Requires:       python3-dbus
Requires:       python3-evdev
Requires:       python3-gobject
Requires:       python3-pyqt6
Requires:       python3-zeroconf
Requires:       qt6-qtdeclarative
Requires:       qt6-qtwayland
Requires:       systemd-udev
Requires:       util-linux
Requires:       xdg-desktop-portal

Requires(post): kmod
Requires(post): systemd-udev
Requires(postun): systemd-udev

Recommends:     (kscreen if plasma-workspace)
Recommends:     (xdg-desktop-portal-kde if plasma-workspace)
Recommends:     (xdg-desktop-portal-gnome if gnome-shell)
Recommends:     (xdg-desktop-portal-hyprland if hyprland)
Recommends:     (nwg-displays if hyprland)

%description
Monitorize turns an Android tablet, laptop, or PC into a secondary monitor for
a Linux desktop. It supports KDE Plasma, GNOME, and Hyprland through PipeWire,
desktop portals, GStreamer, and uinput.


%prep
%autosetup


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel
RPM_OPT_FLAGS="%{build_cflags}" RPM_LD_FLAGS="%{build_ldflags}" \
    linux/native/kde_virtual_output/build.sh monitorize-kde-virtual-output


%install
%pyproject_install
%pyproject_save_files monitorize

install -Dpm 0644 packaging/fedora/monitorize.desktop \
    %{buildroot}%{_datadir}/applications/monitorize.desktop
install -Dpm 0644 linux/monitorize/assets/monitorize_desktop_logo.png \
    %{buildroot}%{_datadir}/icons/hicolor/192x192/apps/monitorize.png
install -Dpm 0644 packaging/fedora/70-monitorize-uinput.rules \
    %{buildroot}%{_udevrulesdir}/70-monitorize-uinput.rules
install -Dpm 0755 monitorize-kde-virtual-output \
    %{buildroot}%{_bindir}/monitorize-kde-virtual-output
install -Dpm 0644 packaging/fedora/monitorize-kde-virtual-output.desktop \
    %{buildroot}%{_datadir}/applications/monitorize-kde-virtual-output.desktop
install -Dpm 0644 packaging/fedora/monitorize.xml \
    %{buildroot}%{_prefix}/lib/firewalld/services/monitorize.xml
install -d %{buildroot}%{_localstatedir}/lib/monitorize


%check
desktop-file-validate %{buildroot}%{_datadir}/applications/monitorize.desktop
desktop-file-validate \
    %{buildroot}%{_datadir}/applications/monitorize-kde-virtual-output.desktop


%post
/usr/sbin/modprobe uinput >/dev/null 2>&1 || :
/usr/bin/udevadm control --reload-rules >/dev/null 2>&1 || :
/usr/bin/udevadm trigger --subsystem-match=misc --sysname-match=uinput >/dev/null 2>&1 || :

state=%{_localstatedir}/lib/monitorize/firewall-zones
mkdir -p "$(dirname "$state")"
touch "$state"
if /usr/bin/firewall-cmd --state >/dev/null 2>&1; then
    /usr/bin/firewall-cmd --reload --quiet || :
    zones=$(/usr/bin/firewall-cmd --get-active-zones 2>/dev/null \
        | sed -n '/^[^[:space:]]/s/[[:space:]].*//p')
    if [ -z "$zones" ]; then
        zones=$(/usr/bin/firewall-cmd --get-default-zone 2>/dev/null || :)
    fi
    for zone in $zones; do
        if ! /usr/bin/firewall-cmd --permanent --zone="$zone" \
                --query-service=monitorize >/dev/null 2>&1 \
                && /usr/bin/firewall-cmd --permanent --zone="$zone" \
                --add-service=monitorize --quiet; then
            grep -qxF "$zone" "$state" || echo "$zone" >> "$state"
        fi
    done
    /usr/bin/firewall-cmd --reload --quiet || :
else
    zone=$(/usr/bin/firewall-offline-cmd --get-default-zone 2>/dev/null || :)
    if [ -n "$zone" ] \
            && ! /usr/bin/firewall-offline-cmd --zone="$zone" \
                --query-service=monitorize >/dev/null 2>&1 \
            && /usr/bin/firewall-offline-cmd --zone="$zone" \
                --add-service=monitorize >/dev/null; then
        grep -qxF "$zone" "$state" || echo "$zone" >> "$state"
    fi
fi


%preun
if [ "$1" -eq 0 ]; then
    state=%{_localstatedir}/lib/monitorize/firewall-zones
    if [ -f "$state" ]; then
        while IFS= read -r zone; do
            [ -n "$zone" ] || continue
            if /usr/bin/firewall-cmd --state >/dev/null 2>&1; then
                /usr/bin/firewall-cmd --permanent --zone="$zone" \
                    --remove-service=monitorize --quiet || :
            else
                /usr/bin/firewall-offline-cmd --zone="$zone" \
                    --remove-service=monitorize >/dev/null 2>&1 || :
            fi
        done < "$state"
        rm -f "$state"
    fi
    /usr/bin/firewall-cmd --reload --quiet >/dev/null 2>&1 || :
fi


%postun
/usr/bin/udevadm control --reload-rules >/dev/null 2>&1 || :


%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/monitorize
%{_bindir}/monitorize-kde-virtual-output
%{_datadir}/applications/monitorize.desktop
%{_datadir}/applications/monitorize-kde-virtual-output.desktop
%{_datadir}/icons/hicolor/192x192/apps/monitorize.png
%{_udevrulesdir}/70-monitorize-uinput.rules
%{_prefix}/lib/firewalld/services/monitorize.xml
%dir %{_localstatedir}/lib/monitorize
%ghost %{_localstatedir}/lib/monitorize/firewall-zones


%changelog
* Sat Jul 11 2026 Monitorize contributors <noreply@example.com> - 0.2.8-1
- Complete Fedora 44 runtime, KDE helper, input ACL, and firewall packaging.

* Thu Jul 09 2026 Monitorize contributors <noreply@example.com> - 0.2.7-1
- Initial Fedora Copr package.
