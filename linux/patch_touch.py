import re

with open("/home/vinnavan/user/MegaProjects/Monitorize/linux/touch_daemon.py", "r") as f:
    content = f.read()

# Replace the TCP server binding loop
old_server = '''    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass

    bind_host = "0.0.0.0" if _WIFI else "127.0.0.1"
    for attempt in range(8):
        try:
            server.bind((bind_host, PORT))
            break
        except OSError:
            log.warning("[TCP] Port %d busy (attempt %d/8) — retrying in 1 s…", PORT, attempt + 1)
            time.sleep(1)
    else:
        log.error("[TCP] Could not bind port %d — touch disabled.", PORT)
        return

    server.listen(2)
    server.settimeout(1.0)
    mode_str = "Wi-Fi (0.0.0.0)" if _WIFI else "USB via adb reverse (127.0.0.1)"
    log.info("[TCP] Server listening on %s:%d (waiting for Android %s)", bind_host, PORT, mode_str)

    while _running:
        try:
            conn, addr = server.accept()
            log.info("[TCP] Client connected from %s", addr)
            _handle_client(conn, fd)
        except socket.timeout:
            continue
        except Exception as e:
            if _running:
                log.error("[TCP] Server error: %s", e)
            break

    server.close()'''

new_server = '''    if _WIFI:
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bind_host = "0.0.0.0"
        PORT_UDP = 7113
        for attempt in range(8):
            try:
                server.bind((bind_host, PORT_UDP))
                break
            except OSError:
                log.warning("[UDP] Port %d busy (attempt %d/8) — retrying in 1 s…", PORT_UDP, attempt + 1)
                time.sleep(1)
        else:
            log.error("[UDP] Could not bind port %d — touch disabled.", PORT_UDP)
            return

        server.settimeout(1.0)
        log.info("[UDP] Server listening on %s:%d (waiting for Android UDP Touch)", bind_host, PORT_UDP)

        while _running:
            try:
                data, addr = server.recvfrom(64)
                if len(data) == 18:
                    pkt_type = data[4]
                    if pkt_type in (PKT_TOUCH, PKT_PEN):
                        payload = data[5:]
                        unpacked = struct.unpack(PAYLOAD_FMT, payload)
                        action, tool, cid, nx, ny, pressure, tx, ty = unpacked
                        _inject_fn(action, cid, nx, ny)
            except socket.timeout:
                continue
            except Exception as e:
                if _running:
                    log.error("[UDP] Server error: %s", e)
                break
        server.close()
    else:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass

        bind_host = "127.0.0.1"
        for attempt in range(8):
            try:
                server.bind((bind_host, PORT))
                break
            except OSError:
                log.warning("[TCP] Port %d busy (attempt %d/8) — retrying in 1 s…", PORT, attempt + 1)
                time.sleep(1)
        else:
            log.error("[TCP] Could not bind port %d — touch disabled.", PORT)
            return

        server.listen(2)
        server.settimeout(1.0)
        log.info("[TCP] Server listening on %s:%d (waiting for Android USB via adb reverse (127.0.0.1))", bind_host, PORT)

        while _running:
            try:
                conn, addr = server.accept()
                log.info("[TCP] Client connected from %s", addr)
                _handle_client(conn, fd)
            except socket.timeout:
                continue
            except Exception as e:
                if _running:
                    log.error("[TCP] Server error: %s", e)
                break

        server.close()'''

content = content.replace(old_server, new_server)

with open("/home/vinnavan/user/MegaProjects/Monitorize/linux/touch_daemon.py", "w") as f:
    f.write(content)
print("touch_daemon.py patched for UDP.")
