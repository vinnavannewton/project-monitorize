"""TCP and UDP input transports."""

import logging
import socket
import threading
import time

from .protocol import parse_udp_packets, pop_framed_packets

log = logging.getLogger("TouchDaemon")


class ActiveTcpClient:
    def __init__(self):
        self.lock = threading.Lock()
        self.client = None

    def replace(self, client, dispatcher):
        with self.lock:
            old = self.client
            if old is not None and old is not client:
                dispatcher.release_all("tcp reconnect")
                try:
                    old.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    old.close()
                except OSError:
                    pass
            self.client = client

    def clear(self, client, dispatcher, reason):
        with self.lock:
            if self.client is client:
                self.client = None
                dispatcher.release_all(reason)


def handle_client(client, addr, dispatcher, shutdown, active_client=None):
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
        try:
            client.close()
        except OSError:
            pass
        if active_client is not None:
            active_client.clear(client, dispatcher, "tcp disconnect")
        else:
            dispatcher.release_all("tcp disconnect")


def run_tcp_server(dispatcher, shutdown, port=7111):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    for attempt in range(8):
        try:
            server.bind(("127.0.0.1", port))
            break
        except OSError:
            log.warning("TCP port %d busy (%d/8)", port, attempt + 1)
            time.sleep(1)
    else:
        log.error("Could not bind TCP port %d", port)
        server.close()
        return
    server.listen(1)
    server.settimeout(1)
    active_client = ActiveTcpClient()
    while not shutdown.is_set():
        try:
            conn, addr = server.accept()
            active_client.replace(conn, dispatcher)
            threading.Thread(
                target=handle_client,
                args=(conn, addr, dispatcher, shutdown, active_client),
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
    idle_released = False
    while not shutdown.is_set():
        try:
            data, _addr = server.recvfrom(64)
            now = time.monotonic()
            if now - last_packet > 3:
                geometry.invalidate()
                idle_released = False
            last_packet = now
            for pkt_type, payload in parse_udp_packets(data):
                dispatcher.dispatch_packet(pkt_type, payload)
        except socket.timeout:
            if last_packet and not idle_released and time.monotonic() - last_packet > 3:
                dispatcher.release_all("udp idle")
                idle_released = True
        except Exception as exc:
            if not shutdown.is_set():
                log.error("UDP receive error: %s", exc)
    server.close()
