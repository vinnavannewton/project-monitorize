/*
    SPDX-FileCopyrightText: 2022 Xaver Hugl <xaver.hugl@kde.org>
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: LGPL-2.1-only OR LGPL-3.0-only OR LicenseRef-KDE-Accepted-LGPL
*/

#pragma once

#include "kwin_export.h"
#include "wayland/qwayland-server-xx-fractional-scale-v2.h"

#include <QObject>
#include <QPointer>

namespace KWin
{

class Display;
class SurfaceInterface;

class KWIN_EXPORT FractionalScaleManagerV2 : public QObject, public QtWaylandServer::xx_fractional_scale_manager_v2
{
    Q_OBJECT

public:
    explicit FractionalScaleManagerV2(Display *display, QObject *parent = nullptr);
    ~FractionalScaleManagerV2() override;

protected:
    void xx_fractional_scale_manager_v2_destroy(Resource *resource) override;
    void xx_fractional_scale_manager_v2_get_fractional_scale(Resource *resource, uint32_t id, struct ::wl_resource *surface) override;
};

class KWIN_EXPORT FractionalScaleV2 : public QtWaylandServer::xx_fractional_scale_v2
{
public:
    FractionalScaleV2(SurfaceInterface *surface, wl_client *client, int id, int version);
    ~FractionalScaleV2() override;

    void setCompositorToClientScale(qreal scale);

protected:
    void xx_fractional_scale_v2_destroy_resource(Resource *resource) override;
    void xx_fractional_scale_v2_set_scale_factor(Resource *resource, uint32_t scale_8_24) override;
    void xx_fractional_scale_v2_destroy(Resource *resource) override;

private:
    QPointer<SurfaceInterface> m_surface;
};

}
