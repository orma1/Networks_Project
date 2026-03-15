"""
UNIFIED ORIGIN SERVER - Complete Implementation
────────────────────────────────────────────────
Serves video files to proxy/clients over TCP and RUDP (Reliable UDP).
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(BASE_DIR, "../.."))
from Application.shared.protocol_utils import (
    encode_data_packet, decode_data_packet, encode_control_message,
    RECOMMENDED_CHUNK_SIZE, DEFAULT_RECV_BUFFER_SIZE
)
from Application.shared.streaming_interfaces import (
    StreamingServer, StreamRequest, StreamMetrics, TransportProtocol, StreamState,
)

from Application.server.sliding_window import SlidingWindow, WindowEntry
from Application.server.congestion_controller import CongestionController, CongestionState
from Application.server.flow_controller import FlowController, calculate_combined_limit
from Application.server.file_repository import LocalFileRepository
from Application.client.session_manager import SimpleSessionManager, SessionInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, "shared", "videos")
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
sys.path.append(PARENT_DIR)
sys.path.append(os.path.join(PARENT_DIR, "dhcp"))

try:
    from dhcp.dhcp_helper import VirtualNetworkInterface
except ImportError:
    VirtualNetworkInterface = None

# CONSTANTS
CHUNK_SIZE = RECOMMENDED_CHUNK_SIZE
RECV_WINDOW_SIZE = DEFAULT_RECV_BUFFER_SIZE
INIT_CWND = 4.0
INIT_SSTHRESH = 64.0
RTO = 0.25  
DUPACK_THRESH = 3  
KEEPALIVE_SECS = 5.0  
ACK_POLL_TIMEOUT = 0.002  
SESSION_IDLE_TIMEOUT = 30.0  
DATA_LOSS_RATE = 0.0  
ACK_LOSS_RATE = 0.0  
LATENCY_MS = 0  

@dataclass
class RUDPMetrics:
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
        return 0.0 if duration == 0 else (self.bytes_sent * 8) / (duration * 1_048_576)
    def loss_rate(self) -> float:
        return 0.0 if self.packets_sent == 0 else (self.packets_retransmitted / self.packets_sent)


class RUDPSession(StreamingServer):
    
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
        
        # 🔥 RESTORED: Dedicated socket so we don't fight the dispatcher thread
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(ACK_POLL_TIMEOUT)
        
        self.window = SlidingWindow(initial_base=0)
        self.congestion = CongestionController(initial_cwnd=INIT_CWND, initial_ssthresh=INIT_SSTHRESH)
        self.flow = FlowController(initial_rwnd=RECV_WINDOW_SIZE, packet_size=CHUNK_SIZE)
        
        self.last_ack_time: float = time.monotonic()
        self.eof: bool = False
        self.active: bool = True
        
        self._metrics = StreamMetrics(connection_state=StreamState.IDLE)
        self.metrics = RUDPMetrics()
        
    def handle_request(self, request: StreamRequest, client_addr: Tuple[str, int]) -> None:
        self.client_addr = client_addr
        self.byte_start = request.byte_start
        self._metrics.connection_state = StreamState.CONNECTING
        self.run()
    
    def get_metrics(self) -> StreamMetrics:
        self._metrics.packets_sent = self.metrics.packets_sent
        self._metrics.packets_retransmitted = self.metrics.packets_retransmitted
        self._metrics.bytes_transferred = self.metrics.bytes_sent
        self._metrics.packets_lost = self.metrics.packets_lost_simulated
        if self.metrics.duration() > 0:
            self._metrics.average_throughput_mbps = self.metrics.throughput_mbps()
        return self._metrics
    
    def close(self) -> None:
        self.active = False
        # 🔥 RESTORED: Close the dedicated socket to prevent memory leaks
        if hasattr(self, 'sock'):
            self.sock.close()
        self._metrics.connection_state = StreamState.CLOSED
    
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
            return False
        
        self._apply_latency()
        try:
            self.sock.sendto(pkt, self.client_addr)
            if not is_ack:
                self.metrics.packets_sent += 1
            return True
        except OSError as e:
            self.metrics.errors += 1
            self.active = False
            return False
    
    def _drain_acks(self):
        try:
            while True:
                raw, _ = self.sock.recvfrom(256)
                msg = raw.decode(errors="ignore").strip()
                if not msg.startswith("ACK|"):
                    continue
                try:
                    parts = msg.split("|")
                    if len(parts) < 2: continue
                    seq = int(parts[1])
                    rwnd = int(parts[2]) if len(parts) > 2 else RECV_WINDOW_SIZE
                except (ValueError, IndexError):
                    continue
                
                self.flow.update_rwnd(rwnd)
                is_new, dup_count = self.window.process_ack(seq)
                
                if is_new:
                    self.congestion.on_ack_received()
                    self.last_ack_time = time.monotonic()
                    self.metrics.packets_acked += 1
                elif dup_count >= DUPACK_THRESH:
                    self.congestion.on_duplicate_ack()
                    packet = self.window.get_packet(self.window.base)
                    if packet:
                        self._send(packet)
                        self.window.mark_retransmitted(self.window.base)
                        self.metrics.packets_retransmitted += 1
        except socket.timeout:
            pass
        except OSError:
            pass
    
    def run(self):
        self._metrics.connection_state = StreamState.STREAMING
        try:
            meta_pkt = encode_control_message("META", self.file_size, RECV_WINDOW_SIZE)
            for _ in range(5):
                self._send(meta_pkt)
            time.sleep(0.03)
            
            with open(self.filepath, "rb") as fh:
                fh.seek(self.byte_start)
                
                while self.active:
                    if time.monotonic() - self.last_ack_time > KEEPALIVE_SECS:
                        self._send(encode_control_message("ALIVE", RECV_WINDOW_SIZE))
                        self.last_ack_time = time.monotonic()
                    
                    if time.monotonic() - self.last_ack_time > SESSION_IDLE_TIMEOUT:
                        break
                    
                    max_in_flight = calculate_combined_limit(
                        self.congestion.get_sending_limit(),
                        self.flow.get_sending_limit()
                    )
                    
                    while self.active and not self.eof and self.window.has_capacity(max_in_flight):
                        chunk = fh.read(CHUNK_SIZE)
                        if not chunk:
                            self.eof = True
                            break
                        try:
                            pkt = encode_data_packet(self.window.next_seq, chunk)
                            seq = self.window.add_packet(pkt)
                            if self._send(pkt, is_ack=False):
                                self.metrics.bytes_sent += len(chunk)
                        except ValueError:
                            self.active = False
                            break
                    
                    self._drain_acks()
                    removed = self.window.slide_window()
                    self.metrics.window_max_size = max(self.metrics.window_max_size, self.window.size)
                    
                    timed_out = self.window.get_timed_out_packets(timeout_seconds=RTO)
                    if timed_out:
                        seq, packet = timed_out[0]
                        self.congestion.on_timeout()
                        self._send(packet, is_ack=False)
                        self.window.mark_retransmitted(seq)
                        self.metrics.packets_retransmitted += 1
                    
                    if self.eof and self.window.is_empty():
                        break
                    
                    time.sleep(0.0001)
        except Exception as e:
            self._metrics.connection_state = StreamState.ERROR
            self._metrics.error_message = str(e)
            self.metrics.errors += 1
        finally:
            self._metrics.connection_state = StreamState.CLOSED
            for _ in range(5):
                self._send(encode_control_message("FIN", "DONE", 0))
                time.sleep(0.01)
            self._print_summary()
    
    def _print_summary(self):
        m = self.metrics
        c_metrics = self.congestion.get_metrics()
        f_metrics = self.flow.get_metrics()
        print(f"""
