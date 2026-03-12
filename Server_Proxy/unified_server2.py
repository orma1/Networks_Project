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

from protocol_utils import (
    encode_data_packet,
    decode_data_packet,
    encode_control_message,
    seq_less_than,
    seq_less_equal,
    seq_greater_than,
    seq_in_range,
    MAX_UDP_PAYLOAD,
    RECOMMENDED_CHUNK_SIZE,
    PACKET_OVERHEAD,
    MAX_PAYLOAD_SIZE,
    DEFAULT_RECV_BUFFER_SIZE
)
from streaming_interfaces import (
    StreamingServer,
    StreamRequest,
    StreamMetadata,
    StreamMetrics,
    TransportProtocol,
    StreamState,
)

from sliding_window import SlidingWindow, WindowEntry
from congestion_controller import CongestionController, CongestionState
from flow_controller import FlowController, calculate_combined_limit
from file_repository import LocalFileRepository
from session_manager import SimpleSessionManager, SessionInfo

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

CHUNK_SIZE = RECOMMENDED_CHUNK_SIZE
RECV_WINDOW_SIZE = DEFAULT_RECV_BUFFER_SIZE

# RUDP tuning
INIT_CWND = 4.0
INIT_SSTHRESH = 64.0
RTO = 0.25  # Retransmission timeout (seconds)
DUPACK_THRESH = 3  # Duplicate ACKs that trigger fast retransmit
KEEPALIVE_SECS = 5.0  # Send keep-alive if idle
ACK_POLL_TIMEOUT = 0.002  # Socket timeout for ACK drain
SESSION_IDLE_TIMEOUT = 30.0  # Close session if no ACKs for this long

# Loss & latency simulation
DATA_LOSS_RATE = 0.0  # Probability to drop data packets
ACK_LOSS_RATE = 0.0  # Probability to lose ACKs  
LATENCY_MS = 0  # Simulated one-way latency


# ══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES FOR STATE TRACKING
# ══════════════════════════════════════════════════════════════════════════════

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
# RUDP SESSION - RELIABLE UDP WITH FLOW CONTROL
# ══════════════════════════════════════════════════════════════════════════════

