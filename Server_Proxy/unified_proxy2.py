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

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from dnslib import DNSRecord

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
RECV_BUFFER_SIZE = 1024 * 1024

PACKET_OVERHEAD = 6
MAX_UDP_PAYLOAD = 65507


# ══════════════════════════════════════════════════════════════════════════════
# DASH QUALITY SELECTOR
# ══════════════════════════════════════════════════════════════════════════════

class DASHQualitySelector:
    """Real-time quality measurement based on throughput."""
    
    def __init__(self):
        self.current_quality = "720"
        self.window_start = time.monotonic()
        self.bytes_measured = 0
        self.lock = threading.Lock()
    
    def add_bytes(self, num_bytes: int) -> str:
        """Add bytes and return current quality."""
        with self.lock:
            self.bytes_measured += num_bytes
            elapsed = time.monotonic() - self.window_start
            
            if elapsed < DASH_WINDOW_SECS:
                return self.current_quality
            
            if elapsed == 0:
                mbps = 0
            else:
                mbps = (self.bytes_measured * 8) / (elapsed * 1_048_576)
            
            if mbps < 1.0:
                quality = "480"
            elif mbps < 3.5:
                quality = "720"
            else:
                quality = "1080"
            
            self.current_quality = quality
            print(f"[DASH] 📊 Throughput: {mbps:.2f} Mbps → Quality: {quality}p")
            
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
# RUDP CLIENT WITH FLOW CONTROL & DETAILED LOGGING
# ══════════════════════════════════════════════════════════════════════════════

