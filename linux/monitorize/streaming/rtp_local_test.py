"""Local RTP decode test — acts as an Android-like client on the desktop.

Usage while a stream is active:
    python -m monitorize.streaming.rtp_local_test [host] [control_port]

Defaults: host=127.0.0.1  control_port=7110
Writes received H.264 to /tmp/monitorize_rtp_test.h264 and reports stats.
"""

import json
import socket
import struct
import sys
import time


MZRP1_PREFIX = b"MZRP1 "


def rtp_parse(data, size):
    """Minimal RTP parser — returns (seq, ts, marker, pt, payload) or None."""
    if size < 12 or (data[0] >> 6) != 2:
        return None
    csrc_count = data[0] & 0x0F
    offset = 12 + csrc_count * 4
    if offset > size:
        return None
    if data[0] & 0x10:  
        if offset + 4 > size:
            return None
        words = (data[offset + 2] << 8) | data[offset + 3]
        offset += 4 + words * 4
    if offset >= size:
        return None
    seq = (data[2] << 8) | data[3]
    ts = struct.unpack(">I", data[4:8])[0]
    marker = bool(data[1] & 0x80)
    pt = data[1] & 0x7F
    return seq, ts, marker, pt, data[offset:size]


START_CODE = b"\x00\x00\x00\x01"


def reassemble_nal(payload):
    """Reassemble one RTP payload into H.264 NAL units (Annex B)."""
    if not payload:
        return b""
    nal_type = payload[0] & 0x1F
    if nal_type in range(1, 24):
        return START_CODE + payload
    elif nal_type == 24:  
        result = b""
        off = 1
        while off + 2 <= len(payload):
            sz = (payload[off] << 8) | payload[off + 1]
            off += 2
            if sz <= 0 or off + sz > len(payload):
                break
            result += START_CODE + payload[off:off + sz]
            off += sz
        return result
    elif nal_type == 28 and len(payload) >= 3:  
        header = payload[1]
        if header & 0x80:  
            reconstructed = bytes([(payload[0] & 0xE0) | (header & 0x1F)])
            return START_CODE + reconstructed + payload[2:]
        else:
            return payload[2:]
    return b""


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    control_port = int(sys.argv[2]) if len(sys.argv) > 2 else 7110

    
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 512 * 1024)
    udp.bind(("0.0.0.0", 0))
    local_port = udp.getsockname()[1]
    print(f"[Test] Local UDP port: {local_port}")

    
    hello = json.dumps({
        "transport": "rtp-udp-v1",
        "port": local_port,
        "fps": 60,
        "width": 2560,
        "height": 1600,
        "decoderProfiles": ["high", "constrained-baseline"],
    }, separators=(",", ":"))
    hello_bytes = f"MZRP1 {hello}\n".encode()

    print(f"[Test] Connecting TCP to {host}:{control_port}...")
    try:
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.settimeout(3)
        tcp.connect((host, control_port))
        tcp.sendall(hello_bytes)
        response = b""
        while b"\n" not in response and len(response) < 4096:
            chunk = tcp.recv(4096)
            if not chunk:
                break
            response += chunk
        tcp.close()
    except Exception as e:
        print(f"[Test] TCP handshake FAILED: {e}")
        udp.close()
        return

    line = response.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    print(f"[Test] Server reply: {line}")
    if "ready" not in line:
        print("[Test] FAIL: Server did not reply ready")
        udp.close()
        return

    
    print("[Test] Receiving RTP packets for 5 seconds...")
    udp.settimeout(0.1)
    output_path = "/tmp/monitorize_rtp_test.h264"
    out = open(output_path, "wb")

    start = time.monotonic()
    total_packets = 0
    total_bytes = 0
    frames = 0
    idr_count = 0
    current_ts = None
    frame_data = b""
    first_packet_time = None
    last_seq = -1
    lost = 0
    pt96_count = 0
    pt122_count = 0
    other_pt = 0

    buf = bytearray(2048)
    while time.monotonic() - start < 5.0:
        try:
            nbytes, addr = udp.recvfrom_into(buf)
        except socket.timeout:
            continue

        if first_packet_time is None:
            first_packet_time = time.monotonic()
            print(f"[Test] First packet from {addr}")

        parsed = rtp_parse(buf, nbytes)
        if parsed is None:
            continue

        seq, ts, marker, pt, payload = parsed
        total_packets += 1
        total_bytes += nbytes

        if pt == 96:
            pt96_count += 1
        elif pt == 122:
            pt122_count += 1
            continue  
        else:
            other_pt += 1
            continue

        
        if last_seq >= 0:
            gap = (seq - last_seq - 1) & 0xFFFF
            if 0 < gap < 1024:
                lost += gap
        last_seq = seq

        
        nal_data = reassemble_nal(bytes(payload))
        if ts != current_ts and current_ts is not None:
            
            if frame_data:
                out.write(frame_data)
                frames += 1
                
                if b"\x00\x00\x00\x01\x65" in frame_data or b"\x00\x00\x01\x65" in frame_data:
                    idr_count += 1
            frame_data = b""
        current_ts = ts
        frame_data += nal_data

        if marker and frame_data:
            out.write(frame_data)
            frames += 1
            if b"\x00\x00\x00\x01\x65" in frame_data or b"\x00\x00\x01\x65" in frame_data:
                idr_count += 1
            frame_data = b""

    
    if frame_data:
        out.write(frame_data)
        frames += 1

    out.close()
    udp.close()

    elapsed = time.monotonic() - start
    print(f"\n{'='*50}")
    print(f"[Test] RESULTS after {elapsed:.1f}s:")
    print(f"  Total RTP packets:  {total_packets}")
    print(f"  PT 96 (video):      {pt96_count}")
    print(f"  PT 122 (FEC):       {pt122_count}")
    print(f"  Other PT:           {other_pt}")
    print(f"  Total bytes:        {total_bytes:,}")
    print(f"  Packets lost:       {lost}")
    print(f"  Frames assembled:   {frames}")
    print(f"  IDR frames:         {idr_count}")
    if elapsed > 0:
        print(f"  Throughput:         {total_bytes * 8 / elapsed / 1_000_000:.2f} Mbps")
        print(f"  Packet rate:        {total_packets / elapsed:.0f} pkt/s")
        print(f"  Frame rate:         {frames / elapsed:.1f} fps")
    print(f"  Output file:        {output_path}")
    file_size = 0
    try:
        import os
        file_size = os.path.getsize(output_path)
    except Exception:
        pass
    print(f"  File size:          {file_size:,} bytes")
    if file_size > 0:
        print(f"\n  Verify with: ffplay {output_path}")
    elif total_packets == 0:
        print("\n  ⚠️  ZERO packets received — pipeline may not be sending to this client")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
