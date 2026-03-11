"""
unified_server.py – Origin Server for Video Streaming

Serves video files to the Proxy over two transport protocols:

  TCP   – standard streaming; sends a "META|<size>\\n" header first so the
          proxy knows Content-Length, then streams raw bytes.

  RUDP  – Reliable UDP implemented from scratch with:
            • Sliding congestion window  (size = cwnd packets)
            • Slow-Start → AIMD Congestion Control  (TCP-Reno flavour)
            • Fast Retransmit on DUPACK_THRESH duplicate ACKs
            • Timeout-based retransmission  (RTO)
            • Keep-Alive probes to detect dead clients
            • File-size advertisement via META packet

Wire protocol (RUDP)
────────────────────
  Server → Client  (session socket – ephemeral port):
    b"META|<file_size>"              repeated 5× at session start
    <4-byte big-endian seq><payload> sequenced data datagrams (≤ CHUNK_SIZE)
    b"ALIVE|"                        keep-alive probe (client ignores payload)
    b"FIN|DONE"                      end-of-stream, repeated 5×

  Client → Server  (to session socket's ephemeral port):
    b"ACK|<seq>"                     per-packet positive acknowledgement
    b"DROP|…"                        simulated-loss trigger (testing only)

Usage:  cd Server_Proxy && python unified_server.py
"""

import os
import sys
import socket
import threading
import signal
import random
import time
import yaml

# ── Path setup: dhcp_helper lives one directory above Server_Proxy/ ───────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR  = os.path.join(BASE_DIR, "videos")
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
sys.path.append(PARENT_DIR)
sys.path.append(os.path.join(PARENT_DIR, "dhcp"))  # for DHCP helper module
from dhcp.dhcp_helper import VirtualNetworkInterface  # project DHCP stage


# ══════════════════════════════════════════════════════════════════════════════
# RUDP tunables
# ══════════════════════════════════════════════════════════════════════════════

CHUNK_SIZE     = 32_768   # bytes per datagram – safely below the 64 KB UDP limit
INIT_CWND      = 4        # starting congestion window (packets in flight)
INIT_SSTHRESH  = 64       # initial slow-start threshold
RTO            = 0.25     # Retransmission TimeOut in seconds
DUPACK_THRESH  = 3        # duplicate ACKs that trigger fast-retransmit
KEEPALIVE_SECS = 5        # idle seconds before a keep-alive probe is sent
ACK_POLL_TIMO  = 0.002    # socket timeout used for the non-blocking ACK drain


# ══════════════════════════════════════════════════════════════════════════════
# Packet helpers
# ══════════════════════════════════════════════════════════════════════════════

def encode_pkt(seq: int, payload: bytes) -> bytes:
    """Prepend a 4-byte big-endian sequence number to a payload chunk."""
    return seq.to_bytes(4, "big") + payload


def decode_seq(packet: bytes) -> int:
    """Extract the 4-byte sequence number from the front of a data packet."""
    return int.from_bytes(packet[:4], "big")


# ══════════════════════════════════════════════════════════════════════════════
# RUDP Session
# ══════════════════════════════════════════════════════════════════════════════

