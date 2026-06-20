"""TCP and UDP input transports."""

import logging
import socket
import subprocess
import threading
import time

from .protocol import parse_udp_packets, pop_framed_packets

log = logging.getLogger("TouchDaemon")


def handle_client(client, addr, dispatcher, shutdown):
    log.info("Android connected from %s", addr)
    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    buffer = bytearray()
    try:
        while not shutdown.is_set():
            chunk = client.recv(4096)
            if not chunk:
                break
            buffer.extend(chunk)
            packets = pop_framed_packets(buffer)
            for index, (pkt_type, payload) in enumerate(packets):
                dispatcher.dispatch_packet(pkt_type, payload, index == len(packets) - 1)
    except Exception as exc:
        if not shutdown.is_set():
            log.error("Client error: %s", exc)
    finally:
        client.close()
        dispatcher.active_fingers.clear()


def run_tcp_server(dispatcher, shutdown, port=7111):
    subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)
    time.sleep(0.5)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
    for attempt in range(8):
        try:
            server.bind(("127.0.0.1", port))
            break
        except OSError:
            log.warning("TCP port %d busy (%d/8)", port, attempt + 1)
            time.sleep(1)
    else:
        log.error("Could not bind TCP port %d", port)
        return
    server.listen(2)
    server.settimeout(1)
    while not shutdown.is_set():
        try:
            conn, addr = server.accept()
            threading.Thread(
                target=handle_client,
                args=(conn, addr, dispatcher, shutdown),
                daemon=True,
            ).start()
        except socket.timeout:
            pass
        except Exception as exc:
            if not shutdown.is_set():
                log.error("TCP accept error: %s", exc)
    server.close()


def run_udp_server(dispatcher, shutdown, geometry, port=7113):
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    try:
        server.bind(("0.0.0.0", port))
    except OSError as exc:
        log.error("Could not bind UDP port %d: %s", port, exc)
        return
    server.settimeout(1)
    last_packet = 0.0
    while not shutdown.is_set():
        try:
            data, _addr = server.recvfrom(64)
            now = time.monotonic()
            if now - last_packet > 3:
                geometry.invalidate()
            last_packet = now
            for pkt_type, payload in parse_udp_packets(data):
                dispatcher.dispatch_packet(pkt_type, payload)
        except socket.timeout:
            pass
        except Exception as exc:
            if not shutdown.is_set():
                log.error("UDP receive error: %s", exc)
    server.close()
