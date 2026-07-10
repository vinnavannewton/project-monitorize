/*
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#pragma once

#include <QObject>
#include <QSize>

struct wl_array;
struct xdg_surface;
struct xdg_toplevel;
struct zxdg_toplevel_decoration_v1;

namespace Demo
{

class Display;
class Surface;

class XdgToplevel : public QObject
{
    Q_OBJECT

public:
    explicit XdgToplevel(Display *display, Surface *surface);
    ~XdgToplevel() override;

    void setTitle(const QString &title);
    void setDecorated(bool decorated);

Q_SIGNALS:
    void configured(const QSizeF &size);
    void closed();

private:
    static void onSurfaceConfigure(void *data, xdg_surface *xdg_surface, uint32_t serial);

    static void onToplevelConfigure(void *data, xdg_toplevel *xdg_toplevel, int32_t width, int32_t height, wl_array *states);
    static void onToplevelClose(void *data, xdg_toplevel *xdg_toplevel);
    static void onToplevelConfigureBounds(void *data, xdg_toplevel *xdg_toplevel, int32_t width, int32_t height);
    static void onToplevelWmCapabilities(void *data, xdg_toplevel *xdg_toplevel, wl_array *capabilities);

    static void onToplevelDecorationConfigure(void *data, zxdg_toplevel_decoration_v1 *zxdg_toplevel_decoration_v1, uint32_t mode);

    Surface *m_surface = nullptr;
    xdg_surface *m_xdgSurface = nullptr;
    xdg_toplevel *m_xdgToplevel = nullptr;
    zxdg_toplevel_decoration_v1 *m_xdgToplevelDecoration = nullptr;

    std::optional<QSizeF> m_configureSize;
};

}
