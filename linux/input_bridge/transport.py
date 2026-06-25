"""TCP and UDP input transports."""

import logging
import socket
import threading
import time
from collections import OrderedDict

from .protocol import ACTION_HOVER, ACTION_MOVE, parse_udp_packets, pop_framed_packets, unpack_packet

log = logging.getLogger("TouchDaemon")

UDP_RCVBUF = 16 * 1024
UDP_DRAIN_CAP = 32


class TransportStats:
    def __init__(self):
        self.received = 0
        self.coalesced = 0
        self.released = 0
        self.injected = 0
        self.inject_total_ms = 0.0
        self.inject_max_ms = 0.0

    def record_inject(self, received_at):
        if received_at is None or not log.isEnabledFor(logging.DEBUG):
            return
        elapsed_ms = (time.perf_counter() - received_at) * 1000.0
        self.injected += 1
        self.inject_total_ms += elapsed_ms
        self.inject_max_ms = max(self.inject_max_ms, elapsed_ms)

    def debug_log(self, prefix):
        if log.isEnabledFor(logging.DEBUG):
            avg_ms = self.inject_total_ms / self.injected if self.injected else 0.0
            log.debug(
                "%s received=%d coalesced=%d released=%d injected=%d "
                "recv_to_inject_avg_ms=%.3f recv_to_inject_max_ms=%.3f",
                prefix, self.received, self.coalesced, self.released,
                self.injected, avg_ms, self.inject_max_ms,
            )


def coalesce_motion_packets(packets, stats=None):
    output = []
    pending = OrderedDict()

    def flush_pending():
        output.extend(pending.values())
        pending.clear()

    for packet in packets:
        pkt_type, payload = packet
        kind, values = unpack_packet(pkt_type, payload)
        if values and values[0] in (ACTION_MOVE, ACTION_HOVER):
            _action, tool, cid = values[:3]
            key = (kind, tool, cid)
            if key in pending and stats is not None:
                stats.coalesced += 1
            pending[key] = packet
            pending.move_to_end(key)
            continue
        flush_pending()
        output.append(packet)

    flush_pending()
    return output


def dispatch_packet_batch(dispatcher, packets, stats=None, received_at=None):
    for index, (pkt_type, payload) in enumerate(packets):
        dispatcher.dispatch_packet(pkt_type, payload, index == len(packets) - 1)
        if stats is not None:
            stats.record_inject(received_at)


def append_udp_batch(batches, addr, packets):
    if not packets:
        return
    if batches and batches[-1][0] == addr:
        batches[-1][1].extend(packets)
    else:
        batches.append((addr, list(packets)))


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
    client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RCVBUF)
    buffer = bytearray()
    stats = TransportStats()
    try:
        while not shutdown.is_set():
            chunk = client.recv(4096)
            if not chunk:
                break
            received_at = time.perf_counter()
            buffer.extend(chunk)
            raw_packets = pop_framed_packets(buffer)
            stats.received += len(raw_packets)
            packets = coalesce_motion_packets(raw_packets, stats)
            dispatch_packet_batch(dispatcher, packets, stats, received_at)
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
        stats.released += 1
        stats.debug_log("tcp")


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


def run_udp_server(dispatcher, shutdown, geometry, host="0.0.0.0", port=7113):
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_RCVBUF)
    try:
        server.bind((host, port))
    except OSError as exc:
        log.error("Could not bind UDP %s:%d: %s", host, port, exc)
        return
    server.settimeout(1)
    last_packet = 0.0
    idle_released = False
    stats = TransportStats()
    while not shutdown.is_set():
        try:
            data, _addr = server.recvfrom(64)
            received_at = time.perf_counter()
            now = time.monotonic()
            if now - last_packet > 3:
                geometry.invalidate()
                idle_released = False
            last_packet = now
            batches = []
            append_udp_batch(batches, _addr, parse_udp_packets(data))
            server.setblocking(False)
            try:
                for _ in range(UDP_DRAIN_CAP):
                    extra, _addr = server.recvfrom(64)
                    append_udp_batch(batches, _addr, parse_udp_packets(extra))
            except BlockingIOError:
                pass
            finally:
                server.setblocking(True)
                server.settimeout(1)
            for _addr, packets in batches:
                stats.received += len(packets)
                coalesced = coalesce_motion_packets(packets, stats)
                dispatch_packet_batch(dispatcher, coalesced, stats, received_at)
        except socket.timeout:
            if last_packet and not idle_released and time.monotonic() - last_packet > 3:
                dispatcher.release_all("udp idle")
                stats.released += 1
                stats.debug_log("udp")
                idle_released = True
        except Exception as exc:
            if not shutdown.is_set():
                log.error("UDP receive error: %s", exc)
    stats.debug_log("udp")
    server.close()
