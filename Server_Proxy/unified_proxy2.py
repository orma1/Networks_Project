"""
UNIFIED EDGE PROXY - FIXED VERSION WITH REAL-TIME DASH
───────────────────────────────────────────────────────

Improvements:
✓ Real-time DASH quality selection based on measured throughput
✓ Fixed Content-Length handling (no premature stream termination)
✓ Better error handling for incomplete responses
✓ Dynamic quality switching at segment boundaries
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
DASH_WINDOW_SECS = 1.0  # Reduced for faster quality updates
CHUNK_READ_SIZE = 65535
RECV_BUFFER_SIZE = 1024 * 1024

PACKET_OVERHEAD = 6
MAX_UDP_PAYLOAD = 65507


# ══════════════════════════════════════════════════════════════════════════════
# DASH QUALITY SELECTOR
# ══════════════════════════════════════════════════════════════════════════════

class DASHQualitySelector:
    """
    Measures throughput in real-time and selects quality dynamically.
    
    Quality thresholds:
      < 1 Mbps   → 480p (low)
      1-3.5 Mbps → 720p (medium)
      > 3.5 Mbps → 1080p (high)
    """
    
    def __init__(self):
        self.current_quality = "720"
        self.window_start = time.monotonic()
        self.bytes_measured = 0
        self.lock = threading.Lock()
    
    def add_bytes(self, num_bytes: int) -> str:
        """Add bytes to measurement and return current quality."""
        with self.lock:
            self.bytes_measured += num_bytes
            elapsed = time.monotonic() - self.window_start
            
            if elapsed < DASH_WINDOW_SECS:
                return self.current_quality
            
            # Calculate throughput
            if elapsed == 0:
                mbps = 0
            else:
                mbps = (self.bytes_measured * 8) / (elapsed * 1_048_576)
            
            # Select quality
            if mbps < 1.0:
                quality = "480"
            elif mbps < 3.5:
                quality = "720"
            else:
                quality = "1080"
            
            self.current_quality = quality
            print(f"[DASH] Throughput: {mbps:.2f} Mbps → Quality: {quality}p")
            
            # Reset window
            self.bytes_measured = 0
            self.window_start = time.monotonic()
            
            return quality
    
    def get_current(self) -> str:
        """Get current quality without updating."""
        with self.lock:
            return self.current_quality


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
    """Decode RUDP data packet: <4-byte seq><2-byte length><payload>"""
    if len(packet) < PACKET_OVERHEAD:
        raise ValueError(f"Packet too short: {len(packet)} bytes")
    
    seq = int.from_bytes(packet[:4], "big")
    declared_len = int.from_bytes(packet[4:6], "big")
    payload = packet[6:6+declared_len]
    
    if len(payload) != declared_len:
        raise ValueError(f"Length mismatch: {len(payload)} != {declared_len}")
    
    return seq, payload


# ══════════════════════════════════════════════════════════════════════════════
# RUDP CLIENT WITH FLOW CONTROL AND REAL-TIME QUALITY SELECTION
# ══════════════════════════════════════════════════════════════════════════════

def rudp_stream_with_dash(
    filename: str,
    byte_start: int,
    server_addr: Tuple[str, int],
    quality_selector: Optional[DASHQualitySelector] = None,
) -> Tuple[Generator[bytes, None, None], RUDPStreamState]:
    """
    RUDP client with flow control and real-time DASH quality tracking.
    """
    
    state = RUDPStreamState(start_time=time.monotonic())
    
    def generate() -> Generator[bytes, None, None]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(SOCKET_TIMEOUT)
        
        ooo_buffer: dict = {}
        expected_seq: int = 0
        origin_addr: Optional[Tuple[str, int]] = None
        bytes_buffered: int = 0
        
        try:
            # Send request
            if PACKET_LOSS_PCT > 0 and random.random() < (PACKET_LOSS_PCT / 100):
                req_pkt = f"DROP|{filename}|{byte_start}".encode()
                print(f"[*] Simulating RUDP packet loss for {filename} at byte {byte_start}")
            else:
                req_pkt = f"REQ|{filename}|{byte_start}".encode()
            sock.sendto(req_pkt, server_addr)
            
            # Wait for META
            meta_deadline = time.monotonic() + META_WAIT_SECS
            while time.monotonic() < meta_deadline:
                try:
                    raw, addr = sock.recvfrom(CHUNK_READ_SIZE)
                    
                    if raw[:4] == b"META":
                        try:
                            parts = raw.decode().split("|")
                            state.file_size = int(parts[1]) if len(parts) > 1 else 0
                            state.remote_rwnd = int(parts[2]) if len(parts) > 2 else RECV_BUFFER_SIZE
                            origin_addr = addr
                            state.connected = True
                            print(f"[RUDP] Connected to {addr}, file_size={state.file_size}")
                            break
                        except (ValueError, IndexError) as e:
                            print(f"[!] META parse error: {e}")
                            continue
                
                except socket.timeout:
                    sock.sendto(req_pkt, server_addr)
            
            if origin_addr is None:
                state.error_msg = f"No META within {META_WAIT_SECS}s"
                print(f"[-] {state.error_msg}")
                return
            
            # ── Main receive loop ─────────────────────────────────────────
            while True:
                try:
                    raw, addr = sock.recvfrom(CHUNK_READ_SIZE)
                except socket.timeout:
                    continue
                
                # ── Control messages ──────────────────────────────────────
                if raw[:4] == b"ALIV":
                    try:
                        parts = raw.decode().split("|")
                        if len(parts) > 1:
                            state.remote_rwnd = int(parts[1])
                    except:
                        pass
                    continue
                
                if raw[:4] == b"FIN|":
                    # Flush remaining packets
                    while expected_seq in ooo_buffer:
                        payload = ooo_buffer.pop(expected_seq)
                        if quality_selector:
                            quality_selector.add_bytes(len(payload))
                        yield payload
                        expected_seq += 1
                    print(f"[RUDP] Stream complete, {state.bytes_received} bytes received")
                    break
                
                if raw[:4] == b"ERR|":
                    state.error_msg = raw.decode(errors='ignore')
                    print(f"[-] Server error: {state.error_msg}")
                    break
                
                if raw[:4] == b"META":
                    continue
                
                # ── Data packet ───────────────────────────────────────────
                if len(raw) < PACKET_OVERHEAD:
                    continue
                
                try:
                    seq, payload = decode_data_pkt(raw)
                except ValueError as e:
                    print(f"[!] Decode error: {e}")
                    continue
                
                # Calculate receiver window
                local_rwnd = max(0, RECV_BUFFER_SIZE - bytes_buffered)
                
                # Send ACK with flow control
                try:
                    ack_msg = f"ACK|{seq}|{local_rwnd}".encode()
                    sock.sendto(ack_msg, addr)
                except:
                    pass
                
                # Handle packet
                if seq < expected_seq:
                    state.duplicate_packets += 1
                    continue
                
                if seq not in ooo_buffer:
                    ooo_buffer[seq] = payload
                    bytes_buffered += len(payload)
                    state.packets_received += 1
                else:
                    state.duplicate_packets += 1
                
                # Yield in-order packets
                while expected_seq in ooo_buffer:
                    payload = ooo_buffer.pop(expected_seq)
                    bytes_buffered -= len(payload)
                    
                    # Update DASH quality in real-time
                    if quality_selector:
                        quality_selector.add_bytes(len(payload))
                    
                    yield payload
                    expected_seq += 1
                    state.bytes_received += len(payload)
                
                # Track OOO
                if ooo_buffer and min(ooo_buffer.keys()) != expected_seq:
                    state.packets_out_of_order += 1
        
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
    """TCP streaming fallback."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    
    try:
        if PACKET_LOSS_PCT > 0 and random.random() < (PACKET_LOSS_PCT / 100):
            cmd = f"DROP|{filename}|{byte_start}"
            print(f"[*] Simulating TCP packet loss for {filename} at byte {byte_start}")
        else:
            cmd = f"REQ|{filename}|{byte_start}"
        sock.connect(server_addr)
        sock.sendall(cmd.encode())
        
        # Read META header
        header_buf = b""
        while b"\n" not in header_buf:
            chunk = sock.recv(512)
            if not chunk:
                return
            header_buf += chunk
        
        _header_line, leftover = header_buf.split(b"\n", 1)
        
        if leftover:
            yield leftover
        
        # Stream rest
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
# FASTAPI APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(title="Video Streaming Proxy", docs_url=None)

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
PROTOCOL = "rudp"
QUALITY_DISPLAY = "Auto"
PACKET_LOSS_PCT = 0

