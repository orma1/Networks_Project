import socket, os, sys, threading, yaml, random, logging, signal

# טיפול בנתיבים
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, '..'))
sys.path.append(PARENT_DIR)

from dhcp_helper import VirtualNetworkInterface

logging.basicConfig(level=logging.INFO)

class OriginServer:
    def __init__(self):
        self.running = True
        signal.signal(signal.SIGINT, self.shutdown)

        # 1. טעינת קונפיגורציה בנתיב כללי
        with open("movies.yaml", 'r' , encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        # 2. קבלת IP מה-DHCP
        self.v_net = VirtualNetworkInterface(client_name="OriginServer" , fixed_id="OriginServer")
        self.my_ip = self.v_net.setup_network()
        
        self.port = self.config['server_config']['origin_port']
        self.loss_chance = self.config['server_config'].get('packet_loss_chance', 0.0)
        
        # 3. פתיחת Socket האזנה
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.my_ip, self.port))
        self.sock.settimeout(1.0)

    def shutdown(self, signum, frame):
        print("\n[*] Origin Server shutting down...")
        self.running = False
        sys.exit(0)

    def start(self):
        print(f"[*] Origin Server listening on {self.my_ip}:{self.port}")
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                msg = data.decode().split("|")
                if msg[0] == "REQ":
                    filename = msg[1]
                    threading.Thread(target=self.handle_request, args=(filename, addr), daemon=True).start()
            except socket.timeout: continue
            except Exception as e: print(f"Error: {e}")

    def handle_request(self, filename, addr):
        # חיפוש הקובץ בתיקייה המקומית
        file_path = os.path.join(BASE_DIR, filename)
        if not os.path.exists(file_path):
            print(f"[!] File {filename} not found")
            return

        print(f"[+] Sending {filename} to {addr} (Thread: {threading.get_ident()})")
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        send_sock.settimeout(0.5)
        
        try:
            with open(file_path, "rb") as f:
                seq = 0
                while self.running:
                    chunk = f.read(30000)
                    if not chunk:
                        send_sock.sendto(b"FIN|DONE", addr)
                        break
                    
                    packet = seq.to_bytes(4, 'big') + chunk
                    acked = False
                    while not acked and self.running:
                        if random.random() >= self.loss_chance:
                            send_sock.sendto(packet, addr)
                        try:
                            ack_data, _ = send_sock.recvfrom(1024)
                            if ack_data.decode() == f"ACK|{seq}":
                                acked = True
                        except socket.timeout: continue
                    seq += 1
        finally:
            send_sock.close()

if __name__ == "__main__":
    OriginServer().start()