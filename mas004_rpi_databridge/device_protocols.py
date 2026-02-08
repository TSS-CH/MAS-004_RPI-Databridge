from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Iterable

# -----------------------------
# ZBC / ZIPHER binary protocol
# -----------------------------

ZBC_START = 0xA5
ZBC_END = 0xE4

ZBC_FLAG_SQS = 1 << 0
ZBC_FLAG_FIN = 1 << 1
ZBC_FLAG_ACK = 1 << 2
ZBC_FLAG_NAK = 1 << 3
ZBC_FLAG_CS = 1 << 4
ZBC_FLAG_ADR = 1 << 5
ZBC_FLAG_ASY = 1 << 6


@dataclass(frozen=True)
class ZbcPacket:
    flags: int
    size: int
    transaction_id: int
    sequence_id: int
    payload: bytes
    has_checksum: bool


def crc16_ccitt(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= (byte & 0xFF) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def zbc_header_checksum(header_without_checksum: bytes) -> int:
    return (~(sum(header_without_checksum) & 0xFF)) & 0xFF


def build_zbc_message(message_id: int, body: bytes = b"") -> bytes:
    body = body or b""
    msg_len = 6 + len(body)
    return struct.pack("<HI", message_id & 0xFFFF, msg_len) + body


def parse_zbc_message(data: bytes) -> tuple[int, bytes]:
    if len(data) < 6:
        raise ValueError("ZBC message too short")
    msg_id, msg_len = struct.unpack("<HI", data[:6])
    if msg_len < 6:
        raise ValueError("ZBC message length invalid")
    if msg_len > len(data):
        raise ValueError("ZBC message incomplete")
    return msg_id, data[6:msg_len]


def build_zbc_packet(
    flags: int,
    transaction_id: int,
    sequence_id: int,
    payload: bytes,
    force_checksum: bool | None = None,
) -> bytes:
    payload = payload or b""
    with_checksum = bool(payload) if force_checksum is None else bool(force_checksum)
    if with_checksum:
        flags |= ZBC_FLAG_CS
    else:
        flags &= ~ZBC_FLAG_CS

    payload_len = len(payload)
    total_size = 10 + payload_len + (2 if with_checksum else 0)
    header = struct.pack(
        "<BBHHHB",
        ZBC_START,
        flags & 0xFF,
        total_size & 0xFFFF,
        transaction_id & 0xFFFF,
        sequence_id & 0xFFFF,
        ZBC_END,
    )
    header_cs = zbc_header_checksum(header)

    out = bytearray(header)
    out.append(header_cs)
    out.extend(payload)
    if with_checksum:
        out.extend(struct.pack("<H", crc16_ccitt(payload)))
    return bytes(out)


def parse_zbc_packet(packet: bytes) -> ZbcPacket:
    if len(packet) < 10:
        raise ValueError("ZBC packet too short")

    start, flags, size, txn, seq, end = struct.unpack("<BBHHHB", packet[:9])
    hdr_checksum = packet[9]
    if start != ZBC_START:
        raise ValueError("ZBC start byte invalid")
    if end != ZBC_END:
        raise ValueError("ZBC end byte invalid")
    if size != len(packet):
        raise ValueError("ZBC packet size mismatch")

    expected_hdr = zbc_header_checksum(packet[:9])
    if hdr_checksum != expected_hdr:
        raise ValueError("ZBC header checksum mismatch")

    has_cs = bool(flags & ZBC_FLAG_CS)
    trailer_len = 2 if has_cs else 0
    payload = packet[10 : len(packet) - trailer_len]

    if has_cs:
        recv_crc = struct.unpack("<H", packet[-2:])[0]
        calc_crc = crc16_ccitt(payload)
        if recv_crc != calc_crc:
            raise ValueError("ZBC data checksum mismatch")

    return ZbcPacket(
        flags=flags,
        size=size,
        transaction_id=txn,
        sequence_id=seq,
        payload=payload,
        has_checksum=has_cs,
    )


def build_zbc_ack(ref_flags: int, transaction_id: int, sequence_id: int) -> bytes:
    flags = (ref_flags & (ZBC_FLAG_SQS | ZBC_FLAG_FIN | ZBC_FLAG_CS | ZBC_FLAG_ADR | ZBC_FLAG_ASY)) | ZBC_FLAG_ACK
    flags &= ~ZBC_FLAG_NAK
    return build_zbc_packet(flags=flags, transaction_id=transaction_id, sequence_id=sequence_id, payload=b"", force_checksum=False)


# -----------------------------
# Ultimate (3350) protocol
# -----------------------------

ULT_ACK = 0x06
ULT_NAK = 0x15


def build_ultimate_command(command: str, args: Iterable[str] | None = None) -> bytes:
    parts = [(command or "").strip()]
    for arg in args or ():
        parts.append(str(arg))
    line = ";".join(parts) + ";\r\n"
    return line.encode("utf-8")


def parse_ultimate_result(raw: bytes) -> tuple[bool, str, list[str]]:
    if not raw:
        raise ValueError("Ultimate response empty")

    state = raw[0]
    if state not in (ULT_ACK, ULT_NAK):
        raise ValueError("Ultimate response missing ACK/NAK prefix")

    text = raw[1:].decode("utf-8", errors="replace").strip()
    text = text.replace("\r", "").replace("\n", "")
    fields = [f for f in text.split(";") if f != ""]
    result_code = fields[0] if fields else ""
    args = fields[1:] if len(fields) > 1 else []
    return state == ULT_ACK, result_code, args
