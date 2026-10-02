%global sunshine_commit 2e7fe1b4dcbcfca819dff172cc702a14ed009591
%global cuda_version 12.9.1
%global cuda_build 575.57.08
%global cuda_sha256 0f6d806ddd87230d2adbe8a6006a9d20144fdbda9de2d6acc677daa5d036417a
%global sunshine_ffmpeg_tag v2026.910.121303
%global sunshine_ffmpeg_sha256 496d2bbb674d01e6033e31b9dfc15cbc9dc1494e882a4505f6ab1e03f75b385c
%global cuda_libxml2_sha256 56637a1b406c68da030032da1191a063bf56df7fd4f99ec2ae4ec6431bf1ee4f
%bcond_without cuda
%global _firewalld_dir %{_prefix}/lib/firewalld

Name:           monitorize
Version:        0.33
Release:        0
Summary:        Sunshine-backed virtual displays for Moonlight clients
License:        GPL-3.0-only
URL:            https://github.com/vinnavannewton/project-monitorize
Source0:        %{name}-%{version}.tar.gz
Source1:        https://github.com/LizardByte/build-deps/releases/download/%{sunshine_ffmpeg_tag}/Linux-x86_64-ffmpeg.tar.gz
Source2:        monitorize.sysusers
%if %{with cuda}
# CUDA 12.9's installer still needs libxml2.so.2; Tumbleweed ships libxml2.so.16.
Source3:        https://download.opensuse.org/distribution/leap/15.6/repo/oss/x86_64/libxml2-2-2.10.3-150500.5.14.1.x86_64.rpm
%endif
ExclusiveArch:  x86_64

BuildRequires:  boost-devel >= 1.89.0
%if %{with cuda}
BuildRequires:  aria2
%endif
BuildRequires:  libboost_filesystem-devel
BuildRequires:  libboost_locale-devel
BuildRequires:  libboost_log-devel
BuildRequires:  libboost_program_options-devel
BuildRequires:  cmake >= 3.26
%if %{with cuda}
BuildRequires:  cpio
%endif
BuildRequires:  curl
BuildRequires:  desktop-file-utils
BuildRequires:  firewall-macros
BuildRequires:  firewalld
BuildRequires:  gcc-c++
%if %{with cuda}
BuildRequires:  gcc14
BuildRequires:  gcc14-c++
%endif
BuildRequires:  git-core
BuildRequires:  glib2-devel
BuildRequires:  libX11-devel
BuildRequires:  libXcursor-devel
BuildRequires:  libXfixes-devel
BuildRequires:  libXi-devel
BuildRequires:  libXinerama-devel
BuildRequires:  libXrandr-devel
BuildRequires:  libXtst-devel
BuildRequires:  libcap-devel
BuildRequires:  libcurl-devel
BuildRequires:  libdrm-devel
BuildRequires:  libevdev-devel
BuildRequires:  libgudev-1_0-devel
BuildRequires:  libgbm-devel
BuildRequires:  libminiupnpc-devel
BuildRequires:  libnuma-devel
BuildRequires:  libopenssl-devel
BuildRequires:  libopus-devel
BuildRequires:  libva-devel
BuildRequires:  libxcb-devel
BuildRequires:  Mesa-libGL-devel
BuildRequires:  nodejs
BuildRequires:  npm
BuildRequires:  patch
BuildRequires:  nlohmann_json-devel
BuildRequires:  pkgconfig
BuildRequires:  pipewire-devel
BuildRequires:  pulseaudio-devel
BuildRequires:  python-rpm-macros
BuildRequires:  python3-Jinja2
BuildRequires:  python3-PyQt6
BuildRequires:  python3-cairo
BuildRequires:  python3-dbus-python
BuildRequires:  python3-devel
BuildRequires:  python3-gobject
BuildRequires:  python3-setuptools
BuildRequires:  python3-wheel
BuildRequires:  systemd-rpm-macros
BuildRequires:  udev
BuildRequires:  vulkan-devel
BuildRequires:  shaderc
BuildRequires:  wayland-devel
BuildRequires:  wayland-protocols-devel

