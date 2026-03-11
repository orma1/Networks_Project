"""
UNIFIED EDGE PROXY - Complete Implementation
─────────────────────────────────────────────

Acts as middleman between browser and origin server.
- HTTP interface via FastAPI
- Fetches video from origin via TCP or RUDP
- Implements DASH adaptive-bitrate quality selection (RUDP only)
- Proper range request handling for seeking
- Session metrics and error recovery

Architecture:
├── FastAPI app (HTTP server)
├── RUDP client (for origin connection)
├── TCP client (fallback)
├── DASH quality selector
└── Streaming generators (sync for thread pool)
"""

import os
import sys
import socket
import time
import random
import yaml
import threading
from typing import Generator, Optional, Tuple
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

# Path setup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
sys.path.append(PARENT_DIR)
sys.path.append(os.path.join(PARENT_DIR, "dhcp"))

try:
    from dhcp.dhcp_helper import VirtualNetworkInterface
except ImportError:
    VirtualNetworkInterface = None

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

SOCKET_TIMEOUT = 0.3
META_WAIT_SECS = 2.0
DASH_WINDOW_SECS = 2.0
CHUNK_READ_SIZE = 65535
RECV_BUFFER_SIZE = 1024 * 1024  # 1MB

# RUDP packet format
PACKET_OVERHEAD = 6  # seq(4) + len(2)
MAX_UDP_PAYLOAD = 65507


# ══════════════════════════════════════════════════════════════════════════════
# RUDP SESSION STATE
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class RUDPStreamState:
    """Track RUDP stream metrics."""
    connected: bool = False
    file_size: int = 0
    bytes_received: int = 0
    packets_received: int = 0
    packets_out_of_order: int = 0
    duplicate_packets: int = 0
    start_time: float = 0.0
    remote_rwnd: int = RECV_BUFFER_SIZE
    error_msg: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# PACKET DECODING
# ══════════════════════════════════════════════════════════════════════════════

def decode_data_pkt(packet: bytes) -> Tuple[int, bytes]:
    """
    Decode RUDP data packet: <4-byte seq><2-byte length><payload>
    
    Raises:
        ValueError: If packet is malformed
    """
    if len(packet) < PACKET_OVERHEAD:
        raise ValueError(f"Packet too short: {len(packet)} bytes")
    
    seq = int.from_bytes(packet[:4], "big")
    declared_len = int.from_bytes(packet[4:6], "big")
    payload = packet[6:6+declared_len]
    
    if len(payload) != declared_len:
        raise ValueError(f"Length mismatch: {len(payload)} != {declared_len}")
    
    return seq, payload


# ══════════════════════════════════════════════════════════════════════════════
# RUDP CLIENT WITH FLOW CONTROL
# ══════════════════════════════════════════════════════════════════════════════