class RUDPSession(StreamingServer):
    """
    RUDP implementation of StreamingServer using extracted components.
    
    Implements reliable UDP with:
    - Sliding window with selective repeat (SlidingWindow)
    - TCP Reno congestion control (CongestionController)
    - Explicit flow control (FlowController)
    """
    
    def __init__(
        self,
        filepath: str,
        client_addr: Optional[Tuple[str, int]] = None,
        data_loss_rate: float = 0.0,
        ack_loss_rate: float = 0.0,
        latency_ms: int = 0,
    ):
        self.filepath = filepath
        self.client_addr = client_addr
        self.byte_start = 0
        self.data_loss_rate = data_loss_rate
        self.ack_loss_rate = ack_loss_rate
        self.latency_ms = latency_ms
        self.session_id = random.randint(1000, 9999)
        
        try:
            self.file_size = os.path.getsize(filepath)
        except OSError as e:
            raise ValueError(f"Cannot access file: {e}")
        
        # Create session socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(ACK_POLL_TIMEOUT)
        
        # ═══ SESSION MANAGEMENT COMPONENTS ═══
        self.window = SlidingWindow(initial_base=0)
        self.congestion = CongestionController(
            initial_cwnd=INIT_CWND,
            initial_ssthresh=INIT_SSTHRESH
        )
        self.flow = FlowController(
            initial_rwnd=RECV_WINDOW_SIZE,
            packet_size=CHUNK_SIZE
        )
        
        # Session state
        self.last_ack_time: float = time.monotonic()
        self.eof: bool = False
        self.active: bool = True
        
        # Metrics tracking
        self._metrics = StreamMetrics(
            connection_state=StreamState.IDLE
        )
        self.metrics = RUDPMetrics()
        
    # ── StreamingServer Interface Implementation ─────────────────────────
    
    def handle_request(
        self,
        request: StreamRequest,
        client_addr: Tuple[str, int]
    ) -> None:
        self.client_addr = client_addr
        self.byte_start = request.byte_start
        self._metrics.connection_state = StreamState.CONNECTING
        self.run()
    
    def get_metrics(self) -> StreamMetrics:
        self._metrics.packets_sent = self.metrics.packets_sent
        self._metrics.packets_retransmitted = self.metrics.packets_retransmitted
        self._metrics.bytes_transferred = self.metrics.bytes_sent
        self._metrics.packets_lost = self.metrics.packets_lost_simulated
        
        duration = self.metrics.duration()
        if duration > 0:
            self._metrics.average_throughput_mbps = self.metrics.throughput_mbps()
        
        return self._metrics
    
    def close(self) -> None:
        self.active = False
        if hasattr(self, 'sock'):
            self.sock.close()
        self._metrics.connection_state = StreamState.CLOSED
    
    # ── Packet Loss & Latency Simulation ──────────────────────────────────
    
    def _should_drop_packet(self, is_ack: bool = False) -> bool:
        if is_ack and self.ack_loss_rate > 0:
            return random.random() < self.ack_loss_rate
        elif not is_ack and self.data_loss_rate > 0:
            return random.random() < self.data_loss_rate
        return False
    
    def _apply_latency(self):
        if self.latency_ms > 0:
            time.sleep(self.latency_ms / 1000.0)
    
    def _send(self, pkt: bytes, is_ack: bool = False) -> bool:
        if self._should_drop_packet(is_ack):
            self.metrics.packets_lost_simulated += 1
            if is_ack:
                pass # print(f"[LOSS] ACK dropped (simulated)")
            else:
                pass # print(f"[LOSS] Data packet dropped (simulated)")
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
    
    # ── ACK Processing ───────────────────────────────────────────────────
    
    def _drain_acks(self):
        """Non-blocking drain of all ACKs using components."""
        try:
            while True:
                raw, _ = self.sock.recvfrom(256)
                msg = raw.decode(errors="ignore").strip()
                
                if not msg.startswith("ACK|"):
                    if msg.startswith("DROP"):
                        pass
                    continue
                
                try:
                    parts = msg.split("|")
                    if len(parts) < 2:
                        continue
                    
                    seq = int(parts[1])
                    rwnd = int(parts[2]) if len(parts) > 2 else RECV_WINDOW_SIZE
                
                except (ValueError, IndexError):
                    continue
                
                # ═══ UPDATE FLOW CONTROL ═══
                self.flow.update_rwnd(rwnd)
                
                # ═══ PROCESS ACK THROUGH WINDOW (handles duplicates) ═══
                is_new, dup_count = self.window.process_ack(seq)
                
                if is_new:
                    # New ACK - increase congestion window
                    self.congestion.on_ack_received()
                    self.last_ack_time = time.monotonic()
                    self.metrics.packets_acked += 1
                
                elif dup_count >= DUPACK_THRESH:
                    # Fast retransmit triggered
                    self.congestion.on_duplicate_ack()
                    
                    # Retransmit base packet
                    packet = self.window.get_packet(self.window.base)
                    if packet:
                        self._send(packet)
                        self.window.mark_retransmitted(self.window.base)
                        self.metrics.packets_retransmitted += 1
        
        except socket.timeout:
            pass
        except OSError:
            pass
    
    # ── Main Send Loop ────────────────────────────────────────────────────
    
    def run(self):
        """Main RUDP session loop using components."""
        self._metrics.connection_state = StreamState.STREAMING
        try:
            # Advertise file size
            meta_pkt = encode_control_message("META", self.file_size, RECV_WINDOW_SIZE)
            for _ in range(5):
                self._send(meta_pkt)
            time.sleep(0.03)
            
            with open(self.filepath, "rb") as fh:
                fh.seek(self.byte_start)
                
                while self.active:
                    # Keep-alive
                    if time.monotonic() - self.last_ack_time > KEEPALIVE_SECS:
                        alive_pkt = encode_control_message("ALIVE", RECV_WINDOW_SIZE)
                        self._send(alive_pkt)
                        self.last_ack_time = time.monotonic()
                    
                    if time.monotonic() - self.last_ack_time > SESSION_IDLE_TIMEOUT:
                        print(f"[!] Session idle timeout for {self.client_addr}")
                        break
                    
                    # ═══ CALCULATE COMBINED LIMIT (both congestion & flow) ═══
                    max_in_flight = calculate_combined_limit(
                        self.congestion.get_sending_limit(),
                        self.flow.get_sending_limit()
                    )
                    
                    # ═══ FILL WINDOW ═══
                    while (
                        self.active
                        and not self.eof
                        and self.window.has_capacity(max_in_flight)
                    ):
                        chunk = fh.read(CHUNK_SIZE)
                        if not chunk:
                            self.eof = True
                            break
                        
                        try:
                            # Encode packet with current next_seq from component
                            pkt = encode_data_packet(self.window.next_seq, chunk)
                            
                            # Add to window (component handles seq assignment)
                            seq = self.window.add_packet(pkt)
                            
                            if self._send(pkt, is_ack=False):
                                self.metrics.bytes_sent += len(chunk)
                        
                        except ValueError as e:
                            print(f"[-] Packet encoding error: {e}")
                            self.active = False
                            break
                    
                    self._drain_acks()
                    
                    # ═══ SLIDE WINDOW ═══
                    removed = self.window.slide_window()
                    
                    self.metrics.window_max_size = max(
                        self.metrics.window_max_size,
                        self.window.size
                    )
                    
                    # ═══ TIMEOUT RETRANSMIT ═══
                    timed_out = self.window.get_timed_out_packets(timeout_seconds=RTO)
                    if timed_out:
                        seq, packet = timed_out[0]
                        self.congestion.on_timeout()
                        self._send(packet, is_ack=False)
                        self.window.mark_retransmitted(seq)
                        self.metrics.packets_retransmitted += 1
                    
                    # ═══ CHECK TERMINATION ═══
                    if self.eof and self.window.is_empty():
                        break
                    
                    time.sleep(0.0001)  # Yield CPU
        
        except Exception as e:
            self._metrics.connection_state = StreamState.ERROR
            self._metrics.error_message = str(e)
            print(f"[-] RUDP session error: {e}")
            self.metrics.errors += 1
            raise
        
        finally:
            self._metrics.connection_state = StreamState.CLOSED
            for _ in range(5):
                fin_pkt = encode_control_message("FIN", "DONE", 0)
                self._send(fin_pkt)
                time.sleep(0.01)
            
            self.sock.close()
            self._print_summary()
    
    def _print_summary(self):
        """Print session statistics using component metrics."""
        m = self.metrics
        
        # Get metrics from components
        window_stats = self.window.get_statistics()
        congestion_metrics = self.congestion.get_metrics()
        flow_metrics = self.flow.get_metrics()
        
        print(f"""
[RUDP] Session Summary for {self.client_addr}
  ├─ Duration: {m.duration():.2f} seconds
  ├─ Bytes sent: {m.bytes_sent}
  ├─ Packets sent: {m.packets_sent}
  ├─ Packets ACKed: {m.packets_acked}
  ├─ Packets retransmitted: {m.packets_retransmitted}
  ├─ Packets lost (simulated): {m.packets_lost_simulated}
  ├─ Throughput: {m.throughput_mbps():.2f} Mbps
  ├─ Loss rate: {m.loss_rate()*100:.1f}%
  │
  ├─ Window Stats:
  │  ├─ Max size: {window_stats['max_size']}
  │  ├─ Total ACKed: {window_stats['total_acked']}
  │  └─ Total retransmitted: {window_stats['total_retransmitted']}
  │
  ├─ Congestion Control:
  │  ├─ Final cwnd: {congestion_metrics.current_cwnd:.2f}
  │  ├─ Final ssthresh: {congestion_metrics.ssthresh:.2f}
  │  ├─ Max cwnd: {congestion_metrics.max_cwnd:.2f}
  │  └─ Final state: {congestion_metrics.state.value}
  │
  └─ Flow Control:
     ├─ Final RWnd: {flow_metrics.current_rwnd} bytes
     ├─ Min RWnd: {flow_metrics.min_rwnd_seen} bytes
     └─ Zero window events: {flow_metrics.zero_window_events}
""")


