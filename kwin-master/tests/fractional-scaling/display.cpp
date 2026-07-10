/*
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#include "display.h"
#include "wayland-viewporter-client-protocol.h"
#include "wayland-wp-fractional-scale-v1-client-protocol.h"
#include "wayland-xdg-decoration-v1-client-protocol.h"
#include "wayland-xdg-shell-client-protocol.h"
#include "wayland-xx-fractional-scale-v2-client-protocol.h"

#include <QDebug>
#include <QMutex>
#include <QThread>
#include <QWaitCondition>

#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
#include <wayland-client.h>

namespace Demo
{

class EventThread : public QThread
{
    Q_OBJECT

public:
    EventThread(wl_display *display)
        : m_display(display)
        , m_fd(wl_display_get_fd(display))
        , m_quitPipe{-1, -1}
        , m_reading(true)
        , m_quitting(false)
    {
        if (pipe2(m_quitPipe, O_CLOEXEC) == -1) {
            qWarning() << "Failed to create quite pipe in WaylandEventThread";
        }
    }

    ~EventThread() override
    {
        if (m_quitPipe[0] != -1) {
            close(m_quitPipe[0]);
            close(m_quitPipe[1]);
        }
    }

    void dispatch()
    {
        while (true) {
            if (wl_display_dispatch_pending(m_display) < 0) {
                qFatal("Wayland connection broke");
            }

            wl_display_flush(m_display);

            if (m_reading.loadAcquire()) {
                break;
            }

            if (wl_display_prepare_read(m_display) == 0) {
                QMutexLocker lock(&m_mutex);
                m_reading.storeRelease(true);
                m_cond.wakeOne();
                break;
            }
        }
    }

    void stop()
    {
        if (m_quitPipe[1] != -1) {
            write(m_quitPipe[1], "\0", 1);
        }

        m_mutex.lock();
        m_quitting = true;
        m_cond.wakeOne();
        m_mutex.unlock();

        wait();
    }

Q_SIGNALS:
    void available();

protected:
    void run() override
    {
        while (true) {
            m_reading.storeRelease(false);

            Q_EMIT available();

            m_mutex.lock();
            while (!m_reading.loadRelaxed() && !m_quitting) {
                m_cond.wait(&m_mutex);
            }
            m_mutex.unlock();

            if (m_quitting) {
                break;
            }

            pollfd fds[2] = {{m_fd, POLLIN, 0}, {m_quitPipe[0], POLLIN, 0}};
            poll(fds, 2, -1);

            if (fds[1].revents & POLLIN) {
                wl_display_cancel_read(m_display);
                break;
            }

            if (fds[0].revents & POLLIN) {
                wl_display_read_events(m_display);
            } else {
                wl_display_cancel_read(m_display);
            }
        }
    }

private:
    wl_display *const m_display;
    int m_fd;
    int m_quitPipe[2];
    QAtomicInteger<bool> m_reading;
    QMutex m_mutex;
    QWaitCondition m_cond;
    bool m_quitting;
};

Display::Display()
{
    m_display = wl_display_connect(nullptr);
    if (!m_display) {
        qFatal("wl_display_connect() failed");
    }

    m_eventThread = std::make_unique<EventThread>(m_display);
    connect(m_eventThread.get(), &EventThread::available, this, &Display::flush, Qt::QueuedConnection);
    m_eventThread->start();

    static wl_registry_listener registryListener{
        .global = onGlobal,
        .global_remove = onGlobalRemove,
    };

    m_registry = wl_display_get_registry(m_display);
    wl_registry_add_listener(m_registry, &registryListener, this);
    wl_display_roundtrip(m_display);
}

Display::~Display()
{
    m_eventThread->stop();
    m_eventThread.reset();

    if (m_shm) {
        wl_shm_destroy(m_shm);
    }
    if (m_compositor) {
        wl_compositor_destroy(m_compositor);
    }
    if (m_subcompositor) {
        wl_subcompositor_destroy(m_subcompositor);
    }
    if (m_fractionalScaleV1) {
        wp_fractional_scale_manager_v1_destroy(m_fractionalScaleV1);
    }
    if (m_fractionalScaleV2) {
        xx_fractional_scale_manager_v2_destroy(m_fractionalScaleV2);
    }
    if (m_viewporter) {
        wp_viewporter_destroy(m_viewporter);
    }
    if (m_xdgShell) {
        xdg_wm_base_destroy(m_xdgShell);
    }
    if (m_xdgDecorationManagerV1) {
        zxdg_decoration_manager_v1_destroy(m_xdgDecorationManagerV1);
    }

    if (m_registry) {
        wl_registry_destroy(m_registry);
    }
    if (m_display) {
        wl_display_disconnect(m_display);
    }
}

void Display::flush()
{
    m_eventThread->dispatch();
}

wl_display *Display::nativeDisplay() const
{
    return m_display;
}

wl_shm *Display::shm() const
{
    return m_shm;
}

wl_compositor *Display::compositor() const
{
    return m_compositor;
}

wl_subcompositor *Display::subcompositor() const
{
    return m_subcompositor;
}

xdg_wm_base *Display::xdgShell() const
{
    return m_xdgShell;
}

zxdg_decoration_manager_v1 *Display::xdgDecorationManagerV1() const
{
    return m_xdgDecorationManagerV1;
}

wp_fractional_scale_manager_v1 *Display::fractionalScaleManagerV1() const
{
    return m_fractionalScaleV1;
}

xx_fractional_scale_manager_v2 *Display::fractionalScaleManagerV2() const
{
    return m_fractionalScaleV2;
}

wp_viewporter *Display::viewporter() const
{
    return m_viewporter;
}

void Display::onGlobal(void *data, wl_registry *registry, uint32_t name, const char *interface, uint32_t version)
{
    Display *display = static_cast<Display *>(data);

    if (strcmp(interface, wl_compositor_interface.name) == 0) {
        display->m_compositor = static_cast<wl_compositor *>(wl_registry_bind(registry, name, &wl_compositor_interface, std::min(version, 4u)));
    } else if (strcmp(interface, wl_subcompositor_interface.name) == 0) {
        display->m_subcompositor = static_cast<wl_subcompositor *>(wl_registry_bind(registry, name, &wl_subcompositor_interface, 1));
    } else if (strcmp(interface, wl_shm_interface.name) == 0) {
        display->m_shm = static_cast<wl_shm *>(wl_registry_bind(registry, name, &wl_shm_interface, std::min(version, 1u)));
    } else if (strcmp(interface, wp_fractional_scale_manager_v1_interface.name) == 0) {
        display->m_fractionalScaleV1 = static_cast<wp_fractional_scale_manager_v1 *>(wl_registry_bind(registry, name, &wp_fractional_scale_manager_v1_interface, 1));
    } else if (strcmp(interface, xx_fractional_scale_manager_v2_interface.name) == 0) {
        display->m_fractionalScaleV2 = static_cast<xx_fractional_scale_manager_v2 *>(wl_registry_bind(registry, name, &xx_fractional_scale_manager_v2_interface, 1));
    } else if (strcmp(interface, wp_viewporter_interface.name) == 0) {
        display->m_viewporter = static_cast<wp_viewporter *>(wl_registry_bind(registry, name, &wp_viewporter_interface, 1));
    } else if (strcmp(interface, xdg_wm_base_interface.name) == 0) {
        display->m_xdgShell = static_cast<xdg_wm_base *>(wl_registry_bind(registry, name, &xdg_wm_base_interface, std::min(version, 1u)));

        static constexpr xdg_wm_base_listener listener = {
            .ping = [](void *data, xdg_wm_base *xdg_wm_base, uint32_t serial) {
                xdg_wm_base_pong(xdg_wm_base, serial);
            },
        };
        xdg_wm_base_add_listener(display->m_xdgShell, &listener, nullptr);
    } else if (strcmp(interface, zxdg_decoration_manager_v1_interface.name) == 0) {
        display->m_xdgDecorationManagerV1 = static_cast<zxdg_decoration_manager_v1 *>(wl_registry_bind(registry, name, &zxdg_decoration_manager_v1_interface, 1));
    }
}

void Display::onGlobalRemove(void *data, wl_registry *registry, uint32_t name)
{
}

}

#include "display.moc"
#include "moc_display.cpp"
