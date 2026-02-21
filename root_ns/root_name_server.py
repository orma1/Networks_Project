import socket
import threading
import sys
import os
import json
import yaml
from pathlib import Path
from dnslib import DNSRecord, QTYPE, RR, A, NS, RCODE

class LocalRootServer:
    def __init__(self, config_filename="root_config.yaml"):
        print("[*] Booting Local Root Server...")
        
        # 1. Calculate Paths
        self.project_root = Path(__file__).resolve().parent.parent
        self.config_path = self.project_root / "configs" / config_filename
        
        # 2. Load Configuration
        self._load_config()
        
        # 3. Load Zone Data
        self.tld_records = self._load_zone_data()
        
        self.running = False
        self.server_sock = None

    def _load_config(self):
        if not self.config_path.exists():
            raise FileNotFoundError(f"[FATAL] Config file missing: {self.config_path}")
            
        with open(self.config_path, 'r') as f:
            config = yaml.safe_load(f)
            self.ip = config['server'].get('bind_ip', '127.0.0.10')
            self.port = config['server'].get('bind_port', 53)
            self.buffer_size = config['server'].get('buffer_size', 512)
            self.zone_file_path = self.project_root / config['data'].get('zone_file', 'zones/root.zone.json')

    def _load_zone_data(self) -> dict:
        if not self.zone_file_path.exists():
            print(f"[WARNING] Zone file not found at {self.zone_file_path}. Starting with empty zones.")
            return {}
            
        try:
            with open(self.zone_file_path, 'r') as f:
                data = json.load(f)
                print(f"[*] Successfully loaded {len(data)} TLD zones.")
                return data
        except json.JSONDecodeError as e:
            print(f"[FATAL] Invalid JSON in zone file: {e}")
            sys.exit(1)

    def extract_tld(self, qname: str) -> str:
        parts = qname.strip(".").split(".")
        if len(parts) > 0:
            return parts[-1] + "."
        return ""

    def handle_query(self, data, addr, sock):
        try:
            request = DNSRecord.parse(data)
            qname = str(request.q.qname)
            
            reply = request.reply()
            reply.header.ra = 0 
            reply.header.aa = 1 

            tld = self.extract_tld(qname)
            
            if tld in self.tld_records:
                print(f"[*] Delegating {qname} -> {self.tld_records[tld]['ns_ip']}")
                ns_name = self.tld_records[tld]["ns_name"]
                ns_ip = self.tld_records[tld]["ns_ip"]
                
                # Delegation logic
                reply.add_auth(RR(tld, QTYPE.NS, rdata=NS(ns_name), ttl=86400))
                reply.add_ar(RR(ns_name, QTYPE.A, rdata=A(ns_ip), ttl=86400))
            else:
                print(f"[*] NXDOMAIN: Unknown TLD '{tld}' for query {qname}")
                reply.header.rcode = getattr(RCODE, 'NXDOMAIN')

            sock.sendto(reply.pack(), addr)
            
        except Exception as e:
            print(f"[ERROR] Handling query: {e}")

    def _listening_loop(self):
        print(f"[*] Root Server Active on {self.ip}:{self.port}")
        while self.running:
            try:
                data, addr = self.server_sock.recvfrom(self.buffer_size)
                worker = threading.Thread(target=self.handle_query, args=(data, addr, self.server_sock))
                worker.daemon = True
                worker.start()
            except OSError:
                break

    def start(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.server_sock.bind((self.ip, self.port))
            self.running = True
            
            listener = threading.Thread(target=self._listening_loop)
            listener.daemon = True
            listener.start()
            
            # Keep main thread alive
            while self.running:
                threading.Event().wait(1)
                
        except KeyboardInterrupt:
            self.stop()
        except OSError as e:
            print(f"[FATAL] Could not bind Root Server: {e}")

    def stop(self):
        print("\n[*] Shutting down Root Server...")
        self.running = False
        if self.server_sock:
            self.server_sock.close()
        sys.exit(0)

if __name__ == "__main__":
    root = LocalRootServer()
    root.start()