#!/usr/bin/make -f

include packaging/deb/$(MONITORIZE_DEB_TARGET)/sunshine.mk

export DH_VERBOSE = 1
export PYBUILD_NAME = monitorize
export DEB_BUILD_MAINT_OPTIONS = hardening=+all

ifeq ($(MONITORIZE_ENABLE_CUDA),0)
SUNSHINE_CC = /usr/bin/gcc
SUNSHINE_CXX = /usr/bin/g++
CUDA_CMAKE_ARGS = -DCUDA_FAIL_ON_MISSING=OFF -DSUNSHINE_ENABLE_CUDA=OFF
CUDA_CHECK = ! grep -q 'src/platform/linux/cuda.cu' sunshine-build/compile_commands.json
else
SUNSHINE_CC = /usr/bin/gcc-14
SUNSHINE_CXX = /usr/bin/g++-14
CUDA_CMAKE_ARGS = -DCUDA_FAIL_ON_MISSING=ON -DSUNSHINE_ENABLE_CUDA=ON \
	-DCMAKE_CUDA_COMPILER=$(MONITORIZE_CUDA_ROOT)/bin/nvcc \
	-DCMAKE_CUDA_FLAGS=-Xcompiler=-fPIC -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/gcc-14
CUDA_CHECK = test -x "$(MONITORIZE_CUDA_ROOT)/bin/nvcc" && grep -q 'src/platform/linux/cuda.cu' sunshine-build/compile_commands.json
endif

%:
	dh $@ --with python3 --buildsystem=pybuild

override_dh_auto_build:
	dh_auto_build
	patch --batch --forward -d external/sunshine -p1 < packaging/sunshine-strict-selection.patch
	CC=$(SUNSHINE_CC) \
	RPM_OPT_FLAGS="$$(dpkg-buildflags --get CFLAGS)" \
	RPM_LD_FLAGS="$$(dpkg-buildflags --get LDFLAGS)" \
		linux/native/kde_virtual_output/build.sh monitorize-kde-virtual-output
	export CC=$(SUNSHINE_CC); \
	export CXX=$(SUNSHINE_CXX); \
	export CFLAGS="$$(dpkg-buildflags --get CFLAGS)"; \
	export CXXFLAGS="$$(dpkg-buildflags --get CXXFLAGS)"; \
	export LDFLAGS="$$(dpkg-buildflags --get LDFLAGS)"; \
	export BRANCH=monitorize; \
	export BUILD_VERSION=0.0.0; \
	export COMMIT=$(SUNSHINE_COMMIT); \
	cmake -B sunshine-build -S external/sunshine \
		-DCMAKE_BUILD_TYPE=Release \
		-DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
		-DCMAKE_INSTALL_PREFIX=/usr \
		-DBUILD_DOCS=OFF \
		-DBUILD_TESTS=OFF \
		-DBOOST_USE_STATIC=OFF \
		$(CUDA_CMAKE_ARGS) \
		-DFETCHCONTENT_SOURCE_DIR_BOOST="$$PWD/.boost-prepared" \
		-DFFMPEG_PREPARED_BINARIES="$$PWD/.ffmpeg-prepared" \
		-DGLAD_SKIP_PIP_INSTALL=ON \
		-DNPM=/usr/bin/npm \
		-DPython_EXECUTABLE=/usr/bin/python3 \
		-DSUNSHINE_ASSETS_DIR=/usr/share/monitorize/sunshine/assets \
		-DSUNSHINE_ENABLE_DRM=ON \
		-DSUNSHINE_ENABLE_KWIN=ON \
		-DSUNSHINE_ENABLE_PORTAL=ON \
		-DSUNSHINE_ENABLE_TRAY=OFF \
		-DSUNSHINE_ENABLE_VAAPI=ON \
		-DSUNSHINE_ENABLE_VULKAN=ON \
		-DSUNSHINE_ENABLE_WAYLAND=ON \
		-DSUNSHINE_ENABLE_X11=ON \
		-DSUNSHINE_EXECUTABLE_PATH=/usr/libexec/monitorize/sunshine; \
	cmake --build sunshine-build --parallel "$${MONITORIZE_BUILD_JOBS}"

