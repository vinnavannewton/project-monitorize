/*
    SPDX-FileCopyrightText: 2026 Vlad Zahorodnii <vlad.zahorodnii@kde.org>

    SPDX-License-Identifier: GPL-2.0-or-later
*/

#include <QCommandLineOption>
#include <QCommandLineParser>
#include <QGuiApplication>
#include <QPainter>

#include "buffer.h"
#include "display.h"
#include "subsurface.h"
#include "surface.h"
#include "xdg_toplevel.h"

template<typename Func>
static void repaint(Demo::Surface *surface, const QSize &bufferSize, const QSize &surfaceSize, Func callback)
{
    Demo::Buffer buffer(surface->display(), bufferSize);
    QPainter painter(buffer.image());
    callback(&painter);
    painter.end();

    surface->attachBuffer(&buffer);
    surface->damageBuffer(0, 0, INT32_MAX, INT32_MAX);
    surface->setDestinationSize(surfaceSize);
    surface->commit();
}

static QRectF scaledRect(const QRect &rect, qreal devicePixelRatio)
{
    return QRectF(QPointF(rect.x(), rect.y()) * devicePixelRatio,
                  QPointF(rect.x() + rect.width(), rect.y() + rect.height()) * devicePixelRatio);
}

static QRect roundedRect(const QRectF &rect)
{
    return QRect(QPoint(std::round(rect.left()),
                        std::round(rect.top())),
                 QPoint(std::round(rect.right()) - 1,
                        std::round(rect.bottom()) - 1));
}

class Window : public QObject
{
    Q_OBJECT

public:
    explicit Window(Demo::Display *display)
        : m_display(display)
    {
        m_surface = std::make_unique<Demo::Surface>(display);
        m_xdgToplevel = std::make_unique<Demo::XdgToplevel>(display, m_surface.get());
        m_xdgToplevel->setDecorated(true);
        m_xdgToplevel->setTitle(QStringLiteral("Fractional scaling test"));
        m_surface->commit();

        for (int i = 0; i < tileCount; ++i) {
            m_tileSurfaces[i] = std::make_unique<Demo::Surface>(display);
            m_tileSubSurfaces[i] = std::make_unique<Demo::SubSurface>(display, m_tileSurfaces[i].get(), m_surface.get());
        }

        connect(m_xdgToplevel.get(), &Demo::XdgToplevel::closed, this, []() {
            qApp->quit();
        });

        connect(m_xdgToplevel.get(), &Demo::XdgToplevel::configured, this, [this](const QSizeF &size) {
            QSizeF surfaceSize = size;
            if (surfaceSize.isEmpty()) {
                surfaceSize = QSize(800, 600);
            }

            const qreal devicePixelRatio = m_forcedScaleFactor.value_or(m_surface->preferredBufferScale());
            const QSize bufferSize = (surfaceSize * devicePixelRatio).toSize();

            const QRect bufferBounds(QPoint(0,
                                            bufferSize.height() / 2),
                                     QPoint(bufferSize.width() - 1,
                                            bufferSize.height() - 1));

            for (int i = 0; i < tileCount; ++i) {
                const int row = i / tilesHorizontally;
                const int column = i % tilesHorizontally;

                // Note that both fractional-scale-v1 and fractional-scale-v2 are used now. This is
                // subject to change.
                //
                // At the moment, we split the problem in two parts: getting a proper fractional scale
                // for the buffer, and making the logical coordinate system more detailed so the client
                // can arrange the surface contents more precisely.
                //
                // The fractional-scale-v1 protocol is used to announce the buffer scale. The
                // fractional-scale-v2 protocol is used to communicate the coordinate system scale.
                //
                // We have different ideas about what scale factors the compositor sends to the client,
                // but the overall idea is still the same, the surface local coordinate system is upscaled
                // by some factor.
                //
                // There are two approaches:
                // - use the buffer scale for the logical coordinate system, i.e. effectively turn
                //   the logical coordinate space in a device coordinate space;
                // - use a different scale for the logical coordinate system, e.g. 64. This way, we
                //   will be able to preserve the separation between logical and device coordinate spaces
                //   and, overall, it will give the compositor and clients more freedom how to arrange
                //   their internals. For example, the compositor could use only integer values for the
                //   logical coordinate space.
                //
                // Both approaches have a leg to stand on. The current implementation is a compromise
                // so one can easily test both approaches. Nothing is set in stone yet, but the current
                // protocol should already be good enough for clients so they start making necessary
                // adjustments.
                const QRect bufferRect(QPoint(bufferBounds.x() + bufferBounds.width() * column / tilesHorizontally,
                                              bufferBounds.y() + bufferBounds.height() * row / tilesVertically),
                                       QPoint(bufferBounds.x() + bufferBounds.width() * (column + 1) / tilesHorizontally - 1,
                                              bufferBounds.y() + bufferBounds.height() * (row + 1) / tilesVertically - 1));
                const QRect surfaceRect = roundedRect(scaledRect(bufferRect, m_tileSurfaces[i]->clientToCompositorScale() / devicePixelRatio));

                m_tileSubSurfaces[i]->setPosition(surfaceRect.topLeft());
                repaint(m_tileSurfaces[i].get(), bufferRect.size(), surfaceRect.size(), [&](QPainter *painter) {
                    const QRect rect(QPoint(), bufferRect.size());
                    painter->fillRect(rect, QColor(253, 213, 12));

                    QFont font = painter->font();
                    font.setPixelSize(24);
                    painter->setFont(font);

                    painter->setPen(QColor(33, 33, 33));
                    painter->drawText(rect, QString::number(i), QTextOption(Qt::AlignCenter));
                });
            }

            repaint(m_surface.get(), bufferSize, (surfaceSize * m_surface->clientToCompositorScale()).toSize(), [&](QPainter *painter) {
                const QRect rect = QRect(0, 0, painter->device()->width(), painter->device()->height());
                painter->fillRect(rect, QColor(33, 33, 33));

                QFont font = painter->font();
                font.setPixelSize(48);
                painter->setFont(font);

                painter->setPen(Qt::white);
                painter->drawText(QRect(0, 0, rect.width(), rect.height() / 2), QStringLiteral("Try to resize the window. There should be no gaps between subsurfaces."), QTextOption(Qt::AlignCenter));
            });
        });
    }

