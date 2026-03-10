"""
unified_proxy.py – Edge Proxy / Streaming Server

Exposes an HTTP interface to the browser via FastAPI + uvicorn.
Fetches video from the Origin Server using TCP or RUDP (user-switchable).
Implements DASH adaptive-bitrate quality selection when using RUDP.

Key routes
──────────
  GET  /                     movie-selection landing page
  POST /switch_protocol      toggle between TCP and RUDP
  POST /set_loss             configure simulated packet-loss % (for demos)
  GET  /play/{filename}      video-player page
  GET  /stream/{filename}    Range-aware streaming endpoint (sync, threadpool)

RUDP client protocol  (mirrors server RUDPSession exactly)
──────────────────────────────────────────────────────────
  → REQ|<filename>|<byte_start>           initial request (well-known port)
  ← META|<file_size>                      repeated by server; proxy waits for 1st
  ← <4-byte big-endian seq><payload>      sequenced data packets (out-of-order ok)
  → ACK|<seq>                             positive ACK for every received data pkt
  ← ALIVE|                                keep-alive probe  (no action required)
  ← FIN|DONE                              end-of-stream

Usage:  cd Server_Proxy && python unified_proxy.py
"""

import os
import sys
import socket
import time
import random
import yaml
from typing import Generator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from dnslib import DNSRecord

# ══════════════════════════════════════════════════════════════════════════════
# FastAPI app
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(title="Video Streaming Proxy")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
static_path = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=static_path), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ── Path setup ────────────────────────────────────────────────────────────────
VIDEO_DIR  = os.path.join(BASE_DIR, "videos")
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
sys.path.append(PARENT_DIR)

from dhcp_helper import VirtualNetworkInterface  # project DHCP stage





# ══════════════════════════════════════════════════════════════════════════════
# Runtime state
# (simple globals – modified only from single route calls; GIL-safe)
# ══════════════════════════════════════════════════════════════════════════════

MY_IP           : str   = "127.0.0.1"
WEB_SERVER_ADDR : tuple = ("127.0.0.13", 9000)   # (host, port) of Origin Server
PROTOCOL        : str   = "rudp"                  # "tcp" | "rudp"
QUALITY_DISPLAY : str   = "Auto"                  # last measured DASH quality label
PACKET_LOSS_PCT : int   = 0                        # 0–100 %  (simulated loss)

# ── RUDP client tunables ──────────────────────────────────────────────────────
_RUDP_SOCKET_TIMEOUT : float = 0.3     # seconds – socket.settimeout during recv loop
_META_WAIT_SECS      : float = 2.0     # max seconds to wait for the first META packet
_RECV_BUF            : int   = 65535   # recvfrom buffer (maximum UDP datagram)
_DASH_WINDOW_SECS    : float = 2.0     # rolling measurement window for DASH estimate


