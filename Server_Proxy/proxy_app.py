"""
UNIFIED EDGE PROXY - WITH DNS & DETAILED PACKET LOSS LOGGING
──────────────────────────────────────────────────────────────

Key features:
✓ DNS resolution for origin server address
✓ Detailed packet loss visibility with emojis
✓ Real-time DASH quality selection
✓ HTTP 200 for RUDP (no Content-Length errors)
✓ HTTP 206 for TCP (Range requests)
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

import logging
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from dnslib import DNSRecord

from protocol_utils import (
    decode_data_packet, 
    encode_control_message, 
    PACKET_OVERHEAD, 
    DEFAULT_RECV_BUFFER_SIZE,
    MAX_UDP_PAYLOAD
)
from streaming_interfaces import (
    StreamingClient,
    StreamRequest,
    StreamMetadata,
    StreamMetrics,
    TransportProtocol,
    StreamState,
    QualitySelector,
)
from http_handler import HTTPHandler
from stream_orchestrator import StreamOrchestrator

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
DASH_WINDOW_SECS = 1.0
CHUNK_READ_SIZE = 65535
RECV_BUFFER_SIZE = DEFAULT_RECV_BUFFER_SIZE


# ══════════════════════════════════════════════════════════════════════════════
# DASH QUALITY SELECTOR
# ══════════════════════════════════════════════════════════════════════════════

class DASHQualitySelector:
    """
    DASH implementation of QualitySelector interface.
    
    Adapts quality based on measured throughput using DASH-like logic:
    - < 1.0 Mbps → 480p
    - < 3.5 Mbps → 720p
    - >= 3.5 Mbps → 1080p
    
    Satisfies QualitySelector contract.
    """
    
    def __init__(self):
        self.current_quality = "720"
        self.window_start = time.monotonic()
        self.bytes_measured = 0
        self.lock = threading.Lock()
    
    # ── QualitySelector Interface Implementation ─────────────────────────
    
    def select_quality(self, throughput_mbps: float, current_quality: str) -> str:
        """
        Select quality based on measured throughput.
        
        Implements: QualitySelector.select_quality()
        """
        if throughput_mbps < 1.0:
            return "480"
        elif throughput_mbps < 3.5:
            return "720"
        else:
            return "1080"
    
    def update_metrics(self, bytes_received: int, elapsed_seconds: float) -> None:
        """
        Update throughput measurements.
        
        Implements: QualitySelector.update_metrics()
        """
        with self.lock:
            self.bytes_measured += bytes_received
            
            if elapsed_seconds >= DASH_WINDOW_SECS:
                mbps = (self.bytes_measured * 8) / (elapsed_seconds * 1_048_576)
                new_quality = self.select_quality(mbps, self.current_quality)
                
                if new_quality != self.current_quality:
                    print(f"[DASH] Quality change: {self.current_quality} → {new_quality}")
                
                self.current_quality = new_quality
                self.bytes_measured = 0
                self.window_start = time.monotonic()
    
    def get_current_quality(self) -> str:
        """
        Get current selected quality.
        
        Implements: QualitySelector.get_current_quality()
        """
        with self.lock:
            return self.current_quality
    
    def reset(self) -> None:
        """
        Reset quality selector state.
        
        Implements: QualitySelector.reset()
        """
        with self.lock:
            self.current_quality = "720"
            self.bytes_measured = 0
            self.window_start = time.monotonic()
    
    # ── Backward-Compatible Methods (deprecated, but kept) ───────────────
    
    def add_bytes(self, num_bytes: int) -> str:
        """
        DEPRECATED: Use update_metrics() instead.
        
        Legacy method for backward compatibility.
        """
        elapsed = time.monotonic() - self.window_start
        self.update_metrics(num_bytes, elapsed)
        return self.get_current_quality()
    
    def get_current(self) -> str:
        """
        DEPRECATED: Use get_current_quality() instead.
        """
        return self.get_current_quality()


# ══════════════════════════════════════════════════════════════════════════════
# TCP CLIENT (FALLBACK)
# ══════════════════════════════════════════════════════════════════════════════

def tcp_stream(
    filename: str,
    byte_start: int,
    server_addr: Tuple[str, int],
    packet_loss_pct: float = 0,
) -> Generator[bytes, None, None]:
    """TCP streaming - server handles loss simulation via DROP packets."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    
    try:
        cmd = encode_control_message("REQ", filename, byte_start)
        print(f"[TCP] 📤 Sending request for {filename}")
        sock.connect(server_addr)
        sock.sendall(cmd)
        
        # Read META header
        header_buf = b""
        while b"\n" not in header_buf:
            chunk = sock.recv(512)
            if not chunk:
                return
            header_buf += chunk
        
        _header_line, leftover = header_buf.split(b"\n", 1)
        print(f"[TCP] ✅ Connected, receiving data...")
        
        if leftover:
            yield leftover
        
        # Stream rest - TCP handles loss internally
        sock.settimeout(5.0)
        while True:
            chunk = sock.recv(131_072)
            if not chunk:
                break
            yield chunk
        
        print(f"[TCP] ✅ Stream complete!")
    
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
SERVER_PORT = 9000
PROTOCOL = "rudp"
QUALITY_DISPLAY = "Auto"
PACKET_LOSS_PCT = 0

