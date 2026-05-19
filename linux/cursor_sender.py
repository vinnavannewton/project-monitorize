"""
cursor_sender.py — Sends cursor position to Android over TCP (via ADB forward).

Reads cursor position from X11 (via XWayland) and sends compact binary
packets to Android over ADB-forwarded TCP port 7112.

Packet format: 4 bytes — 2 bytes X (uint16 big-endian) + 2 bytes Y (uint16 big-endian)
Sentinel 0xFFFF, 0xFFFF means cursor is off-screen.

Usage: python3 cursor_sender.py <width> <height>
"""

import socket
import struct
import sys
import time
import signal

try:
    from Xlib import display as xdisplay
except ImportError:
    print("[CursorSender] ERROR: python-xlib not installed. Run: pip install python-xlib")
    sys.exit(1)


WIDTH  = int(sys.argv[1]) if len(sys.argv) > 1 else 2560
HEIGHT = int(sys.argv[2]) if len(sys.argv) > 2 else 1600

PORT = 7112
INTERVAL = 1.0 / 120   # poll at 120Hz for smooth cursor, send only on change

running = True

def _shutdown(sig=None, frame=None):
    global running
    running = False

signal.signal(signal.SIGINT,  _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


def detect_virtual_display_offset():
    """
    Detect the pixel offset of the virtual display (TabletDisplay / HEADLESS)
    by querying xrandr. Returns (x_offset, y_offset).
    """
    import subprocess
    import re
    try:
        result = subprocess.run(
            ["xrandr", "--query"],
            capture_output=True, text=True, timeout=5,
        )
        # First pass: look for known virtual display keywords
        for line in result.stdout.splitlines():
            lower = line.lower()
            if ("headless" in lower or "virtual" in lower or "tablet" in lower) and "connected" in lower:
                m = re.search(r'(\d+)x(\d+)\+(\d+)\+(\d+)', line)
                if m:
                    ox, oy = int(m.group(3)), int(m.group(4))
                    print(f"[CursorSender] Virtual display offset: +{ox}+{oy}")
                    return ox, oy

        # Fallback: match by resolution
        for line in result.stdout.splitlines():
            if f"{WIDTH}x{HEIGHT}" in line and "connected" in line:
                m = re.search(r'(\d+)x(\d+)\+(\d+)\+(\d+)', line)
                if m:
                    ox, oy = int(m.group(3)), int(m.group(4))
                    print(f"[CursorSender] Matched display by resolution, offset: +{ox}+{oy}")
                    return ox, oy
    except Exception as e:
        print(f"[CursorSender] xrandr detection failed: {e}")

    print("[CursorSender] WARNING: Could not detect virtual display offset, assuming +0+0")
    return 0, 0


def main():
    global running

    virt_x, virt_y = detect_virtual_display_offset()

    # Open X11 display (connects to XWayland on Wayland sessions)
    try:
        disp = xdisplay.Display()
    except Exception as e:
        print(f"[CursorSender] Cannot open X11 display: {e}")
        sys.exit(1)

    root = disp.screen().root

    print(f"[CursorSender] Tracking cursor for virtual display {WIDTH}x{HEIGHT} at offset +{virt_x}+{virt_y}")
    print(f"[CursorSender] Connecting to 127.0.0.1:{PORT} (TCP via ADB forward)")

    while running:
        conn = None
        try:
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.settimeout(5.0)
            conn.connect(("127.0.0.1", PORT))
            conn.settimeout(0.5)
            print(f"[CursorSender] Connected to Android")

            last_x, last_y = -1, -1

            while running:
                try:
                    ptr = root.query_pointer()
                    gx, gy = ptr.root_x, ptr.root_y

                    # Convert to virtual-display-local coordinates
                    lx = gx - virt_x
                    ly = gy - virt_y

                    if 0 <= lx < WIDTH and 0 <= ly < HEIGHT:
                        if lx != last_x or ly != last_y:
                            conn.sendall(struct.pack(">HH", lx, ly))
                            last_x, last_y = lx, ly
                    else:
                        # Cursor left the virtual display
                        if last_x != 0xFFFF:
                            conn.sendall(struct.pack(">HH", 0xFFFF, 0xFFFF))
                            last_x, last_y = 0xFFFF, 0xFFFF

                except (BrokenPipeError, ConnectionResetError):
                    print("[CursorSender] Connection lost, reconnecting...")
                    break
                except Exception:
                    pass

                time.sleep(INTERVAL)

        except (ConnectionRefusedError, OSError) as e:
            # Android not ready yet — retry
            if running:
                time.sleep(1.0)
        finally:
            if conn:
                try: conn.close()
                except Exception: pass

    print("[CursorSender] Stopped")


if __name__ == "__main__":
    main()
