"""Wire format parsing for Monitorize touch and stylus packets."""

import struct

COORD_MAX = 65535
PKT_TOUCH = 0x03
PKT_PEN = 0x04
PKT_PEN_EXT = 0x05

ACTION_DOWN = 0
ACTION_MOVE = 1
ACTION_UP = 2
ACTION_HOVER = 3

TOOL_FINGER = 0
TOOL_STYLUS = 1
TOOL_ERASER = 2
TOOL_MOUSE = 3

PAYLOAD_FMT = ">BBBHHHhh"
PAYLOAD_SIZE = struct.calcsize(PAYLOAD_FMT)
PEN_EXT_FMT = ">BBBHHHhhHHH"
PEN_EXT_SIZE = struct.calcsize(PEN_EXT_FMT)

PEN_FLAG_CANCELED = 1
PEN_FLAG_HOVER_EXIT = 1 << 1
ANDROID_STYLUS_PRIMARY = 0x20
ANDROID_STYLUS_SECONDARY = 0x40
DISTANCE_MAX = 1024


def pop_framed_packets(buffer: bytearray) -> list[tuple[int, bytes]]:
    packets = []
    while len(buffer) >= 5:
        payload_len = int.from_bytes(buffer[:4], "big")
        if payload_len not in (PAYLOAD_SIZE, PEN_EXT_SIZE):
            del buffer[0]
            continue
        total_len = 5 + payload_len
        if len(buffer) < total_len:
            break
        packets.append((buffer[4], bytes(buffer[5:total_len])))
        del buffer[:total_len]
    return packets


def parse_udp_packets(data: bytes) -> list[tuple[int, bytes]]:
    if len(data) >= 5:
        payload_len = int.from_bytes(data[:4], "big")
        if payload_len in (PAYLOAD_SIZE, PEN_EXT_SIZE) and len(data) >= 5 + payload_len:
            return [(data[4], data[5:5 + payload_len])]
    if len(data) >= 1 + PAYLOAD_SIZE and data[0] in (PKT_TOUCH, PKT_PEN):
        return [(data[0], data[1:1 + PAYLOAD_SIZE])]
    return []


def unpack_packet(pkt_type: int, payload: bytes):
    if pkt_type == PKT_TOUCH and len(payload) == PAYLOAD_SIZE:
        return "touch", struct.unpack(PAYLOAD_FMT, payload)
    if pkt_type == PKT_PEN and len(payload) == PAYLOAD_SIZE:
        return "pen", struct.unpack(PAYLOAD_FMT, payload)
    if pkt_type == PKT_PEN_EXT and len(payload) == PEN_EXT_SIZE:
        return "pen_ext", struct.unpack(PEN_EXT_FMT, payload)
    return None, None
