# SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>
# SPDX-License-Identifier: BSD-3-Clause

cmake_minimum_required(VERSION 3.20)
project(fractional-scaling)

set(CMAKE_C_STANDARD 99)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(ECM 6.0.0 REQUIRED NO_MODULE)
set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH})

include(CheckCXXCompilerFlag)
include(FeatureSummary)
include(FindWaylandScanner)
include(KDEInstallDirs)
include(KDECMakeSettings)
include(KDECompilerSettings NO_POLICY_SCOPE)

find_package(Qt6 CONFIG REQUIRED COMPONENTS
    Core
    Gui
)

find_package(Wayland REQUIRED COMPONENTS
    Client
)

find_package(WaylandProtocols REQUIRED)

check_cxx_compiler_flag(-Wno-unused-parameter COMPILER_UNUSED_PARAMETER_SUPPORTED)
if (COMPILER_UNUSED_PARAMETER_SUPPORTED)
    add_compile_options(-Wno-unused-parameter)
endif()

add_executable(fractionalscalingtest)

target_sources(fractionalscalingtest PRIVATE
    buffer.cpp
    display.cpp
    main.cpp
    subsurface.cpp
    surface.cpp
    xdg_toplevel.cpp
)

ecm_add_wayland_client_protocol(fractionalscalingtest
    PRIVATE_CODE
    PROTOCOL ${WaylandProtocols_DATADIR}/stable/viewporter/viewporter.xml
    BASENAME viewporter
)

ecm_add_wayland_client_protocol(fractionalscalingtest
    PRIVATE_CODE
    PROTOCOL ${WaylandProtocols_DATADIR}/stable/xdg-shell/xdg-shell.xml
    BASENAME xdg-shell
)

ecm_add_wayland_client_protocol(fractionalscalingtest
    PRIVATE_CODE
    PROTOCOL ${WaylandProtocols_DATADIR}/staging/fractional-scale/fractional-scale-v1.xml
    BASENAME wp-fractional-scale-v1
)

ecm_add_wayland_client_protocol(fractionalscalingtest
    PRIVATE_CODE
    PROTOCOL ${CMAKE_CURRENT_SOURCE_DIR}/../../src/wayland/protocols/xx-fractional-scale-v2.xml
    BASENAME xx-fractional-scale-v2
)

ecm_add_wayland_client_protocol(fractionalscalingtest
    PRIVATE_CODE
    PROTOCOL ${WaylandProtocols_DATADIR}/unstable/xdg-decoration/xdg-decoration-unstable-v1.xml
    BASENAME xdg-decoration-v1
)

target_link_libraries(fractionalscalingtest PRIVATE
    Qt::Gui
    Wayland::Client
)

feature_summary(WHAT ALL FATAL_ON_MISSING_REQUIRED_PACKAGES)