# ══════════════════════════════════════════════════════════════════════════════
# TCP HANDLER - SYNCHRONOUS FILE SERVING
# ══════════════════════════════════════════════════════════════════════════════

class TCPHandler:
    """Handle one TCP connection."""
    
    def __init__(self, server):
        self.server = server
    
    def handle(self, conn: socket.socket, addr: Tuple[str, int]):
        """Process one TCP file request."""
        try:
            conn.settimeout(10.0)
            req_data = conn.recv(4096).decode(errors="ignore")
            parts = req_data.split("|")
            
            if not parts or len(parts) < 3:
                conn.sendall(b"ERR|BAD_REQUEST\n")
                return
            
            msg_type = parts[0]
            
            if msg_type == "DROP":
                print(f"[TCP] DROP from {addr}")
                return
            
            if msg_type != "REQ":
                conn.sendall(b"ERR|UNKNOWN_MSG_TYPE\n")
                return
            
            filename = os.path.basename(parts[1]) if len(parts) > 1 else ""
            if not filename:
                conn.sendall(b"ERR|NO_FILENAME\n")
                return
            
            try:
                byte_start = int(parts[2]) if len(parts) > 2 else 0
                byte_start = max(0, byte_start)
            except ValueError:
                conn.sendall(b"ERR|INVALID_OFFSET\n")
                return
            
            # ═══ RESOLVE FILE VIA REPOSITORY ═══
            try:
                filepath = self.server.file_repo.get_file_path(filename, quality="auto")
                file_size = self.server.file_repo.get_file_size(filename, quality="auto")
            except FileNotFoundError:
                print(f"[TCP] File not found: {filename} from {addr}")
                conn.sendall(b"ERR|NOT_FOUND\n")
                return
            except ValueError as e:
                print(f"[TCP] Invalid filename: {e}")
                conn.sendall(b"ERR|BAD_REQUEST\n")
                return
            
            header = f"META|{file_size}\n"
            conn.sendall(header.encode())
            
            try:
                with open(filepath, "rb") as fh:
                    fh.seek(byte_start)
                    while True:
                        chunk = fh.read(131_072)
                        if not chunk:
                            break
                        conn.sendall(chunk)
                print(f"[TCP] Served {filename} to {addr}")
            
            except FileNotFoundError:
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
    """Origin server supporting both TCP and RUDP."""
    
    def __init__(self, config_file: str = "configs/dhcp_config.yaml"):
        self.active = True
        signal.signal(signal.SIGINT, self._on_sigint)
        
        try:
            with open(config_file, "r") as f:
                config = yaml.safe_load(f)
                self.port = config.get("server_config", {}).get(
                    "origin_port", 9000
                )
                video_dir = config.get("server_config", {}).get(
                    "base_directory", "./videos"
                )
        except (FileNotFoundError, KeyError):
            self.port = 9000
            video_dir = "./videos"
            print("[!] Config not found, using default port 9000 and ./videos")
        
        if VirtualNetworkInterface:
            self.v_net = VirtualNetworkInterface(
                client_name="OriginServer",
                fixed_id="OriginServer"
            )
            self.my_ip = self.v_net.setup_network()
        else:
            self.my_ip = "127.0.0.1"
        
        self.data_loss_rate = float(os.environ.get("DATA_LOSS_RATE", DATA_LOSS_RATE))
        self.ack_loss_rate = float(os.environ.get("ACK_LOSS_RATE", ACK_LOSS_RATE))
        self.latency_ms = int(os.environ.get("LATENCY_MS", LATENCY_MS))
        
        # ═══ FILE REPOSITORY ═══
        try:
            self.file_repo = LocalFileRepository(base_dir=video_dir)
        except ValueError as e:
            print(f"[-] Failed to initialize file repository: {e}")
            self.file_repo = None

        # ═══ SESSION MANAGER ═══
        self.session_manager = SimpleSessionManager(max_sessions=100)
        
        # ═══ SESSION CLEANUP THREAD ═══
        def cleanup_loop():
            while self.active:
                time.sleep(30)
                cleaned = self.session_manager.cleanup_idle_sessions(timeout_seconds=60.0)
                if cleaned > 0:
                    print(f"[*] Cleaned up {cleaned} idle sessions")
        
        threading.Thread(target=cleanup_loop, daemon=True).start()

        # Get repository info for startup message
        repo_info = "Not initialized"
        if self.file_repo:
            report = self.file_repo.validate_repository()
            repo_info = f"{report['base_dir']} ({report['total_files']} files)"

        print(f"""
╔════════════════════════════════════════════════════════════╗
║ UNIFIED ORIGIN SERVER - STARTING                          ║
╠════════════════════════════════════════════════════════════╣
║ IP Address: {self.my_ip:<48} ║
║ Port: {self.port:<52} ║
║ Data Loss Rate: {self.data_loss_rate*100:<44.1f}% ║
║                                                            ║
║ File Repository:                                           ║
║   {repo_info:<56} ║
║                                                            ║
║ Session Manager:                                           ║
║   Max sessions: {self.session_manager._max_sessions:<43} ║
╚════════════════════════════════════════════════════════════╝
""")
    
    def _on_sigint(self, signum, frame):
        print("\n[*] Shutting down server...")
        self.active = False
        sys.exit(0)
    
    def _run_tcp(self):
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
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        try:
            sock.bind((self.my_ip, self.port))
            sock.settimeout(0.01)
            print(f"[RUDP] Listening on {self.my_ip}:{self.port}")
            
            while self.active:
                try:
                    data, addr = sock.recvfrom(4096)
                    msg = data.decode(errors="ignore")
                    parts = msg.split("|")
                    
                    if parts and parts[0] == "LOSS_RATE":
                        try:
                            new_loss_rate = float(parts[1])
                            if new_loss_rate != self.data_loss_rate:
                                self.data_loss_rate = new_loss_rate
                                print(f"[SERVER] 🔄 Loss rate updated to: {new_loss_rate*100:.1f}%")
                        except (ValueError, IndexError):
                            pass
                        continue
                    
                    if not parts or parts[0] != "REQ":
                        continue
                    
                    sock.settimeout(0.001)
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
                            break
                    sock.settimeout(0.01)
                    
                    # ═══ PARSE REQUEST ═══
                    try:
                        if len(parts) < 3:
                            raise ValueError("Missing required fields")
                        
                        filename = os.path.basename(parts[1])
                        byte_start = int(parts[2])
                        
                        request = StreamRequest(
                            filename=filename,
                            byte_start=byte_start,
                            protocol=TransportProtocol.RUDP
                        )
                    except (ValueError, IndexError) as e:
                        sock.sendto(encode_control_message("ERR", f"BAD_REQUEST|{e}"), addr)
                        continue
                    
                    # ═══ RESOLVE FILE VIA REPOSITORY ═══
                    try:
                        filepath = self.file_repo.get_file_path(filename, quality="auto")
                    except FileNotFoundError:
                        print(f"[RUDP] File not found: {filename} from {addr}")
                        sock.sendto(b"ERR|NOT_FOUND", addr)
                        continue
                    except ValueError as e:
                        print(f"[RUDP] Invalid filename: {e}")
                        sock.sendto(b"ERR|BAD_REQUEST", addr)
                        continue
                    
                    # ═══ CREATE MANAGED SESSION ═══
                    try:
                        session_id = f"rudp_{addr[0]}_{addr[1]}_{int(time.time()*1000)}"
                        
                        rudp_session = RUDPSession(
                            filepath=filepath,
                            data_loss_rate=self.data_loss_rate,
                            ack_loss_rate=self.ack_loss_rate,
                            latency_ms=self.latency_ms,
                        )
                        
                        self.session_manager.create_session(
                            session_id=session_id,
                            protocol=TransportProtocol.RUDP,
                            client_addr=addr,
                            server=rudp_session
                        )
                        
                        def run_and_cleanup():
                            try:
                                rudp_session.handle_request(request, addr)
                            finally:
                                self.session_manager.close_session(session_id)
                        
                        threading.Thread(target=run_and_cleanup, daemon=True).start()
                        print(f"[RUDP] Created session {session_id} for {addr}")
                    
                    except ValueError as e:
                        print(f"[-] Session creation failed: {e}")
                        sock.sendto(encode_control_message("ERR", str(e)[:50]), addr)
                
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[-] RUDP dispatcher error: {e}")
        
        except OSError as e:
            print(f"[-] RUDP bind failed: {e}")
        
        finally:
            sock.close()
    
    def start(self):
        tcp_thread = threading.Thread(target=self._run_tcp, daemon=True)
        tcp_thread.start()
        self._run_rudp()


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    server = UnifiedServer()
    server.start()