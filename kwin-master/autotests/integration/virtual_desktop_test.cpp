/*
    KWin - the KDE window manager
    This file is part of the KDE project.

    SPDX-FileCopyrightText: 2017 Martin Flöser <mgraesslin@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/
#include "kwin_wayland_test.h"

#include "main.h"
#include "virtualdesktops.h"
#include "wayland_server.h"
#include "window.h"
#include "workspace.h"

#if KWIN_BUILD_X11
#include "utils/xcbutils.h"
#endif

#include <KWayland/Client/surface.h>

using namespace KWin;

class VirtualDesktopTest : public QObject
{
    Q_OBJECT
private Q_SLOTS:
    void initTestCase();
    void init();
    void cleanup();
    void current_data();
    void current();
    void currentChangeOnCountChange_data();
    void currentChangeOnCountChange();
    void next_data();
    void next();
    void previous_data();
    void previous();
    void left_data();
    void left();
    void right_data();
    void right();
    void above_data();
    void above();
    void below_data();
    void below();
    void switchToShortcuts();
#if KWIN_BUILD_X11
    void testNetCurrentDesktop();
#endif
    void testLastDesktopRemoved();
    void testWindowOnMultipleDesktops();
    void testRemoveDesktopWithWindow();
    void testPerOutputDesktopSwitching();
    void testTogglePerOutputDesktops();

private:
    void addDirectionColumns();
    void testDirection(const QString &actionName, VirtualDesktopManager::Direction direction);
    LogicalOutput *findInactiveOutput() const;
};

void VirtualDesktopTest::initTestCase()
{
    qRegisterMetaType<KWin::Window *>();
    QVERIFY(waylandServer()->init(qAppName()));

    kwinApp()->setConfig(KSharedConfig::openConfig(QString(), KConfig::SimpleConfig));
    qputenv("KWIN_XKB_DEFAULT_KEYMAP", "1");
    qputenv("XKB_DEFAULT_RULES", "evdev");

    kwinApp()->start();
    Test::setOutputConfig({
        Rect(0, 0, 1280, 1024),
        Rect(1280, 0, 1280, 1024),
    });

#if KWIN_BUILD_X11
    if (kwinApp()->x11Connection()) {
        // verify the current desktop x11 property on startup, see BUG: 391034
        Xcb::Atom currentDesktopAtom("_NET_CURRENT_DESKTOP");
        QVERIFY(currentDesktopAtom.isValid());
        Xcb::Property currentDesktop(0, kwinApp()->x11RootWindow(), currentDesktopAtom, XCB_ATOM_CARDINAL, 0, 1);
        QCOMPARE(currentDesktop.value<uint32_t>(), 0);
    }
#endif
}

void VirtualDesktopTest::init()
{
    QVERIFY(Test::setupWaylandConnection());
    workspace()->setActiveOutput(QPoint(640, 512));
    VirtualDesktopManager::self()->setCount(1);
}

void VirtualDesktopTest::cleanup()
{
    Test::destroyWaylandConnection();
    VirtualDesktopManager::self()->setPerOutputVirtualDesktops(false);
}

#if KWIN_BUILD_X11
void VirtualDesktopTest::testNetCurrentDesktop()
{
    if (!kwinApp()->x11Connection()) {
        QSKIP("Skipped on Wayland only");
    }
    QCOMPARE(VirtualDesktopManager::self()->count(), 1u);
    VirtualDesktopManager::self()->setCount(4);
    QCOMPARE(VirtualDesktopManager::self()->count(), 4u);

    Xcb::Atom currentDesktopAtom("_NET_CURRENT_DESKTOP");
    QVERIFY(currentDesktopAtom.isValid());
    Xcb::Property currentDesktop(0, kwinApp()->x11RootWindow(), currentDesktopAtom, XCB_ATOM_CARDINAL, 0, 1);
    QCOMPARE(currentDesktop.value<uint32_t>(), 0);

    // go to desktop 2
    VirtualDesktopManager::self()->setCurrent(2);
    currentDesktop = Xcb::Property(0, kwinApp()->x11RootWindow(), currentDesktopAtom, XCB_ATOM_CARDINAL, 0, 1);
    QCOMPARE(currentDesktop.value<uint32_t>(), 1);

    // go to desktop 3
    VirtualDesktopManager::self()->setCurrent(3);
    currentDesktop = Xcb::Property(0, kwinApp()->x11RootWindow(), currentDesktopAtom, XCB_ATOM_CARDINAL, 0, 1);
    QCOMPARE(currentDesktop.value<uint32_t>(), 2);

    // go to desktop 4
    VirtualDesktopManager::self()->setCurrent(4);
    currentDesktop = Xcb::Property(0, kwinApp()->x11RootWindow(), currentDesktopAtom, XCB_ATOM_CARDINAL, 0, 1);
    QCOMPARE(currentDesktop.value<uint32_t>(), 3);

    // and back to first
    VirtualDesktopManager::self()->setCurrent(1);
    currentDesktop = Xcb::Property(0, kwinApp()->x11RootWindow(), currentDesktopAtom, XCB_ATOM_CARDINAL, 0, 1);
    QCOMPARE(currentDesktop.value<uint32_t>(), 0);
}
#endif

void VirtualDesktopTest::testLastDesktopRemoved()
{
    // first create a new desktop
    QCOMPARE(VirtualDesktopManager::self()->count(), 1u);
    VirtualDesktopManager::self()->setCount(2);
    QCOMPARE(VirtualDesktopManager::self()->count(), 2u);

    // switch to last desktop
    VirtualDesktopManager::self()->setCurrent(VirtualDesktopManager::self()->desktops().last());
    QCOMPARE(VirtualDesktopManager::self()->current(), 2u);

    // now create a window on this desktop
    std::unique_ptr<KWayland::Client::Surface> surface(Test::createSurface());
    std::unique_ptr<Test::XdgToplevel> shellSurface(Test::createXdgToplevelSurface(surface.get()));
    auto window = Test::renderAndWaitForShown(surface.get(), QSize(100, 50), Qt::blue);

    QVERIFY(window);
    QCOMPARE(window->desktops().count(), 1u);
    QCOMPARE(VirtualDesktopManager::self()->currentDesktop(), window->desktops().first());

    // and remove last desktop
    VirtualDesktopManager::self()->setCount(1);
    QCOMPARE(VirtualDesktopManager::self()->count(), 1u);
    // now the window should be moved as well
    QCOMPARE(window->desktops().count(), 1u);
    QCOMPARE(VirtualDesktopManager::self()->currentDesktop(), window->desktops().first());
}

void VirtualDesktopTest::testWindowOnMultipleDesktops()
{
    // first create two new desktops
    QCOMPARE(VirtualDesktopManager::self()->count(), 1u);
    VirtualDesktopManager::self()->setCount(3);
    QCOMPARE(VirtualDesktopManager::self()->count(), 3u);

    // switch to last desktop
    const auto desktops = VirtualDesktopManager::self()->desktops();
    VirtualDesktopManager::self()->setCurrent(desktops.at(2));

    // now create a window on this desktop
    std::unique_ptr<KWayland::Client::Surface> surface(Test::createSurface());
    std::unique_ptr<Test::XdgToplevel> shellSurface(Test::createXdgToplevelSurface(surface.get()));
    auto window = Test::renderAndWaitForShown(surface.get(), QSize(100, 50), Qt::blue);
    QVERIFY(window);
    QCOMPARE(window->desktops(), (QList<VirtualDesktop *>{desktops.at(2)}));

    // Set the window on desktop 2 as well
    window->enterDesktop(VirtualDesktopManager::self()->desktopForX11Id(2));
    QCOMPARE(window->desktops().count(), 2u);
    QCOMPARE(window->desktops()[0], desktops.at(2));
    QCOMPARE(window->desktops()[1], desktops.at(1));

    // leave desktop 3
    window->leaveDesktop(desktops.at(2));
    QCOMPARE(window->desktops(), (QList<VirtualDesktop *>{desktops.at(1)}));
    // leave desktop 2
    window->leaveDesktop(desktops.at(1));
    QCOMPARE(window->desktops(), QList<VirtualDesktop *>{});
    // we should be on all desktops now
    QVERIFY(window->isOnAllDesktops());
    // put on desktop 1
    window->enterDesktop(desktops.at(0));
    QVERIFY(window->isOnDesktop(desktops.at(0)));
    QVERIFY(!window->isOnDesktop(desktops.at(1)));
    QVERIFY(!window->isOnDesktop(desktops.at(2)));
    QCOMPARE(window->desktops().count(), 1u);
    // put on desktop 2
    window->enterDesktop(desktops.at(1));
    QVERIFY(window->isOnDesktop(desktops.at(0)));
    QVERIFY(window->isOnDesktop(desktops.at(1)));
    QVERIFY(!window->isOnDesktop(desktops.at(2)));
    QCOMPARE(window->desktops().count(), 2u);
    // put on desktop 3
    window->enterDesktop(desktops.at(2));
    QVERIFY(window->isOnDesktop(desktops.at(0)));
    QVERIFY(window->isOnDesktop(desktops.at(1)));
    QVERIFY(window->isOnDesktop(desktops.at(2)));
    QCOMPARE(window->desktops().count(), 3u);

    // entering twice dooes nothing
    window->enterDesktop(desktops.at(2));
    QCOMPARE(window->desktops().count(), 3u);

    // adding to "all desktops" results in just that one desktop
    window->setOnAllDesktops(true);
    QCOMPARE(window->desktops().count(), 0u);
    window->enterDesktop(desktops.at(2));
    QVERIFY(window->isOnDesktop(desktops.at(2)));
    QCOMPARE(window->desktops().count(), 1u);

    // leaving a desktop on "all desktops" puts on everything else
    window->setOnAllDesktops(true);
    QCOMPARE(window->desktops().count(), 0u);
    window->leaveDesktop(desktops.at(2));
    QVERIFY(window->isOnDesktop(desktops.at(0)));
    QVERIFY(window->isOnDesktop(desktops.at(1)));
    QCOMPARE(window->desktops().count(), 2u);
}

void VirtualDesktopTest::testRemoveDesktopWithWindow()
{
    // first create two new desktops
    QCOMPARE(VirtualDesktopManager::self()->count(), 1u);
    VirtualDesktopManager::self()->setCount(3);
    QCOMPARE(VirtualDesktopManager::self()->count(), 3u);

    // switch to last desktop
    VirtualDesktopManager::self()->setCurrent(VirtualDesktopManager::self()->desktops().last());
    QCOMPARE(VirtualDesktopManager::self()->current(), 3u);

    // now create a window on this desktop
    std::unique_ptr<KWayland::Client::Surface> surface(Test::createSurface());
    std::unique_ptr<Test::XdgToplevel> shellSurface(Test::createXdgToplevelSurface(surface.get()));
    auto window = Test::renderAndWaitForShown(surface.get(), QSize(100, 50), Qt::blue);

    QVERIFY(window);

    QCOMPARE(window->desktops().count(), 1u);
    QCOMPARE(VirtualDesktopManager::self()->currentDesktop(), window->desktops().first());

    // Set the window on desktop 2 as well
    window->enterDesktop(VirtualDesktopManager::self()->desktops()[1]);
    QCOMPARE(window->desktops().count(), 2u);
    QCOMPARE(VirtualDesktopManager::self()->desktops()[2], window->desktops()[0]);
    QCOMPARE(VirtualDesktopManager::self()->desktops()[1], window->desktops()[1]);

    // remove desktop 3
    VirtualDesktopManager::self()->setCount(2);
    QCOMPARE(window->desktops().count(), 1u);
    // window is only on desktop 2
    QCOMPARE(VirtualDesktopManager::self()->desktops()[1], window->desktops()[0]);

    // Again 3 desktops
    VirtualDesktopManager::self()->setCount(3);
    // move window to be only on desktop 3
    window->enterDesktop(VirtualDesktopManager::self()->desktops()[2]);
    window->leaveDesktop(VirtualDesktopManager::self()->desktops()[1]);
    QCOMPARE(window->desktops().count(), 1u);
    // window is only on desktop 3
    QCOMPARE(VirtualDesktopManager::self()->desktops()[2], window->desktops()[0]);

    // remove desktop 3
    VirtualDesktopManager::self()->setCount(2);
    QCOMPARE(window->desktops().count(), 1u);
    // window is only on desktop 2
    QCOMPARE(VirtualDesktopManager::self()->desktops()[1], window->desktops()[0]);
}

void VirtualDesktopTest::current_data()
{
    QTest::addColumn<uint>("count");
    QTest::addColumn<uint>("init");
    QTest::addColumn<uint>("request");
    QTest::addColumn<uint>("result");
    QTest::addColumn<bool>("signal");

    QTest::newRow("lower") << (uint)4 << (uint)3 << (uint)2 << (uint)2 << true;
    QTest::newRow("higher") << (uint)4 << (uint)1 << (uint)2 << (uint)2 << true;
    QTest::newRow("maximum") << (uint)4 << (uint)1 << (uint)4 << (uint)4 << true;
    QTest::newRow("above maximum") << (uint)4 << (uint)1 << (uint)5 << (uint)1 << false;
    QTest::newRow("minimum") << (uint)4 << (uint)2 << (uint)1 << (uint)1 << true;
    QTest::newRow("below minimum") << (uint)4 << (uint)2 << (uint)0 << (uint)2 << false;
    QTest::newRow("unchanged") << (uint)4 << (uint)2 << (uint)2 << (uint)2 << false;
}

void VirtualDesktopTest::current()
{
    VirtualDesktopManager *vds = VirtualDesktopManager::self();
    QCOMPARE(vds->current(), (uint)1);
    QFETCH(uint, count);
    QFETCH(uint, init);
    vds->setCount(count);
    vds->setCurrent(init);
    QCOMPARE(vds->current(), init);

    QSignalSpy spy(vds, &VirtualDesktopManager::currentChanged);

    QFETCH(uint, request);
    QFETCH(uint, result);
    QFETCH(bool, signal);
    QCOMPARE(vds->setCurrent(request), signal);
    QCOMPARE(vds->current(), result);

    for (LogicalOutput *output : workspace()->outputs()) {
        QCOMPARE(vds->current(output), result);
    }

    QCOMPARE(spy.isEmpty(), !signal);
    if (!spy.isEmpty()) {
        QList<QVariant> arguments = spy.takeFirst();
        QCOMPARE(arguments.count(), 3);

        VirtualDesktop *previous = arguments.at(0).value<VirtualDesktop *>();
        QCOMPARE(previous->x11DesktopNumber(), init);

        VirtualDesktop *current = arguments.at(1).value<VirtualDesktop *>();
        QCOMPARE(current->x11DesktopNumber(), result);
    }
}

void VirtualDesktopTest::currentChangeOnCountChange_data()
{
    QTest::addColumn<uint>("initCount");
    QTest::addColumn<uint>("initCurrent");
    QTest::addColumn<uint>("request");
    QTest::addColumn<uint>("current");
    QTest::addColumn<bool>("signal");

    QTest::newRow("increment") << (uint)4 << (uint)2 << (uint)5 << (uint)2 << false;
    QTest::newRow("increment on last") << (uint)4 << (uint)4 << (uint)5 << (uint)4 << false;
    QTest::newRow("decrement") << (uint)4 << (uint)2 << (uint)3 << (uint)2 << false;
    QTest::newRow("decrement on second last") << (uint)4 << (uint)3 << (uint)3 << (uint)3 << false;
    QTest::newRow("decrement on last") << (uint)4 << (uint)4 << (uint)3 << (uint)3 << true;
    QTest::newRow("multiple decrement") << (uint)4 << (uint)2 << (uint)1 << (uint)1 << true;
}

void VirtualDesktopTest::currentChangeOnCountChange()
{
    VirtualDesktopManager *vds = VirtualDesktopManager::self();
    QFETCH(uint, initCount);
    QFETCH(uint, initCurrent);
    vds->setCount(initCount);
    vds->setCurrent(initCurrent);

    QSignalSpy spy(vds, &VirtualDesktopManager::currentChanged);

    QFETCH(uint, request);
    QFETCH(uint, current);
    QFETCH(bool, signal);

    vds->setCount(request);
    QCOMPARE(vds->current(), current);
    QCOMPARE(spy.isEmpty(), !signal);
}

void VirtualDesktopTest::addDirectionColumns()
{
    QTest::addColumn<uint>("initCount");
    QTest::addColumn<uint>("initCurrent");
    QTest::addColumn<bool>("wrap");
    QTest::addColumn<uint>("result");
}

void VirtualDesktopTest::testDirection(const QString &actionName, VirtualDesktopManager::Direction direction)
{
    VirtualDesktopManager *vds = VirtualDesktopManager::self();
    QFETCH(uint, initCount);
    QFETCH(uint, initCurrent);
    vds->setCount(initCount);
    vds->setCurrent(initCurrent);
    vds->setRows(2);

    QFETCH(bool, wrap);
    QFETCH(uint, result);
    QCOMPARE(vds->inDirection(nullptr, direction, wrap)->x11DesktopNumber(), result);

    vds->setNavigationWrappingAround(wrap);
    vds->initShortcuts();
    QAction *action = vds->findChild<QAction *>(actionName);
    QVERIFY(action);
    action->trigger();
    QCOMPARE(vds->current(), result);
    QCOMPARE(vds->inDirection(initCurrent, direction, wrap), result);
}

void VirtualDesktopTest::next_data()
{
    addDirectionColumns();

    QTest::newRow("one desktop, wrap") << (uint)1 << (uint)1 << true << (uint)1;
    QTest::newRow("one desktop, no wrap") << (uint)1 << (uint)1 << false << (uint)1;
    QTest::newRow("desktops, wrap") << (uint)4 << (uint)1 << true << (uint)2;
    QTest::newRow("desktops, no wrap") << (uint)4 << (uint)1 << false << (uint)2;
    QTest::newRow("desktops at end, wrap") << (uint)4 << (uint)4 << true << (uint)1;
    QTest::newRow("desktops at end, no wrap") << (uint)4 << (uint)4 << false << (uint)4;
}

void VirtualDesktopTest::next()
{
    testDirection(QStringLiteral("Switch to Next Desktop"), VirtualDesktopManager::Direction::Next);
}

void VirtualDesktopTest::previous_data()
{
    addDirectionColumns();

    QTest::newRow("one desktop, wrap") << (uint)1 << (uint)1 << true << (uint)1;
    QTest::newRow("one desktop, no wrap") << (uint)1 << (uint)1 << false << (uint)1;
    QTest::newRow("desktops, wrap") << (uint)4 << (uint)3 << true << (uint)2;
    QTest::newRow("desktops, no wrap") << (uint)4 << (uint)3 << false << (uint)2;
    QTest::newRow("desktops at start, wrap") << (uint)4 << (uint)1 << true << (uint)4;
    QTest::newRow("desktops at start, no wrap") << (uint)4 << (uint)1 << false << (uint)1;
}

void VirtualDesktopTest::previous()
{
    testDirection(QStringLiteral("Switch to Previous Desktop"), VirtualDesktopManager::Direction::Previous);
}

void VirtualDesktopTest::left_data()
{
    addDirectionColumns();
    QTest::newRow("one desktop, wrap") << (uint)1 << (uint)1 << true << (uint)1;
    QTest::newRow("one desktop, no wrap") << (uint)1 << (uint)1 << false << (uint)1;
    QTest::newRow("desktops, wrap, 1st row") << (uint)4 << (uint)2 << true << (uint)1;
    QTest::newRow("desktops, no wrap, 1st row") << (uint)4 << (uint)2 << false << (uint)1;
    QTest::newRow("desktops, wrap, 2nd row") << (uint)4 << (uint)4 << true << (uint)3;
    QTest::newRow("desktops, no wrap, 2nd row") << (uint)4 << (uint)4 << false << (uint)3;

    QTest::newRow("desktops at start, wrap, 1st row") << (uint)4 << (uint)1 << true << (uint)2;
    QTest::newRow("desktops at start, no wrap, 1st row") << (uint)4 << (uint)1 << false << (uint)1;
    QTest::newRow("desktops at start, wrap, 2nd row") << (uint)4 << (uint)3 << true << (uint)4;
    QTest::newRow("desktops at start, no wrap, 2nd row") << (uint)4 << (uint)3 << false << (uint)3;

    QTest::newRow("non symmetric, start") << (uint)5 << (uint)5 << false << (uint)4;
    QTest::newRow("non symmetric, end, no wrap") << (uint)5 << (uint)4 << false << (uint)4;
    QTest::newRow("non symmetric, end, wrap") << (uint)5 << (uint)4 << true << (uint)5;
}

void VirtualDesktopTest::left()
{
    testDirection(QStringLiteral("Switch One Desktop to the Left"), VirtualDesktopManager::Direction::Left);
}

void VirtualDesktopTest::right_data()
{
    addDirectionColumns();
    QTest::newRow("one desktop, wrap") << (uint)1 << (uint)1 << true << (uint)1;
    QTest::newRow("one desktop, no wrap") << (uint)1 << (uint)1 << false << (uint)1;
    QTest::newRow("desktops, wrap, 1st row") << (uint)4 << (uint)1 << true << (uint)2;
    QTest::newRow("desktops, no wrap, 1st row") << (uint)4 << (uint)1 << false << (uint)2;
    QTest::newRow("desktops, wrap, 2nd row") << (uint)4 << (uint)3 << true << (uint)4;
    QTest::newRow("desktops, no wrap, 2nd row") << (uint)4 << (uint)3 << false << (uint)4;

    QTest::newRow("desktops at start, wrap, 1st row") << (uint)4 << (uint)2 << true << (uint)1;
    QTest::newRow("desktops at start, no wrap, 1st row") << (uint)4 << (uint)2 << false << (uint)2;
    QTest::newRow("desktops at start, wrap, 2nd row") << (uint)4 << (uint)4 << true << (uint)3;
    QTest::newRow("desktops at start, no wrap, 2nd row") << (uint)4 << (uint)4 << false << (uint)4;

    QTest::newRow("non symmetric, start") << (uint)5 << (uint)4 << false << (uint)5;
    QTest::newRow("non symmetric, end, no wrap") << (uint)5 << (uint)5 << false << (uint)5;
    QTest::newRow("non symmetric, end, wrap") << (uint)5 << (uint)5 << true << (uint)4;
}

void VirtualDesktopTest::right()
{
    testDirection(QStringLiteral("Switch One Desktop to the Right"), VirtualDesktopManager::Direction::Right);
}

void VirtualDesktopTest::above_data()
{
    addDirectionColumns();
    QTest::newRow("one desktop, wrap") << (uint)1 << (uint)1 << true << (uint)1;
    QTest::newRow("one desktop, no wrap") << (uint)1 << (uint)1 << false << (uint)1;
    QTest::newRow("desktops, wrap, 1st column") << (uint)4 << (uint)3 << true << (uint)1;
    QTest::newRow("desktops, no wrap, 1st column") << (uint)4 << (uint)3 << false << (uint)1;
    QTest::newRow("desktops, wrap, 2nd column") << (uint)4 << (uint)4 << true << (uint)2;
    QTest::newRow("desktops, no wrap, 2nd column") << (uint)4 << (uint)4 << false << (uint)2;

    QTest::newRow("desktops at start, wrap, 1st column") << (uint)4 << (uint)1 << true << (uint)3;
    QTest::newRow("desktops at start, no wrap, 1st column") << (uint)4 << (uint)1 << false << (uint)1;
    QTest::newRow("desktops at start, wrap, 2nd column") << (uint)4 << (uint)2 << true << (uint)4;
    QTest::newRow("desktops at start, no wrap, 2nd column") << (uint)4 << (uint)2 << false << (uint)2;
}

void VirtualDesktopTest::above()
{
    testDirection(QStringLiteral("Switch One Desktop Up"), VirtualDesktopManager::Direction::Up);
}

void VirtualDesktopTest::below_data()
{
    addDirectionColumns();
    QTest::newRow("one desktop, wrap") << (uint)1 << (uint)1 << true << (uint)1;
    QTest::newRow("one desktop, no wrap") << (uint)1 << (uint)1 << false << (uint)1;
    QTest::newRow("desktops, wrap, 1st column") << (uint)4 << (uint)1 << true << (uint)3;
    QTest::newRow("desktops, no wrap, 1st column") << (uint)4 << (uint)1 << false << (uint)3;
    QTest::newRow("desktops, wrap, 2nd column") << (uint)4 << (uint)2 << true << (uint)4;
    QTest::newRow("desktops, no wrap, 2nd column") << (uint)4 << (uint)2 << false << (uint)4;

    QTest::newRow("desktops at start, wrap, 1st column") << (uint)4 << (uint)3 << true << (uint)1;
    QTest::newRow("desktops at start, no wrap, 1st column") << (uint)4 << (uint)3 << false << (uint)3;
    QTest::newRow("desktops at start, wrap, 2nd column") << (uint)4 << (uint)4 << true << (uint)2;
    QTest::newRow("desktops at start, no wrap, 2nd column") << (uint)4 << (uint)4 << false << (uint)4;
}

void VirtualDesktopTest::below()
{
    testDirection(QStringLiteral("Switch One Desktop Down"), VirtualDesktopManager::Direction::Down);
}

void VirtualDesktopTest::switchToShortcuts()
{
    VirtualDesktopManager *vds = VirtualDesktopManager::self();
    vds->setCount(vds->maximum());
    vds->setCurrent(vds->maximum());
    QCOMPARE(vds->current(), vds->maximum());
    vds->initShortcuts();
    const QString toDesktop = QStringLiteral("Switch to Desktop %1");
    for (uint i = 1; i <= vds->maximum(); ++i) {
        const QString desktop(toDesktop.arg(i));
        QAction *action = vds->findChild<QAction *>(desktop);
        QVERIFY2(action, desktop.toUtf8().constData());
        action->trigger();
        QCOMPARE(vds->current(), i);
    }
    // invoke switchTo not from a QAction
    QMetaObject::invokeMethod(vds, "slotSwitchTo");
    // should still be on max
    QCOMPARE(vds->current(), vds->maximum());
}

void VirtualDesktopTest::testPerOutputDesktopSwitching()
{
    VirtualDesktopManager *vds = VirtualDesktopManager::self();
    vds->setPerOutputVirtualDesktops(true);
    LogicalOutput *activeOutput = workspace()->activeOutput();
    LogicalOutput *inactiveOutput = findInactiveOutput();

    QCOMPARE(vds->current(activeOutput), (uint)1);
    QCOMPARE(vds->current(inactiveOutput), (uint)1);
    vds->setCount(4);
    QSignalSpy spy(vds, &VirtualDesktopManager::currentChanged);
    vds->setCurrent(2, inactiveOutput);
    QCOMPARE(vds->current(activeOutput), (uint)1);
    QCOMPARE(vds->current(inactiveOutput), (uint)2);
    QCOMPARE(spy.size(), 1);
    QList<QVariant> arguments = spy.takeFirst();
    QCOMPARE(arguments.count(), 3);

    VirtualDesktop *previous = arguments.at(0).value<VirtualDesktop *>();
    QCOMPARE(previous->x11DesktopNumber(), (uint)1);

    VirtualDesktop *current = arguments.at(1).value<VirtualDesktop *>();
    QCOMPARE(current->x11DesktopNumber(), (uint)2);

    LogicalOutput *output = arguments.at(2).value<LogicalOutput *>();
    QCOMPARE(output, inactiveOutput);
}

void VirtualDesktopTest::testTogglePerOutputDesktops()
{
    VirtualDesktopManager *vds = VirtualDesktopManager::self();
    vds->setPerOutputVirtualDesktops(false);
    LogicalOutput *activeOutput = workspace()->activeOutput();
    LogicalOutput *inactiveOutput = findInactiveOutput();

    QCOMPARE(vds->current(activeOutput), (uint)1);
    QCOMPARE(vds->current(inactiveOutput), (uint)1);
    vds->setCount(4);
    QSignalSpy spy(vds, &VirtualDesktopManager::currentChanged);
    vds->setCurrent(2, inactiveOutput);
    QCOMPARE(vds->current(activeOutput), (uint)2);
    QCOMPARE(vds->current(inactiveOutput), (uint)2);

    QCOMPARE(spy.size(), 2);

    while (!spy.isEmpty()) {
        QList<QVariant> arguments = spy.takeFirst();
        QCOMPARE(arguments.count(), 3);

        VirtualDesktop *previous = arguments.at(0).value<VirtualDesktop *>();
        QCOMPARE(previous->x11DesktopNumber(), (uint)1);

        VirtualDesktop *current = arguments.at(1).value<VirtualDesktop *>();
        QCOMPARE(current->x11DesktopNumber(), (uint)2);
        // ignore output
    }

    vds->setPerOutputVirtualDesktops(true);
    QCOMPARE(spy.isEmpty(), true);
    QCOMPARE(vds->current(activeOutput), (uint)2);
    QCOMPARE(vds->current(inactiveOutput), (uint)2);

    vds->setCurrent(3, inactiveOutput);
    QCOMPARE(vds->current(activeOutput), (uint)2);
    QCOMPARE(vds->current(inactiveOutput), (uint)3);

    QCOMPARE(spy.size(), 1);

    {
        QList<QVariant> arguments = spy.takeFirst();
        QCOMPARE(arguments.count(), 3);

        VirtualDesktop *previous = arguments.at(0).value<VirtualDesktop *>();
        QCOMPARE(previous->x11DesktopNumber(), (uint)2);

        VirtualDesktop *current = arguments.at(1).value<VirtualDesktop *>();
        QCOMPARE(current->x11DesktopNumber(), (uint)3);

        LogicalOutput *output = arguments.at(2).value<LogicalOutput *>();
        QCOMPARE(output, inactiveOutput);
    }

    vds->setPerOutputVirtualDesktops(false);
    QCOMPARE(vds->current(activeOutput), (uint)2);
    QCOMPARE(vds->current(inactiveOutput), (uint)2);

    QCOMPARE(spy.size(), 1);

    {
        QList<QVariant> arguments = spy.takeFirst();
        QCOMPARE(arguments.count(), 3);

        VirtualDesktop *previous = arguments.at(0).value<VirtualDesktop *>();
        QCOMPARE(previous->x11DesktopNumber(), (uint)3);

        VirtualDesktop *current = arguments.at(1).value<VirtualDesktop *>();
        QCOMPARE(current->x11DesktopNumber(), (uint)2);

        LogicalOutput *output = arguments.at(2).value<LogicalOutput *>();
        QCOMPARE(output, inactiveOutput);
    }
}

LogicalOutput *VirtualDesktopTest::findInactiveOutput() const
{
    LogicalOutput *activeOutput = workspace()->activeOutput();

    for (LogicalOutput *output : workspace()->outputs()) {
        if (output != activeOutput) {
            return output;
        }
    }

    QTest::qFail("Expected to find inactive output, but didn't find one.", __FILE__, __LINE__);

    return nullptr;
}

WAYLANDTEST_MAIN(VirtualDesktopTest)
#include "virtual_desktop_test.moc"
