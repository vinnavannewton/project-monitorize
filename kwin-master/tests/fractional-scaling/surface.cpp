/*
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#include "surface.h"
#include "buffer.h"
#include "display.h"

#include "wayland-viewporter-client-protocol.h"
#include "wayland-wp-fractional-scale-v1-client-protocol.h"
#include "wayland-xx-fractional-scale-v2-client-protocol.h"

namespace Demo
{

Surface::Surface(Display *display)
    : m_display(display)
{
    static constexpr wl_surface_listener surfaceListener = {
        .enter = onSurfaceEnter,
        .leave = onSurfaceLeave,
        .preferred_buffer_scale = onSurfacePreferredBufferScale,
        .preferred_buffer_transform = onSurfacePreferredBufferTransform,
    };

    m_surface = wl_compositor_create_surface(display->compositor());
    wl_surface_add_listener(m_surface, &surfaceListener, this);

    static constexpr wp_fractional_scale_v1_listener fractionalScaleListener = {
        .preferred_scale = onFractionalScaleV1PreferredScale,
    };

    m_fractionalScaleV1 = wp_fractional_scale_manager_v1_get_fractional_scale(display->fractionalScaleManagerV1(), m_surface);
    wp_fractional_scale_v1_add_listener(m_fractionalScaleV1, &fractionalScaleListener, this);

    if (display->fractionalScaleManagerV2()) {
        static constexpr xx_fractional_scale_v2_listener fractionalScaleListener = {
            .scale_factor = onFractionalScaleV2ScaleFactor,
        };

        m_fractionalScaleV2 = xx_fractional_scale_manager_v2_get_fractional_scale(display->fractionalScaleManagerV2(), m_surface);
        xx_fractional_scale_v2_add_listener(m_fractionalScaleV2, &fractionalScaleListener, this);
    }

    m_viewport = wp_viewporter_get_viewport(display->viewporter(), m_surface);
}

Surface::~Surface()
{
    wl_surface_destroy(m_surface);
    wp_fractional_scale_v1_destroy(m_fractionalScaleV1);
    wp_viewport_destroy(m_viewport);
}

Display *Surface::display() const
{
    return m_display;
}

wl_surface *Surface::object() const
{
    return m_surface;
}

qreal Surface::preferredBufferScale() const
{
    return m_preferredBufferScale;
}

qreal Surface::compositorToClientScale() const
{
    return m_compositorToClientScale;
}

qreal Surface::clientToCompositorScale() const
{
    return m_clientToCompositorScale;
}

void Surface::setDestinationSize(const QSize &size)
{
    wp_viewport_set_destination(m_viewport, size.width(), size.height());
}

void Surface::attachBuffer(Buffer *buffer)
{
    wl_surface_attach(m_surface, buffer->handle(), 0, 0);
}

void Surface::damageBuffer(int x, int y, int width, int height)
{
    wl_surface_damage_buffer(m_surface, x, y, width, height);
}

void Surface::commit()
{
    wl_surface_commit(m_surface);
}

void Surface::onSurfaceEnter(void *data, wl_surface *wl_surface, wl_output *output)
{
}

void Surface::onSurfaceLeave(void *data, wl_surface *wl_surface, wl_output *output)
{
}

void Surface::onSurfacePreferredBufferScale(void *data, wl_surface *wl_surface, int32_t factor)
{
}

void Surface::onSurfacePreferredBufferTransform(void *data, wl_surface *wl_surface, uint32_t transform)
{
}

void Surface::onFractionalScaleV1PreferredScale(void *data, wp_fractional_scale_v1 *wp_fractional_scale_v1, uint32_t scale)
{
    auto self = static_cast<Surface *>(data);
    self->m_preferredBufferScale = scale / 120.0;
}

void Surface::onFractionalScaleV2ScaleFactor(void *data, xx_fractional_scale_v2 *xx_fractional_scale_v2, uint32_t scale_8_24)
{
    auto self = static_cast<Surface *>(data);
    self->m_compositorToClientScale = scale_8_24 / qreal(1UL << 24);
    self->m_clientToCompositorScale = scale_8_24 / qreal(1UL << 24);
    xx_fractional_scale_v2_set_scale_factor(xx_fractional_scale_v2, scale_8_24);
}

}
