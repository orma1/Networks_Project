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
import queue

from Server_Proxy.shared.protocol_utils import (
    decode_data_packet,
    decode_control_message,
    encode_control_message,
    seq_less_than,
)
from Server_Proxy.shared.streaming_interfaces import (
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
    """
    buffer: Dict[int, bytes] = field(default_factory=dict)
    next_expected: int = 0
    
    def add_packet(self, seq: int, payload: bytes) -> None:
        """Add packet to reassembly buffer."""
        # Ignore duplicates
        if seq in self.buffer:
            return
        
        # Ignore old packets (already delivered)
        if seq_less_than(seq, self.next_expected):
            return
        
        self.buffer[seq] = payload
    
    def get_ready_packets(self) -> Generator[bytes, None, None]:
        """Yield packets that are ready for in-order delivery."""
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

class RUDPClient(StreamingClient):
    """
    RUDP client for fetching streams from origin server.
    """
    
    def __init__(
        self,
        recv_buffer_size: int = 1024 * 1024,  # 1MB
        socket_timeout: float = 0.1,
        max_buffer_packets: int = 1000,
    ):
        self.recv_buffer_size = recv_buffer_size
        self.socket_timeout = socket_timeout
        self.max_buffer_packets = max_buffer_packets
        self._data_queue = queue.Queue()
        
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
        """Connect to RUDP server and request stream."""
        self.server_addr = server_addr
        self.state = StreamState.CONNECTING
        
        # Create socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.recv_buffer_size)
        self.sock.settimeout(self.socket_timeout)
        
        self.active = True
        self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.recv_thread.start()
        
        # Send REQ message
        req_msg = encode_control_message(
            "REQ",
            request.filename,
            request.byte_start,
        )
        self.sock.sendto(req_msg, server_addr)
        
        # Wait for META to be populated by the thread
        metadata = self._wait_for_meta(timeout=2.0)
        
        if metadata is None:
            self.state = StreamState.ERROR
            raise TimeoutError("META not received from server")
        
        self.state = StreamState.CONNECTED
        return metadata
    
    def stream(self) -> Generator[bytes, None, None]:
        """Stream data from server using a blocking queue."""
        self.state = StreamState.STREAMING
        start_time = time.monotonic()
        bytes_received = 0
        
        while self.active:
            try:
                # This blocks the thread efficiently until data exists
                # We use a 0.5s timeout just so we can check if self.active changed
                payload = self._data_queue.get(timeout=0.5)
                
                if payload is None: # This is our signal that the stream is finished
                    break
                    
                yield payload
                
                # Update metrics
                bytes_received += len(payload)
                elapsed = time.monotonic() - start_time
                if elapsed > 0:
                    self._metrics.current_throughput_mbps = (bytes_received * 8) / (elapsed * 1_000_000)
                    
            except queue.Empty:
                continue # No data in 0.5s, check self.active and try again
    
    def get_metrics(self) -> StreamMetrics:
        """Get current streaming metrics."""
        return self._metrics
    
    def close(self) -> None:
        """Close connection and cleanup."""
        self.active = False
        self.state = StreamState.CLOSED
        
        if self.recv_thread:
            self.recv_thread.join(timeout=1.0)
        if self.sock:
            self.sock.close()
    
    # ── Internal Methods ─────────────────────────────────────────────────
    
    def _wait_for_meta(self, timeout: float = 2.0) -> Optional[StreamMetadata]:
        """Wait for META message from server."""
        deadline = time.monotonic() + timeout
        
        while time.monotonic() < deadline:
            if self.metadata is not None:
                return self.metadata
            time.sleep(0.001)
            
        return None
    
    def _recv_loop(self) -> None:
        """Background thread for receiving packets."""
        last_seq = -1
        # Match against control prefixes first to avoid bad decoding
        control_prefixes = (b"META", b"ALIV", b"FIN|", b"ERR|", b"DROP")
        
        while self.active:
            try:
                # Capture the 'addr' of the incoming packet
                raw, addr = self.sock.recvfrom(65535)
                
                # 1. Check for control messages
                if raw.startswith(control_prefixes):
                    msg = decode_control_message(raw)
                    if msg:
                        if msg[0] == "META":
                            
                            # This ensures ACKs go to the session's ephemeral port.
                            self.server_addr = addr 
                            
                            args = msg[1] if isinstance(msg[1], (list, tuple)) else msg[1:]
                            self.metadata = StreamMetadata(
                                file_size=int(args[0]),
                                remote_window=int(args[1]) if len(args) > 1 else 1048576,
                                content_type="video/mp4",
                                supports_range=True,
                            )
                        elif msg[0] == "FIN":
                            self.state = StreamState.CLOSED
                            self.active = False
                            for payload in self.reassembler.get_ready_packets():
                                self._fin_flush_buffer.append(payload)
                            self.data_ready.set()
                            self._data_queue.put(None)
                    continue
                
                # 2. Process DATA packet
                result = None
                try:
                    result = decode_data_packet(raw)
                except ValueError:
                    continue
                
                if result:
                    seq, payload = result

                    self._metrics.packets_received += 1
                    self._metrics.bytes_transferred += len(payload)
                    
                    if last_seq != -1 and seq != (last_seq + 1) & 0xFFFFFFFF:
                        self._metrics.packets_out_of_order += 1
                    
                    last_seq = seq
                    
                    prev_size = self.reassembler.get_buffer_size()
                    self.reassembler.add_packet(seq, payload)
                    for ready_payload in self.reassembler.get_ready_packets():
                        self._data_queue.put(ready_payload)
                    self.data_ready.set()
                    curr_size = self.reassembler.get_buffer_size()
                    
                    if curr_size == prev_size:
                        self._metrics.duplicate_packets += 1
                    
                    self._send_ack(seq)
                    self.data_ready.set()
            
            except socket.timeout:
                continue
            except Exception as e:
                pass
    
    def _send_ack(self, seq: int) -> None:
        """Send ACK for received packet."""
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
    
    reassembler = PacketReassembler()
    reassembler.add_packet(0, b"chunk0")
    reassembler.add_packet(1, b"chunk1")
    reassembler.add_packet(2, b"chunk2")
    
    packets = list(reassembler.get_ready_packets())
    assert len(packets) == 3
    assert packets[0] == b"chunk0"
    print("✓ PacketReassembler: in-order packets")
    
    reassembler2 = PacketReassembler()
    reassembler2.add_packet(2, b"chunk2")
    reassembler2.add_packet(0, b"chunk0")
    reassembler2.add_packet(1, b"chunk1")
    
    packets = list(reassembler2.get_ready_packets())
    assert len(packets) == 3
    assert packets[0] == b"chunk0"
    print("✓ PacketReassembler: out-of-order packets")
    
    print("\n✅ All RUDPClient tests passed!")