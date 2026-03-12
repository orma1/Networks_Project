import os, zlib, time
from dataclasses import dataclass, field
from typing import Dict, Tuple

PACKET_OVERHEAD = 10
CHUNK_SIZE = 1400
CHUNK_READ_SIZE = 65535
RECV_BUFFER_SIZE = 1024 * 1024
INIT_CWND = 4.0
INIT_SSTHRESH = 64.0
RTO = 0.25
ACK_POLL_TIMEOUT = 0.002
KEEPALIVE_SECS = 5.0
SESSION_IDLE_TIMEOUT = 30.0

@dataclass
class WindowEntry:
    data: bytes
    timestamp: float
    acked: bool = False

@dataclass
class RUDPStreamState:
    connected: bool = False
    file_size: int = 0
    bytes_received: int = 0
    packets_received: int = 0
    start_time: float = 0.0

def encode_data_pkt(seq: int, payload: bytes) -> bytes:
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    return seq.to_bytes(4, "big") + len(payload).to_bytes(2, "big") + checksum.to_bytes(4, "big") + payload

def decode_data_pkt(packet: bytes) -> Tuple[int, bytes]:
    seq = int.from_bytes(packet[:4], "big")
    d_len = int.from_bytes(packet[4:6], "big")
    r_crc = int.from_bytes(packet[6:10], "big")
    payload = packet[10:10+d_len]
    if zlib.crc32(payload) & 0xFFFFFFFF != r_crc: raise ValueError("CRC Mismatch")
    return seq, payload