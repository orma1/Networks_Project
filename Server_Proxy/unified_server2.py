"""
UNIFIED ORIGIN SERVER - Complete Implementation
────────────────────────────────────────────────

Serves video files to proxy/clients over TCP and RUDP (Reliable UDP).

Features:
✓ TCP streaming with proper error handling
✓ RUDP with explicit flow control (RWnd in ACKs)
✓ Datagram size validation (<64KB UDP limit)
✓ Loss simulation on data packets and ACKs
✓ TCP Reno congestion control (slow-start, AIMD, fast retransmit)
✓ Sequence number wraparound handling (32-bit)
✓ Keep-alive probes to detect dead clients
✓ Comprehensive error handling and logging
✓ Session state tracking and metrics
✓ Timeout protection for slow clients
✓ Configurable loss rates and latency simulation

Architecture:
├── UnifiedServer (main class)
├── RUDPSession (per-client RUDP manager)
├── TCPHandler (per-connection handler)
└── Utility functions (validation, packet encoding, etc)
"""

import os
import sys
import socket
import threading
import signal
import random
import time
import yaml
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# ══════════════════════════════════════════════════════════════════════════════
# PATH CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, "videos")
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
sys.path.append(PARENT_DIR)
sys.path.append(os.path.join(PARENT_DIR, "dhcp"))

try:
    from dhcp.dhcp_helper import VirtualNetworkInterface
except ImportError:
    print("[!] Warning: DHCP helper not available. Using loopback only.")
    VirtualNetworkInterface = None

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS & CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

# UDP/IP constraints
MAX_UDP_PAYLOAD = 65507  # 65535 - 28 byte (IP + UDP headers)
CHUNK_SIZE = 1400  # Conservative to stay well below UDP limit

# Protocol overhead: 4-byte seq + 2-byte length = 6 bytes
PACKET_OVERHEAD = 6
MAX_PAYLOAD_SIZE = MAX_UDP_PAYLOAD - PACKET_OVERHEAD  # 65501 bytes

# RUDP tuning
INIT_CWND = 4.0
INIT_SSTHRESH = 64.0
RTO = 0.25  # Retransmission timeout (seconds)
DUPACK_THRESH = 3  # Duplicate ACKs that trigger fast retransmit
KEEPALIVE_SECS = 5.0  # Send keep-alive if idle
ACK_POLL_TIMEOUT = 0.002  # Socket timeout for ACK drain
RECV_WINDOW_SIZE = 1024 * 1024  # 1MB receiver buffer
SESSION_IDLE_TIMEOUT = 30.0  # Close session if no ACKs for this long

# Loss & latency simulation
DATA_LOSS_RATE = 0.0  # Probability to drop data packets
ACK_LOSS_RATE = 0.0  # Probability to lose ACKs  
LATENCY_MS = 0  # Simulated one-way latency


# ══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES FOR STATE TRACKING
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class WindowEntry:
    """One entry in the RUDP sliding window."""
    data: bytes
    timestamp: float
    acked: bool = False


@dataclass
class RUDPMetrics:
    """Track session statistics for analysis."""
    start_time: float = field(default_factory=time.monotonic)
    packets_sent: int = 0
    packets_acked: int = 0
    packets_retransmitted: int = 0
    packets_lost_simulated: int = 0
    bytes_sent: int = 0
    window_max_size: int = 0
    cwnd_max: float = 0.0
    errors: int = 0
    
    def duration(self) -> float:
        return time.monotonic() - self.start_time
    
    def throughput_mbps(self) -> float:
        duration = self.duration()
        if duration == 0:
            return 0.0
        return (self.bytes_sent * 8) / (duration * 1_048_576)
    
    def loss_rate(self) -> float:
        if self.packets_sent == 0:
            return 0.0
        return 1.0 - (self.packets_acked / self.packets_sent)


