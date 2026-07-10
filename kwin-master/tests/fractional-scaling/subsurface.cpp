/*
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#include "subsurface.h"
#include "display.h"
#include "surface.h"

#include <QPoint>

#include <wayland-client-protocol.h>

namespace Demo
{

SubSurface::SubSurface(Display *display, Surface *surface, Surface *parentSurface)
{
    m_subsurface = wl_subcompositor_get_subsurface(display->subcompositor(), surface->object(), parentSurface->object());
}

SubSurface::~SubSurface()
{
    wl_subsurface_destroy(m_subsurface);
}

void SubSurface::setPosition(const QPoint &position)
{
    wl_subsurface_set_position(m_subsurface, position.x(), position.y());
}

}
