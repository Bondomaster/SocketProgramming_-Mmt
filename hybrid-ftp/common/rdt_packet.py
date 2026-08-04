import struct
import zlib

HEADER_FORMAT = "!IIHHI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT) 

FLAG_ACK = 1 << 0
FLAG_SYN = 1 << 1
FLAG_FIN = 1 << 2
FLAG_NAK = 1 << 3

def pack_packet(seq_num: int, ack_num: int, flags: int, payload: bytes) -> bytes:
    header_no_checksum = struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, len(payload), 0)
    checksum = zlib.crc32(header_no_checksum + payload) & 0xFFFFFFFF
    header = struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, len(payload), checksum)
    return header + payload

def unpack_packet(data: bytes) -> tuple[int, int, int, bytes, bool]:
    """Returns (seq_num, ack_num, flags, payload, checksum_ok)."""
    seq_num, ack_num, flags, length, checksum = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])
    payload = data[HEADER_SIZE:HEADER_SIZE + length]
    header_no_checksum = struct.pack(HEADER_FORMAT, seq_num, ack_num, flags, length, 0)
    ok = (zlib.crc32(header_no_checksum + payload) & 0xFFFFFFFF) == checksum
    return seq_num, ack_num, flags, payload, ok