import os, socket, threading, yaml
from proto_tcp import handle_tcp_server_connection
from proto_rudp import RUDPSession

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# IMPORTANT: Video must be in a folder called 'storage' next to this file!
VIDEO_DIR = os.path.join(BASE_DIR, "storage")
os.makedirs(VIDEO_DIR, exist_ok=True)

class Server:
    def __init__(self):
        with open(os.path.join(BASE_DIR, "server_config.yaml"), "r", encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)

    def run_tcp(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("0.0.0.0", 8000))
        s.listen(5)
        print("[*] TCP Server on 8000")
        while True:
            c, a = s.accept()
            threading.Thread(target=handle_tcp_server_connection, args=(c, a, VIDEO_DIR)).start()

    def run_rudp(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("0.0.0.0", 9000))
        print("[*] RUDP Server on 9000")
        while True:
            data, addr = s.recvfrom(1024)
            msg = data.decode()
            if msg.startswith("REQ|"):
                fname = msg.split("|")[1]
                path = os.path.join(VIDEO_DIR, fname)
                if os.path.exists(path):
                    sess = RUDPSession(addr, path, int(msg.split("|")[2]))
                    threading.Thread(target=sess.run).start()

    def start(self):
        threading.Thread(target=self.run_tcp).start()
        self.run_rudp()

if __name__ == "__main__":
    Server().start()