Name:           monitorize
Version:        0.2.7
Release:        1%{?dist}
Summary:        Linux to Android display bridge

License:        AGPL-3.0-only
URL:            https://github.com/vinnavannewton/project-monitorize
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  desktop-file-utils
BuildRequires:  python3-devel
BuildRequires:  python3-wheel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  systemd-rpm-macros

Requires:       gstreamer1
Requires:       gstreamer1-plugin-openh264
Requires:       gstreamer1-plugins-bad-free
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
Requires:       gstreamer1-plugins-ugly-free
Requires:       iproute
Requires:       kmod
Requires:       pipewire
Requires:       python3-cryptography
Requires:       python3-dbus
Requires:       python3-evdev
Requires:       python3-gobject
Requires:       python3-pyqt6
Requires:       python3-zeroconf
Requires:       shadow-utils
Requires:       systemd-udev
Requires:       util-linux
Requires:       xdg-desktop-portal

Requires(pre):  shadow-utils
Requires(post): kmod
Requires(post): systemd-udev
Requires(postun): systemd-udev

Recommends:     android-tools
Recommends:     kscreen
Recommends:     xdg-desktop-portal-gnome
Recommends:     xdg-desktop-portal-kde
Recommends:     xdg-desktop-portal-wlr

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


%install
%pyproject_install
%pyproject_save_files monitorize

install -Dpm 0644 packaging/fedora/monitorize.desktop \
    %{buildroot}%{_datadir}/applications/monitorize.desktop
install -Dpm 0644 linux/monitorize/assets/monitorize_desktop_logo.png \
    %{buildroot}%{_datadir}/icons/hicolor/192x192/apps/monitorize.png
install -Dpm 0644 packaging/fedora/70-monitorize-uinput.rules \
    %{buildroot}%{_udevrulesdir}/70-monitorize-uinput.rules


%check
desktop-file-validate %{buildroot}%{_datadir}/applications/monitorize.desktop


%pre
getent group monitorize >/dev/null || groupadd -r monitorize


%post
/usr/sbin/modprobe uinput >/dev/null 2>&1 || :
/usr/bin/udevadm control --reload-rules >/dev/null 2>&1 || :
/usr/bin/udevadm trigger --subsystem-match=misc --sysname-match=uinput >/dev/null 2>&1 || :


%postun
/usr/bin/udevadm control --reload-rules >/dev/null 2>&1 || :


%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/monitorize
%{_datadir}/applications/monitorize.desktop
%{_datadir}/icons/hicolor/192x192/apps/monitorize.png
%{_udevrulesdir}/70-monitorize-uinput.rules


%changelog
* Thu Jul 09 2026 Monitorize contributors <noreply@example.com> - 0.2.7-1
- Initial Fedora Copr package.
