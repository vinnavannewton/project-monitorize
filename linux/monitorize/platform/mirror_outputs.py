"""Active physical output selection. Never silently substitute a missing target."""
import re

VIRTUAL = re.compile(r"^(?:Monitorize|HEADLESS|Virtual|Meta)(?:[-_].*|\d.*)?$", re.I)


def physical_outputs():
    from PyQt6.QtGui import QGuiApplication
    app = QGuiApplication.instance()
    if not app or not hasattr(app, "screens"):
        return []
    result = []
    for screen in app.screens():
        name = screen.name()
        if not name or VIRTUAL.match(name) or name.upper().startswith("XWAYLAND"):
            continue
        geometry = screen.geometry()
        if geometry.width() <= 0 or geometry.height() <= 0:
            continue
        description = " ".join(filter(None, [screen.manufacturer(), screen.model()]))
        result.append({"id": name, "label": f"{name} — {description}" if description else name,
                       "x": geometry.x(), "y": geometry.y(),
                       "width": geometry.width(), "height": geometry.height(),
                       "primary": screen == app.primaryScreen()})
    return result


def select_output(outputs, requested):
    matches = [item for item in outputs if item["id"] == requested]
    if requested:
        if len(matches) != 1:
            raise ValueError("Selected mirror monitor is unavailable. Choose a connected monitor.")
        return matches[0]
    if len(outputs) == 1:
        return outputs[0]
    raise ValueError("Choose the monitor to mirror before starting." if outputs else
                     "Cannot identify a physical monitor. Run Monitorize with the native Wayland Qt platform.")
