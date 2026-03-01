import socket, os, sys, threading, yaml, signal

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, '..'))
VIDEO_DIR = os.path.join(BASE_DIR, 'videos')
sys.path.append(PARENT_DIR)

from dhcp_helper import VirtualNetworkInterface

class TCPServer:
    def __init__(self):
        self.running = True
        signal.signal(signal.SIGINT, self.shutdown)

        with open("movies.yaml", 'r' , encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        
        self.v_net = VirtualNetworkInterface(client_name="OriginServerTCP", fixed_id="OriginServerTCP")
        self.my_ip = self.v_net.setup_network()
        
        self.port = self.config['server_config']['origin_port']
        
        # Initialize TCP Socket (SOCK_STREAM)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind((self.my_ip, self.port))
            self.sock.listen(1)
            self.sock.settimeout(1.0)
        except Exception as e:
            print(f"Error binding socket: {e}")
            sys.exit(1)

    def shutdown(self, signum, frame):
        print("\n[*] TCP Server shutting down...")
        self.running = False
        sys.exit(0)

    def start(self):
        print(f"[*] TCP Server listening on {self.my_ip}:{self.port}")
        while self.running:
            try:
                # Accept new TCP connection
                conn, addr = self.sock.accept()
                threading.Thread(target=self.handle_connection, args=(conn, addr), daemon=True).start()
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Error: {e}")

    def handle_connection(self, conn, addr):
        print(addr[0])
        try:
            data = conn.recv(1024).decode().split("|")
            if data[0] == "REQ":
                filename = data[1]
                file_path = os.path.join(VIDEO_DIR, filename)
                
                if not os.path.exists(file_path):
                    print(f"[!] File {filename} not found")
                    return

                print(f"[+] Sending {filename} to {addr} via TCP")
                with open(file_path, "rb") as f:
                    while self.running:
                        chunk = f.read(65536) # 64KB chunks
                        if not chunk:
                            break
                        conn.sendall(chunk) # TCP handles ACKs and retransmission
        except Exception as e:
            print(f"[!] Connection error with {addr}: {e}")
        finally:
            conn.close()

if __name__ == "__main__":
    TCPServer().start()