# ══════════════════════════════════════════════════════════════════════════════
# Template helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load_movies() -> list:
    """Return the movie list from movies.yaml."""
    path = os.path.join(BASE_DIR, "movies.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["movies"]


def _ctx(request: Request, extra: dict | None = None) -> dict:
    """Build a base template context with all common variables."""
    ctx = {
        "request"     : request,
        "protocol"    : PROTOCOL.upper(),
        "quality"     : QUALITY_DISPLAY,
        "packet_loss" : PACKET_LOSS_PCT,
        "movies"      : _load_movies(),
    }
    if extra:
        ctx.update(extra)
    return ctx


# ══════════════════════════════════════════════════════════════════════════════
# HTTP routes
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
async def index(request: Request):
    """Landing page – lists all available movies."""
    return templates.TemplateResponse("index.html", _ctx(request))


@app.post("/switch_protocol")
async def switch_protocol():
    """Toggle the active transport between TCP and RUDP."""
    global PROTOCOL
    PROTOCOL = "rudp" if PROTOCOL == "tcp" else "tcp"
    return RedirectResponse("/", status_code=303)  # 303 prevents form re-submission


@app.post("/set_loss")
async def set_loss(request: Request):
    """Set the simulated UDP packet-loss percentage (0–100)."""
    global PACKET_LOSS_PCT
    form = await request.form()
    PACKET_LOSS_PCT = max(0, min(100, int(form.get("loss_percent", 0))))
    return RedirectResponse("/", status_code=303)


@app.get("/play/{filename}")
async def play(request: Request, filename: str, force_quality: str = "auto"):
    """Video-player page – renders the player for a specific file."""
    return templates.TemplateResponse(
        "index.html",
        _ctx(request, {"video_file": filename, "selected_quality": force_quality}),
    )


@app.get("/stream/{filename}")
def stream(request: Request, filename: str, force_quality: str = "auto"):
    """
    Range-aware video streaming endpoint.

    This route is intentionally *synchronous* (def, not async def) so that
    FastAPI runs it in its built-in thread-pool.  The generator it returns
    contains blocking socket calls that must not block the async event loop.
    Starlette automatically wraps sync generators in iterate_in_threadpool()
    when they are passed to StreamingResponse.

    Supports:
      • Range: bytes=X-     (browser requests tail of file, common for seeking)
      • Range: bytes=X-Y    (explicit range, less common for video)
    """
    # ── Resolve quality → target filename ────────────────────────────────────
    quality = "720" if force_quality == "auto" else force_quality
    target  = filename.replace(".mp4", f"_{quality}.mp4")

    # ── Determine file size from the local copy (proxy shares VIDEO_DIR) ─────
    filepath  = os.path.join(VIDEO_DIR, target)
    file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0

    # ── Parse HTTP Range header ───────────────────────────────────────────────
    range_hdr  = request.headers.get("Range", "bytes=0-")
    spec_parts = range_hdr.replace("bytes=", "").split("-")
    byte_start = int(spec_parts[0]) if spec_parts[0] else 0
    # If no explicit end is given, stream to the end of the file
    byte_end   = (
        int(spec_parts[1])
        if len(spec_parts) > 1 and spec_parts[1]
        else file_size - 1
    )

    # ── Select transport generator ────────────────────────────────────────────
    if PROTOCOL == "tcp":
        gen = _tcp_stream(target, byte_start)
    else:
        gen = _rudp_stream(target, byte_start, force_quality)

    # ── Build response headers ────────────────────────────────────────────────
    # Content-Length lets the browser render a proper seek bar and progress ring.
    headers = {
        "Content-Range"  : f"bytes {byte_start}-{byte_end}/{file_size}",
        "Accept-Ranges"  : "bytes",
        "Content-Length" : str(max(byte_end - byte_start + 1, 0)),
    }
    return StreamingResponse(
        gen, status_code=206, media_type="video/mp4", headers=headers
    )


# ══════════════════════════════════════════════════════════════════════════════
# TCP streaming generator
# ══════════════════════════════════════════════════════════════════════════════

def _tcp_stream(filename: str, byte_start: int) -> Generator[bytes, None, None]:
    """
    Fetch a video segment from the Origin Server over TCP and yield raw bytes.

    Protocol:
      → "REQ|<filename>|<byte_start>"
      ← "META|<file_size>\\n"            (header, terminated by newline)
      ← <raw video bytes> …

    Simulated loss:
      If PACKET_LOSS_PCT is set and the random roll wins, the proxy sends
      "DROP|…" instead of "REQ|…" so the server exercises its drop path.
    """
    # Randomly simulate request loss for testing purposes
    if PACKET_LOSS_PCT > 0 and random.random() < PACKET_LOSS_PCT / 100:
        cmd = f"DROP|{filename}|{byte_start}"
    else:
        cmd = f"REQ|{filename}|{byte_start}"

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)

    try:
        sock.connect(WEB_SERVER_ADDR)
        sock.sendall(cmd.encode())

        if cmd.startswith("DROP"):
            return    # nothing to receive; generator ends here

        # ── Read the META header (terminated by '\n') ─────────────────────────
        # Loop in case the first recv() doesn't include the full header line.
        header_buf = b""
        while b"\n" not in header_buf:
            chunk = sock.recv(512)
            if not chunk:
                return   # server closed connection unexpectedly
            header_buf += chunk

        _header_line, leftover = header_buf.split(b"\n", 1)
        # _header_line  = b"META|<file_size>"  – already known from os.path.getsize
        # leftover      = any video bytes that arrived in the same recv() call

        if leftover:
            yield leftover    # don't discard bytes that came with the header

        # ── Stream the rest of the file ───────────────────────────────────────
        # Use a large recv buffer – TCP is reliable, so bigger is faster.
        sock.settimeout(5.0)
        while True:
            chunk = sock.recv(131_072)   # 128 KB per recv()
            if not chunk:
                break
            yield chunk

    except GeneratorExit:
        pass    # browser closed connection (e.g. user navigated away)
    except Exception as exc:
        print(f"[-] TCP stream error: {exc}")
    finally:
        sock.close()