class RUDPSession:
    """
    Manages a single RUDP file-send transaction for one client address.

    Congestion control  (TCP-Reno)
    ───────────────────────────────
      Slow Start        cwnd += 1 per new ACK   → doubles each RTT
                        until cwnd reaches ssthresh
      AIMD              cwnd += 1/cwnd per ACK  → linear growth
      Triple-DupACK     ssthresh = cwnd/2 ; cwnd = ssthresh (skip slow-start)
      Timeout           ssthresh = cwnd/2 ; cwnd = 2         (restart slow-start)

    Flow control
    ─────────────
      At most int(cwnd) un-ACKed packets exist in flight at any moment.

    Reliability
    ────────────
      Every sent packet is stored in self.window until ACKed.
      Only the specific timed-out packet is retransmitted (selective repeat),
      not the entire window, to avoid unnecessary traffic.
      FIN is sent 5× to survive terminal loss.
    """

    def __init__(
        self,
        addr,
        filepath: str,
        byte_start: int,
        loss_chance: float,
    ):
        self.addr        = addr
        self.filepath    = filepath
        self.byte_start  = byte_start
        self.loss_chance = loss_chance
        self.file_size   = os.path.getsize(filepath)

        # Each session creates its own socket.
        # Unbound here – Linux assigns an ephemeral port on the first sendto().
        # The client learns that port from the source address of the first
        # META packet and directs all ACKs to it.
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(ACK_POLL_TIMO)   # used by _drain_acks()

        # ── Sliding-window bookkeeping ─────────────────────────────────────
        # window maps  seq → {"data": bytes, "ts": float, "acked": bool}
        self.window   : dict  = {}
        self.base     : int   = 0     # oldest un-ACKed sequence number
        self.next_seq : int   = 0     # sequence number to assign to the next packet

        # ── Congestion-control state ───────────────────────────────────────
        self.cwnd     : float = float(INIT_CWND)
        self.ssthresh : float = float(INIT_SSTHRESH)

        # ── Duplicate-ACK counter for fast retransmit ─────────────────────
        self.last_new_ack : int = -1
        self.dup_cnt      : int = 0

        self.last_ack_time : float = time.monotonic()
        self.eof           : bool  = False

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _send(self, pkt: bytes):
        """Transmit pkt to the client, honouring the configured loss probability."""
        if random.random() >= self.loss_chance:
            self.sock.sendto(pkt, self.addr)

    def _grow_cwnd(self):
        """Per-ACK cwnd increase: exponential in slow-start, linear in AIMD."""
        if self.cwnd < self.ssthresh:
            self.cwnd += 1.0                # slow start  – doubles every RTT
        else:
            self.cwnd += 1.0 / self.cwnd    # AIMD additive increase

    def _shrink_cwnd(self, fast_retransmit: bool = False):
        """
        Halve ssthresh on a loss event, then:
          fast_retransmit=True  →  cwnd = ssthresh  (stay in AIMD phase)
          timeout               →  cwnd = 2          (restart slow-start)
        """
        self.ssthresh = max(self.cwnd / 2.0, 2.0)
        self.cwnd     = self.ssthresh if fast_retransmit else 2.0

    def _drain_acks(self):
        """
        Non-blocking ACK drain.

        Reads every ACK that is currently queued in the OS receive buffer,
        returning as soon as the queue is empty (socket.timeout raised).
        This avoids blocking the send loop while still processing all
        available feedback before the next window-fill iteration.
        """
        try:
            while True:
                raw, _ = self.sock.recvfrom(256)
                msg = raw.decode(errors="ignore")

                if msg.startswith("DROP"):
                    continue   # client-side loss simulation – just discard

                if not msg.startswith("ACK|"):
                    continue   # unexpected format – skip

                try:
                    seq = int(msg.split("|")[1])
                except (IndexError, ValueError):
                    continue   # malformed ACK – skip

                # Ignore ACKs for packets that have already slid out of window
                if seq not in self.window:
                    continue

                if not self.window[seq]["acked"]:
                    # ── Brand-new ACK ─────────────────────────────────────────
                    self.window[seq]["acked"] = True
                    self.last_ack_time        = time.monotonic()
                    self._grow_cwnd()
                    self.last_new_ack = seq
                    self.dup_cnt      = 0

                else:
                    # ── Duplicate ACK ─────────────────────────────────────────
                    # A dup-ACK means a later packet arrived but this one is
                    # still missing at the receiver.
                    if seq == self.last_new_ack:
                        self.dup_cnt += 1
                        if self.dup_cnt >= DUPACK_THRESH:
                            # Three dup-ACKs → fast-retransmit the window base
                            self._shrink_cwnd(fast_retransmit=True)
                            if self.base in self.window:
                                self._send(self.window[self.base]["data"])
                                print(f"[RUDP] Fast-retransmit seq={self.base} → {self.addr}")
                            self.dup_cnt = 0   # reset after acting on it

        except socket.timeout:
            pass    # queue drained – normal exit
        except OSError:
            pass    # socket was closed by run() – ignore

    # ── Main send loop ────────────────────────────────────────────────────────

    def run(self):
        """
        Entry point; called in a dedicated daemon thread.

        Loop structure  (each iteration ≈ one micro-RTT):
          1. Send keep-alive probe if idle too long
          2. Fill window: send new packets up to base + cwnd
          3. Drain ACK queue (non-blocking)
          4. Slide base forward over consecutive ACKed packets
          5. Timeout-retransmit any packet older than RTO
          6. Break when EOF reached and all packets are ACKed
        """
        # ── Step 0: advertise file size ───────────────────────────────────────
        # Repeated 5× to survive initial UDP loss.  The proxy waits for this
        # packet before starting the receive loop (so it knows Content-Length).
        meta_pkt = f"META|{self.file_size}".encode()
        for _ in range(5):
            self._send(meta_pkt)
        time.sleep(0.03)    # brief pause before data flood

        with open(self.filepath, "rb") as fh:
            fh.seek(self.byte_start)

            while True:

                # ── 1. Keep-alive ─────────────────────────────────────────────
                if time.monotonic() - self.last_ack_time > KEEPALIVE_SECS:
                    self._send(b"ALIVE|")
                    self.last_ack_time = time.monotonic()

                # ── 2. Fill the congestion window ─────────────────────────────
                # next_seq is the global send counter; base is the oldest
                # un-ACKed seq.  The window is at most int(cwnd) packets wide.
                while (
                    not self.eof
                    and self.next_seq < self.base + int(self.cwnd)
                ):
                    chunk = fh.read(CHUNK_SIZE)
                    if not chunk:
                        self.eof = True
                        break
                    pkt = encode_pkt(self.next_seq, chunk)
                    self.window[self.next_seq] = {
                        "data"  : pkt,
                        "ts"    : time.monotonic(),
                        "acked" : False,
                    }
                    self._send(pkt)
                    self.next_seq += 1

                # ── 3. Drain ACK queue (non-blocking) ─────────────────────────
                self._drain_acks()

                # ── 4. Slide window base ──────────────────────────────────────
                # Remove all consecutively ACKed packets from the front of the
                # window so that the window-fill step can send new ones.
                while (
                    self.base in self.window
                    and self.window[self.base]["acked"]
                ):
                    del self.window[self.base]
                    self.base += 1

                # ── 5. Timeout retransmission ──────────────────────────────────
                # Retransmit only the *oldest* timed-out packet per loop pass
                # to avoid a burst that would worsen congestion.
                now = time.monotonic()
                for seq, info in list(self.window.items()):
                    if not info["acked"] and (now - info["ts"]) > RTO:
                        self._shrink_cwnd(fast_retransmit=False)
                        self._send(info["data"])
                        info["ts"] = now    # reset per-packet retransmit timer
                        break               # one retransmit per main-loop pass

                # ── 6. Termination check ──────────────────────────────────────
                if self.eof and not self.window:
                    break   # all bytes sent and all ACKs received

                # Yield CPU briefly so other sessions can run on the same thread pool
                time.sleep(0.0001)

        # ── Signal end-of-stream (repeated for reliability) ──────────────────
        for _ in range(5):
            self._send(b"FIN|DONE")
            time.sleep(0.01)

        self.sock.close()
        print(f"[RUDP] Session complete → {self.addr}")


