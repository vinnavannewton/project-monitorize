/*
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#include "buffer.h"
#include "display.h"

#include <unistd.h>
#include <wayland-client-protocol.h>

namespace Demo
{

Buffer::Buffer(Display *display, const QSize &size)
{
    if (!m_backingStore.open()) {
        qFatal("Failed to create a temporary file");
    }

    unlink(m_backingStore.fileName().toUtf8().constData());

    const size_t stride = size.width() * 4;
    const size_t bufferSize = stride * size.height();

    if (!m_backingStore.resize(bufferSize)) {
        qFatal("Failed to resize a wl_shm_buffer");
    }

    uchar *data = m_backingStore.map(0, bufferSize);
    if (!data) {
        qFatal("Failed to map a wl_shm_buffer");
    }

    m_image = QImage(data, size.width(), size.height(), QImage::Format_ARGB32_Premultiplied);

    wl_shm_pool *shmPool = wl_shm_create_pool(display->shm(), m_backingStore.handle(), bufferSize);
    m_handle = wl_shm_pool_create_buffer(shmPool, 0, size.width(), size.height(), stride, WL_SHM_FORMAT_ARGB8888);
    wl_shm_pool_destroy(shmPool);
}

Buffer::~Buffer()
{
    if (m_handle) {
        wl_buffer_destroy(m_handle);
    }
}

wl_buffer *Buffer::handle() const
{
    return m_handle;
}

QImage *Buffer::image()
{
    return &m_image;
}

}