# ═══ COMPONENTS ═══
quality_selector = DASHQualitySelector()
http_handler = HTTPHandler()
stream_orchestrator = StreamOrchestrator(
    http_handler=http_handler,
    default_protocol=TransportProtocol.RUDP,
    quality_selector=quality_selector
)


# ══════════════════════════════════════════════════════════════════════════════
# DNS RESOLUTION
# ══════════════════════════════════════════════════════════════════════════════

def resolve_origin_server(server_name = "originserver.homelab", server_port = SERVER_PORT):
    """Resolve origin server address via DNS."""
    
    try:
        print(f"[DNS] 🔍 Resolving {server_name} via DNS (127.0.0.2:53)...")
        ans = DNSRecord.question(server_name).send("127.0.0.2", 53, timeout=2.0)
        reply = DNSRecord.parse(ans)
        resolved_ip = None
        
        for rr in reply.rr:
            if rr.rtype == 1:  # A record
                resolved_ip = str(rr.rdata)
                break
        
        if resolved_ip:
            answer = (resolved_ip, server_port)
            print(f"[DNS] ✅ Successfully resolved {server_name} to {resolved_ip}:{server_port}")
            return answer
        else:
            print(f"[DNS] ⚠️  No A record found in response, using default")
            answer = ("127.0.0.13", server_port)
            return answer
    
    except Exception as e:
        print(f"[DNS] ❌ Failed to resolve origin server via DNS: {e}")
        print(f"[DNS] ⚠️  Using default address: 127.0.0.13:{server_port}")
        answer = ("127.0.0.13", server_port)
        return answer


WEB_SERVER_ADDR = resolve_origin_server()  

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
        "selected_quality": QUALITY_DISPLAY,
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
            "selected_quality": QUALITY_DISPLAY,
            "packet_loss": PACKET_LOSS_PCT,
            "movies": load_movies()
        }
    return templates.TemplateResponse("index.html", get_context(request))


@app.post("/switch_protocol")
async def switch_protocol():
    """Toggle TCP/RUDP."""
    global PROTOCOL
    PROTOCOL = "rudp" if PROTOCOL == "tcp" else "tcp"
    print(f"[*] 🔄 Switched to {PROTOCOL.upper()}")
    return RedirectResponse("/", status_code=303)


@app.post("/set_loss")
async def set_loss(request: Request):
    """Set packet loss percentage and notify server."""
    global PACKET_LOSS_PCT
    form = await request.form()
    try:
        new_loss_pct = max(0, min(100, int(form.get("loss_percent", 0))))
        PACKET_LOSS_PCT = new_loss_pct
        loss_rate = new_loss_pct / 100.0
        
        print(f"[*] 🔴 Packet loss set to {PACKET_LOSS_PCT}% (loss_rate={loss_rate:.2f})")
        
        # Notify server of new loss rate via UDP
        try:
            notify_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            notify_msg = encode_control_message("LOSS_RATE", loss_rate)
            notify_sock.sendto(notify_msg, WEB_SERVER_ADDR)
            notify_sock.close()
            print(f"[*] ✅ Sent LOSS_RATE={loss_rate:.2f} to server")
        except Exception as e:
            print(f"[!] Could not notify server: {e}")
    except:
        pass
    return RedirectResponse("/", status_code=303)


