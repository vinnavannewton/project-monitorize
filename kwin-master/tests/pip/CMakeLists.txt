# SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>
# SPDX-License-Identifier: BSD-3-Clause

cmake_minimum_required(VERSION 3.20)
project(pip)

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
    Widgets
    WaylandClientPrivate
)

# For Wayland_DATADIR.
find_package(Wayland REQUIRED COMPONENTS
    Client
)

find_package(WaylandProtocols REQUIRED)

check_cxx_compiler_flag(-Wno-unused-parameter COMPILER_UNUSED_PARAMETER_SUPPORTED)
if (COMPILER_UNUSED_PARAMETER_SUPPORTED)
    add_compile_options(-Wno-unused-parameter)
endif()

add_executable(piptest)

target_sources(piptest PRIVATE
    main.cpp
    pipshellsurface.cpp
    pip.cpp
    window.cpp
)

qt6_generate_wayland_protocol_client_sources(piptest
    PRIVATE_CODE
    FILES
        ${CMAKE_CURRENT_SOURCE_DIR}/../../src/wayland/protocols/xx-pip-v1.xml
        ${WaylandProtocols_DATADIR}/stable/xdg-shell/xdg-shell.xml
        ${Wayland_DATADIR}/wayland.xml
)

target_link_libraries(piptest PRIVATE
    Qt::Gui
    Qt::WaylandClientPrivate
    Qt::Widgets
)
