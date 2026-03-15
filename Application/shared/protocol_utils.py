"""
PROTOCOL UTILITIES - Shared Between Server and Proxy
═════════════════════════════════════════════════════

Provides low-level protocol operations that are independent of
transport logic. Both server and proxy use these utilities.

Includes:
- Packet encoding/decoding (RUDP format)
- Sequence number arithmetic (32-bit wraparound handling)
- Protocol constants
- Validation utilities
"""

from typing import Tuple

# ══════════════════════════════════════════════════════════════════════════════
# PROTOCOL CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

# UDP/IP constraints
MAX_UDP_PAYLOAD = 65507  # 65535 - 28 bytes (IP + UDP headers)
RECOMMENDED_CHUNK_SIZE = 1400  # Conservative size for avoiding fragmentation

# RUDP packet format: <4-byte seq><2-byte length><payload>
PACKET_OVERHEAD = 6  # seq(4) + len(2)
MAX_PAYLOAD_SIZE = MAX_UDP_PAYLOAD - PACKET_OVERHEAD  # 65501 bytes

# Receiver buffer size for flow control
DEFAULT_RECV_BUFFER_SIZE = 1024 * 1024  # 1MB

# Timeout values
DEFAULT_SOCKET_TIMEOUT = 0.3  # seconds
DEFAULT_META_WAIT_SECS = 2.0  # seconds


# ══════════════════════════════════════════════════════════════════════════════
# PACKET ENCODING & DECODING
# ══════════════════════════════════════════════════════════════════════════════

def encode_data_packet(seq: int, payload: bytes) -> bytes:
    """
    Encode RUDP data packet with format: <4-byte seq><2-byte length><payload>
    
    This format allows the receiver to know the exact payload length even if
    the UDP packet was padded, which is critical for reliable reassembly.
    
    Args:
        seq: Sequence number (32-bit unsigned, will wrap at 2^32)
        payload: Data chunk to encode
        
    Returns:
        Complete encoded packet ready for transmission
        
    Raises:
        ValueError: If payload is empty or exceeds size limits
        
    Example:
        >>> packet = encode_data_packet(42, b"Hello World")
        >>> len(packet)
        17  # 4 (seq) + 2 (len) + 11 (payload)
    """
    if len(payload) == 0:
        raise ValueError("Payload cannot be empty")
    
    if len(payload) > MAX_PAYLOAD_SIZE:
        raise ValueError(
            f"Payload too large: {len(payload)} bytes "
            f"(max {MAX_PAYLOAD_SIZE} bytes)"
        )
    
    # Ensure seq is 32-bit
    seq = seq & 0xFFFFFFFF
    
    # Format: seq(4 bytes) + length(2 bytes) + payload
    packet = (
        seq.to_bytes(4, "big") +
        len(payload).to_bytes(2, "big") +
        payload
    )
    
    # Validate total size
    if len(packet) > MAX_UDP_PAYLOAD:
        raise ValueError(
            f"Encoded packet exceeds UDP limit: {len(packet)} > {MAX_UDP_PAYLOAD}"
        )
    
    return packet


def decode_data_packet(packet: bytes) -> Tuple[int, bytes]:
    """
    Decode RUDP data packet.
    
    Args:
        packet: Raw packet bytes received from network
        
    Returns:
        Tuple of (sequence_number, payload)
        
    Raises:
        ValueError: If packet is malformed or has mismatched lengths
        
    Example:
        >>> seq, data = decode_data_packet(packet)
        >>> print(f"Seq {seq}: {len(data)} bytes")
        Seq 42: 11 bytes
    """
    if len(packet) < PACKET_OVERHEAD:
        raise ValueError(
            f"Packet too short: {len(packet)} bytes "
            f"(minimum {PACKET_OVERHEAD} bytes required)"
        )
    
    # Extract sequence number (4 bytes)
    seq = int.from_bytes(packet[:4], "big")
    
    # Extract declared payload length (2 bytes)
    declared_len = int.from_bytes(packet[4:6], "big")
    
    # Extract payload
    payload = packet[6:6 + declared_len]
    
    # Validate payload length matches declaration
    if len(payload) != declared_len:
        raise ValueError(
            f"Payload length mismatch: declared {declared_len}, "
            f"got {len(payload)} bytes"
        )
    
    return seq, payload