@app.get("/play/{filename}")
async def play(request: Request, filename: str, force_quality: str = "auto"):
    """Video player page."""
    if templates is None:
        return {"status": "ok", "file": filename, "selected_quality": force_quality}
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
    Stream video with clean component-based architecture.
    """
    global QUALITY_DISPLAY
    
    # Resolve quality → filename
    quality = "720" if force_quality == "auto" else force_quality
    target = filename.replace(".mp4", f"_{quality}.mp4")
    
    # Get file size for bounds checking
    video_dir = os.path.join(BASE_DIR, "videos")
    filepath = os.path.join(video_dir, target)
    file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        
    # ═══ HTTP LAYER: Parse range request ═══
    range_request = http_handler.parse_range_header(
        request.headers.get("Range"),
        file_size=file_size if file_size > 0 else 1
    )

    byte_start = range_request.start
    byte_end = range_request.end if range_request.end is not None else max(0, file_size - 1)
    
    print(f"\n{'='*70}")
    print(f"[*] 🎬 New stream request")
    print(f"    File: {target}")
    print(f"    Protocol: {PROTOCOL.upper()}")
    print(f"    Quality: {quality}p")
    print(f"    Server loss rate: {PACKET_LOSS_PCT}%")
    print(f"    Byte range: {byte_start}-{byte_end}/{file_size}")
    print(f"{'='*70}\n")
    
    # ── STREAMING LAYER ────────────────────────────────────────
    
    if PROTOCOL == "tcp":
        # TCP streaming (fallback)
        generator = tcp_stream(
            target,
            byte_start,
            WEB_SERVER_ADDR,
            packet_loss_pct=PACKET_LOSS_PCT
        )
    
    else:
        # RUDP streaming via orchestrator
        quality_selector.reset()
        
        generator, metrics = stream_orchestrator.fetch_stream(
            filename=target,
            byte_start=byte_start,
            server_addr=WEB_SERVER_ADDR,
            protocol=TransportProtocol.RUDP,
            quality="auto" if force_quality == "auto" else force_quality,
            enable_quality_adaptation=(force_quality == "auto")
        )
        
        if force_quality == "auto" and quality_selector:
            QUALITY_DISPLAY = quality_selector.get_current_quality()
        else:
            QUALITY_DISPLAY = force_quality
            
    # ═══ HTTP LAYER: Create response ═══
    http_response = http_handler.create_response(
        range_request=range_request,
        file_size=file_size,
        media_type="video/mp4"
    )
    
    return StreamingResponse(
        generator,
        status_code=http_response.status_code,
        media_type=http_response.media_type,
        headers=http_response.headers
    )


@app.get("/quality")
async def get_quality():
    """Get current DASH quality."""
    return {
        "protocol": PROTOCOL,
        "selected_quality": quality_selector.get_current(),
        "packet_loss": PACKET_LOSS_PCT,
    }


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "healthy",
        "protocol": PROTOCOL,
        "selected_quality": QUALITY_DISPLAY,
        "packet_loss": PACKET_LOSS_PCT,
        "origin_server": WEB_SERVER_ADDR,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Filter to suppress specific log messages (like Content-Length errors from HTTP)
# ══════════════════════════════════════════════════════════════════════════════
class _SuppressKnownNoise(logging.Filter):
    _suppress = [
        "Too little data for declared Content-Length",
        "WinError 10054",
        "An existing connection was forcibly closed by the remote host",
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if any(s in msg for s in self._suppress):
            return False
        if record.exc_info:
            import traceback
            exc_text = "".join(traceback.format_exception(*record.exc_info))
            if any(s in exc_text for s in self._suppress):
                return False
        return True

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
    
    # Resolve origin server via DNS
    resolve_origin_server()
    
    print(f"""
╔═════════════════════════════════════════════════════════════╗
║ UNIFIED EDGE PROXY - WITH DNS & PACKET LOSS LOGGING        ║
╠═════════════════════════════════════════════════════════════╣
║ Listening: {MY_IP}:5000
║ Origin Server: {WEB_SERVER_ADDR[0]}:{WEB_SERVER_ADDR[1]} (DNS resolved)
║ Protocol: {PROTOCOL.upper()}
║ Packet Loss: {PACKET_LOSS_PCT}%
║
║ Features:
║  ✓ DNS resolution for origin server
║  ✓ Detailed packet loss visibility
║  ✓ Real-time DASH quality selection
║  ✓ HTTP 200 for RUDP (no Content-Length errors)
║  ✓ HTTP 206 for TCP (Range requests)
║
║ Watch the logs to see packet loss events!
╚═════════════════════════════════════════════════════════════╝
""")

    _noise_filter = _SuppressKnownNoise()
    logging.getLogger("uvicorn.error").addFilter(_noise_filter)
    logging.getLogger("asyncio").addFilter(_noise_filter)

    uvicorn.run(
        app,
        host=MY_IP,
        port=5000,
        log_level="warning",
        workers=1
    )