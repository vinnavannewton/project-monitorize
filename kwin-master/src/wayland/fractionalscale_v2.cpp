/*
    SPDX-FileCopyrightText: 2022 Xaver Hugl <xaver.hugl@kde.org>
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: LGPL-2.1-only OR LGPL-3.0-only OR LicenseRef-KDE-Accepted-LGPL
*/

#include "wayland/fractionalscale_v2.h"
#include "wayland/display.h"
#include "wayland/surface_p.h"

namespace KWin
{

FractionalScaleManagerV2::FractionalScaleManagerV2(Display *display, QObject *parent)
    : QObject(parent)
    , QtWaylandServer::xx_fractional_scale_manager_v2(*display, 1)
{
}

FractionalScaleManagerV2::~FractionalScaleManagerV2()
{
}

void FractionalScaleManagerV2::xx_fractional_scale_manager_v2_destroy(Resource *resource)
{
    wl_resource_destroy(resource->handle);
}

void FractionalScaleManagerV2::xx_fractional_scale_manager_v2_get_fractional_scale(Resource *resource, uint32_t id, struct ::wl_resource *surface_resource)
{
    SurfaceInterface *surface = SurfaceInterface::get(surface_resource);
    SurfaceInterfacePrivate *surfacePrivate = SurfaceInterfacePrivate::get(surface);

    if (surfacePrivate->fractionalScaleV2) {
        wl_resource_post_error(resource->handle, error_fractional_scale_exists, "The surface already has a fractional_scale_v2 associated with it");
        return;
    }

    new FractionalScaleV2(surface, resource->client(), id, resource->version());
}

FractionalScaleV2::FractionalScaleV2(SurfaceInterface *surface, wl_client *client, int id, int version)
    : QtWaylandServer::xx_fractional_scale_v2(client, id, version)
    , m_surface(surface)
{
    SurfaceInterfacePrivate *surfacePrivate = SurfaceInterfacePrivate::get(surface);
    surfacePrivate->fractionalScaleV2 = this;

    if (surfacePrivate->preferredBufferScale) {
        setCompositorToClientScale(*surfacePrivate->preferredBufferScale);
    }
}

FractionalScaleV2::~FractionalScaleV2()
{
    if (m_surface) {
        SurfaceInterfacePrivate *surfacePrivate = SurfaceInterfacePrivate::get(m_surface);
        surfacePrivate->fractionalScaleV2 = nullptr;
        surfacePrivate->clientToCompositorScale = 1;
    }
}

void FractionalScaleV2::setCompositorToClientScale(qreal scale)
{
    SurfaceInterfacePrivate *surfacePrivate = SurfaceInterfacePrivate::get(m_surface);
    surfacePrivate->compositorToClientScale = scale;
    send_scale_factor(scale * (1UL << 24));
}

void FractionalScaleV2::xx_fractional_scale_v2_destroy_resource(Resource *resource)
{
    delete this;
}

void FractionalScaleV2::xx_fractional_scale_v2_set_scale_factor(Resource *resource, uint32_t scale_8_24)
{
    if (!m_surface) {
        wl_resource_post_error(resource->handle, 0, "the wl_surface no longer exists");
        return;
    }

    SurfaceInterfacePrivate *surfacePrivate = SurfaceInterfacePrivate::get(m_surface);
    surfacePrivate->clientToCompositorScale = scale_8_24 / qreal(1UL << 24);
}

void FractionalScaleV2::xx_fractional_scale_v2_destroy(Resource *resource)
{
    wl_resource_destroy(resource->handle);
}

}

#include "moc_fractionalscale_v2.cpp"
