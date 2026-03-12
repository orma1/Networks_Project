import socket, time, os
from shared_models import *

class RUDPSession:
    def __init__(self, client_addr, filepath, byte_start, data_loss=0.0, ack_loss=0.0, lat=0):
        self.client_addr, self.filepath, self.byte_start = client_addr, filepath, byte_start
        self.data_loss, self.ack_loss, self.lat = data_loss, ack_loss, lat
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.01)
        self.window = {}
        self.base = 0
        self.next_seq = 0
        self.cwnd = 4.0
        self.last_ack = time.monotonic()

    def run(self):
        print(f"[RUDP] Starting session for {self.client_addr}")
        try:
            self.sock.sendto(f"META|{os.path.getsize(self.filepath)}|{RECV_BUFFER_SIZE}".encode(), self.client_addr)
            with open(self.filepath, "rb") as f:
                f.seek(self.byte_start)
                while True:
                    # Fill Window
                    while len(self.window) < int(self.cwnd):
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk: break
                        pkt = encode_data_pkt(self.next_seq, chunk)
                        self.window[self.next_seq] = WindowEntry(pkt, time.monotonic())
                        self.sock.sendto(pkt, self.client_addr)
                        self.next_seq += 1
                    
                    # Receive ACKs
                    try:
                        raw, _ = self.sock.recvfrom(1024)
                        msg = raw.decode()
                        if msg.startswith("ACK|"):
                            seq = int(msg.split("|")[1])
                            if seq in self.window:
                                self.window[seq].acked = True
                                self.last_ack = time.monotonic()
                                self.cwnd += 1/self.cwnd
                    except: pass

                    # Slide & Retransmit
                    while self.base in self.window and self.window[self.base].acked:
                        del self.window[self.base]
                        self.base += 1
                    
                    if time.monotonic() - self.last_ack > 10: break
                    if not self.window and f.tell() == os.path.getsize(self.filepath): break
        finally:
            for _ in range(3): self.sock.sendto(b"FIN|DONE", self.client_addr)
            self.sock.close()

def rudp_client_stream(filename, byte_start, addr, quality_selector=None):
    state = RUDPStreamState(start_time=time.monotonic())
    def generate():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        expected = 0
        buffer = {}
        try:
            sock.sendto(f"REQ|{filename}|{byte_start}".encode(), addr)
            while True:
                try:
                    raw, srv = sock.recvfrom(CHUNK_READ_SIZE)
                    if raw.startswith(b"META|"): continue
                    if raw.startswith(b"FIN|"): break
                    seq, payload = decode_data_pkt(raw)
                    sock.sendto(f"ACK|{seq}".encode(), srv)
                    if seq >= expected and seq not in buffer:
                        buffer[seq] = payload
                    while expected in buffer:
                        p = buffer.pop(expected)
                        if quality_selector: quality_selector.add_bytes(len(p))
                        yield p
                        expected += 1
                except socket.timeout: continue
        finally: sock.close()
    return generate(), state