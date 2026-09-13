"""Generate validated base-block EDIDs for Monitorize custom VKMS modes.

The timing calculation is a small Python port of the standard CVT calculation
used by libxcvt 0.1.3 (MIT).  It intentionally has no runtime dependency on
the optional ``cvt`` executable.
"""

from __future__ import annotations

from dataclasses import dataclass


class EdidError(ValueError):
    """A requested mode cannot be represented safely in a base-block EDID."""


@dataclass(frozen=True)
class Timing:
    width: int
    height: int
    refresh_hz: float
    pixel_clock_khz: int
    h_total: int
    v_total: int
    h_sync_start: int
    h_sync_end: int
    v_sync_start: int
    v_sync_end: int


EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"


def _descriptor(tag: int, payload: bytes) -> bytes:
    return bytes((0, 0, 0, tag, 0)) + payload[:13].ljust(13, b" ")


def generate_cvt_timing(width: int, height: int, refresh_hz: float) -> Timing:
    """Return a non-interlaced standard-CVT timing for an exact active size.

    The calculations are derived from libxcvt's standard (non-reduced
    blanking) path.  Unlike Xorg modeline generation, the requested EDID active
    width is retained exactly: EDID and the VKMS DRM path can represent widths
    that are not divisible by CVT's historical eight-pixel character cell.
    """
    try:
        width, height = int(width), int(height)
        refresh = float(refresh_hz)
    except (TypeError, ValueError) as exc:
        raise EdidError("Custom VKMS dimensions and refresh must be numeric.") from exc
    if not 1 <= width <= 4095 or not 1 <= height <= 4095:
        raise EdidError("Custom VKMS dimensions must fit the EDID detailed-timing fields.")
    if not 24.0 <= refresh <= 240.0:
        raise EdidError("Custom VKMS refresh must be between 24 and 240 Hz.")

    
    min_vsync_back_porch_us = 550.0
    min_v_porch = 3
    min_v_back_porch = 6
    h_granularity = 8
    hsync_percentage = 8
    m_prime = 600 * 128 / 256
    c_prime = (40 - 20) * 128 / 256 + 20

    hperiod_us = (1_000_000.0 / refresh - min_vsync_back_porch_us) / (
        height + min_v_porch
    )
    if hperiod_us <= 0:
        raise EdidError("Custom VKMS refresh cannot produce a valid CVT timing.")
    vsync = _vertical_sync_width(width, height)
    sync_back_porch = max(
        int(min_vsync_back_porch_us / hperiod_us) + 1,
        vsync + min_v_back_porch,
    )
    v_total = height + min_v_porch + sync_back_porch
    hblank_percent = max(20.0, c_prime - m_prime * hperiod_us / 1000.0)
    h_blank = int(width * hblank_percent / (100.0 - hblank_percent))
    h_blank -= h_blank % (2 * h_granularity)
    h_total = width + h_blank
    h_sync_end = width + h_blank // 2
    h_sync_width = int(h_total * hsync_percentage / 100)
    h_sync_width -= h_sync_width % h_granularity
    h_sync_start = h_sync_end - h_sync_width
    pixel_clock_khz = int(h_total * 1000.0 / hperiod_us)
    pixel_clock_khz -= pixel_clock_khz % 250

    timing = Timing(
        width=width,
        height=height,
        refresh_hz=1000.0 * pixel_clock_khz / (h_total * v_total),
        pixel_clock_khz=pixel_clock_khz,
        h_total=h_total,
        v_total=v_total,
        h_sync_start=h_sync_start,
        h_sync_end=h_sync_end,
        v_sync_start=height + min_v_porch,
        v_sync_end=height + min_v_porch + vsync,
    )
    _validate_timing(timing)
    return timing


def _vertical_sync_width(width: int, height: int) -> int:
    if height % 3 == 0 and height * 4 // 3 == width:
        return 4
    if height % 9 == 0 and height * 16 // 9 == width:
        return 5
    if height % 10 == 0 and height * 16 // 10 == width:
        return 6
    if height % 4 == 0 and height * 5 // 4 == width:
        return 7
    if height % 9 == 0 and height * 15 // 9 == width:
        return 7
    return 10