# ══════════════════════════════════════════════════════════════════════════════
# RUDP streaming generator
# ══════════════════════════════════════════════════════════════════════════════

def _rudp_stream(
    filename: str,
    byte_start: int,
    force_quality: str = "auto",
) -> Generator[bytes, None, None]:
    """
    RUDP client receive loop.

    Reliability
    ───────────
      • Sends ACK for *every* received data packet (including duplicates and
        out-of-order arrivals) so the server can advance its congestion window.
      • Maintains an out-of-order (OOO) reorder buffer keyed by sequence number.
      • Yields payload bytes only after all earlier sequence numbers have been
        received, guaranteeing in-order delivery to the HTTP response body.
      • The OOO buffer is bounded by the server's congestion window (cwnd),
        so memory usage stays proportional to the measured bandwidth.

    DASH adaptive bitrate
    ──────────────────────
      • Measures raw byte throughput over rolling _DASH_WINDOW_SECS windows.
      • Updates the global QUALITY_DISPLAY label shown in the UI.
      • An actual quality switch requires the player to issue a new request
        with a different force_quality parameter.

    Loss resilience
    ───────────────
      • Short socket timeout (_RUDP_SOCKET_TIMEOUT) means a lost packet only
        stalls delivery for < 300 ms before the server's RTO fires and
        retransmits.  The OOO buffer holds subsequent packets during that wait.
      • If no META arrives within _META_WAIT_SECS the REQ is resent, handling
        the common case of the initial request being dropped.
    """
    global QUALITY_DISPLAY

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(_RUDP_SOCKET_TIMEOUT)

    # ── Simulate request drop ─────────────────────────────────────────────────
    if PACKET_LOSS_PCT > 0 and random.random() < PACKET_LOSS_PCT / 100:
        print(f"[RUDP] Simulating REQ drop for {filename}")
        sock.close()
        return

    req_pkt = f"REQ|{filename}|{byte_start}".encode()
    sock.sendto(req_pkt, WEB_SERVER_ADDR)

    # ── Wait for the first META packet ────────────────────────────────────────
    # The server sends META 5× to survive early loss.  We resend REQ on each
    # timeout in case our request was dropped.
    origin_addr  = None
    meta_deadline = time.monotonic() + _META_WAIT_SECS
    while time.monotonic() < meta_deadline:
        try:
            raw, addr = sock.recvfrom(_RECV_BUF)
            if raw[:4] == b"META":
                # Parse file_size from META (could differ from local getsize
                # if files are on a remote origin without a shared volume)
                try:
                    _file_size_from_server = int(raw.decode().split("|")[1])
                except (ValueError, IndexError):
                    pass
                origin_addr = addr
                break
        except socket.timeout:
            # REQ may have been lost – resend
            sock.sendto(req_pkt, WEB_SERVER_ADDR)

    if origin_addr is None:
        print(f"[-] RUDP: no META received for {filename} within {_META_WAIT_SECS}s")
        sock.close()
        return

    # ── Receive loop state ────────────────────────────────────────────────────
    expected_seq   : int   = 0           # next seq we want to yield
    ooo_buffer     : dict  = {}          # seq → payload (packets that arrived early)
    bytes_measured : int   = 0           # bytes received in the current DASH window
    window_t0      : float = time.monotonic()

    try:
        while True:
            # ── Receive one datagram ──────────────────────────────────────────
            try:
                raw, addr = sock.recvfrom(_RECV_BUF)
            except socket.timeout:
                # No packet in this window – the server is probably retransmitting
                # a lost packet.  The OOO buffer holds everything we have so far.
                continue

            # ── Route control messages ────────────────────────────────────────
            # Control messages start with ASCII prefixes; data packets start with
            # a 4-byte binary sequence number (seq 0-100 looks like \x00\x00…).

            if raw[:4] == b"ALIV":
                # Keep-alive probe from server – no action needed
                continue

            if raw[:4] == b"FIN|":
                # End of stream – flush whatever is left in the OOO buffer
                while expected_seq in ooo_buffer:
                    yield ooo_buffer.pop(expected_seq)
                    expected_seq += 1
                break

            if raw[:4] == b"ERR|":
                print(f"[-] RUDP origin error: {raw.decode(errors='ignore')}")
                break

            if raw[:4] == b"META":
                # Duplicate META from server (it sends 5×) – ignore after first
                continue

            # ── Data packet ───────────────────────────────────────────────────
            if len(raw) < 5:
                continue    # too short to contain a seq + any payload – discard

            seq     = int.from_bytes(raw[:4], "big")
            payload = raw[4:]

            # ACK every data packet we receive.
            # Sending ACK for duplicates/OOO lets the server know we have them
            # (supports fast-retransmit and window advancement).
            sock.sendto(f"ACK|{seq}".encode(), addr)

            # Discard packets we have already delivered to the response body
            if seq < expected_seq:
                continue

            # Buffer packet (first arrival only – ignore retransmitted duplicates)
            if seq not in ooo_buffer:
                ooo_buffer[seq] = payload
                bytes_measured += len(payload)

            # ── Yield consecutive in-order chunks ─────────────────────────────
            # This is the core reorder-buffer logic:
            # keep yielding as long as the next expected seq is available.
            while expected_seq in ooo_buffer:
                yield ooo_buffer.pop(expected_seq)
                expected_seq += 1

            # ── DASH bitrate measurement ───────────────────────────────────────
            # Every _DASH_WINDOW_SECS seconds, compute throughput and update the
            # quality-tier label displayed in the UI.
            elapsed = time.monotonic() - window_t0
            if elapsed >= _DASH_WINDOW_SECS:
                mbps = (bytes_measured * 8) / (elapsed * 1_048_576)   # Mbit/s
                if force_quality == "auto":
                    if   mbps < 1.0: QUALITY_DISPLAY = "480p (Low)"
                    elif mbps < 3.5: QUALITY_DISPLAY = "720p (Med)"
                    else:            QUALITY_DISPLAY = "1080p (High)"
                else:
                    QUALITY_DISPLAY = f"{force_quality}p (Forced)"

                # Reset measurement window
                bytes_measured = 0
                window_t0      = time.monotonic()

    except GeneratorExit:
        pass    # browser closed connection – clean up in finally
    except Exception as exc:
        print(f"[-] RUDP stream error: {exc}")
    finally:
        sock.close()