    void setForcedScaleFactor(qreal scale)
    {
        m_forcedScaleFactor = scale;
    }

private:
    Demo::Display *m_display;
    std::unique_ptr<Demo::Surface> m_surface;
    std::unique_ptr<Demo::XdgToplevel> m_xdgToplevel;
    std::optional<qreal> m_forcedScaleFactor = std::nullopt;

    static constexpr int tilesVertically = 3;
    static constexpr int tilesHorizontally = 5;
    static constexpr int tileCount = tilesVertically * tilesHorizontally;
    std::array<std::unique_ptr<Demo::Surface>, tileCount> m_tileSurfaces;
    std::array<std::unique_ptr<Demo::SubSurface>, tileCount> m_tileSubSurfaces;
};

int main(int argc, char **argv)
{
    qputenv("QT_QPA_PLATFORM", "offscreen");
    QGuiApplication app(argc, argv);

    QCommandLineParser parser;
    parser.addHelpOption();

    QCommandLineOption forceScaleFactor(QStringLiteral("force-scale-factor"),
                                        QStringLiteral("Force specific scale factor, e.g. 1.0"),
                                        QStringLiteral("scale"));
    parser.addOption(forceScaleFactor);

    parser.process(app);

    Demo::Display display;
    Window window(&display);

    if (parser.isSet(forceScaleFactor)) {
        bool ok;
        const qreal scale = parser.value(forceScaleFactor).toDouble(&ok);
        if (!ok) {
            parser.showHelp(-1);
        }
        window.setForcedScaleFactor(scale);
    }

    return app.exec();
}

#include "main.moc"
