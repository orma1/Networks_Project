import socket
import threading
import sys
import os
import json
import yaml
from pathlib import Path
from dnslib import DNSRecord, QTYPE, RR, A, NS, RCODE

class LocalTLDServer:
    def __init__(self, config_filename="tld_config.yaml"):
        print("[*] Booting Local TLD Server (.homelab)...")
        
        self.project_root = Path(__file__).resolve().parent.parent
        self.config_path = self.project_root / "configs" / config_filename
        
        self._load_config()
        self.domain_records = self._load_zone_data()
        
        self.running = False
        self.server_sock = None

    def _load_config(self):
        if not self.config_path.exists():
            raise FileNotFoundError(f"[FATAL] Config file missing: {self.config_path}")
            
        with open(self.config_path, 'r') as f:
            config = yaml.safe_load(f)
            self.ip = config['server'].get('bind_ip', '127.0.0.11')
            self.port = config['server'].get('bind_port', 53)
            self.buffer_size = config['server'].get('buffer_size', 512)
            self.zone_file_path = self.project_root / config['data'].get('zone_file', 'zones/tld.zone.json')

    def _load_zone_data(self) -> dict:
        if not self.zone_file_path.exists():
            print(f"[WARNING] Zone file not found at {self.zone_file_path}.")
            return {}
        try:
            with open(self.zone_file_path, 'r') as f:
                data = json.load(f)
                print(f"[*] Successfully loaded {len(data)} domains for this TLD.")
                return data
        except json.JSONDecodeError as e:
            print(f"[FATAL] Invalid JSON in zone file: {e}")
            sys.exit(1)

    def extract_domain(self, qname: str) -> str:
        """ 
        Extracts the domain and TLD from a query. 
        E.g., 'www.test.homelab.' -> 'test.homelab.' 
        """
        parts = qname.strip(".").split(".")
        if len(parts) >= 2:
            return f"{parts[-2]}.{parts[-1]}."
        return qname

    def handle_query(self, data, addr, sock):
        try:
            request = DNSRecord.parse(data)
            qname = str(request.q.qname)
            
            reply = request.reply()
            reply.header.ra = 0 
            reply.header.aa = 0 # TLD is not the final authority for the A record
            
            # Identify which domain they are asking for
            domain = self.extract_domain(qname)
            
            if domain in self.domain_records:
                print(f"[*] Delegating {qname} -> {self.domain_records[domain]['ns_ip']}")
                ns_name = self.domain_records[domain]["ns_name"]
                ns_ip = self.domain_records[domain]["ns_ip"]
                
                # Delegation: Authority and Additional Sections
                reply.add_auth(RR(domain, QTYPE.NS, rdata=NS(ns_name), ttl=86400))
                reply.add_ar(RR(ns_name, QTYPE.A, rdata=A(ns_ip), ttl=86400))
            else:
                print(f"[*] NXDOMAIN: Domain '{domain}' not registered in this TLD.")
                reply.header.rcode = getattr(RCODE, 'NXDOMAIN')
                # Note: In the future, this is exactly where we would attach an NSEC record 
                # to prove alphabetically that the domain doesn't exist!

            sock.sendto(reply.pack(), addr)
            
        except Exception as e:
            print(f"[ERROR] Handling query: {e}")

    def _listening_loop(self):
        print(f"[*] TLD Server Active on {self.ip}:{self.port}")
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
            
            while self.running:
                threading.Event().wait(1)
                
        except KeyboardInterrupt:
            self.stop()
        except OSError as e:
            print(f"[FATAL] Could not bind TLD Server: {e}")

    def stop(self):
        print("\n[*] Shutting down TLD Server...")
        self.running = False
        if self.server_sock:
            self.server_sock.close()
        sys.exit(0)

if __name__ == "__main__":
    tld = LocalTLDServer()
    tld.start()