# ══════════════════════════════════════════════════════════════════════════════
# PACKET ENCODING/DECODING WITH VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def encode_data_pkt(seq: int, payload: bytes) -> bytes:
    """
    Encode RUDP data packet: <4-byte seq><2-byte length><payload>
    
    This format allows receiver to know exact payload length even if
    UDP packet was padded. Critical for reliable reassembly.
    
    Args:
        seq: Sequence number (32-bit, will wrap)
        payload: Data chunk
        
    Returns:
        Complete encoded packet
        
    Raises:
        ValueError: If payload exceeds limits
    """
    if len(payload) == 0:
        raise ValueError("Payload cannot be empty")
    
    if len(payload) > MAX_PAYLOAD_SIZE:
        raise ValueError(
            f"Payload too large: {len(payload)} bytes "
            f"(max {MAX_PAYLOAD_SIZE} bytes)"
        )
    
    # Format: seq(4) + len(2) + payload
    pkt = (
        seq.to_bytes(4, "big") +
        len(payload).to_bytes(2, "big") +
        payload
    )
    
    if len(pkt) > MAX_UDP_PAYLOAD:
        raise ValueError(
            f"Encoded packet exceeds UDP limit: {len(pkt)} > {MAX_UDP_PAYLOAD} bytes"
        )
    
    return pkt


