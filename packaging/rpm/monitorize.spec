%global sunshine_commit e3ce79f3b966df388e905a3c6b3784832a328e34
%global sunshine_ffmpeg_tag v2026.724.203728
%global sunshine_ffmpeg_sha256 2c27d4694b4ed0e734f497d4bd62f1b3662cbbc4ded2a69f2dc4b703441eebb3

Name:           monitorize
Version:        0.2.8
Release:        2%{?dist}
Summary:        Sunshine-backed virtual displays for Moonlight clients

License:        GPL-3.0-only
URL:            https://github.com/vinnavannewton/project-monitorize
Source0:        %{name}-%{version}.tar.gz
Source1:        https://github.com/LizardByte/build-deps/releases/download/%{sunshine_ffmpeg_tag}/Linux-x86_64-ffmpeg.tar.gz
Source2:        monitorize.sysusers

ExclusiveArch:  x86_64

BuildRequires:  bash
BuildRequires:  boost-devel >= 1.89.0
BuildRequires:  cmake >= 3.26
BuildRequires:  desktop-file-utils
BuildRequires:  firewalld-filesystem
BuildRequires:  gcc15
BuildRequires:  gcc15-c++
BuildRequires:  git
BuildRequires:  glib2-devel
BuildRequires:  glslc
BuildRequires:  json-devel
BuildRequires:  libcap-devel
BuildRequires:  libcurl-devel
BuildRequires:  libdrm-devel
BuildRequires:  libevdev-devel
BuildRequires:  libgudev
BuildRequires:  libva-devel
BuildRequires:  libX11-devel
BuildRequires:  libxcb-devel
BuildRequires:  libXcursor-devel
BuildRequires:  libXfixes-devel
BuildRequires:  libXi-devel
BuildRequires:  libXinerama-devel
BuildRequires:  libXrandr-devel
BuildRequires:  libXtst-devel
BuildRequires:  make
BuildRequires:  mesa-libGL-devel
BuildRequires:  mesa-libgbm-devel
BuildRequires:  miniupnpc-devel
BuildRequires:  nodejs22
BuildRequires:  nodejs22-npm
BuildRequires:  numactl-devel
BuildRequires:  openssl-devel
BuildRequires:  opus-devel
BuildRequires:  pipewire-devel
BuildRequires:  pkgconf-pkg-config
BuildRequires:  pulseaudio-libs-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  python3-cairo
BuildRequires:  python3-dbus
BuildRequires:  python3-devel
BuildRequires:  python3-gobject
BuildRequires:  python3-jinja2
BuildRequires:  python3-pip
BuildRequires:  python3-pyqt6
BuildRequires:  python3-setuptools
BuildRequires:  python3-wheel
BuildRequires:  redhat-rpm-config
BuildRequires:  systemd-rpm-macros
BuildRequires:  systemd-udev
BuildRequires:  vulkan-loader-devel
BuildRequires:  wayland-devel
BuildRequires:  wayland-protocols-devel

Requires:       avahi
Requires:       firewalld-filesystem
Requires:       iproute
Requires:       libva-utils
Requires:       polkit
Requires:       python3-cairo
Requires:       python3-dbus
Requires:       python3-gobject
Requires:       python3-jinja2
Requires:       python3-pyqt6
Requires:       systemd-udev
Requires:       which
Requires:       xdg-desktop-portal
Requires(post): kmod
%{?sysusers_requires_compat}

%description
Monitorize creates compositor-native virtual displays on KDE Plasma, GNOME,
and Hyprland and streams them to Moonlight clients through isolated, bundled
Sunshine instances.


%prep
%autosetup
patch --batch --forward -d external/sunshine -p1 < packaging/sunshine-strict-selection.patch
mkdir .ffmpeg-prepared
tar -xzf %{SOURCE1} -C .ffmpeg-prepared --strip-components=1 --no-same-owner
# Fedora 44 ships a newer compatible Boost. Sunshine requests 1.89 EXACT and
# otherwise downloads the whole Boost superproject, which is inappropriate for
# a Fedora RPM build. Keep 1.89 as the minimum while using Fedora's package.
sed -i 's/find_package(Boost CONFIG ${BOOST_VERSION} EXACT /find_package(Boost CONFIG ${BOOST_VERSION} /' \
    external/sunshine/cmake/dependencies/Boost_Sunshine.cmake


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel

CC=/usr/bin/gcc-15 \
RPM_OPT_FLAGS="%{build_cflags}" \
RPM_LD_FLAGS="%{build_ldflags}" \
    linux/native/kde_virtual_output/build.sh monitorize-kde-virtual-output

export CC=/usr/bin/gcc-15
export CXX=/usr/bin/g++-15
export CFLAGS="%{build_cflags}"
export CXXFLAGS="%{build_cxxflags}"
export LDFLAGS="%{build_ldflags}"
export BRANCH=monitorize
export BUILD_VERSION=0.0.0
export COMMIT=%{sunshine_commit}

cmake -B sunshine-build -S external/sunshine \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=%{_prefix} \
    -DBUILD_DOCS=OFF \
    -DBUILD_TESTS=OFF \
    -DBOOST_USE_STATIC=OFF \
    -DCUDA_FAIL_ON_MISSING=OFF \
    -DFFMPEG_PREPARED_BINARIES="$PWD/.ffmpeg-prepared" \
    -DGLAD_SKIP_PIP_INSTALL=ON \
    -DNPM=/usr/bin/npm \
    -DPython_EXECUTABLE=/usr/bin/python3 \
    -DSUNSHINE_ASSETS_DIR=%{_datadir}/monitorize/sunshine/assets \
    -DSUNSHINE_ENABLE_CUDA=ON \
    -DSUNSHINE_ENABLE_DRM=ON \
    -DSUNSHINE_ENABLE_KWIN=ON \
    -DSUNSHINE_ENABLE_PORTAL=ON \
    -DSUNSHINE_ENABLE_TRAY=OFF \
    -DSUNSHINE_ENABLE_VAAPI=ON \
    -DSUNSHINE_ENABLE_VULKAN=ON \
    -DSUNSHINE_ENABLE_WAYLAND=ON \
    -DSUNSHINE_ENABLE_X11=ON \
    -DSUNSHINE_EXECUTABLE_PATH=%{_libexecdir}/monitorize/sunshine
cmake --build sunshine-build --parallel %{_smp_build_ncpus}


%install
%pyproject_install
%pyproject_save_files monitorize

install -Dpm 0755 monitorize-kde-virtual-output \
    %{buildroot}%{_bindir}/monitorize-kde-virtual-output
install -Dpm 0755 sunshine-build/sunshine \
    %{buildroot}%{_libexecdir}/monitorize/sunshine
install -Dpm 0755 packaging/common/monitorize-system-setup \
    %{buildroot}%{_libexecdir}/monitorize/monitorize-system-setup
mkdir -p %{buildroot}%{_datadir}/monitorize/sunshine/assets
cp -aL sunshine-build/assets/. \
    %{buildroot}%{_datadir}/monitorize/sunshine/assets/

cat > %{buildroot}%{_bindir}/monitorize <<'WRAPPER'
#!/usr/bin/bash
export MONITORIZE_SUNSHINE_BIN=/usr/libexec/monitorize/sunshine
export MONITORIZE_SUNSHINE_ASSETS_DIR=/usr/share/monitorize/sunshine/assets
exec /usr/bin/python3 -m monitorize "$@"
WRAPPER
chmod 0755 %{buildroot}%{_bindir}/monitorize

install -Dpm 0644 packaging/fedora/monitorize.desktop \
    %{buildroot}%{_datadir}/applications/monitorize.desktop
install -Dpm 0644 packaging/fedora/monitorize-kde-virtual-output.desktop \
    %{buildroot}%{_datadir}/applications/monitorize-kde-virtual-output.desktop
install -Dpm 0644 linux/monitorize/assets/monitorize_desktop_logo.png \
    %{buildroot}%{_datadir}/icons/hicolor/512x512/apps/monitorize.png
install -Dpm 0644 packaging/fedora/monitorize.xml \
    %{buildroot}%{_prefix}/lib/firewalld/services/monitorize.xml
install -Dpm 0644 packaging/common/io.github.vinnavannewton.monitorize.system-setup.policy \
    %{buildroot}%{_datadir}/polkit-1/actions/io.github.vinnavannewton.monitorize.system-setup.policy