# ══════════════════════════════════════════════════════════════════════════════
# Unified Origin Server
# ══════════════════════════════════════════════════════════════════════════════

class UnifiedServer:
    """
    Binds to the same (ip, port) on *both* a TCP socket and a UDP socket
    (different socket types can share a port number on Linux).

    TCP  : one handler thread per accepted connection
    RUDP : one RUDPSession thread per REQ datagram (stateless dispatcher)
    """

    def __init__(self):
        self.active = True
        signal.signal(signal.SIGINT, self._on_sigint)

        config_path = os.path.join(BASE_DIR, "movies.yaml")
        with open(config_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        # Obtain a virtual IP address via the project's DHCP implementation
        self.v_net = VirtualNetworkInterface(
            client_name="OriginServer", fixed_id="OriginServer"
        )
        self.my_ip       = self.v_net.setup_network()
        self.port        = self.config["server_config"]["origin_port"]
        self.loss_chance = self.config["server_config"].get("packet_loss_chance", 0.05)

        print(
            f"[*] Origin Server  ip={self.my_ip}  port={self.port}"
            f"  simulated-loss={self.loss_chance:.0%}"
        )

    def _on_sigint(self, *_):
        print("[*] Shutting down Origin Server…")
        self.active = False
        sys.exit(0)

    # ── TCP listener ─────────────────────────────────────────────────────────

    def _run_tcp(self):
        """Accept loop – spawns one handler thread per incoming connection."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.my_ip, self.port))
        srv.listen(20)
        srv.settimeout(1.0)     # wakes up every second to check self.active
        print(f"[TCP] Listening on {self.my_ip}:{self.port}")

        while self.active:
            try:
                conn, addr = srv.accept()
                threading.Thread(
                    target=self._handle_tcp, args=(conn, addr), daemon=True
                ).start()
            except socket.timeout:
                continue

    def _handle_tcp(self, conn: socket.socket, addr):
        """
        Handle one TCP file transfer.

        Expected request  : "REQ|<filename>|<byte_start>"
        Response          : "META|<file_size>\\n"  followed by raw video bytes

        Loss simulation   : "DROP|<filename>|…"  – server logs and closes
        """
        try:
            conn.settimeout(10.0)
            req   = conn.recv(4096).decode(errors="ignore")
            parts = req.split("|")

            if parts[0] == "DROP":
                # Simulated request loss – log it and return without sending data
                name = parts[1] if len(parts) > 1 else "?"
                print(f"[TCP] Simulated DROP from {addr}: {name}")
                return

            if parts[0] != "REQ" or len(parts) < 3:
                conn.sendall(b"ERR|BAD_REQUEST\n")
                return

            # os.path.basename prevents "../../etc/passwd" path-traversal
            filename   = os.path.basename(parts[1])
            byte_start = max(int(parts[2]), 0)
            filepath   = os.path.join(VIDEO_DIR, filename)

            if not os.path.exists(filepath):
                conn.sendall(b"ERR|NOT_FOUND\n")
                return

            file_size = os.path.getsize(filepath)

            # ── META header ───────────────────────────────────────────────────
            # The proxy reads bytes until it sees '\n', then starts treating
            # the rest of the stream as raw video data.
            conn.sendall(f"META|{file_size}\n".encode())

            # ── Stream file bytes ─────────────────────────────────────────────
            # TCP is reliable so we use large chunks to maximise throughput.
            with open(filepath, "rb") as fh:
                fh.seek(byte_start)
                while self.active:
                    chunk = fh.read(131_072)    # 128 KB per sendall()
                    if not chunk:
                        break
                    conn.sendall(chunk)

        except (BrokenPipeError, ConnectionResetError):
            pass   # client disconnected mid-stream (e.g. video seek) – harmless
        except Exception as exc:
            print(f"[-] TCP handler error from {addr}: {exc}")
        finally:
            conn.close()

    # ── RUDP dispatcher ───────────────────────────────────────────────────────

    def _run_rudp(self):
        """
        Single shared UDP socket that only *receives* REQ datagrams.
        Each REQ spawns a RUDPSession thread whose own socket handles all
        subsequent communication for that client, keeping sessions isolated.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.my_ip, self.port))
        sock.settimeout(1.0)
        print(f"[RUDP] Listening on {self.my_ip}:{self.port}  loss={self.loss_chance:.0%}")

        while self.active:
            try:
                data, addr = sock.recvfrom(4096)
                msg = data.decode(errors="ignore").split("|")

                if msg[0] == "REQ" and len(msg) >= 3:
                    filename   = os.path.basename(msg[1])   # path-traversal guard
                    byte_start = max(int(msg[2]), 0)
                    filepath   = os.path.join(VIDEO_DIR, filename)

                    if os.path.exists(filepath):
                        session = RUDPSession(
                            addr, filepath, byte_start, self.loss_chance
                        )
                        threading.Thread(
                            target=session.run, daemon=True
                        ).start()
                    else:
                        sock.sendto(b"ERR|NOT_FOUND", addr)

            except socket.timeout:
                continue
            except Exception as exc:
                print(f"[-] RUDP dispatcher error: {exc}")

    # ── Entry point ───────────────────────────────────────────────────────────

    def start(self):
        """Run the TCP listener in a background thread; RUDP blocks the main thread."""
        threading.Thread(target=self._run_tcp, daemon=True).start()
        self._run_rudp()    # ^C → _on_sigint → sys.exit()


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    UnifiedServer().start()