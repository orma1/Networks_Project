"""
RUDP CLIENT
═════════════════════════════════════════════════════════════

Client-side RUDP implementation for fetching streams from origin.

Responsibilities:
- Connect to RUDP server
- Handle RUDP protocol (META, ACK, data packets, FIN)
- Reassemble out-of-order packets
- Provide streaming generator interface

Does NOT handle:
- HTTP concerns (that's HTTPHandler's job)
- Quality selection (that's QualitySelector's job)
- Orchestration (that's StreamOrchestrator's job)

Implements: StreamingClient interface
"""

import socket
import time
import threading
from typing import Generator, Optional, Tuple, Dict
from collections import defaultdict
from dataclasses import dataclass, field

from protocol_utils import (
    decode_data_packet,
    decode_control_message,
    encode_control_message,
    seq_less_than,
)
from streaming_interfaces import (
    StreamingClient,
    StreamRequest,
    StreamMetadata,
    StreamMetrics,
    TransportProtocol,
    StreamState,
)


# ══════════════════════════════════════════════════════════════════════════════
# PACKET REASSEMBLER
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PacketReassembler:
    """
    Reassembles out-of-order RUDP packets.
    
    Handles:
    - Out-of-order arrival
    - Duplicate detection
    - Gap detection
    - In-order delivery
    """
    
    buffer: Dict[int, bytes] = field(default_factory=dict)
    next_expected: int = 0
    
    def add_packet(self, seq: int, payload: bytes) -> None:
        """
        Add packet to reassembly buffer.
        
        Args:
            seq: Sequence number
            payload: Packet payload
        """
        # Ignore duplicates
        if seq in self.buffer:
            return
        
        # Ignore old packets (already delivered)
        if seq_less_than(seq, self.next_expected):
            return
        
        self.buffer[seq] = payload
    
    def get_ready_packets(self) -> Generator[bytes, None, None]:
        """
        Yield packets that are ready for in-order delivery.
        
        Yields:
            Packet payloads in sequence order
        """
        while self.next_expected in self.buffer:
            payload = self.buffer.pop(self.next_expected)
            yield payload
            self.next_expected = (self.next_expected + 1) & 0xFFFFFFFF
    
    def has_gaps(self) -> bool:
        """Check if there are gaps in the sequence."""
        if not self.buffer:
            return False
        
        min_seq = min(self.buffer.keys())
        return seq_less_than(self.next_expected, min_seq)
    
    def get_buffer_size(self) -> int:
        """Get number of packets in buffer."""
        return len(self.buffer)


# ══════════════════════════════════════════════════════════════════════════════
# RUDP CLIENT
# ══════════════════════════════════════════════════════════════════════════════