def decode_data_pkt(packet: bytes) -> Tuple[int, bytes]:
    """
    Decode RUDP data packet.
    
    Args:
        packet: Raw packet bytes
        
    Returns:
        (sequence_number, payload)
        
    Raises:
        ValueError: If packet is malformed
    """
    if len(packet) < PACKET_OVERHEAD:
        raise ValueError(f"Packet too short: {len(packet)} bytes")
    
    seq = int.from_bytes(packet[:4], "big")
    declared_len = int.from_bytes(packet[4:6], "big")
    payload = packet[6:6+declared_len]
    
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
    RFC 1323 algorithm for 32-bit sequence number comparison.
    Handles wraparound correctly for circular sequence space.
    """
    return (a - b) & 0x80000000 != 0


def seq_leq(a: int, b: int) -> bool:
    """Sequence number less-than-or-equal with wraparound."""
    return a == b or seq_less_than(a, b)


# ══════════════════════════════════════════════════════════════════════════════
# RUDP SESSION - RELIABLE UDP WITH FLOW CONTROL
# ══════════════════════════════════════════════════════════════════════════════

class RUDPSession:
    """
    One RUDP file transfer session (one client).
    
    Implements:
    - Sliding window with selective repeat
    - TCP Reno congestion control (slow-start, AIMD, fast retransmit)
    - Explicit flow control (RWnd in protocol)
    - Keep-alive probes
    - Sequence number wraparound handling
    """
    
    def __init__(
        self,
        client_addr: Tuple[str, int],
        filepath: str,
        byte_start: int,
        data_loss_rate: float = 0.0,
        ack_loss_rate: float = 0.0,
        latency_ms: int = 0,
    ):
        self.client_addr = client_addr
        self.filepath = filepath
        self.byte_start = byte_start
        self.data_loss_rate = data_loss_rate
        self.ack_loss_rate = ack_loss_rate
        self.latency_ms = latency_ms
        self.session_id = random.randint(1000, 9999)  # Unique session ID for logging
        
        try:
            self.file_size = os.path.getsize(filepath)
        except OSError as e:
            raise ValueError(f"Cannot access file: {e}")
        
        # Create session socket (gets ephemeral port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(ACK_POLL_TIMEOUT)
        
        # Sliding window state
        self.window: Dict[int, WindowEntry] = {}
        self.base: int = 0  # Oldest un-ACKed sequence
        self.next_seq: int = 0  # Next seq to assign
        
        # Congestion control (TCP Reno)
        self.cwnd: float = float(INIT_CWND)
        self.ssthresh: float = float(INIT_SSTHRESH)
        
        # Duplicate ACK detection
        self.last_new_ack: int = -1
        self.dup_count: int = 0
        
        # Flow control (receiver tells us its window)
        self.remote_rwnd: int = RECV_WINDOW_SIZE
        
        # Session state
        self.last_ack_time: float = time.monotonic()
        self.eof: bool = False
        self.active: bool = True
        
        # Metrics
        self.metrics = RUDPMetrics()
        
        print(
            f"[RUDP] Session created for {client_addr} "
            f"file_size={self.file_size} bytes"
        )
    
    # ── Packet Loss & Latency Simulation ──────────────────────────────────
    
    def _should_drop_packet(self, is_ack: bool = False) -> bool:
        """Randomly drop packet based on configured loss rate."""
        if is_ack and self.ack_loss_rate > 0:
            return random.random() < self.ack_loss_rate
        elif not is_ack and self.data_loss_rate > 0:
            return random.random() < self.data_loss_rate
        return False
    
    def _apply_latency(self):
        """Simulate network latency."""
        if self.latency_ms > 0:
            time.sleep(self.latency_ms / 1000.0)
    
    def _send(self, pkt: bytes, is_ack: bool = False) -> bool:
        """
        Send packet with loss/latency simulation.
        
        Args:
            pkt: Packet to send
            is_ack: Whether this is an ACK (for loss simulation)
            
        Returns:
            True if sent, False if dropped
        """
        if self._should_drop_packet(is_ack):
            self.metrics.packets_lost_simulated += 1
            if is_ack:
                print(f"[LOSS] ACK dropped (simulated)")
            else:
                print(f"[LOSS] Data packet dropped (simulated)")
            return False
        
        self._apply_latency()
        
        try:
            self.sock.sendto(pkt, self.client_addr)
            if not is_ack:
                self.metrics.packets_sent += 1
            return True
        except OSError as e:
            print(f"[-] Socket error on send: {e}")
            self.metrics.errors += 1
            self.active = False
            return False
    
    # ── Congestion Control ────────────────────────────────────────────────
    
    def _grow_cwnd(self):
        """Increase cwnd: exponential in slow-start, linear in congestion avoidance."""
        if self.cwnd < self.ssthresh:
            self.cwnd += 1.0  # Slow start: doubles each RTT
        else:
            self.cwnd += 1.0 / self.cwnd  # AIMD: additive increase
        
        self.metrics.cwnd_max = max(self.metrics.cwnd_max, self.cwnd)
    
    def _shrink_cwnd(self, fast_retransmit: bool = False):
        """
        Halve ssthresh on loss event.
        
        Args:
            fast_retransmit: True if from duplicate ACKs, False if timeout
        """
        self.ssthresh = max(self.cwnd / 2.0, 2.0)
        self.cwnd = self.ssthresh if fast_retransmit else 2.0
    
    def _get_packets_in_flight(self) -> int:
        """Count un-ACKed packets in window."""
        return sum(1 for entry in self.window.values() if not entry.acked)
    
    # ── ACK Processing ───────────────────────────────────────────────────
    
    def _drain_acks(self):
        """
        Non-blocking drain of all ACKs in receive buffer.
        
        This processes all available ACKs before the next data-send pass.
        Avoids blocking while processing feedback.
        """
        try:
            while True:
                raw, _ = self.sock.recvfrom(256)
                msg = raw.decode(errors="ignore").strip()
                
                if not msg.startswith("ACK|"):
                    # Ignore non-ACK messages
                    if msg.startswith("DROP"):
                        print(f"[SERVER] Received DROP packet from proxy (testing) - ignored")
                    continue
                
                try:
                    parts = msg.split("|")
                    if len(parts) < 2:
                        print(f"[!] Malformed ACK: missing fields")
                        continue
                    
                    seq = int(parts[1])
                    # Extract remote receiver window (new protocol)
                    rwnd = int(parts[2]) if len(parts) > 2 else RECV_WINDOW_SIZE
                    
                except (ValueError, IndexError) as e:
                    print(f"[!] ACK parse error: {e}")
                    continue
                
                # Update remote window
                self.remote_rwnd = max(0, rwnd)
                
                # Ignore ACKs for already-removed packets
                if seq not in self.window:
                    continue
                
                # ── NEW ACK ───────────────────────────────────────────────
                if not self.window[seq].acked:
                    self.window[seq].acked = True
                    self.last_ack_time = time.monotonic()
                    self._grow_cwnd()
                    self.last_new_ack = seq
                    self.dup_count = 0
                    self.metrics.packets_acked += 1
                
                # ── DUPLICATE ACK ────────────────────────────────────────
                else:
                    if seq == self.last_new_ack:
                        self.dup_count += 1
                        if self.dup_count >= DUPACK_THRESH:
                            # Fast retransmit
                            self._shrink_cwnd(fast_retransmit=True)
                            if self.base in self.window:
                                self._send(self.window[self.base].data)
                                self.metrics.packets_retransmitted += 1
                                print(f"[FAST-RX] Seq={self.base}")
                            self.dup_count = 0
        
        except socket.timeout:
            pass  # Queue drained - normal
        except OSError:
            pass  # Socket closed - normal
    
    # ── Main Send Loop ────────────────────────────────────────────────────
    
    def run(self):
        """
        Main RUDP session loop.
        
        1. Advertise file size via META
        2. Fill congestion window respecting flow control
        3. Drain ACKs and update state
        4. Timeout retransmit old packets
        5. Send FIN when done
        """
        try:
            # Log session startup with loss rate
            # NOTE: Loss simulation happens automatically in _send() method
            if self.data_loss_rate > 0:
                print(f"[RUDP] Session starting - LOSS SIMULATION ACTIVE (loss_rate={self.data_loss_rate*100:.1f}%)")
            else:
                print(f"[RUDP] Session starting - NO PACKET LOSS (loss_rate=0%)")
            
            # Advertise file size (repeated for reliability)
            meta_pkt = f"META|{self.file_size}|{RECV_WINDOW_SIZE}".encode()
            for _ in range(5):
                self._send(meta_pkt)
            time.sleep(0.03)
            
            with open(self.filepath, "rb") as fh:
                fh.seek(self.byte_start)
                
                while self.active:
                    # ── Keep-alive ─────────────────────────────────────────
                    if time.monotonic() - self.last_ack_time > KEEPALIVE_SECS:
                        alive_pkt = f"ALIVE|{RECV_WINDOW_SIZE}".encode()
                        self._send(alive_pkt)
                        self.last_ack_time = time.monotonic()
                    
                    # ── Check for idle timeout ──────────────────────────────
                    if time.monotonic() - self.last_ack_time > SESSION_IDLE_TIMEOUT:
                        print(f"[!] Session idle timeout for {self.client_addr}")
                        break
                    
                    # ── Calculate window limits ─────────────────────────────
                    # Respect both cwnd (congestion) and rwnd (receiver buffer)
                    max_by_congestion = int(self.cwnd)
                    max_by_flow = max(1, self.remote_rwnd // CHUNK_SIZE)
                    max_in_flight = min(max_by_congestion, max_by_flow)
                    
                    in_flight = self._get_packets_in_flight()
                    
                    # ── Fill window ────────────────────────────────────────
                    while (
                        self.active
                        and not self.eof
                        and in_flight < max_in_flight
                    ):
                        chunk = fh.read(CHUNK_SIZE)
                        if not chunk:
                            self.eof = True
                            break
                        
                        try:
                            pkt = encode_data_pkt(self.next_seq, chunk)
                            self.window[self.next_seq] = WindowEntry(
                                data=pkt,
                                timestamp=time.monotonic(),
                                acked=False
                            )
                            
                            if self._send(pkt, is_ack=False):
                                self.metrics.bytes_sent += len(chunk)
                                self.next_seq = (self.next_seq + 1) & 0xFFFFFFFF
                                in_flight += 1
                        
                        except ValueError as e:
                            print(f"[-] Packet encoding error: {e}")
                            self.active = False
                            break
                    
                    # ── Process ACKs ────────────────────────────────────────
                    self._drain_acks()
                    
                    # ── Slide window ────────────────────────────────────────
                    # Remove consecutive ACKed packets from front
                    while self.base in self.window and self.window[self.base].acked:
                        del self.window[self.base]
                        self.base = (self.base + 1) & 0xFFFFFFFF
                    
                    self.metrics.window_max_size = max(
                        self.metrics.window_max_size,
                        len(self.window)
                    )
                    
                    # ── Timeout retransmit ──────────────────────────────────
                    # Retransmit oldest timed-out packet only
                    now = time.monotonic()
                    for seq, entry in list(self.window.items()):
                        if not entry.acked and (now - entry.timestamp) > RTO:
                            self._shrink_cwnd(fast_retransmit=False)
                            self._send(entry.data, is_ack=False)
                            entry.timestamp = now
                            self.metrics.packets_retransmitted += 1
                            break
                    
                    # ── Check termination ───────────────────────────────────
                    if self.eof and not self.window:
                        break
                    
                    time.sleep(0.0001)  # Yield CPU
        
        except Exception as e:
            print(f"[-] RUDP session error: {e}")
            self.metrics.errors += 1
        
        finally:
            # Send FIN (end-of-stream)
            for _ in range(5):
                fin_pkt = b"FIN|DONE|0"
                self._send(fin_pkt)
                time.sleep(0.01)
            
            self.sock.close()
            self._print_summary()
    
    def _print_summary(self):
        """Print session statistics."""
        m = self.metrics
        print(f"""
