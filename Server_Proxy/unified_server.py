import socket, os, sys, threading, yaml, random, time, signal, argparse

# Pathing as requested
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, 'videos')
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, '..'))
sys.path.append(PARENT_DIR)

from dhcp_helper import VirtualNetworkInterface

class UnifiedServer:
    def __init__(self, protocol):
        self.running = True
        self.protocol = protocol.lower()
        signal.signal(signal.SIGINT, self.shutdown)

        with open("movies.yaml", 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        self.v_net = VirtualNetworkInterface(client_name="OriginServer", fixed_id="OriginServer")
        self.my_ip = self.v_net.setup_network()
        self.port = self.config['server_config']['origin_port']
        self.loss_chance = self.config['server_config'].get('packet_loss_chance', 0.0)
        
        # Sliding Window Constants
        self.WINDOW_SIZE = 64 
        self.TIMEOUT = 0.15

    def shutdown(self, signum, frame):
        print(f"\n[*] {self.protocol.upper()} Server shutting down...")
        self.running = False
        sys.exit(0)

    def start(self):
        if self.protocol == "tcp": self.run_tcp()
        else: self.run_rudp()

    def run_tcp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.my_ip, self.port))
        sock.listen(5)
        sock.settimeout(1.0)
        print(f"[*] TCP Mode active on {self.my_ip}:{self.port}")
        while self.running:
            try:
                conn, addr = sock.accept()
                threading.Thread(target=self.handle_tcp, args=(conn, addr), daemon=True).start()
            except socket.timeout: continue

    def handle_tcp(self, conn, addr):
        try:
            data = conn.recv(1024).decode().split("|")
            if data[0] == "REQ":
                filename = data[1]
                byte_start = int(data[2]) if len(data) > 2 else 0
                file_path = os.path.join(VIDEO_DIR, filename)
                if os.path.exists(file_path):
                    print(f"[TCP] Sending {filename}")
                    with open(file_path, "rb") as f:
                        f.seek(byte_start)
                        while self.running:
                            chunk = f.read(65536)
                            if not chunk: break
                            conn.sendall(chunk)
        finally: conn.close()

    def run_rudp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.my_ip, self.port))
        sock.settimeout(1.0)
        print(f"[*] RUDP Mode active on {self.my_ip}:{self.port}")
        while self.running:
            try:
                data, addr = sock.recvfrom(1024)
                msg = data.decode().split("|")
                if msg[0] == "REQ":
                    filename = msg[1]
                    byte_start = int(msg[2]) if len(msg) > 2 else 0
                    threading.Thread(target=self.handle_rudp, args=(filename, addr, byte_start), daemon=True).start()
            except socket.timeout: continue

    def handle_rudp(self, filename, addr, byte_start):
        file_path = os.path.join(VIDEO_DIR, filename)
        if not os.path.exists(file_path): return
        
        # print(f"[RUDP] Streaming {filename} (Start: {byte_start}) to {addr}")
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send_sock.settimeout(0.001)
        
        window = {} 
        base = 0
        next_seq = 0
        
        with open(file_path, "rb") as f:
            f.seek(byte_start)
            while (base == 0 or window) and self.running:
                while next_seq < base + self.WINDOW_SIZE:
                    chunk = f.read(4096) # Smaller chunks for better RUDP reliability
                    if not chunk: break
                    packet = next_seq.to_bytes(4, 'big') + chunk
                    window[next_seq] = {"data": packet, "time": time.time(), "acked": False}
                    if random.random() >= self.loss_chance:
                        send_sock.sendto(packet, addr)
                    next_seq += 1
                
                try:
                    while True:
                        ack_data, _ = send_sock.recvfrom(1024)
                        a_seq = int(ack_data.decode().split("|")[1])
                        if a_seq in window: window[a_seq]["acked"] = True
                except: pass

                while base in window and window[base]["acked"]:
                    del window[base]
                    base += 1

                now = time.time()
                for s, p in window.items():
                    if not p["acked"] and (now - p["time"]) > self.TIMEOUT:
                        send_sock.sendto(p["data"], addr)
                        p["time"] = now
                
                if not window and not chunk: break
            for _ in range(3): send_sock.sendto(b"FIN|DONE", addr)
        send_sock.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", required=True)
    args = parser.parse_args()
    UnifiedServer(args.protocol).start()