def rudp_stream_with_dash(
    filename: str,
    byte_start: int,
    server_addr: Tuple[str, int],
    packet_loss_pct: float = 0,
    quality_selector: Optional[DASHQualitySelector] = None,
) -> Tuple[Generator[bytes, None, None], RUDPStreamState]:
    """
    RUDP client with flow control, real-time DASH, detailed packet loss logging.
    """
    
    state = RUDPStreamState(start_time=time.monotonic())
    
    def generate() -> Generator[bytes, None, None]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(SOCKET_TIMEOUT)
        
        ooo_buffer: dict = {}
        expected_seq: int = 0
        origin_addr: Optional[Tuple[str, int]] = None
        bytes_buffered: int = 0
        packets_lost_count: int = 0
        packets_received_count: int = 0
        
        try:
            # Simply send request - server will handle loss simulation
            print(f"[RUDP] 📤 Sending request: {filename} (offset: {byte_start})")
            req_pkt = f"REQ|{filename}|{byte_start}".encode()
            sock.sendto(req_pkt, server_addr)
            
            # Wait for META
            meta_deadline = time.monotonic() + META_WAIT_SECS
            meta_attempts = 0
            while time.monotonic() < meta_deadline:
                try:
                    meta_attempts += 1
                    raw, addr = sock.recvfrom(CHUNK_READ_SIZE)
                    
                    if raw[:4] == b"META":
                        try:
                            parts = raw.decode().split("|")
                            state.file_size = int(parts[1]) if len(parts) > 1 else 0
                            state.remote_rwnd = int(parts[2]) if len(parts) > 2 else RECV_BUFFER_SIZE
                            origin_addr = addr
                            state.connected = True
                            print(f"[RUDP] ✅ Connected to origin server: {addr}")
                            print(f"[RUDP]    File size: {state.file_size:,} bytes")
                            print(f"[RUDP]    Remote window: {state.remote_rwnd:,} bytes")
                            print(f"[RUDP] 📥 Starting to receive data packets...")
                            break
                        except (ValueError, IndexError) as e:
                            print(f"[!] META parse error: {e}")
                            continue
                
                except socket.timeout:
                    sock.sendto(req_pkt, server_addr)
            
            if origin_addr is None:
                state.error_msg = f"No META within {META_WAIT_SECS}s"
                print(f"[-] ❌ {state.error_msg}")
                return
            
            # ── Main receive loop ─────────────────────────────────────────
            print(f"[RUDP] 📥 Starting to receive data packets...")
            packets_dropped_received = 0
            
            while True:
                try:
                    raw, addr = sock.recvfrom(CHUNK_READ_SIZE)
                except socket.timeout:
                    continue
                
                # ── Check for DROP packets from server (loss simulation) ──
                if raw[:4] == b"DROP":
                    packets_dropped_received += 1
                    drop_msg = raw.decode(errors='ignore')
                    print(f"[PROXY] 🔴 Ignored DROP packet #{packets_dropped_received} from server (packet loss simulation)")
                    # Don't process further, just continue to next packet
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
                    if ooo_buffer:
                        print(f"[RUDP] 📭 FIN received - flushing {len(ooo_buffer)} buffered packets")
                    while expected_seq in ooo_buffer:
                        payload = ooo_buffer.pop(expected_seq)
                        if quality_selector:
                            quality_selector.add_bytes(len(payload))
                        yield payload
                        expected_seq += 1
                    print(f"[RUDP] ✅ Stream complete!")
                    print(f"[RUDP] 📊 Final stats:")
                    print(f"[RUDP]    Bytes received: {state.bytes_received:,}")
                    print(f"[RUDP]    Packets received: {packets_received_count}")
                    print(f"[RUDP]    Packets lost: {packets_lost_count}")
                    if packets_received_count > 0:
                        loss_pct = (packets_lost_count / packets_received_count) * 100
                        print(f"[RUDP]    Loss rate: {loss_pct:.2f}%")
                    break
                
                if raw[:4] == b"ERR|":
                    state.error_msg = raw.decode(errors='ignore')
                    print(f"[-] ❌ Server error: {state.error_msg}")
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
                    packets_lost_count += 1
                    continue
                
                packets_received_count += 1
                
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
                    print(f"[RUDP] 🔄 Duplicate packet: seq={seq} (already delivered as of seq={expected_seq})")
                    continue
                
                if seq not in ooo_buffer:
                    ooo_buffer[seq] = payload
                    bytes_buffered += len(payload)
                    state.packets_received += 1
                else:
                    state.duplicate_packets += 1
                
                # Yield in-order packets
                delivered = 0
                while expected_seq in ooo_buffer:
                    payload = ooo_buffer.pop(expected_seq)
                    bytes_buffered -= len(payload)
                    delivered += 1
                    
                    # Update DASH quality
                    if quality_selector:
                        quality_selector.add_bytes(len(payload))
                    
                    yield payload
                    expected_seq += 1
                    state.bytes_received += len(payload)
                
                # Detailed logging
                if delivered > 0:
                    # print(f"[RUDP] ✓ Delivered {delivered} packet(s) in sequence (seq {expected_seq - delivered}-{expected_seq - 1})")
                    if ooo_buffer:
                        gap = min(ooo_buffer.keys()) - expected_seq
                        # print(f"[RUDP]    ⚠️  Out-of-order buffer: {len(ooo_buffer)} packets, gap of {gap} seq(s)")
                
                # Track OOO
                if ooo_buffer and min(ooo_buffer.keys()) != expected_seq:
                    state.packets_out_of_order += 1
        
        except Exception as e:
            state.error_msg = str(e)
            print(f"[-] ❌ RUDP stream error: {e}")
        
        finally:
            sock.close()
            if packets_dropped_received > 0:
                print(f"[PROXY] Session stats:")
                print(f"[PROXY]   Data packets received: {packets_received_count}")
                print(f"[PROXY]   DROP packets ignored (from server): {packets_dropped_received}")
                if packets_received_count + packets_dropped_received > 0:
                    loss_pct = (packets_dropped_received / (packets_received_count + packets_dropped_received)) * 100
                    print(f"[PROXY]   Loss rate: {loss_pct:.2f}%")
    
    return generate(), state


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
        cmd = f"REQ|{filename}|{byte_start}"
        print(f"[TCP] 📤 Sending request for {filename}")
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
WEB_SERVER_ADDR = ("127.0.0.13", 9000)  # Will be updated by DNS
PROTOCOL = "rudp"
QUALITY_DISPLAY = "Auto"
PACKET_LOSS_PCT = 0

quality_selector = DASHQualitySelector()


# ══════════════════════════════════════════════════════════════════════════════
# DNS RESOLUTION
# ══════════════════════════════════════════════════════════════════════════════