override_dh_auto_install:
	dh_auto_install
	install -Dpm 0755 monitorize-kde-virtual-output \
		debian/monitorize/usr/bin/monitorize-kde-virtual-output
	install -Dpm 0755 packaging/deb/common/monitorize-wrapper \
		debian/monitorize/usr/bin/monitorize
	install -Dpm 0755 sunshine-build/sunshine \
		debian/monitorize/usr/libexec/monitorize/sunshine
	install -Dpm 0755 packaging/common/monitorize-system-setup \
		debian/monitorize/usr/libexec/monitorize/monitorize-system-setup
	install -dpm 0755 debian/monitorize/usr/share/monitorize/sunshine/assets
	cp -aL sunshine-build/assets/. \
		debian/monitorize/usr/share/monitorize/sunshine/assets/
	install -Dpm 0644 packaging/fedora/monitorize.desktop \
		debian/monitorize/usr/share/applications/monitorize.desktop
	install -Dpm 0644 packaging/fedora/monitorize-kde-virtual-output.desktop \
		debian/monitorize/usr/share/applications/monitorize-kde-virtual-output.desktop
	install -Dpm 0644 linux/monitorize/assets/monitorize_desktop_logo.png \
		debian/monitorize/usr/share/icons/hicolor/512x512/apps/monitorize.png
	install -Dpm 0644 packaging/fedora/monitorize.xml \
		debian/monitorize/usr/lib/firewalld/services/monitorize.xml
	install -Dpm 0644 packaging/common/io.github.vinnavannewton.monitorize.system-setup.policy \
		debian/monitorize/usr/share/polkit-1/actions/io.github.vinnavannewton.monitorize.system-setup.policy
	install -Dpm 0644 packaging/deb/common/monitorize.ufw.profile \
		debian/monitorize/etc/ufw/applications.d/monitorize
	install -Dpm 0644 packaging/fedora/70-monitorize-uinput.rules \
		debian/monitorize/usr/lib/udev/rules.d/70-monitorize-uinput.rules
	install -dpm 0755 debian/monitorize/usr/lib/modules-load.d
	printf 'uinput\n' > debian/monitorize/usr/lib/modules-load.d/monitorize.conf
	install -Dpm 0644 LICENSE \
		debian/monitorize/usr/share/doc/monitorize/Monitorize-LICENSE
	install -Dpm 0644 external/sunshine/LICENSE \
		debian/monitorize/usr/share/doc/monitorize/Sunshine-LICENSE

override_dh_auto_test:
	dh_auto_test
	desktop-file-validate packaging/fedora/monitorize.desktop
	desktop-file-validate packaging/fedora/monitorize-kde-virtual-output.desktop
	test -x monitorize-kde-virtual-output
	test -x sunshine-build/sunshine
	$(CUDA_CHECK)
	test -f packaging/common/monitorize-system-setup
	python3 -c 'from pathlib import Path; compile(Path("packaging/common/monitorize-system-setup").read_text(), "monitorize-system-setup", "exec")'
	test -d sunshine-build/assets/web
	test ! -e sunshine-build/usr/local
	./sunshine-build/sunshine --version
	MONITORIZE_SUNSHINE_BIN="$$PWD/sunshine-build/sunshine" \
	MONITORIZE_SUNSHINE_ASSETS_DIR="$$PWD/sunshine-build/assets" \
	PYTHONPATH=linux python3 -c 'from PyQt6.QtQuickWidgets import QQuickWidget; from monitorize.desktop import main_window; from monitorize.platform.sunshine_service import get_sunshine_assets_dir, get_sunshine_candidates; command = get_sunshine_candidates()[0]; assert command[0].endswith("/sunshine-build/sunshine"); assert get_sunshine_assets_dir(command[0]).endswith("/sunshine-build/assets"); assert QQuickWidget is not None; assert main_window is not None'
