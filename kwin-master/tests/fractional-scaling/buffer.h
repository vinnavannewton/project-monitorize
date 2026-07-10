/*
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#pragma once

#include <QImage>
#include <QObject>
#include <QTemporaryFile>

struct wl_buffer;

namespace Demo
{

class Display;

class Buffer : public QObject
{
    Q_OBJECT

public:
    Buffer(Display *display, const QSize &size);
    ~Buffer() override;

    wl_buffer *handle() const;
    QImage *image();

private:
    QTemporaryFile m_backingStore;
    QImage m_image;
    wl_buffer *m_handle = nullptr;
};

}
