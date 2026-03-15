"""
ARP-like IP conflict detection for the custom DHCP system.

Two modes:
  - Loopback (simulation): UDP probe to a dedicated port on the candidate IP.
    Active clients run a ProbeListener bound to their assigned IP; they reply
    to any probe, proving the IP is in use.
  - Real network: ICMP echo (scapy) — standard ARP-probe equivalent.
"""

import socket
import threading

ARP_PROBE_PORT = 6701
PROBE_MSG      = b"ARP_PROBE"
REPLY_MSG      = b"ARP_REPLY"


# ─────────────────────────────────────────────
# Server-side probe functions
# ─────────────────────────────────────────────

def probe_ip_loopback(ip: str, timeout: float = 0.3) -> bool:
    """
    Send a UDP probe to ip:ARP_PROBE_PORT.
    Returns True if an active client replies (IP is already claimed).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(PROBE_MSG, (ip, ARP_PROBE_PORT))
        data, _ = sock.recvfrom(64)
        return data == REPLY_MSG
    except (socket.timeout, OSError):
        return False
    finally:
        sock.close()


def probe_ip_real(ip: str, timeout: float = 0.5) -> bool:
    """
    Send an ICMP echo request using scapy.
    Returns True if the host responds (IP is in use on the real network).
    """
    try:
        from scapy.all import sr1, IP, ICMP
        reply = sr1(IP(dst=ip) / ICMP(), timeout=timeout, verbose=0)
        return reply is not None
    except Exception:
        return False


# ─────────────────────────────────────────────
# Client-side listener
# ─────────────────────────────────────────────

class ProbeListener:
    """
    Runs inside a DHCP client process.

    Binds a UDP socket to {assigned_ip}:ARP_PROBE_PORT so that when the DHCP
    server probes that address before offering it to another client, this
    listener replies with REPLY_MSG — proving the IP is already occupied.

    Usage:
        listener = ProbeListener(lambda: self.ip)
        listener.start()   # call after DORA succeeds
        ...
        listener.stop()    # call on release / shutdown
    """

    def __init__(self, get_ip_fn):
        """
        get_ip_fn: zero-argument callable that returns the client's current IP
                   string (or None if not yet assigned).
        """
        self._get_ip  = get_ip_fn
        self._sock    = None
        self._thread  = None
        self._running = False

    def start(self):
        ip = self._get_ip()
        if not ip:
            return
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((ip, ARP_PROBE_PORT))
            self._sock.settimeout(1.0)
            self._running = True
            self._thread = threading.Thread(target=self._listen_loop, daemon=True)
            self._thread.start()
            print(f"[ARP] ProbeListener active on {ip}:{ARP_PROBE_PORT}")
        except OSError as e:
            print(f"[ARP] ProbeListener could not bind to {ip}:{ARP_PROBE_PORT} — {e}")

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass

    def _listen_loop(self):
        while self._running:
            try:
                data, addr = self._sock.recvfrom(64)
                if data == PROBE_MSG:
                    self._sock.sendto(REPLY_MSG, addr)
            except socket.timeout:
                continue
            except OSError:
                break