install -Dpm 0644 packaging/fedora/70-monitorize-uinput.rules \
    %{buildroot}%{_udevrulesdir}/70-monitorize-uinput.rules
install -Dpm 0644 %{SOURCE2} \
    %{buildroot}%{_sysusersdir}/monitorize.conf
install -dpm 0755 %{buildroot}%{_modulesloaddir}
printf 'uinput\n' > %{buildroot}%{_modulesloaddir}/monitorize.conf
install -Dpm 0644 LICENSE \
    %{buildroot}%{_licensedir}/%{name}/Monitorize-LICENSE
install -Dpm 0644 external/sunshine/LICENSE \
    %{buildroot}%{_licensedir}/%{name}/Sunshine-LICENSE


%check
desktop-file-validate %{buildroot}%{_datadir}/applications/monitorize.desktop
desktop-file-validate %{buildroot}%{_datadir}/applications/monitorize-kde-virtual-output.desktop
bash -n %{buildroot}%{_bindir}/monitorize
test -x %{buildroot}%{_bindir}/monitorize-kde-virtual-output
test -x %{buildroot}%{_libexecdir}/monitorize/sunshine
test -x %{buildroot}%{_libexecdir}/monitorize/monitorize-system-setup
python3 -c 'from pathlib import Path; compile(Path("%{buildroot}%{_libexecdir}/monitorize/monitorize-system-setup").read_text(), "monitorize-system-setup", "exec")'
test -d %{buildroot}%{_datadir}/monitorize/sunshine/assets/web
test ! -e %{buildroot}%{_bindir}/sunshine
test ! -e %{buildroot}/usr/local
! ldd %{buildroot}%{_bindir}/monitorize-kde-virtual-output | grep -q 'not found'
%{buildroot}%{_libexecdir}/monitorize/sunshine --version
MONITORIZE_SUNSHINE_BIN=%{buildroot}%{_libexecdir}/monitorize/sunshine \
MONITORIZE_SUNSHINE_ASSETS_DIR=%{buildroot}%{_datadir}/monitorize/sunshine/assets \
PYTHONPATH=%{buildroot}%{python3_sitelib} \
python3 - <<'PYTHON'
from PyQt6.QtQuickWidgets import QQuickWidget
from monitorize.desktop import main_window
from monitorize.platform.sunshine_service import get_sunshine_assets_dir, get_sunshine_candidates

command = get_sunshine_candidates()[0]
assert command[0].endswith("/usr/libexec/monitorize/sunshine")
assert get_sunshine_assets_dir(command[0]).endswith("/usr/share/monitorize/sunshine/assets")
assert QQuickWidget is not None
assert main_window is not None
PYTHON


%pre
%sysusers_create_compat %{SOURCE2}


%post
%udev_rules_update
/usr/sbin/modprobe uinput >/dev/null 2>&1 || :
%firewalld_reload


%postun
%udev_rules_update
%firewalld_reload


%files -f %{pyproject_files}
%doc README.md
%license %{_licensedir}/%{name}/Monitorize-LICENSE
%license %{_licensedir}/%{name}/Sunshine-LICENSE
%{_bindir}/monitorize
%{_bindir}/monitorize-kde-virtual-output
%{_libexecdir}/monitorize/sunshine
%{_libexecdir}/monitorize/monitorize-system-setup
%dir %{_datadir}/monitorize
%dir %{_datadir}/monitorize/sunshine
%{_datadir}/monitorize/sunshine/assets/
%{_datadir}/applications/monitorize.desktop
%{_datadir}/applications/monitorize-kde-virtual-output.desktop
%{_datadir}/icons/hicolor/512x512/apps/monitorize.png
%{_prefix}/lib/firewalld/services/monitorize.xml
%{_datadir}/polkit-1/actions/io.github.vinnavannewton.monitorize.system-setup.policy
%{_udevrulesdir}/70-monitorize-uinput.rules
%{_sysusersdir}/monitorize.conf
%{_modulesloaddir}/monitorize.conf


%changelog
* Mon Aug 24 2026 Monitorize contributors <noreply@example.com> - 0.2.8-2
- Load uinput during installation and request it automatically at boot.

* Sun Aug 23 2026 Monitorize contributors <noreply@example.com> - 0.2.8-1
- Add the Fedora 44 package with the bundled Monitorize Sunshine fork.
