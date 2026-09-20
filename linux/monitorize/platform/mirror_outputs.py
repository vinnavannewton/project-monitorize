"""Active compositor output selection for Mirror mode."""


def _compositor_modes(desktop):
    """Return authoritative current modes without making discovery depend on it."""
    try:
        if desktop == "kde":
            from monitorize.platform.kde_virtual_monitor import active_output_modes

            return active_output_modes()
        if desktop == "gnome":
            from monitorize.platform.gnome_virtual_monitor import active_output_modes

            return active_output_modes()
        if desktop in ("hyprland", "sway"):
            from monitorize.platform.display_controller import DisplayController

            return DisplayController(desktop).active_output_modes()
    except Exception:
        pass
    return {}


def active_outputs(desktop=""):
    """Return every named, usable screen exposed by the active Qt platform.

    ``QGuiApplication.screens()`` is already the compositor-neutral inventory
    of active outputs available to this process.  Output origin and optional
    EDID-derived metadata are deliberately not eligibility criteria.
    """
    from PyQt6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if not app or not hasattr(app, "screens"):
        return []
    modes = _compositor_modes(str(desktop or "").lower())
    result = []
    for screen in app.screens():
        name = str(screen.name() or "").strip()
        if not name:
            continue
        geometry = screen.geometry()
        if geometry.width() <= 0 or geometry.height() <= 0:
            continue
        description = " ".join(
            str(value).strip()
            for value in (screen.manufacturer(), screen.model())
            if value and str(value).strip()
        )
        mode = modes.get(name) or {}
        native_width = int(mode.get("width") or 0)
        native_height = int(mode.get("height") or 0)
        if not native_width or not native_height:
            try:
                unscaled = abs(float(screen.devicePixelRatio()) - 1.0) < 0.001
            except (TypeError, ValueError):
                unscaled = False
            if unscaled:
                native_width = geometry.width()
                native_height = geometry.height()
        result.append(
            {
                "id": name,
                "label": f"{name} — {description}" if description else name,
                "x": geometry.x(),
                "y": geometry.y(),
                "width": geometry.width(),
                "height": geometry.height(),
                "native_width": native_width,
                "native_height": native_height,
                "refresh_rate": float(mode.get("refresh_rate") or 0),
                "primary": screen == app.primaryScreen(),
            }
        )
    return result


def select_output(outputs, requested):
    matches = [item for item in outputs if item["id"] == requested]
    if requested:
        if len(matches) != 1:
            raise ValueError("Selected mirror monitor is unavailable. Choose a connected monitor.")
        return matches[0]
    if len(outputs) == 1:
        return outputs[0]
    raise ValueError(
        "Choose the monitor to mirror before starting."
        if outputs
        else "Cannot identify an active monitor. Run Monitorize with the native Wayland Qt platform."
    )