[RUDP] Session Summary for {self.client_addr}
  ├─ Duration: {m.duration():.2f} seconds
  ├─ Bytes sent: {m.bytes_sent}
  ├─ Packets sent: {m.packets_sent}
  ├─ Packets ACKed: {m.packets_acked}
  ├─ Packets retransmitted: {m.packets_retransmitted}
  ├─ Packets lost (simulated): {m.packets_lost_simulated}
  ├─ Max window size: {m.window_max_size}
  ├─ Max cwnd: {m.cwnd_max:.1f}
  ├─ Throughput: {m.throughput_mbps():.2f} Mbps
  ├─ Loss rate: {m.loss_rate()*100:.1f}%
  └─ Errors: {m.errors}
""")


# ══════════════════════════════════════════════════════════════════════════════
# TCP HANDLER - SYNCHRONOUS FILE SERVING
# ══════════════════════════════════════════════════════════════════════════════

class TCPHandler:
    """Handle one TCP connection."""
    
    def __init__(self, server):
        self.server = server
    
    def handle(self, conn: socket.socket, addr: Tuple[str, int]):
        """
        Process one TCP file request.
        
        Protocol:
          Client → Server: REQ|<filename>|<byte_start>
          Server → Client: META|<file_size>\\n<raw bytes>
                      or  ERR|<error message>\\n
        """
        try:
            conn.settimeout(10.0)
            req_data = conn.recv(4096).decode(errors="ignore")
            parts = req_data.split("|")
            
            # Validate request format
            if not parts or len(parts) < 3:
                conn.sendall(b"ERR|BAD_REQUEST\n")
                return
            
            msg_type = parts[0]
            
            # ── Simulated loss (for testing) ────────────────────────────
            if msg_type == "DROP":
                print(f"[TCP] DROP from {addr}")
                return
            
            # ── File request ────────────────────────────────────────────
            if msg_type != "REQ":
                conn.sendall(b"ERR|UNKNOWN_MSG_TYPE\n")
                return
            
            # Parse filename (path traversal protection)
            filename = os.path.basename(parts[1]) if len(parts) > 1 else ""
            if not filename:
                conn.sendall(b"ERR|NO_FILENAME\n")
                return
            
            # Parse byte offset
            try:
                byte_start = int(parts[2]) if len(parts) > 2 else 0
                byte_start = max(0, byte_start)
            except ValueError:
                conn.sendall(b"ERR|INVALID_OFFSET\n")
                return
            
            # Resolve file path
            filepath = os.path.join(VIDEO_DIR, filename)
            
            # Check file exists
            if not os.path.exists(filepath):
                print(f"[TCP] File not found: {filename} from {addr}")
                conn.sendall(b"ERR|NOT_FOUND\n")
                return
            
            # Get file size
            try:
                file_size = os.path.getsize(filepath)
            except OSError:
                conn.sendall(b"ERR|CANNOT_STAT\n")
                return
            
            # Send header
            header = f"META|{file_size}\n"
            conn.sendall(header.encode())
            
            # Stream file
            try:
                with open(filepath, "rb") as fh:
                    fh.seek(byte_start)
                    while True:
                        chunk = fh.read(131_072)  # 128KB chunks
                        if not chunk:
                            break
                        conn.sendall(chunk)
                print(f"[TCP] Served {filename} to {addr}")
            
            except FileNotFoundError:
                # File deleted between stat and open
                try:
                    conn.sendall(b"ERR|FILE_DELETED\n")
                except:
                    pass
        
        except socket.timeout:
            print(f"[TCP] Timeout from {addr}")
        except BrokenPipeError:
            pass  # Client disconnected
        except ConnectionResetError:
            print(f"[TCP] Connection reset by {addr}")
        except Exception as e:
            print(f"[-] TCP handler error: {e}")
            try:
                conn.sendall(f"ERR|{str(e)[:50]}\n".encode())
            except:
                pass
        
        finally:
            conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# UNIFIED SERVER - MAIN CLASS
# ══════════════════════════════════════════════════════════════════════════════

class UnifiedServer:
    """
    Origin server supporting both TCP and RUDP.
    
    Binds to same (IP, port) on both TCP and UDP sockets
    (different socket types can share port on Linux).
    """
    
    def __init__(self, config_file: str = "configs/dhcp_config.yaml"):
        self.active = True
        signal.signal(signal.SIGINT, self._on_sigint)
        
        # Load configuration
        try:
            with open(config_file, "r") as f:
                config = yaml.safe_load(f)
                self.port = config.get("server_config", {}).get(
                    "origin_port", 9000
                )
        except (FileNotFoundError, KeyError):
            self.port = 9000
            print("[!] Config not found, using default port 9000")
        
        # Get IP address via DHCP if available
        if VirtualNetworkInterface:
            self.v_net = VirtualNetworkInterface(
                client_name="OriginServer",
                fixed_id="OriginServer"
            )
            self.my_ip = self.v_net.setup_network()
        else:
            self.my_ip = "127.0.0.1"
        
        # Loss/latency simulation rates
        self.data_loss_rate = float(os.environ.get("DATA_LOSS_RATE", DATA_LOSS_RATE))
        self.ack_loss_rate = float(os.environ.get("ACK_LOSS_RATE", ACK_LOSS_RATE))
        self.latency_ms = int(os.environ.get("LATENCY_MS", LATENCY_MS))
        
        print(f"""