# ══════════════════════════════════════════════════════════════════════════════
# SEQUENCE NUMBER ARITHMETIC (32-BIT WITH WRAPAROUND)
# ══════════════════════════════════════════════════════════════════════════════

def seq_less_than(a: int, b: int) -> bool:
    """
    Compare sequence numbers with wraparound handling (RFC 1323).
    
    Treats sequence space as circular, so after 2^32-1 comes 0.
    Uses modular arithmetic to determine ordering.
    
    Args:
        a: First sequence number
        b: Second sequence number
        
    Returns:
        True if a < b in circular sequence space
        
    Example:
        >>> seq_less_than(100, 200)
        True
        >>> seq_less_than(0xFFFFFFFE, 5)  # Wraparound case
        True
        >>> seq_less_than(200, 100)
        False
    """
    # Use signed arithmetic in 32-bit space
    # If (a - b) has sign bit set, then a < b in circular space
    return ((a - b) & 0x80000000) != 0


def seq_less_equal(a: int, b: int) -> bool:
    """
    Check if a <= b with wraparound handling.
    
    Args:
        a: First sequence number
        b: Second sequence number
        
    Returns:
        True if a <= b in circular sequence space
    """
    return a == b or seq_less_than(a, b)


def seq_greater_than(a: int, b: int) -> bool:
    """
    Check if a > b with wraparound handling.
    
    Args:
        a: First sequence number
        b: Second sequence number
        
    Returns:
        True if a > b in circular sequence space
    """
    return seq_less_than(b, a)


def seq_greater_equal(a: int, b: int) -> bool:
    """
    Check if a >= b with wraparound handling.
    
    Args:
        a: First sequence number
        b: Second sequence number
        
    Returns:
        True if a >= b in circular sequence space
    """
    return a == b or seq_greater_than(a, b)


def seq_in_range(seq: int, start: int, end: int) -> bool:
    """
    Check if sequence number is in range [start, end] with wraparound.
    
    Args:
        seq: Sequence number to check
        start: Start of range (inclusive)
        end: End of range (inclusive)
        
    Returns:
        True if start <= seq <= end in circular space
        
    Example:
        >>> seq_in_range(100, 50, 150)
        True
        >>> seq_in_range(10, 0xFFFFFFF0, 20)  # Wraparound range
        True
    """
    return seq_greater_equal(seq, start) and seq_less_equal(seq, end)


def seq_distance(a: int, b: int) -> int:
    """
    Calculate distance from a to b in sequence space.
    
    Args:
        a: Start sequence
        b: End sequence
        
    Returns:
        Number of sequence numbers from a to b (can be negative)
        
    Example:
        >>> seq_distance(100, 105)
        5
        >>> seq_distance(0xFFFFFFFE, 2)  # Wraparound
        4
    """
    return (b - a) & 0xFFFFFFFF


# ══════════════════════════════════════════════════════════════════════════════
# CONTROL MESSAGE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def encode_control_message(msg_type: str, *args) -> bytes:
    """
    Encode a control message (META, ACK, FIN, etc).
    
    Format: MSG_TYPE|arg1|arg2|...
    
    Args:
        msg_type: Message type string (e.g., "ACK", "META", "FIN")
        *args: Additional arguments to encode
        
    Returns:
        Encoded message bytes
        
    Example:
        >>> encode_control_message("ACK", 42, 1024000)
        b'ACK|42|1024000'
    """
    parts = [msg_type] + [str(arg) for arg in args]
    return "|".join(parts).encode()


