import socket, os, sys, threading, yaml, random, time, signal

# Pathing setup to find dhcp_helper in parent directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, 'videos')
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, '..'))
sys.path.append(PARENT_DIR)

from dhcp_helper import VirtualNetworkInterface

class UnifiedServer:
    def __init__(self):
        self.running = True
        signal.signal(signal.SIGINT, self.shutdown)

        # Load configuration
        with open("movies.yaml", 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        # Initializing Network Interface
        self.v_net = VirtualNetworkInterface(client_name="OriginServer", fixed_id="OriginServer")
        self.my_ip = self.v_net.setup_network()
        self.port = self.config['server_config']['origin_port']
        
        # Requirements: Packet loss and Keep-Alive
        # Logic: If multiple entries exist in YAML, use the last one (0.05 recommended)
        self.loss_chance = self.config['server_config'].get('packet_loss_chance', 0.05)
        self.keep_alive_interval = self.config['server_config'].get('keep_alive_interval', 5)

    def shutdown(self, signum, frame):
        print(f"\n[*] Shutting down Origin Server...")
        self.running = False
        sys.exit(0)

    def start(self):
        # Run TCP in background, RUDP in main thread
        threading.Thread(target=self.run_tcp, daemon=True).start()
        self.run_rudp()

    def run_tcp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.my_ip, self.port))
        sock.listen(10)
        sock.settimeout(1.0)
        print(f"[*] TCP Listener active on {self.my_ip}:{self.port}")
        while self.running:
            try:
                conn, addr = sock.accept()
                threading.Thread(target=self.handle_tcp, args=(conn, addr), daemon=True).start()
            except: continue

    def handle_tcp(self, conn, addr):
        try:
            data = conn.recv(1024).decode().split("|")
            if data[0] == "DROP":
                print(f"[TCP] Simulated Drop: {data[1]} at {data[2]} from {addr}")
                return
            if data[0] == "REQ":
                filename, byte_start = data[1], int(data[2])
                file_path = os.path.join(VIDEO_DIR, filename)
                # print(f"[TCP] Request: {filename} from {byte_start} | Found: {os.path.exists(file_path)}")
                if os.path.exists(file_path):
                    with open(file_path, "rb") as f:
                        f.seek(byte_start)
                        while self.running:
                            chunk = f.read(60000) 
                            if not chunk: break
                            try:
                                conn.sendall(chunk)
                            except (ConnectionResetError, BrokenPipeError):
                                break 
        except Exception as e:
            print(f"[-] TCP Thread Error: {e}")
        finally:
            conn.close()

    def run_rudp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.my_ip, self.port))
        sock.settimeout(1.0)
        print(f"[*] RUDP Listener active on {self.my_ip}:{self.port} (Loss: {self.loss_chance})")
        while self.running:
            try:
                data, addr = sock.recvfrom(1024)
                msg = data.decode().split("|")
                if msg[0] == "REQ":
                    threading.Thread(target=self.handle_rudp, args=(msg[1], addr, int(msg[2])), daemon=True).start()
            except: continue

    def handle_rudp(self, filename, addr, byte_start):
        file_path = os.path.join(VIDEO_DIR, filename)
        # print(f"[RUDP] Request: {filename} from {byte_start} | Found: {os.path.exists(file_path)}")
        if not os.path.exists(file_path): return
        
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send_sock.settimeout(0.01)
        
        # Congestion Control (AIMD)
        cwd = 2.0        
        ssthresh = 32    
        window, base, next_seq = {}, 0, 0
        last_ack_time = time.time()

        with open(file_path, "rb") as f:
            f.seek(byte_start)
            while (base == 0 or window) and self.running:
                # Keep Alive
                if time.time() - last_ack_time > self.keep_alive_interval:
                    send_sock.sendto(b"ALIVE|", addr)
                    last_ack_time = time.time()

                # Filling the window
                while next_seq < base + int(cwd):
                    chunk = f.read(60000) # Datagram < 64kB Requirement
                    if not chunk: break
                    packet = next_seq.to_bytes(4, 'big') + chunk
                    window[next_seq] = {"data": packet, "time": time.time(), "acked": False}
                    
                    if random.random() >= self.loss_chance: 
                        send_sock.sendto(packet, addr)
                    next_seq += 1

                # ACK Processing
                try:
                    for _ in range(int(cwd)):
                        ack_data, _ = send_sock.recvfrom(1024)
                        if ack_data.startswith(b"DROP"):
                            print(f"[RUDP] Simulated Drop: {ack_data.decode()} from {addr}")
                            continue
                        if ack_data.startswith(b"ACK|"):
                            a_seq = int(ack_data.decode().split("|")[1])
                            if a_seq in window and not window[a_seq]["acked"]:
                                window[a_seq]["acked"] = True
                                last_ack_time = time.time()
                                if cwd < ssthresh: cwd += 1.0
                                else: cwd += (1.0 / int(cwd))
                except: pass

                # Slide window
                while base in window and window[base]["acked"]:
                    del window[base]
                    base += 1

                # Retransmission on Timeout
                now = time.time()
                for s, p in window.items():
                    if not p["acked"] and (now - p["time"]) > 0.2:
                        ssthresh = max(int(cwd) // 2, 2)
                        cwd = 2.0
                        send_sock.sendto(p["data"], addr)
                        p["time"] = now
                
                if not window and not chunk: break
            for _ in range(5): send_sock.sendto(b"FIN|DONE", addr)
        send_sock.close()

if __name__ == "__main__":
    UnifiedServer().start()