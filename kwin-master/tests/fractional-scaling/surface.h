/*
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#pragma once

#include <QObject>

struct wl_output;
struct wl_surface;
struct wp_fractional_scale_v1;
struct wp_viewport;
struct xx_fractional_scale_v2;

namespace Demo
{

class Buffer;
class Display;

class Surface : public QObject
{
    Q_OBJECT

public:
    Surface(Display *display);
    ~Surface() override;

    Display *display() const;
    wl_surface *object() const;
    qreal preferredBufferScale() const;
    qreal compositorToClientScale() const;
    qreal clientToCompositorScale() const;

    void setDestinationSize(const QSize &size);
    void attachBuffer(Buffer *buffer);
    void damageBuffer(int x, int y, int width, int height);
    void commit();

private:
    static void onSurfaceEnter(void *data, wl_surface *wl_surface, wl_output *output);
    static void onSurfaceLeave(void *data, wl_surface *wl_surface, wl_output *output);
    static void onSurfacePreferredBufferScale(void *data, wl_surface *wl_surface, int32_t factor);
    static void onSurfacePreferredBufferTransform(void *data, wl_surface *wl_surface, uint32_t transform);

    static void onFractionalScaleV1PreferredScale(void *data, wp_fractional_scale_v1 *wp_fractional_scale_v1, uint32_t scale);
    static void onFractionalScaleV2ScaleFactor(void *data, xx_fractional_scale_v2 *xx_fractional_scale_v2, uint32_t scale_8_24);

    Display *m_display;
    wl_surface *m_surface = nullptr;
    wp_fractional_scale_v1 *m_fractionalScaleV1 = nullptr;
    xx_fractional_scale_v2 *m_fractionalScaleV2 = nullptr;
    wp_viewport *m_viewport = nullptr;
    qreal m_preferredBufferScale = 1.0;
    qreal m_compositorToClientScale = 1.0;
    qreal m_clientToCompositorScale = 1.0;
};

}