def _validate_timing(timing: Timing) -> None:
    h_blank = timing.h_total - timing.width
    v_blank = timing.v_total - timing.height
    h_sync_offset = timing.h_sync_start - timing.width
    h_sync_width = timing.h_sync_end - timing.h_sync_start
    v_sync_offset = timing.v_sync_start - timing.height
    v_sync_width = timing.v_sync_end - timing.v_sync_start
    if not (1 <= timing.pixel_clock_khz // 10 <= 0xFFFF):
        raise EdidError("Custom VKMS pixel clock does not fit an EDID detailed timing.")
    if not all(0 <= value <= 0xFFF for value in (timing.width, h_blank, timing.height, v_blank)):
        raise EdidError("Custom VKMS timing totals do not fit an EDID detailed timing.")
    if not (0 <= h_sync_offset <= 0x3FF and 1 <= h_sync_width <= 0x3FF):
        raise EdidError("Custom VKMS horizontal sync does not fit an EDID detailed timing.")
    if not (0 <= v_sync_offset <= 0x3F and 1 <= v_sync_width <= 0x3F):
        raise EdidError("Custom VKMS vertical sync does not fit an EDID detailed timing.")


def generate_edid(width: int, height: int, refresh_hz: float) -> bytes:
    """Generate and validate one preferred detailed timing EDID base block."""
    timing = generate_cvt_timing(width, height, refresh_hz)
    h_blank = timing.h_total - timing.width
    v_blank = timing.v_total - timing.height
    h_sync_offset = timing.h_sync_start - timing.width
    h_sync_width = timing.h_sync_end - timing.h_sync_start
    v_sync_offset = timing.v_sync_start - timing.height
    v_sync_width = timing.v_sync_end - timing.v_sync_start
    width_mm = min(0xFFF, max(1, round(timing.width * 25.4 / 96)))
    height_mm = min(0xFFF, max(1, round(timing.height * 25.4 / 96)))

    dtd = bytearray(18)
    dtd[0:2] = (timing.pixel_clock_khz // 10).to_bytes(2, "little")
    dtd[2], dtd[3] = timing.width & 0xFF, h_blank & 0xFF
    dtd[4] = ((timing.width >> 8) << 4) | (h_blank >> 8)
    dtd[5], dtd[6] = timing.height & 0xFF, v_blank & 0xFF
    dtd[7] = ((timing.height >> 8) << 4) | (v_blank >> 8)
    dtd[8], dtd[9] = h_sync_offset & 0xFF, h_sync_width & 0xFF
    dtd[10] = ((v_sync_offset & 0x0F) << 4) | (v_sync_width & 0x0F)
    dtd[11] = ((h_sync_offset >> 8) << 6) | ((h_sync_width >> 8) << 4) | (
        (v_sync_offset >> 4) << 2
    ) | (v_sync_width >> 4)
    dtd[12], dtd[13] = width_mm & 0xFF, height_mm & 0xFF
    dtd[14] = ((width_mm >> 8) << 4) | (height_mm >> 8)
    dtd[17] = 0x1A  

    edid = bytearray(128)
    edid[:8] = EDID_HEADER
    edid[8:10] = (0x35EE).to_bytes(2, "big")  
    edid[10:12] = (0x7502).to_bytes(2, "little")
    edid[12:16] = (1).to_bytes(4, "little")
    edid[16:18] = bytes((1, 36))
    edid[18:25] = bytes((1, 4, 0x80, min(255, width_mm // 10), min(255, height_mm // 10), 120, 0x0A))
    for offset in range(38, 54, 2):
        edid[offset:offset + 2] = b"\x01\x01"
    edid[54:72] = dtd
    edid[72:90] = _descriptor(0xFC, b"MONITORIZE\n")
    edid[90:108] = _descriptor(0xFD, bytes((24, 240, 30, 255, 20, 0, 0, 0)))
    edid[108:126] = _descriptor(0x10, b"")
    edid[126] = 0
    edid[127] = (-sum(edid[:127])) & 0xFF
    validate_edid(edid)
    return bytes(edid)


def parse_preferred_timing(edid: bytes) -> Timing:
    """Decode the first DTD for tests and pre-write validation."""
    validate_edid(edid)
    dtd = edid[54:72]
    clock_khz = int.from_bytes(dtd[:2], "little") * 10
    width = dtd[2] | ((dtd[4] >> 4) << 8)
    h_blank = dtd[3] | ((dtd[4] & 0x0F) << 8)
    height = dtd[5] | ((dtd[7] >> 4) << 8)
    v_blank = dtd[6] | ((dtd[7] & 0x0F) << 8)
    h_sync_offset = dtd[8] | ((dtd[11] >> 6) << 8)
    h_sync_width = dtd[9] | (((dtd[11] >> 4) & 0x03) << 8)
    v_sync_offset = ((dtd[10] >> 4) & 0x0F) | (((dtd[11] >> 2) & 0x03) << 4)
    v_sync_width = (dtd[10] & 0x0F) | ((dtd[11] & 0x03) << 4)
    h_total, v_total = width + h_blank, height + v_blank
    return Timing(width, height, clock_khz * 1000 / (h_total * v_total), clock_khz,
                  h_total, v_total, width + h_sync_offset, width + h_sync_offset + h_sync_width,
                  height + v_sync_offset, height + v_sync_offset + v_sync_width)


def validate_edid(edid: bytes) -> None:
    if len(edid) != 128:
        raise EdidError(f"Custom VKMS EDID must be 128 bytes, got {len(edid)}.")
    if edid[:8] != EDID_HEADER:
        raise EdidError("Custom VKMS EDID has an invalid header.")
    if edid[126] != 0:
        raise EdidError("Custom VKMS EDID must not contain extension blocks.")
    if sum(edid) % 256:
        raise EdidError("Custom VKMS EDID checksum validation failed.")