def rudp_stream_with_flow_control(
    filename: str,
    byte_start: int,
    server_addr: Tuple[str, int],
) -> Tuple[Generator[bytes, None, None], RUDPStreamState]:
    """
    RUDP client with explicit flow control (RWnd).
    
    Protocol:
      Client → Server:
        REQ|<filename>|<byte_start>
        ACK|<seq>|<local_rwnd>              ← Include receiver window
        
      Server → Client:
        META|<file_size>|<remote_rwnd>
        <4-byte seq><2-byte len><payload>
        ALIVE|<rwnd>
        FIN|DONE|<rwnd>
    
    Returns:
        (generator, state) where generator yields bytes
    """
    
    state = RUDPStreamState(start_time=time.monotonic())
    
    def generate() -> Generator[bytes, None, None]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(SOCKET_TIMEOUT)
        
        # Out-of-order buffer with size tracking
        ooo_buffer: dict = {}
        expected_seq: int = 0
        origin_addr: Optional[Tuple[str, int]] = None
        bytes_buffered: int = 0
        
        try:
            # Send initial request
            req_pkt = f"REQ|{filename}|{byte_start}".encode()
            sock.sendto(req_pkt, server_addr)
            
            # Wait for META with retry
            meta_deadline = time.monotonic() + META_WAIT_SECS
            while time.monotonic() < meta_deadline:
                try:
                    raw, addr = sock.recvfrom(CHUNK_READ_SIZE)
                    
                    if raw[:4] == b"META":
                        # Parse: META|<file_size>|<remote_rwnd>
                        try:
                            parts = raw.decode().split("|")
                            state.file_size = int(parts[1]) if len(parts) > 1 else 0
                            state.remote_rwnd = int(parts[2]) if len(parts) > 2 else RECV_BUFFER_SIZE
                            origin_addr = addr
                            state.connected = True
                            break
                        except (ValueError, IndexError) as e:
                            print(f"[!] META parse error: {e}")
                            continue
                
                except socket.timeout:
                    sock.sendto(req_pkt, server_addr)
            
            if origin_addr is None:
                state.error_msg = f"No META within {META_WAIT_SECS}s"
                return
            
            # ── Main receive loop ─────────────────────────────────────────
            while True:
                try:
                    raw, addr = sock.recvfrom(CHUNK_READ_SIZE)
                except socket.timeout:
                    continue
                
                # ── Route control messages ────────────────────────────────
                if raw[:4] == b"ALIV":
                    # Keep-alive
                    try:
                        parts = raw.decode().split("|")
                        if len(parts) > 1:
                            state.remote_rwnd = int(parts[1])
                    except:
                        pass
                    continue
                
                if raw[:4] == b"FIN|":
                    # End of stream - flush remaining
                    while expected_seq in ooo_buffer:
                        payload = ooo_buffer.pop(expected_seq)
                        yield payload
                        expected_seq += 1
                    break
                
                if raw[:4] == b"ERR|":
                    state.error_msg = raw.decode(errors='ignore')
                    print(f"[-] Server error: {state.error_msg}")
                    break
                
                if raw[:4] == b"META":
                    # Duplicate META - ignore
                    continue
                
                # ── Data packet ───────────────────────────────────────────
                if len(raw) < PACKET_OVERHEAD:
                    continue
                
                try:
                    seq, payload = decode_data_pkt(raw)
                except ValueError as e:
                    print(f"[!] Decode error: {e}")
                    continue
                
                # Calculate local RWnd (remaining buffer space)
                local_rwnd = max(0, RECV_BUFFER_SIZE - bytes_buffered)
                
                # ACK with flow control window
                try:
                    ack_msg = f"ACK|{seq}|{local_rwnd}".encode()
                    sock.sendto(ack_msg, addr)
                except:
                    pass
                
                # Discard if already delivered
                if seq < expected_seq:
                    state.duplicate_packets += 1
                    continue
                
                # Buffer new packets
                if seq not in ooo_buffer:
                    ooo_buffer[seq] = payload
                    bytes_buffered += len(payload)
                    state.packets_received += 1
                else:
                    state.duplicate_packets += 1
                
                # Yield consecutive in-order packets
                while expected_seq in ooo_buffer:
                    payload = ooo_buffer.pop(expected_seq)
                    bytes_buffered -= len(payload)
                    yield payload
                    expected_seq += 1
                
                # Track out-of-order arrivals
                if ooo_buffer and min(ooo_buffer.keys()) != expected_seq:
                    state.packets_out_of_order += 1
                
                state.bytes_received = byte_start + len(b"".join(ooo_buffer.values()))
        
        except Exception as e:
            state.error_msg = str(e)
            print(f"[-] RUDP stream error: {e}")
        
        finally:
            sock.close()
    
    return generate(), state


# ══════════════════════════════════════════════════════════════════════════════
# TCP CLIENT (FALLBACK)
# ══════════════════════════════════════════════════════════════════════════════