Requires:       avahi
Requires:       firewalld
Requires:       iproute2
Requires:       libva-utils
Requires:       polkit
Requires:       python3-Jinja2
Requires:       python3-PyQt6
Requires:       python3-cairo
Requires:       python3-dbus-python
Requires:       python3-gobject
Requires:       udev
Requires:       which
Requires:       xdg-desktop-portal
Suggests:       monitorize-vkms
Requires(pre):  sysuser-tools
Requires(post): kmod
Requires(post): udev
Requires(postun): udev

%description
Monitorize creates compositor-native virtual displays on KDE Plasma, GNOME,
and Hyprland and streams them to Moonlight clients through isolated, bundled
Sunshine instances. Optional VKMS displays require the separate monitorize-vkms
package for both preset and custom resolutions.

%prep
%autosetup
patch --batch --forward -d external/sunshine -p1 < packaging/sunshine-strict-selection.patch
mkdir .ffmpeg-prepared
tar -xzf %{SOURCE1} -C .ffmpeg-prepared --strip-components=1 --no-same-owner
# Tumbleweed can ship a newer compatible Boost than Sunshine's exact request.
sed -i 's/find_package(Boost CONFIG ${BOOST_VERSION} EXACT /find_package(Boost CONFIG ${BOOST_VERSION} /' \
    external/sunshine/cmake/dependencies/Boost_Sunshine.cmake

%build
%if %{with cuda}
cuda_archive=${MONITORIZE_CUDA_ARCHIVE:-%{_builddir}/cuda_%{cuda_version}_%{cuda_build}_linux.run}
mkdir -p "$(dirname "$cuda_archive")"
if echo '%{cuda_sha256}  '"$cuda_archive" | sha256sum --check --strict --status; then
    echo "Using cached CUDA installer: $cuda_archive"
else
    if [ "${MONITORIZE_OFFLINE:-0}" = 1 ]; then
        echo "Missing cached CUDA installer: $cuda_archive. Run a normal build first." >&2
        exit 1
    fi
    aria2c --continue=true --max-connection-per-server=8 --split=8 --min-split-size=1M \
        --file-allocation=none --max-tries=3 --retry-wait=5 \
        --summary-interval=30 --console-log-level=warn \
        --dir="$(dirname "$cuda_archive")" --out="$(basename "$cuda_archive")" \
        https://developer.download.nvidia.com/compute/cuda/%{cuda_version}/local_installers/cuda_%{cuda_version}_%{cuda_build}_linux.run
    echo '%{cuda_sha256}  '"$cuda_archive" | sha256sum --check --strict
fi
echo '%{cuda_libxml2_sha256}  %{SOURCE3}' | sha256sum --check --strict
mkdir -p %{_builddir}/cuda-installer-compat
cd %{_builddir}/cuda-installer-compat
rpm2cpio %{SOURCE3} | cpio -idm --quiet './usr/lib64/libxml2.so.2*'
test -e usr/lib64/libxml2.so.2
cd -
LD_LIBRARY_PATH=%{_builddir}/cuda-installer-compat/usr/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH} \
bash "$cuda_archive" --silent --toolkit --toolkitpath=%{_builddir}/cuda \
    --no-drm --no-man-page --no-opengl-libs --override
patch -p2 --directory=%{_builddir}/cuda \
    < external/sunshine/packaging/linux/patches/x86_64/cuda-12-math_functions.patch
test -x %{_builddir}/cuda/bin/nvcc
%endif

%if %{with cuda}
build_cc=/usr/bin/gcc-14
build_cxx=/usr/bin/g++-14
cuda_args='-DCUDA_FAIL_ON_MISSING=ON -DSUNSHINE_ENABLE_CUDA=ON -DCMAKE_CUDA_COMPILER=%{_builddir}/cuda/bin/nvcc -DCMAKE_CUDA_FLAGS=-Xcompiler=-fPIC -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/gcc-14'
%else
build_cc=/usr/bin/gcc
build_cxx=/usr/bin/g++
cuda_args='-DCUDA_FAIL_ON_MISSING=OFF -DSUNSHINE_ENABLE_CUDA=OFF'
%endif
CC="$build_cc" RPM_OPT_FLAGS="%{optflags}" \
    linux/native/kde_virtual_output/build.sh monitorize-kde-virtual-output