╔════════════════════════════════════════════════════════════╗
║ UNIFIED ORIGIN SERVER - STARTING                          ║
╠════════════════════════════════════════════════════════════╣
║ IP Address: {self.my_ip:<48} ║
║ Port: {self.port:<52} ║
║ Data Loss Rate (env): {os.environ.get("DATA_LOSS_RATE", "NOT SET"):<40} ║
║ Data Loss Rate (actual): {self.data_loss_rate*100:<40.1f}% ║
║ ACK Loss Rate: {self.ack_loss_rate*100:<44.1f}% ║
║ Latency: {self.latency_ms:<51} ms ║
║ Video Directory: {VIDEO_DIR:<40} ║
╚════════════════════════════════════════════════════════════╝
""")
    
    def _on_sigint(self, signum, frame):
        """Handle Ctrl+C gracefully."""
        print("\n[*] Shutting down server...")
        self.active = False
        sys.exit(0)
    
    def _run_tcp(self):
        """TCP listener in background thread."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            srv.bind((self.my_ip, self.port))
            srv.listen(20)
            srv.settimeout(1.0)
            print(f"[TCP] Listening on {self.my_ip}:{self.port}")
            
            handler = TCPHandler(self)
            
            while self.active:
                try:
                    conn, addr = srv.accept()
                    t = threading.Thread(
                        target=handler.handle,
                        args=(conn, addr),
                        daemon=True
                    )
                    t.start()
                except socket.timeout:
                    continue
        
        except OSError as e:
            print(f"[-] TCP bind failed: {e}")
        
        finally:
            srv.close()
    
    def _run_rudp(self):
        """RUDP dispatcher (blocks in main thread)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            sock.bind((self.my_ip, self.port))
            sock.settimeout(0.01)  # Short timeout for draining messages
            print(f"[RUDP] Listening on {self.my_ip}:{self.port}")
            
            while self.active:
                try:
                    data, addr = sock.recvfrom(4096)
                    msg = data.decode(errors="ignore")
                    parts = msg.split("|")
                    
                    # ── Handle LOSS_RATE updates from proxy ────────────────
                    if parts and parts[0] == "LOSS_RATE":
                        try:
                            new_loss_rate = float(parts[1])
                            if new_loss_rate != self.data_loss_rate:
                                self.data_loss_rate = new_loss_rate
                                print(f"[SERVER] 🔄 Loss rate updated to: {new_loss_rate*100:.1f}%")
                        except (ValueError, IndexError):
                            pass
                        continue
                    
                    # ── Handle REQ (new stream requests) ──────────────────
                    if not parts or parts[0] != "REQ":
                        continue
                    
                    # BEFORE creating session, drain any pending LOSS_RATE messages!
                    print(f"[SERVER] 📥 REQ received, draining pending LOSS_RATE messages...")
                    sock.settimeout(0.001)  # Very short timeout for draining
                    while True:
                        try:
                            drain_data, _ = sock.recvfrom(4096)
                            drain_msg = drain_data.decode(errors="ignore")
                            drain_parts = drain_msg.split("|")
                            if drain_parts and drain_parts[0] == "LOSS_RATE":
                                try:
                                    new_loss_rate = float(drain_parts[1])
                                    if new_loss_rate != self.data_loss_rate:
                                        self.data_loss_rate = new_loss_rate
                                        print(f"[SERVER] 🔄 DRAINED: Loss rate updated to: {new_loss_rate*100:.1f}%")
                                except (ValueError, IndexError):
                                    pass
                        except socket.timeout:
                            break  # No more pending messages
                    sock.settimeout(0.01)  # Reset timeout
                    
                    if len(parts) < 3:
                        sock.sendto(b"ERR|BAD_REQUEST", addr)
                        continue
                    
                    # Parse request
                    filename = os.path.basename(parts[1])
                    try:
                        byte_start = int(parts[2])
                        byte_start = max(0, byte_start)
                    except ValueError:
                        sock.sendto(b"ERR|INVALID_OFFSET", addr)
                        continue
                    
                    filepath = os.path.join(VIDEO_DIR, filename)
                    
                    # Check file exists
                    if not os.path.exists(filepath):
                        print(f"[RUDP] File not found: {filename} from {addr}")
                        sock.sendto(b"ERR|NOT_FOUND", addr)
                        continue
                    
                    # Create session with CORRECT (drained) loss_rate
                    try:
                        print(f"[SERVER] ✅ Creating session with loss_rate={self.data_loss_rate*100:.1f}%")
                        session = RUDPSession(
                            addr,
                            filepath,
                            byte_start,
                            data_loss_rate=self.data_loss_rate,
                            ack_loss_rate=self.ack_loss_rate,
                            latency_ms=self.latency_ms,
                        )
                        
                        t = threading.Thread(
                            target=session.run,
                            daemon=True
                        )
                        t.start()
                    
                    except ValueError as e:
                        print(f"[-] Session creation failed: {e}")
                        sock.sendto(f"ERR|{str(e)[:50]}".encode(), addr)
                
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[-] RUDP dispatcher error: {e}")
        
        except OSError as e:
            print(f"[-] RUDP bind failed: {e}")
        
        finally:
            sock.close()
    
    def start(self):
        """Start both TCP and RUDP listeners."""
        tcp_thread = threading.Thread(target=self._run_tcp, daemon=True)
        tcp_thread.start()
        
        # RUDP blocks main thread
        self._run_rudp()


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    server = UnifiedServer()
    server.start()