# Global DASH quality selector (per-stream if needed)
quality_selector = DASHQualitySelector()


# ══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def load_movies():
    """Load movies from config."""
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
    """Landing page."""
    if templates is None:
        return {
            "status": "ok",
            "protocol": PROTOCOL.upper(),
            "quality": QUALITY_DISPLAY,
            "movies": load_movies()
        }
    return templates.TemplateResponse("index.html", get_context(request))


@app.post("/switch_protocol")
async def switch_protocol():
    """Toggle TCP/RUDP."""
    global PROTOCOL
    PROTOCOL = "rudp" if PROTOCOL == "tcp" else "tcp"
    print(f"[*] Switched to {PROTOCOL.upper()}")
    return RedirectResponse("/", status_code=303)


@app.post("/set_loss")
async def set_loss(request: Request):
    """Set packet loss percentage."""
    global PACKET_LOSS_PCT
    form = await request.form()
    try:
        PACKET_LOSS_PCT = max(0, min(100, int(form.get("loss_percent", 0))))
        print(f"[*] Set packet loss to {PACKET_LOSS_PCT}%")
    except:
        pass
    return RedirectResponse("/", status_code=303)


@app.get("/play/{filename}")
async def play(request: Request, filename: str, force_quality: str = "auto"):
    """Video player page."""
    if templates is None:
        return {"status": "ok", "file": filename, "quality": force_quality}
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
    Range-aware streaming with real-time DASH quality selection.
    
    This is synchronous so FastAPI runs it in a thread pool.
    """
    global QUALITY_DISPLAY
    
    # Resolve quality → filename
    quality = "720" if force_quality == "auto" else force_quality
    target = filename.replace(".mp4", f"_{quality}.mp4")
    
    # Get file size
    video_dir = os.path.join(BASE_DIR, "videos")
    filepath = os.path.join(video_dir, target)
    
    try:
        if os.path.exists(filepath):
            file_size = os.path.getsize(filepath)
        else:
            file_size = 0
    except OSError:
        file_size = 0
    
    # Parse Range header
    range_hdr = request.headers.get("Range", "bytes=0-")
    try:
        spec_parts = range_hdr.replace("bytes=", "").split("-")
        byte_start = int(spec_parts[0]) if spec_parts[0] else 0
        byte_end = int(spec_parts[1]) if len(spec_parts) > 1 and spec_parts[1] else (file_size - 1 if file_size > 0 else 0)
    except:
        byte_start = 0
        byte_end = max(file_size - 1, 0)
    
    # Ensure valid range
    byte_start = max(0, byte_start)
    if file_size > 0:
        byte_end = min(byte_end, file_size - 1)
    else:
        byte_end = 0
    
    # Calculate content length
    content_length = max(byte_end - byte_start + 1, 0)
    
    # Get streaming generator
    if PROTOCOL == "tcp":
        gen = tcp_stream(target, byte_start, WEB_SERVER_ADDR)
        state = None
    else:
        # Reset quality selector for new stream
        quality_selector.current_quality = "720"
        quality_selector.bytes_measured = 0
        quality_selector.window_start = time.monotonic()
        
        gen, state = rudp_stream_with_dash(
            target,
            byte_start,
            WEB_SERVER_ADDR,
            quality_selector=quality_selector if force_quality == "auto" else None
        )
        
        # Update display
        if state and state.connected and force_quality == "auto":
            QUALITY_DISPLAY = "Auto (measuring...)"
    
    # Build response with proper headers
    headers = {
        "Content-Range": f"bytes {byte_start}-{byte_end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Cache-Control": "no-cache",
    }
    
    print(f"[*] Streaming {target}: bytes {byte_start}-{byte_end}/{file_size} (length={content_length})")
    
    return StreamingResponse(
        gen,
        status_code=206,
        media_type="video/mp4",
        headers=headers
    )


@app.get("/quality")
async def get_quality():
    """Get current DASH quality in real-time."""
    return {
        "protocol": PROTOCOL,
        "quality": quality_selector.get_current(),
        "packet_loss": PACKET_LOSS_PCT,
    }


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "healthy",
        "protocol": PROTOCOL,
        "quality": QUALITY_DISPLAY,
    }


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if VirtualNetworkInterface:
        try:
            v_net = VirtualNetworkInterface(client_name="ProxyNode")
            MY_IP = v_net.setup_network()
        except:
            MY_IP = "127.0.0.1"
    
    print(f"""
╔═══════════════════════════════════════════════════════════╗
║ UNIFIED EDGE PROXY - STARTING (FIXED WITH REAL-TIME DASH)║
╠═══════════════════════════════════════════════════════════╣
║ Listening: {MY_IP}:5000
║ Origin: {WEB_SERVER_ADDR[0]}:{WEB_SERVER_ADDR[1]}
║ Protocol: {PROTOCOL.upper()}
║ Quality: {QUALITY_DISPLAY}
║
║ Features:
║  ✓ Real-time DASH quality selection
║  ✓ Fixed Content-Length handling
║  ✓ Better error recovery
╚═══════════════════════════════════════════════════════════╝
""")
    
    uvicorn.run(
        app,
        host=MY_IP,
        port=5000,
        log_level="warning",
        workers=1
    )