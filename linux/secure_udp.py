"""AES-GCM packet codec for encrypted Wi-Fi input."""

import hashlib
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"MZIU"
VERSION = 1
HEADER_FMT = ">4sB4s4sQ"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
KEY_CONTEXT = b"Monitorize UDP input v1\x00"
KEY_ID_CONTEXT = b"Monitorize UDP input key id v1\x00"


class SecureUdpError(Exception):
    pass


def _normalize_token(token: str) -> bytes:
    return str(token).strip().lower().encode("ascii")


def _normalize_fingerprint(fingerprint: str) -> bytes:
    return str(fingerprint).strip().upper().encode("ascii")


def derive_key(token: str, fingerprint: str) -> bytes:
    return hashlib.sha256(
        KEY_CONTEXT + _normalize_token(token) + b"\x00" + _normalize_fingerprint(fingerprint)
    ).digest()


def key_id_for_key(key: bytes) -> bytes:
    return hashlib.sha256(KEY_ID_CONTEXT + key).digest()[:4]


def encrypt_packet(payload: bytes, token: str, fingerprint: str, nonce_prefix: bytes, counter: int) -> bytes:
    if len(nonce_prefix) != 4:
        raise ValueError("nonce_prefix must be 4 bytes")
    key = derive_key(token, fingerprint)
    key_id = key_id_for_key(key)
    header = struct.pack(HEADER_FMT, MAGIC, VERSION, key_id, nonce_prefix, counter)
    nonce = nonce_prefix + counter.to_bytes(8, "big")
    return header + AESGCM(key).encrypt(nonce, payload, header)


def decrypt_packet(packet: bytes, tokens: set[str], fingerprint: str, replay_state=None, peer=None) -> bytes:
    if len(packet) <= HEADER_SIZE:
        raise SecureUdpError("packet too short")
    try:
        magic, version, key_id, nonce_prefix, counter = struct.unpack(
            HEADER_FMT, packet[:HEADER_SIZE]
        )
    except struct.error as exc:
        raise SecureUdpError("bad header") from exc
    if magic != MAGIC or version != VERSION:
        raise SecureUdpError("bad magic or version")
    if counter <= 0:
        raise SecureUdpError("bad counter")

    key = None
    for token in tokens:
        candidate = derive_key(token, fingerprint)
        if key_id_for_key(candidate) == key_id:
            key = candidate
            break
    if key is None:
        raise SecureUdpError("unknown key")

    replay_key = (peer, key_id, nonce_prefix)
    if replay_state is not None and counter <= replay_state.get(replay_key, 0):
        raise SecureUdpError("replayed packet")

    header = packet[:HEADER_SIZE]
    nonce = nonce_prefix + counter.to_bytes(8, "big")
    try:
        payload = AESGCM(key).decrypt(nonce, packet[HEADER_SIZE:], header)
    except Exception as exc:
        raise SecureUdpError("authentication failed") from exc

    if replay_state is not None:
        replay_state[replay_key] = counter
    return payload