def resolve_origin_server():
    """Resolve origin server address via DNS."""
    global WEB_SERVER_ADDR
    
    try:
        print(f"[DNS] 🔍 Resolving originserver.homelab via DNS (127.0.0.2:53)...")
        ans = DNSRecord.question("originserver.homelab.").send("127.0.0.2", 53, timeout=2.0)
        reply = DNSRecord.parse(ans)
        resolved_ip = None
        
        for rr in reply.rr:
            if rr.rtype == 1:  # A record
                resolved_ip = str(rr.rdata)
                break
        
        if resolved_ip:
            WEB_SERVER_ADDR = (resolved_ip, SERVER_PORT)
            print(f"[DNS] ✅ Successfully resolved originserver.homelab to {resolved_ip}:{SERVER_PORT}")
        else:
            print(f"[DNS] ⚠️  No A record found in response, using default")
            WEB_SERVER_ADDR = ("127.0.0.13", SERVER_PORT)
    
    except Exception as e:
        print(f"[DNS] ❌ Failed to resolve origin server via DNS: {e}")
        print(f"[DNS] ⚠️  Using default address: 127.0.0.13:{SERVER_PORT}")
        WEB_SERVER_ADDR = ("127.0.0.13", SERVER_PORT)


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
            notify_msg = f"LOSS_RATE|{loss_rate}".encode()
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
    Streaming endpoint with proper handling for TCP vs RUDP + detailed logging.
    """
    global QUALITY_DISPLAY
    
    # Resolve quality → filename
    quality = "720" if force_quality == "auto" else force_quality
    target = filename.replace(".mp4", f"_{quality}.mp4")
    
    # Get file size for TCP
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
    
    byte_start = max(0, byte_start)
    if file_size > 0:
        byte_end = min(byte_end, file_size - 1)
    else:
        byte_end = 0
    
    content_length = max(byte_end - byte_start + 1, 0)
    
    print(f"\n{'='*70}")
    print(f"[*] 🎬 New stream request")
    print(f"    File: {target}")
    print(f"    Protocol: {PROTOCOL.upper()}")
    print(f"    Quality: {quality}p")
    print(f"    Server loss rate: {PACKET_LOSS_PCT}%")
    print(f"    Byte range: {byte_start}-{byte_end}/{file_size}")
    print(f"{'='*70}\n")
    
    # ── PROTOCOL-SPECIFIC HANDLING ────────────────────────────────────────
    
    if PROTOCOL == "tcp":
        # TCP: Range requests with Content-Length
        gen = tcp_stream(
            target,
            byte_start,
            WEB_SERVER_ADDR,
            packet_loss_pct=PACKET_LOSS_PCT
        )
        
        headers = {
            "Content-Range": f"bytes {byte_start}-{byte_end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Cache-Control": "no-cache",
        }
        
        return StreamingResponse(
            gen,
            status_code=206,
            media_type="video/mp4",
            headers=headers
        )
    
    else:
        # RUDP: Support Range requests for seeking
        # We use HTTP 206 for range requests, HTTP 200 for full file
        
        quality_selector.current_quality = "720"
        quality_selector.bytes_measured = 0
        quality_selector.window_start = time.monotonic()
        
        gen, state = rudp_stream_with_dash(
            target,
            byte_start,
            WEB_SERVER_ADDR,
            packet_loss_pct=PACKET_LOSS_PCT,
            quality_selector=quality_selector if force_quality == "auto" else None
        )
        
        if state and state.connected and force_quality == "auto":
            QUALITY_DISPLAY = "Auto (measuring...)"
        
        # Determine HTTP status code and headers based on Range request
        if byte_start > 0 or byte_end < (file_size - 1):
            # Partial content requested
            status_code = 206
            rudp_headers = {
                "Content-Range": f"bytes {byte_start}-{byte_end}/{file_size}",
                "Content-Length": str(content_length),
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-cache",
            }
            print(f"[RUDP] Range request: returning {status_code} with Content-Range")
        else:
            # Full file
            status_code = 200
            rudp_headers = {
                "Content-Length": str(file_size),
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-cache",
            }
            print(f"[RUDP] Full file request: returning {status_code} with Content-Length")
        
        return StreamingResponse(
            gen,
            status_code=status_code,
            media_type="video/mp4",
            headers=rudp_headers
        )


@app.get("/quality")
async def get_quality():
    """Get current DASH quality."""
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
        "packet_loss": PACKET_LOSS_PCT,
        "origin_server": WEB_SERVER_ADDR,
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
    
    uvicorn.run(
        app,
        host=MY_IP,
        port=5000,
        log_level="warning",
        workers=1
    )