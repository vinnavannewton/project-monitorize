"""Stable Monitorize endpoints, separate from standalone Sunshine defaults.

Keep package firewall profiles and installer templates aligned with these bases.
Sunshine derives HTTPS, RTSP and streaming UDP ports from the base port.
"""

SUNSHINE_BASE_PORT = 50089
SUNSHINE_SECOND_PORT = 51089


def get_sunshine_port(instance: int = 1) -> int:
    inst = int(instance) if isinstance(instance, (int, str)) and str(instance).isdigit() else 1
    return SUNSHINE_BASE_PORT if inst == 1 else SUNSHINE_SECOND_PORT


def sunshine_port_conflict(log: str) -> str:
    """Describe a bind collision without exposing arbitrary remote log text."""
    import re
    for line in reversed(log.splitlines()):
        if 'bind' not in line.lower() or 'address already in use' not in line.lower():
            continue
        match = re.search(r'port\s*\[?(\d{1,5})\]?', line, re.IGNORECASE)
        endpoint = f"port {match[1]}" if match else "a required network port"
        return (f"Monitorize Sunshine cannot start because {endpoint} is already in use. "
                "Another streaming host may be using Monitorize's ports. "
                "Close the conflicting host and retry the session.")
    return ''
