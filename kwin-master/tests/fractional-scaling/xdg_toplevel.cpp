/*
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#include "xdg_toplevel.h"
#include "display.h"
#include "surface.h"

#include "wayland-xdg-decoration-v1-client-protocol.h"
#include "wayland-xdg-shell-client-protocol.h"

namespace Demo
{

XdgToplevel::XdgToplevel(Display *display, Surface *surface)
    : m_surface(surface)
{
    static constexpr xdg_surface_listener xdgSurfaceListener = {
        .configure = onSurfaceConfigure,
    };

    m_xdgSurface = xdg_wm_base_get_xdg_surface(display->xdgShell(), surface->object());
    xdg_surface_add_listener(m_xdgSurface, &xdgSurfaceListener, this);

    static constexpr xdg_toplevel_listener xdgToplevelListener = {
        .configure = onToplevelConfigure,
        .close = onToplevelClose,
        .configure_bounds = onToplevelConfigureBounds,
        .wm_capabilities = onToplevelWmCapabilities,
    };

    m_xdgToplevel = xdg_surface_get_toplevel(m_xdgSurface);
    xdg_toplevel_add_listener(m_xdgToplevel, &xdgToplevelListener, this);

    static constexpr zxdg_toplevel_decoration_v1_listener xdgToplevelDecorationListener = {
        .configure = onToplevelDecorationConfigure,
    };

    m_xdgToplevelDecoration = zxdg_decoration_manager_v1_get_toplevel_decoration(display->xdgDecorationManagerV1(), m_xdgToplevel);
    zxdg_toplevel_decoration_v1_add_listener(m_xdgToplevelDecoration, &xdgToplevelDecorationListener, this);
}

XdgToplevel::~XdgToplevel()
{
    zxdg_toplevel_decoration_v1_destroy(m_xdgToplevelDecoration);
    xdg_toplevel_destroy(m_xdgToplevel);
    xdg_surface_destroy(m_xdgSurface);
}

void XdgToplevel::setTitle(const QString &title)
{
    xdg_toplevel_set_title(m_xdgToplevel, title.toUtf8().constData());
}

void XdgToplevel::setDecorated(bool decorated)
{
    if (decorated) {
        zxdg_toplevel_decoration_v1_set_mode(m_xdgToplevelDecoration, ZXDG_TOPLEVEL_DECORATION_V1_MODE_SERVER_SIDE);
    } else {
        zxdg_toplevel_decoration_v1_set_mode(m_xdgToplevelDecoration, ZXDG_TOPLEVEL_DECORATION_V1_MODE_CLIENT_SIDE);
    }
}

void XdgToplevel::onSurfaceConfigure(void *data, xdg_surface *xdg_surface, uint32_t serial)
{
    xdg_surface_ack_configure(xdg_surface, serial);

    auto self = static_cast<XdgToplevel *>(data);
    if (self->m_configureSize) {
        Q_EMIT self->configured(self->m_configureSize.value());
        self->m_configureSize.reset();
    }
}

void XdgToplevel::onToplevelConfigure(void *data, xdg_toplevel *xdg_toplevel, int32_t width, int32_t height, wl_array *states)
{
    auto self = static_cast<XdgToplevel *>(data);
    self->m_configureSize = QSizeF(width, height) / self->m_surface->compositorToClientScale();
}

void XdgToplevel::onToplevelClose(void *data, xdg_toplevel *xdg_toplevel)
{
    auto self = static_cast<XdgToplevel *>(data);
    Q_EMIT self->closed();
}

void XdgToplevel::onToplevelConfigureBounds(void *data, xdg_toplevel *xdg_toplevel, int32_t width, int32_t height)
{
}

void XdgToplevel::onToplevelWmCapabilities(void *data, xdg_toplevel *xdg_toplevel, wl_array *capabilities)
{
}

void XdgToplevel::onToplevelDecorationConfigure(void *data, zxdg_toplevel_decoration_v1 *zxdg_toplevel_decoration_v1, uint32_t mode)
{
}

}