# ══════════════════════════════════════════════════════════════════════════════
# Startup
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # ── DHCP: obtain a virtual IP for this proxy node ─────────────────────────
    v_net = VirtualNetworkInterface(client_name="ProxyNode")
    MY_IP = v_net.setup_network()

    # ── DNS: resolve the Origin Server's hostname → IP ────────────────────────
    # The DNS server address (127.0.0.2) is configured by the project's local
    # DNS stage.  Fall back to a hardcoded address if resolution fails.
    try:
        answer = DNSRecord.question("originserver.homelab.").send(
            "127.0.0.2", 53, timeout=2.0
        )
        reply = DNSRecord.parse(answer)
        for rr in reply.rr:
            if rr.rtype == 1:   # A record
                WEB_SERVER_ADDR = (str(rr.rdata), 9000)
                print(f"[*] Origin server resolved → {WEB_SERVER_ADDR}")
                break
    except Exception as exc:
        print(f"[!] DNS resolution failed ({exc}), defaulting to {WEB_SERVER_ADDR}")

    # ── FastAPI via uvicorn ────────────────────────────────────────────────────
    # workers=1 is intentional: PROTOCOL/QUALITY_DISPLAY are process-globals.
    # For multi-worker deployments, move state to Redis or a shared store.
    uvicorn.run(app, host="127.0.0.1", port=5000, log_level="info", workers=1)