class RUDPClient:
    """
    RUDP client for fetching streams from origin server.
    
    Implements StreamingClient interface.
    
    Features:
    - Connects to RUDP origin server
    - Handles RUDP protocol (META, data, ACKs, FIN)
    - Reassembles out-of-order packets
    - Provides generator interface for streaming
    - Tracks metrics
    
    Example:
        >>> client = RUDPClient()
        >>> 
        >>> # Connect
        >>> request = StreamRequest(
        ...     filename="video.mp4",
        ...     byte_start=1024,
        ...     protocol=TransportProtocol.RUDP
        ... )
        >>> metadata = client.connect(server_addr, request)
        >>> 
        >>> # Stream data
        >>> for chunk in client.stream():
        ...     process(chunk)
        >>> 
        >>> # Cleanup
        >>> client.close()
    """
    
    def __init__(
        self,
        recv_buffer_size: int = 1024 * 1024,  # 1MB
        socket_timeout: float = 0.1,
        max_buffer_packets: int = 1000,
    ):
        """
        Initialize RUDP client.
        
        Args:
            recv_buffer_size: Socket receive buffer size
            socket_timeout: Socket timeout for non-blocking receives
            max_buffer_packets: Max packets to buffer for reassembly
        """
        self.recv_buffer_size = recv_buffer_size
        self.socket_timeout = socket_timeout
        self.max_buffer_packets = max_buffer_packets
        
        # Connection state
        self.sock: Optional[socket.socket] = None
        self.server_addr: Optional[Tuple[str, int]] = None
        self.state = StreamState.IDLE
        
        # Packet reassembly
        self.reassembler = PacketReassembler()
        
        # Metrics
        self._metrics = StreamMetrics(
            bytes_transferred=0,
            packets_sent=0,
            packets_received=0,
            packets_lost=0,
            packets_retransmitted=0,
            packets_out_of_order=0,
            duplicate_packets=0,
            current_throughput_mbps=0.0,
            connection_state=StreamState.IDLE,
        )
        
        # Metadata from server
        self.metadata: Optional[StreamMetadata] = None
        
        # Receive thread
        self.active = False
        self.recv_thread: Optional[threading.Thread] = None
        self.data_ready = threading.Event()
        
    # ── StreamingClient Interface Implementation ─────────────────────────
    
    def connect(
        self,
        server_addr: Tuple[str, int],
        request: StreamRequest
    ) -> StreamMetadata:
        """
        Connect to RUDP server and request stream.
        
        Implements: StreamingClient.connect()
        
        Args:
            server_addr: Server address tuple (host, port)
            request: Stream request parameters
            
        Returns:
            Stream metadata from server
            
        Raises:
            ConnectionError: If connection fails
            TimeoutError: If META not received
            
        Example:
            >>> client = RUDPClient()
            >>> request = StreamRequest(filename="video.mp4")
            >>> metadata = client.connect(("127.0.0.1", 9000), request)
        """
        self.server_addr = server_addr
        self.state = StreamState.CONNECTING
        
        # Create socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.recv_buffer_size)
        self.sock.settimeout(self.socket_timeout)
        
        # Send REQ message
        req_msg = encode_control_message(
            "REQ",
            request.filename,
            request.byte_start,
        )
        
        self.sock.sendto(req_msg, server_addr)
        
        # Wait for META
        metadata = self._wait_for_meta(timeout=2.0)
        
        if metadata is None:
            self.state = StreamState.ERROR
            raise TimeoutError("META not received from server")
        
        self.metadata = metadata
        self.state = StreamState.CONNECTED
        
        # Start receive thread
        self.active = True
        self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.recv_thread.start()
        
        return metadata
    
    def stream(self) -> Generator[bytes, None, None]:
        """
        Stream data from server.
        
        Implements: StreamingClient.stream()
        
        Yields:
            Data chunks as they arrive
            
        Example:
            >>> for chunk in client.stream():
            ...     write_to_file(chunk)
        """
        self.state = StreamState.STREAMING
        
        start_time = time.monotonic()
        bytes_received = 0
        
        while self.active:
            # Wait for data to be ready
            self.data_ready.wait(timeout=0.1)
            self.data_ready.clear()
            
            # Get ready packets
            for payload in self.reassembler.get_ready_packets():
                yield payload
                bytes_received += len(payload)
                
                # Update throughput
                elapsed = time.monotonic() - start_time
                if elapsed > 0:
                    mbps = (bytes_received * 8) / (elapsed * 1_000_000)
                    self._metrics.current_throughput_mbps = mbps
            
            # Check if done
            if self.state == StreamState.CLOSED:
                break
    
    def get_metrics(self) -> StreamMetrics:
        """
        Get current streaming metrics.
        
        Implements: StreamingClient.get_metrics()
        
        Returns:
            Current metrics
            
        Example:
            >>> metrics = client.get_metrics()
            >>> print(f"Received: {metrics.bytes_transferred} bytes")
        """
        return self._metrics
    
    def close(self) -> None:
        """
        Close connection and cleanup.
        
        Implements: StreamingClient.close()
        
        Example:
            >>> client.close()
        """
        self.active = False
        self.state = StreamState.CLOSED
        
        if self.recv_thread:
            self.recv_thread.join(timeout=1.0)
        
        if self.sock:
            self.sock.close()
    
    # ── Internal Methods ─────────────────────────────────────────────────
    def _wait_for_meta(self, timeout: float = 2.0) -> Optional[StreamMetadata]:
        """ Wait for META message from server. """
        deadline = time.monotonic() + timeout
        
        while time.monotonic() < deadline:
            try:
                raw, _ = self.sock.recvfrom(65535)
                msg = decode_control_message(raw)
                
                if msg and msg[0] == "META":
                    # Handle the arguments whether they are returned as a nested list or flat tuple
                    args = msg[1] if isinstance(msg[1], (list, tuple)) else msg[1:]
                    
                    file_size = int(args[0])
                    remote_window = int(args[1]) if len(args) > 1 else 1048576
                    
                    return StreamMetadata(
                        file_size=file_size,
                        remote_window=remote_window,
                        content_type="video/mp4",
                        supports_range=True,
                    )
            
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[!] Error waiting for META: {e}")
                continue
        
        return None
    def _recv_loop(self) -> None:
        """Background thread for receiving packets."""
        last_seq = -1
        
        while self.active:
            try:
                raw, _ = self.sock.recvfrom(65535)
                
                # ═══ NEW: Safely attempt to decode data packet ═══
                result = None
                try:
                    result = decode_data_packet(raw)
                except ValueError:
                    # If length mismatches, it's likely a control message (META, ALIVE, etc.)
                    pass 
                
                if result:
                    seq, payload = result
                    
                    # Track metrics
                    self._metrics.packets_received += 1
                    self._metrics.bytes_transferred += len(payload)
                    
                    # Detect out-of-order
                    if last_seq != -1 and seq != (last_seq + 1) & 0xFFFFFFFF:
                        self._metrics.packets_out_of_order += 1
                    
                    last_seq = seq
                    
                    # Add to reassembler
                    prev_size = self.reassembler.get_buffer_size()
                    self.reassembler.add_packet(seq, payload)
                    curr_size = self.reassembler.get_buffer_size()
                    
                    # Detect duplicate
                    if curr_size == prev_size:
                        self._metrics.duplicate_packets += 1
                    
                    # Send ACK
                    self._send_ack(seq)
                    
                    # Signal data ready
                    self.data_ready.set()
                
                else:
                    # Try to decode as control message
                    msg = decode_control_message(raw)
                    
                    if msg and msg[0] == "FIN":
                        # Server finished sending
                        self.state = StreamState.CLOSED
                        self.active = False
                        self.data_ready.set()
            
            except socket.timeout:
                continue
            except Exception as e:
                if self.active:
                    print(f"[!] Receive error: {e}")
                continue
        
    def _recv_loop(self) -> None:
        """Background thread for receiving packets."""
        last_seq = -1
        
        while self.active:
            try:
                raw, _ = self.sock.recvfrom(65535)
                
                # Try to decode as data packet
                result = decode_data_packet(raw)
                
                if result:
                    seq, payload = result
                    
                    # Track metrics
                    self._metrics.packets_received += 1
                    self._metrics.bytes_transferred += len(payload)
                    
                    # Detect out-of-order
                    if last_seq != -1 and seq != (last_seq + 1) & 0xFFFFFFFF:
                        self._metrics.packets_out_of_order += 1
                    
                    last_seq = seq
                    
                    # Add to reassembler
                    prev_size = self.reassembler.get_buffer_size()
                    self.reassembler.add_packet(seq, payload)
                    curr_size = self.reassembler.get_buffer_size()
                    
                    # Detect duplicate
                    if curr_size == prev_size:
                        self._metrics.duplicate_packets += 1
                    
                    # Send ACK
                    self._send_ack(seq)
                    
                    # Signal data ready
                    self.data_ready.set()
                
                else:
                    # Try to decode as control message
                    msg = decode_control_message(raw)
                    
                    if msg and msg[0] == "FIN":
                        # Server finished sending
                        self.state = StreamState.CLOSED
                        self.active = False
                        self.data_ready.set()
            
            except socket.timeout:
                continue
            except Exception as e:
                if self.active:
                    print(f"[!] Receive error: {e}")
                continue
    
    def _send_ack(self, seq: int) -> None:
        """
        Send ACK for received packet.
        
        Args:
            seq: Sequence number to ACK
        """
        if self.sock and self.server_addr:
            try:
                ack_msg = encode_control_message("ACK", seq, self.recv_buffer_size)
                self.sock.sendto(ack_msg, self.server_addr)
            except Exception as e:
                print(f"[!] Error sending ACK: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Running RUDPClient self-tests...\n")
    
    # Test 1: PacketReassembler - in-order packets
    reassembler = PacketReassembler()
    reassembler.add_packet(0, b"chunk0")
    reassembler.add_packet(1, b"chunk1")
    reassembler.add_packet(2, b"chunk2")
    
    packets = list(reassembler.get_ready_packets())
    assert len(packets) == 3
    assert packets[0] == b"chunk0"
    assert packets[1] == b"chunk1"
    assert packets[2] == b"chunk2"
    print("✓ PacketReassembler: in-order packets")
    
    # Test 2: PacketReassembler - out-of-order packets
    reassembler2 = PacketReassembler()
    reassembler2.add_packet(2, b"chunk2")
    reassembler2.add_packet(0, b"chunk0")
    reassembler2.add_packet(1, b"chunk1")
    
    packets = list(reassembler2.get_ready_packets())
    assert len(packets) == 3
    assert packets[0] == b"chunk0"
    print("✓ PacketReassembler: out-of-order packets")
    
    # Test 3: PacketReassembler - duplicates ignored
    reassembler3 = PacketReassembler()
    reassembler3.add_packet(0, b"chunk0")
    reassembler3.add_packet(0, b"duplicate")  # Should be ignored
    
    packets = list(reassembler3.get_ready_packets())
    assert len(packets) == 1
    assert packets[0] == b"chunk0"
    print("✓ PacketReassembler: duplicates ignored")
    
    # Test 4: PacketReassembler - gap detection
    reassembler4 = PacketReassembler()
    reassembler4.add_packet(0, b"chunk0")
    reassembler4.add_packet(2, b"chunk2")  # Gap at seq=1
    
    packets = list(reassembler4.get_ready_packets())
    assert len(packets) == 1  # Only chunk0 delivered
    assert reassembler4.has_gaps() == True
    assert reassembler4.get_buffer_size() == 1  # chunk2 buffered
    print("✓ PacketReassembler: gap detection")
    
    # Test 5: PacketReassembler - fill gap
    reassembler4.add_packet(1, b"chunk1")  # Fill the gap
    
    packets = list(reassembler4.get_ready_packets())
    assert len(packets) == 2  # chunk1 and chunk2 now delivered
    assert reassembler4.get_buffer_size() == 0
    print("✓ PacketReassembler: fill gap delivers buffered packets")
    
    # Test 6: RUDPClient initialization
    client = RUDPClient()
    assert client.state == StreamState.IDLE
    assert client.sock is None
    print("✓ RUDPClient: initialization")
    
    # Test 7: RUDPClient metrics
    metrics = client.get_metrics()
    assert metrics.bytes_transferred == 0
    assert metrics.packets_received == 0
    assert metrics.connection_state == StreamState.IDLE
    print("✓ RUDPClient: get metrics")
    
    print("\n✅ All RUDPClient tests passed!")
    print("\nExample usage:")
    print("  client = RUDPClient()")
    print("  request = StreamRequest(filename='video.mp4', byte_start=1024)")
    print("  metadata = client.connect(('127.0.0.1', 9000), request)")
    print("  ")
    print("  for chunk in client.stream():")
    print("      process(chunk)")
    print("  ")
    print("  client.close()")
