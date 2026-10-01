#!/usr/bin/env bash
# Shared by the local PKGBUILD and the prepared Podman image.

monitorize_runtime_deps=(
    avahi boost-libs curl glib2 glibc iproute2 kmod libcap libdrm
    libevdev libgcc libglvnd libpipewire libpulse libstdc++ libva libva-utils libvdpau libx11 libxcb libxcursor
    libxfixes libxi libxinerama libxrandr libxtst mesa miniupnpc numactl
    openssl opus pipewire polkit python python-cairo python-dbus
    python-gobject python-jinja python-pyqt6 qt6-declarative shadow systemd
    vulkan-icd-loader wayland xdg-desktop-portal
)

monitorize_build_deps=(
    base-devel boost cmake curl desktop-file-utils git glib2-devel glslang
    libgudev namcap nlohmann-json nodejs npm patch pkgconf
    python-build python-installer python-setuptools python-wheel shaderc
    vulkan-headers wayland-protocols
)
if [[ "${MONITORIZE_ENABLE_CUDA:-1}" == 1 ]]; then
    monitorize_build_deps+=(cuda gcc15 libxml2-legacy)
fi