def decode_control_message(data: bytes) -> Tuple[str, list]:
    """
    Decode a control message.
    
    Args:
        data: Raw message bytes
        
    Returns:
        Tuple of (msg_type, [arg1, arg2, ...])
        
    Raises:
        ValueError: If message cannot be decoded
        
    Example:
        >>> msg_type, args = decode_control_message(b'ACK|42|1024000')
        >>> msg_type
        'ACK'
        >>> args
        ['42', '1024000']
    """
    try:
        decoded = data.decode(errors="ignore").strip()
        parts = decoded.split("|")
        if not parts:
            raise ValueError("Empty message")
        return parts[0], parts[1:]
    except Exception as e:
        raise ValueError(f"Failed to decode control message: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def validate_packet_size(packet: bytes) -> bool:
    """
    Check if packet size is within UDP limits.
    
    Args:
        packet: Packet bytes to validate
        
    Returns:
        True if valid, False otherwise
    """
    return 0 < len(packet) <= MAX_UDP_PAYLOAD


def validate_payload_size(payload: bytes) -> bool:
    """
    Check if payload size is within limits for encoding.
    
    Args:
        payload: Payload bytes to validate
        
    Returns:
        True if valid, False otherwise
    """
    return 0 < len(payload) <= MAX_PAYLOAD_SIZE


def calculate_packets_needed(file_size: int, chunk_size: int = RECOMMENDED_CHUNK_SIZE) -> int:
    """
    Calculate number of packets needed to transfer a file.
    
    Args:
        file_size: Size of file in bytes
        chunk_size: Chunk size per packet
        
    Returns:
        Number of packets required
        
    Example:
        >>> calculate_packets_needed(10000, 1400)
        8  # ceil(10000 / 1400)
    """
    if chunk_size <= 0:
        raise ValueError("Chunk size must be positive")
    return (file_size + chunk_size - 1) // chunk_size


# ══════════════════════════════════════════════════════════════════════════════
# TESTING & UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def format_packet_info(packet: bytes) -> str:
    """
    Format packet information for debugging/logging.
    
    Args:
        packet: Packet to format
        
    Returns:
        Human-readable string describing packet
        
    Example:
        >>> info = format_packet_info(packet)
        >>> print(info)
        Packet: seq=42, len=11, total=17 bytes
    """
    try:
        if len(packet) >= PACKET_OVERHEAD:
            seq, payload = decode_data_packet(packet)
            return f"Packet: seq={seq}, len={len(payload)}, total={len(packet)} bytes"
        else:
            return f"Control/Invalid: {len(packet)} bytes"
    except Exception as e:
        return f"Malformed packet: {e}"


if __name__ == "__main__":
    # Self-test
    print("Running protocol_utils self-tests...")
    
    # Test packet encoding/decoding
    test_payload = b"Hello, RUDP World!"
    test_seq = 12345
    
    encoded = encode_data_packet(test_seq, test_payload)
    decoded_seq, decoded_payload = decode_data_packet(encoded)
    
    assert decoded_seq == test_seq, "Sequence number mismatch"
    assert decoded_payload == test_payload, "Payload mismatch"
    print(f"✓ Packet encode/decode: seq={decoded_seq}, payload={len(decoded_payload)} bytes")
    
    # Test sequence arithmetic
    assert seq_less_than(100, 200) == True
    assert seq_less_than(200, 100) == False
    assert seq_less_than(0xFFFFFFFE, 5) == True  # Wraparound
    print("✓ Sequence arithmetic with wraparound")
    
    # Test control messages
    ack_msg = encode_control_message("ACK", 42, 1024000)
    msg_type, args = decode_control_message(ack_msg)
    assert msg_type == "ACK"
    assert args == ["42", "1024000"]
    print("✓ Control message encoding/decoding")
    
    # Test validation
    assert validate_payload_size(b"x" * 1400) == True
    assert validate_payload_size(b"x" * 100000) == False
    print("✓ Payload size validation")
    
    print("\n✅ All protocol_utils tests passed!")