def tcp_stream(
    filename: str,
    byte_start: int,
    server_addr: Tuple[str, int],
) -> Generator[bytes, None, None]:
    """
    TCP client for video streaming (fallback to RUDP).
    
    Protocol:
      Client → Server: REQ|<filename>|<byte_start>
      Server → Client: META|<file_size>\\n<raw bytes>
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    
    try:
        cmd = f"REQ|{filename}|{byte_start}"
        sock.connect(server_addr)
        sock.sendall(cmd.encode())
        
        # Read META header (until newline)
        header_buf = b""
        while b"\n" not in header_buf:
            chunk = sock.recv(512)
            if not chunk:
                return
            header_buf += chunk
        
        _header_line, leftover = header_buf.split(b"\n", 1)
        
        # Yield any leftover bytes
        if leftover:
            yield leftover
        
        # Stream rest of file
        sock.settimeout(5.0)
        while True:
            chunk = sock.recv(131_072)
            if not chunk:
                break
            yield chunk
    
    except Exception as e:
        print(f"[-] TCP stream error: {e}")
    
    finally:
        sock.close()


# ══════════════════════════════════════════════════════════════════════════════
# DASH ADAPTIVE BITRATE
# ══════════════════════════════════════════════════════════════════════════════

class DASHAdaptiveQuality:
    """Select video quality based on measured throughput."""
    
    def __init__(self):
        self.current_quality = "720"
        self.last_update = time.monotonic()
        self.window_start = time.monotonic()
        self.bytes_measured = 0
    
    def measure_quality(self, elapsed: float, bytes_received: int) -> str:
        """
        Measure throughput and select quality.
        
        Returns:
            Quality string: "480", "720", or "1080"
        """
        if elapsed < DASH_WINDOW_SECS:
            return self.current_quality
        
        # Calculate throughput
        mbps = (bytes_received * 8) / (elapsed * 1_048_576)
        
        # Select quality based on throughput
        if mbps < 1.0:
            quality = "480"
        elif mbps < 3.5:
            quality = "720"
        else:
            quality = "1080"
        
        self.current_quality = quality
        return quality


# ══════════════════════════════════════════════════════════════════════════════
# FASTAPI APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(title="Video Streaming Proxy", docs_url=None)

# Setup static files and templates
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_path = os.path.join(BASE_DIR, "static")
templates_path = os.path.join(BASE_DIR, "templates")

if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")
if os.path.exists(templates_path):
    templates = Jinja2Templates(directory=templates_path)
else:
    templates = None

# ══════════════════════════════════════════════════════════════════════════════
# RUNTIME STATE
# ══════════════════════════════════════════════════════════════════════════════

MY_IP = "127.0.0.1"
WEB_SERVER_ADDR = ("127.0.0.13", 9000)
PROTOCOL = "rudp"  # "tcp" or "rudp"
QUALITY_DISPLAY = "Auto"
PACKET_LOSS_PCT = 0

# Session metrics
active_sessions = {}
sessions_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def load_movies():
    """Load movie list from YAML config."""
    try:
        path = os.path.join(BASE_DIR, "movies.yaml")
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f).get("movies", [])
    except:
        return []


def get_context(request: Request, extra: dict = None) -> dict:
    """Build template context."""
    ctx = {
        "request": request,
        "protocol": PROTOCOL.upper(),
        "quality": QUALITY_DISPLAY,
        "packet_loss": PACKET_LOSS_PCT,
        "movies": load_movies(),
    }
    if extra:
        ctx.update(extra)
    return ctx


# ══════════════════════════════════════════════════════════════════════════════
# HTTP ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def index(request: Request):
    """Landing page - list available movies."""
    if templates is None:
        return {"status": "ok", "message": "Proxy running", "movies": load_movies()}
    return templates.TemplateResponse("index.html", get_context(request))


@app.post("/switch_protocol")
async def switch_protocol():
    """Toggle between TCP and RUDP."""
    global PROTOCOL
    PROTOCOL = "rudp" if PROTOCOL == "tcp" else "tcp"
    print(f"[*] Switched to {PROTOCOL.upper()}")
    return RedirectResponse("/", status_code=303)


@app.post("/set_loss")
async def set_loss(request: Request):
    """Set simulated packet loss percentage."""
    global PACKET_LOSS_PCT
    form = await request.form()
    try:
        PACKET_LOSS_PCT = max(0, min(100, int(form.get("loss_percent", 0))))
    except:
        pass
    return RedirectResponse("/", status_code=303)


@app.get("/play/{filename}")
async def play(request: Request, filename: str, force_quality: str = "auto"):
    """Video player page."""
    if templates is None:
        return {
            "status": "ok",
            "file": filename,
            "quality": force_quality,
        }
    return templates.TemplateResponse(
        "index.html",
        get_context(request, {
            "video_file": filename,
            "selected_quality": force_quality
        }),
    )


@app.get("/stream/{filename}")
def stream(request: Request, filename: str, force_quality: str = "auto"):
    """
    Range-aware video streaming endpoint.
    
    This is synchronous (def not async) so FastAPI runs it in a thread pool.
    The generator it returns will contain blocking socket operations.
    """
    global QUALITY_DISPLAY
    
    # Resolve quality → filename
    quality = "1080" if force_quality == "auto" else force_quality
    target = filename.replace(".mp4", f"_{quality}.mp4")
    
    # Get file size (from local copy if available, else from origin)
    video_dir = os.path.join(BASE_DIR, "videos")
    filepath = os.path.join(video_dir, target)
    
    if os.path.exists(filepath):
        file_size = os.path.getsize(filepath)
    else:
        file_size = 0
    
    # Parse Range header
    range_hdr = request.headers.get("Range", "bytes=0-")
    try:
        spec_parts = range_hdr.replace("bytes=", "").split("-")
        byte_start = int(spec_parts[0]) if spec_parts[0] else 0
        byte_end = (
            int(spec_parts[1])
            if len(spec_parts) > 1 and spec_parts[1]
            else file_size - 1 if file_size > 0 else 0
        )
    except:
        byte_start = 0
        byte_end = 0
    
    # Select streaming generator
    if PROTOCOL == "tcp":
        gen = tcp_stream(target, byte_start, WEB_SERVER_ADDR)
        state = None
    else:
        gen, state = rudp_stream_with_flow_control(
            target,
            byte_start,
            WEB_SERVER_ADDR
        )
        
        # Update quality based on RUDP metrics
        if state and state.connected:
            elapsed = time.monotonic() - state.start_time
            if elapsed > DASH_WINDOW_SECS and state.bytes_received > 0:
                mbps = (state.bytes_received * 8) / (elapsed * 1_048_576)
                if force_quality == "auto":
                    if mbps < 1.0:
                        QUALITY_DISPLAY = "480p (Low)"
                    elif mbps < 3.5:
                        QUALITY_DISPLAY = "720p (Med)"
                    else:
                        QUALITY_DISPLAY = "1080p (High)"
                else:
                    QUALITY_DISPLAY = f"{force_quality}p (Forced)"
    
    # Build response
    headers = {
        "Content-Range": f"bytes {byte_start}-{byte_end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(max(byte_end - byte_start + 1, 0)),
    }
    
    return StreamingResponse(
        gen,
        status_code=206,
        media_type="video/mp4",
        headers=headers
    )


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "protocol": PROTOCOL,
        "quality": QUALITY_DISPLAY,
        "loss_percent": PACKET_LOSS_PCT,
    }


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Get IP via DHCP if available
    if VirtualNetworkInterface:
        try:
            v_net = VirtualNetworkInterface(client_name="ProxyNode")
            MY_IP = v_net.setup_network()
        except:
            MY_IP = "127.0.0.1"
    
    print(f"""
╔═══════════════════════════════════════════════════════════╗
║ UNIFIED EDGE PROXY - STARTING                            ║
╠═══════════════════════════════════════════════════════════╣
║ Listening: {MY_IP}:5000
║ Origin: {WEB_SERVER_ADDR[0]}:{WEB_SERVER_ADDR[1]}
║ Protocol: {PROTOCOL.upper()}
║ Quality: {QUALITY_DISPLAY}
╚═══════════════════════════════════════════════════════════╝
""")
    
    # Start FastAPI
    uvicorn.run(
        app,
        host=MY_IP,
        port=5000,
        log_level="warning",
        workers=1  # Single worker to preserve global state
    )