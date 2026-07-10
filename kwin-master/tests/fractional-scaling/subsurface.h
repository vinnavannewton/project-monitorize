/*
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#pragma once

#include <QObject>

struct wl_subsurface;

namespace Demo
{

class Display;
class Surface;

class SubSurface : public QObject
{
    Q_OBJECT

public:
    SubSurface(Display *display, Surface *surface, Surface *parentSurface);
    ~SubSurface() override;

    void setPosition(const QPoint &position);

private:
    wl_subsurface *m_subsurface;
};

}
