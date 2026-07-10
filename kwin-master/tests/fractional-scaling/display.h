/*
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#pragma once

#include <QHash>
#include <QObject>

#include <memory>

struct wl_display;
struct wl_compositor;
struct wl_subcompositor;
struct wl_registry;
struct wl_shm;
struct wp_fractional_scale_manager_v1;
struct wp_viewporter;
struct xdg_wm_base;
struct xx_fractional_scale_manager_v2;
struct zxdg_decoration_manager_v1;

namespace Demo
{

class EventThread;

class Display : public QObject
{
    Q_OBJECT

public:
    Display();
    ~Display() override;

    wl_display *nativeDisplay() const;
    wl_shm *shm() const;
    wl_compositor *compositor() const;
    wl_subcompositor *subcompositor() const;
    xdg_wm_base *xdgShell() const;
    zxdg_decoration_manager_v1 *xdgDecorationManagerV1() const;
    wp_fractional_scale_manager_v1 *fractionalScaleManagerV1() const;
    xx_fractional_scale_manager_v2 *fractionalScaleManagerV2() const;
    wp_viewporter *viewporter() const;

public Q_SLOTS:
    void flush();

private:
    static void onGlobal(void *data, wl_registry *registry, uint32_t name, const char *interface, uint32_t version);
    static void onGlobalRemove(void *data, wl_registry *registry, uint32_t name);

    std::unique_ptr<EventThread> m_eventThread;
    wl_display *m_display = nullptr;
    wl_registry *m_registry = nullptr;
    wl_shm *m_shm = nullptr;
    wl_compositor *m_compositor = nullptr;
    wl_subcompositor *m_subcompositor = nullptr;
    wp_fractional_scale_manager_v1 *m_fractionalScaleV1 = nullptr;
    xx_fractional_scale_manager_v2 *m_fractionalScaleV2 = nullptr;
    wp_viewporter *m_viewporter = nullptr;
    xdg_wm_base *m_xdgShell = nullptr;
    zxdg_decoration_manager_v1 *m_xdgDecorationManagerV1 = nullptr;
};

}
