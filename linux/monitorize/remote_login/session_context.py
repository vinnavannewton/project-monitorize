"""Whitelist root-resolved desktop metadata; never accept arbitrary environment."""

import re


def desktop_context(environment):
    session_type = environment.get('XDG_SESSION_TYPE')
    desktop = environment.get('XDG_CURRENT_DESKTOP', '').lower().split(':')
    cinnamon = bool({'cinnamon', 'x-cinnamon'}.intersection(desktop))
    if session_type == 'x11' and cinnamon:
        display = environment.get('DISPLAY', '')
        if not re.fullmatch(r':[0-9]+(?:\.[0-9]+)?', display):
            raise ValueError('Cinnamon X11 remote login requires a local X display.')
        return {'desktop': 'X-Cinnamon', 'session_type': 'x11', 'display': display}
    if session_type != 'wayland':
        raise ValueError('Remote login requires a supported graphical desktop session.')
    if any(value in ('kde','plasma') for value in desktop):value='KDE'
    elif 'hyprland' in desktop:value='Hyprland'
    elif 'gnome' in desktop:value='GNOME'
    elif cinnamon:value='X-Cinnamon'
    else:raise ValueError('This desktop has no validated remote-login activation/layout adapter.')
    return {'desktop':value}
