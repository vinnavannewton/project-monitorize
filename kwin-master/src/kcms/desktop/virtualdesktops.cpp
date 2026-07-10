/*
    SPDX-FileCopyrightText: 2018 Eike Hein <hein@kde.org>
    SPDX-FileCopyrightText: 2018 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#include "virtualdesktops.h"
#include "animationsmodel.h"
#include "desktopsmodel.h"
#include "virtualdesktopsdata.h"
#include "virtualdesktopssettings.h"

#include <KAboutApplicationDialog>
#include <KAboutData>
#include <KConfigGroup>
#include <KLocalizedString>
#include <KPluginFactory>
#include <QDBusConnection>
#include <QDBusMessage>

K_PLUGIN_FACTORY_WITH_JSON(VirtualDesktopsFactory,
                           "kcm_kwin_virtualdesktops.json",
                           registerPlugin<KWin::VirtualDesktops>();
                           registerPlugin<KWin::VirtualDesktopsData>();)

namespace KWin
{

VirtualDesktops::VirtualDesktops(QObject *parent, const KPluginMetaData &metaData)
    : KQuickManagedConfigModule(parent, metaData)
    , m_data(new VirtualDesktopsData(this))
{
    qmlRegisterAnonymousType<VirtualDesktopsSettings>("org.kde.kwin.kcm.desktop", 0);

    setButtons(Apply | Default | Help);

    QObject::connect(m_data->desktopsModel(), &KWin::DesktopsModel::userModifiedChanged,
                     this, &VirtualDesktops::settingsChanged);
    connect(m_data->animationsModel(), &AnimationsModel::animationEnabledChanged,
            this, &VirtualDesktops::settingsChanged);
    connect(m_data->animationsModel(), &AnimationsModel::animationIndexChanged,
            this, &VirtualDesktops::settingsChanged);
}

VirtualDesktops::~VirtualDesktops()
{
}

QAbstractItemModel *VirtualDesktops::desktopsModel() const
{
    return m_data->desktopsModel();
}

QAbstractItemModel *VirtualDesktops::animationsModel() const
{
    return m_data->animationsModel();
}

VirtualDesktopsSettings *VirtualDesktops::virtualDesktopsSettings() const
{
    return m_data->settings();
}

void VirtualDesktops::load()
{
    KQuickManagedConfigModule::load();

    m_data->desktopsModel()->load();
    m_data->animationsModel()->load();
}

void VirtualDesktops::save()
{
    KQuickManagedConfigModule::save();

    m_data->desktopsModel()->syncWithServer();
    m_data->animationsModel()->save();

    QDBusMessage message = QDBusMessage::createSignal(QStringLiteral("/KWin"),
                                                      QStringLiteral("org.kde.KWin"), QStringLiteral("reloadConfig"));
    QDBusConnection::sessionBus().send(message);
}

void VirtualDesktops::defaults()
{
    KQuickManagedConfigModule::defaults();

    m_data->desktopsModel()->defaults();
    m_data->animationsModel()->defaults();
}

bool VirtualDesktops::isDefaults() const
{
    return m_data->isDefaults();
}

void VirtualDesktops::configureAnimation(QQuickItem *context)
{
    const QModelIndex index = m_data->animationsModel()->index(m_data->animationsModel()->animationIndex(), 0);
    if (!index.isValid()) {
        return;
    }

    m_data->animationsModel()->requestConfigure(index, context);
}

void VirtualDesktops::showAboutAnimation(QQuickItem *context)
{
    const QModelIndex index = m_data->animationsModel()->index(m_data->animationsModel()->animationIndex(), 0);
    if (!index.isValid()) {
        return;
    }

    m_data->animationsModel()->requestAbout(index, context);
}

bool VirtualDesktops::isSaveNeeded() const
{
    return m_data->animationsModel()->needsSave() || m_data->desktopsModel()->needsSave();
}

}

#include "moc_virtualdesktops.cpp"
#include "virtualdesktops.moc"