[RUDP] Session Summary for {self.client_addr}
  ├─ Duration: {m.duration():.2f} seconds
  ├─ Bytes sent: {m.bytes_sent}
  ├─ Packets sent: {m.packets_sent}
  ├─ Packets ACKed: {m.packets_acked}
  ├─ Retransmitted: {m.packets_retransmitted}
  ├─ Throughput: {m.throughput_mbps():.2f} Mbps
  ├─ Loss rate: {m.loss_rate()*100:.1f}%
  ├─ Cwnd: {c_metrics.current_cwnd:.1f}
  └─ RWnd: {f_metrics.current_rwnd}
""")

class TCPHandler:
    def __init__(self, server): self.server = server
    def handle(self, conn: socket.socket, addr: Tuple[str, int]):
        try:
            conn.settimeout(10.0)
            req_data = conn.recv(4096).decode(errors="ignore")
            parts = req_data.split("|")
            if len(parts) < 3 or parts[0] != "REQ":
                return conn.sendall(b"ERR|BAD_REQUEST\n")
            
            filename = os.path.basename(parts[1])
            byte_start = max(0, int(parts[2]))
            
            try:
                filepath = self.server.file_repo.get_file_path(filename, quality="auto")
                file_size = self.server.file_repo.get_file_size(filename, quality="auto")
            except FileNotFoundError:
                return conn.sendall(b"ERR|NOT_FOUND\n")
            
            conn.sendall(f"META|{file_size}\n".encode())
            with open(filepath, "rb") as fh:
                fh.seek(byte_start)
                while True:
                    chunk = fh.read(131_072)
                    if not chunk: break
                    conn.sendall(chunk)
        except Exception:
            pass
        finally:
            conn.close()

class UnifiedServer:
    def __init__(self, config_file = os.path.join(PARENT_DIR, "shared", "configs", "movies.yaml")):

        self.active = True
        signal.signal(signal.SIGINT, self._on_sigint)
        
        try:
            with open(config_file, "r") as f:
                    config = yaml.safe_load(f)
                    self.port = config.get("server_config", {}).get("origin_port", 9000)
            current_script_dir = os.path.dirname(os.path.abspath(__file__))
            
            # Build path to shared/videos relative to the script location
            default_video_dir = os.path.abspath(os.path.join(current_script_dir, "..", "shared", "videos"))

            # Use the config value if it exists, otherwise use the absolute path we just built
            video_dir = config.get("server_config", {}).get("base_directory", default_video_dir)
        except Exception as e:
            print(f"[!] Failed to load config, using defaults: {e}")
            self.port, video_dir = 9000, os.path.join(BASE_DIR, "shared", "videos")
            
        self.my_ip = VirtualNetworkInterface(client_name="OriginServer", fixed_id="OriginServer").setup_network() if VirtualNetworkInterface else "127.0.0.1"
        self.data_loss_rate = float(os.environ.get("DATA_LOSS_RATE", DATA_LOSS_RATE))
        self.ack_loss_rate = float(os.environ.get("ACK_LOSS_RATE", ACK_LOSS_RATE))
        self.latency_ms = int(os.environ.get("LATENCY_MS", LATENCY_MS))



        try:
            self.file_repo = LocalFileRepository(default_video_dir)
        except Exception as e:
            print(f"[!] Failed to initialize file repository: {e}")
            self.file_repo = None

        self.session_manager = SimpleSessionManager(max_sessions=100)
        
        def cleanup_loop():
            while self.active:
                time.sleep(30)
                self.session_manager.cleanup_idle_sessions(timeout_seconds=60.0)
        threading.Thread(target=cleanup_loop, daemon=True).start()

        print(f"║ UNIFIED ORIGIN SERVER LISTENING ON {self.my_ip}:{self.port} ║")
    
    def _on_sigint(self, signum, frame):
        self.active = False
        sys.exit(0)
    
    def _run_tcp(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((self.my_ip, self.port))
            srv.listen(20)
            handler = TCPHandler(self)
            while self.active:
                try:
                    conn, addr = srv.accept()
                    threading.Thread(target=handler.handle, args=(conn, addr), daemon=True).start()
                except socket.timeout: continue
        except OSError: pass
        finally: srv.close()
    
    def _run_rudp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.my_ip, self.port))
            sock.settimeout(0.01)
            while self.active:
                try:
                    data, addr = sock.recvfrom(4096)
                    msg = data.decode(errors="ignore")
                    parts = msg.split("|")
                    
                    if parts and parts[0] == "LOSS_RATE":
                        self.data_loss_rate = float(parts[1])
                        continue
                    if not parts or parts[0] != "REQ":
                        continue
                    
                    filename = os.path.basename(parts[1])
                    byte_start = int(parts[2])
                    request = StreamRequest(filename=filename, byte_start=byte_start, protocol=TransportProtocol.RUDP)
                    filepath = self.file_repo.get_file_path(filename, quality="auto")
                    
                    # 🔥 RESTORED: Create the session WITHOUT passing the dispatcher sock!
                    session_id = f"rudp_{addr[0]}_{addr[1]}_{int(time.time()*1000)}"
                    rudp_session = RUDPSession(
                        filepath=filepath,
                        client_addr=addr,
                        data_loss_rate=self.data_loss_rate,
                        ack_loss_rate=self.ack_loss_rate,
                        latency_ms=self.latency_ms,
                    )
                    self.session_manager.create_session(session_id, TransportProtocol.RUDP, addr, rudp_session)
                    
                    def run_and_cleanup():
                        try: rudp_session.handle_request(request, addr)
                        finally: self.session_manager.close_session(session_id)
                    
                    threading.Thread(target=run_and_cleanup, daemon=True).start()
                except (socket.timeout, FileNotFoundError, ValueError):
                    continue
        finally:
            sock.close()
    
    def start(self):
        threading.Thread(target=self._run_tcp, daemon=True).start()
        self._run_rudp()

if __name__ == "__main__":
    server = UnifiedServer()
    server.start()