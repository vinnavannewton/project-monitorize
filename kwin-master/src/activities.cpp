/*
    KWin - the KDE window manager
    This file is part of the KDE project.

    SPDX-FileCopyrightText: 2013 Martin Gräßlin <mgraesslin@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/
#include "activities.h"
// KWin
#include "virtualdesktops.h"
#include "window.h"
#include "workspace.h"
#if KWIN_BUILD_X11
#include "x11window.h"
#endif
// KDE
#include <KConfigGroup>
// Qt
#include <QDBusInterface>
#include <QDBusPendingCall>
#include <QFutureWatcher>
#include <QtConcurrentRun>

namespace KWin
{

Activities::Activities()
    : m_controller(new KActivities::Controller(this))
{
    connect(m_controller, &KActivities::Controller::activityRemoved, this, &Activities::slotRemoved);
    connect(m_controller, &KActivities::Controller::activityRemoved, this, &Activities::removed);
    connect(m_controller, &KActivities::Controller::activityAdded, this, &Activities::added);
    connect(m_controller, &KActivities::Controller::currentActivityChanged, this, &Activities::slotCurrentChanged);
    connect(m_controller, &KActivities::Controller::serviceStatusChanged, this, &Activities::slotServiceStatusChanged);

    m_config = KSharedConfig::openStateConfig();
    // remove old config
    m_config->group("Activities").deleteGroup("LastVirtualDesktop");
    kwinApp()->config()->group("Activities").deleteGroup("LastVirtualDesktop");
    auto perOutputLastDesktopConfig = m_config->group("Activities").group("PerOutputLastVirtualDesktop");

    const auto &activities = perOutputLastDesktopConfig.groupList();
    for (const auto &activity : activities) {
        auto activityLastDesktopConfig = perOutputLastDesktopConfig.group(activity);
        const auto &outputUuids = activityLastDesktopConfig.keyList();
        for (const auto &outputUuid : outputUuids) {
            const QString desktop = activityLastDesktopConfig.readEntry(outputUuid);
            if (!desktop.isEmpty()) {
                m_lastVirtualDesktop[activity][outputUuid] = desktop;
            }
        }
    }

    // Clean up no longer used subsession data
    const auto sessionsConfig = KSharedConfig::openConfig();
    const auto groups = sessionsConfig->groupList();
    for (const QString &groupName : groups) {
        if (groupName.startsWith(QLatin1StringView("SubSession: "))) {
            sessionsConfig->deleteGroup(groupName);
        }
    }
}

KActivities::Consumer::ServiceStatus Activities::serviceStatus() const
{
    return m_controller->serviceStatus();
}

std::optional<QString> Activities::findLastDesktopForOutput(LogicalOutput *output) const
{
    const auto it = m_lastVirtualDesktop.find(m_current);
    if (it == m_lastVirtualDesktop.end()) {
        return {};
    }

    const auto outputDesktopIt = it->second.find(output->uuid());
    if (outputDesktopIt == it->second.end()) {
        return {};
    }

    return outputDesktopIt->second;
}

void Activities::slotServiceStatusChanged()
{
    if (m_controller->serviceStatus() != KActivities::Consumer::Running) {
        return;
    }
    const auto windows = Workspace::self()->windows();
    for (auto *const window : windows) {
        if (!window->isClient()) {
            continue;
        }
        if (window->isDesktop()) {
            continue;
        }
        window->checkActivities();
    }
}

void Activities::setCurrent(const QString &activity, VirtualDesktop *desktop, LogicalOutput *output)
{
    if (desktop && output) {
        m_lastVirtualDesktop[activity][output->uuid()] = desktop->id();
    }
    m_controller->setCurrentActivity(activity);
}

void Activities::notifyCurrentDesktopChanged(VirtualDesktop *desktop, LogicalOutput *output)
{
    m_lastVirtualDesktop[m_current][output->uuid()] = desktop->id();
    auto lastDesktopConfig = m_config->group("Activities").group("PerOutputLastVirtualDesktop").group(m_current);
    lastDesktopConfig.writeEntry(output->uuid(), desktop->id());
}

void Activities::slotCurrentChanged(const QString &newActivity)
{
    if (m_current == newActivity) {
        return;
    }
    Q_EMIT currentAboutToChange();
    m_previous = m_current;
    m_current = newActivity;

    const auto it = m_lastVirtualDesktop.find(m_current);
    if (it != m_lastVirtualDesktop.end()) {
        const auto &outputDesktops = it->second;
        const auto outputs = workspace()->outputs();
        for (LogicalOutput *output : outputs) {
            const auto outputDesktopIt = outputDesktops.find(output->uuid());
            if (outputDesktopIt != outputDesktops.end()) {
                VirtualDesktop *desktop = VirtualDesktopManager::self()->desktopForId(outputDesktopIt->second);
                if (desktop) {
                    VirtualDesktopManager::self()->setCurrent(desktop, output);
                }
            }
        }
    }

    Q_EMIT currentChanged(newActivity);
}

void Activities::slotRemoved(const QString &activity)
{
    const auto windows = Workspace::self()->windows();
    for (auto *const window : windows) {
        if (!window->isClient()) {
            continue;
        }
        if (window->isDesktop()) {
            continue;
        }
        window->setOnActivity(activity, false);
    }

    m_lastVirtualDesktop.erase(activity);
    m_config->group("Activities").group("PerOutputLastVirtualDesktop").deleteGroup(activity);
}

void Activities::toggleWindowOnActivity(Window *window, const QString &activity, bool dont_activate)
{
    // int old_desktop = window->desktop();
    bool was_on_activity = window->isOnActivity(activity);
    bool was_on_all = window->isOnAllActivities();
    // note: all activities === no activities
    bool enable = was_on_all || !was_on_activity;
    window->setOnActivity(activity, enable);
    if (window->isOnActivity(activity) == was_on_activity && window->isOnAllActivities() == was_on_all) { // No change
        return;
    }

    Workspace *ws = Workspace::self();
    if (window->isOnCurrentActivity()) {
        if (window->wantsTabFocus() && options->focusPolicyIsReasonable() && !was_on_activity && // for stickiness changes
                                                                                                 // FIXME not sure if the line above refers to the correct activity
            !dont_activate) {
            ws->requestFocus(window);
        } else {
            ws->restackWindowUnderActive(window);
        }
    } else {
        ws->raiseWindow(window);
    }

    // notifyWindowDesktopChanged( c, old_desktop );

    const auto transients_stacking_order = ws->ensureStackingOrder(window->transients());
    for (auto *const window : transients_stacking_order) {
        if (!window) {
            continue;
        }
        toggleWindowOnActivity(window, activity, dont_activate);
    }
    ws->rearrange();
}

} // namespace

#include "moc_activities.cpp"