export CC="$build_cc"
export CXX="$build_cxx"
export CFLAGS="%{optflags}"
export CXXFLAGS="%{optflags}"
unset LDFLAGS
export BRANCH=monitorize
export BUILD_VERSION=0.0.0
export COMMIT=%{sunshine_commit}

cmake -B sunshine-build -S external/sunshine \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -DCMAKE_INSTALL_PREFIX=%{_prefix} \
    -DBUILD_DOCS=OFF \
    -DBUILD_TESTS=OFF \
    -DBOOST_USE_STATIC=OFF \
    $cuda_args \
    -DFFMPEG_PREPARED_BINARIES="$PWD/.ffmpeg-prepared" \
    -DGLAD_SKIP_PIP_INSTALL=ON \
    -DNPM=/usr/bin/npm \
    -DPython_EXECUTABLE=/usr/bin/python3 \
    -DSUNSHINE_ASSETS_DIR=%{_datadir}/monitorize/sunshine/assets \
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
%if %{with cuda}
grep -q 'src/platform/linux/cuda.cu' sunshine-build/compile_commands.json
%else
! grep -q 'src/platform/linux/cuda.cu' sunshine-build/compile_commands.json
%endif

%install
install -d %{buildroot}%{python3_sitelib}
cp -a linux/monitorize %{buildroot}%{python3_sitelib}/
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
    %{buildroot}%{_firewalld_dir}/services/monitorize.xml
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
%sysusers_create_package %{name} %{SOURCE2}

%post
%udev_rules_update
/usr/sbin/modprobe uinput >/dev/null 2>&1 || :
%firewalld_reload

%postun
%udev_rules_update
%firewalld_reload

%files
%doc README.md
%license %{_licensedir}/%{name}/Monitorize-LICENSE
%license %{_licensedir}/%{name}/Sunshine-LICENSE
%{python3_sitelib}/monitorize/
%{_bindir}/monitorize
%{_bindir}/monitorize-kde-virtual-output
%caps(cap_sys_admin,cap_sys_nice+p) %{_libexecdir}/monitorize/sunshine
%{_libexecdir}/monitorize/monitorize-system-setup
%dir %{_datadir}/monitorize
%dir %{_datadir}/monitorize/sunshine
%{_datadir}/monitorize/sunshine/assets/
%{_datadir}/applications/monitorize.desktop
%{_datadir}/applications/monitorize-kde-virtual-output.desktop
%{_datadir}/icons/hicolor/512x512/apps/monitorize.png
%dir %{_firewalld_dir}
%dir %{_firewalld_dir}/services
%{_firewalld_dir}/services/monitorize.xml
%{_datadir}/polkit-1/actions/io.github.vinnavannewton.monitorize.system-setup.policy
%{_udevrulesdir}/70-monitorize-uinput.rules
%{_sysusersdir}/monitorize.conf
%{_modulesloaddir}/monitorize.conf

%changelog
* Tue Sep 29 2026 Monitorize contributors <noreply@example.com> - 0.33-0
- Set current Monitorize package version to 0.33.
- Supply the legacy libxml2 ABI required by the CUDA installer on Tumbleweed.
- Reuse cached CUDA, FFmpeg, and zypper downloads across local build attempts.
- Add an offline --rebuild-offline mode using a prepared dependency image.

* Mon Sep 21 2026 Monitorize contributors <noreply@example.com> - 0.39-0
- Release Monitorize 0.39 with compositor-native and VKMS virtual displays.

* Mon Aug 24 2026 Monitorize contributors <noreply@example.com> - 0.2.8-0
- Add the openSUSE Tumbleweed package with the bundled Monitorize Sunshine fork.
