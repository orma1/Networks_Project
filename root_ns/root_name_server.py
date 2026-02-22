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
        self.zone_records = self._load_zone_data()
        
        self.running = False
        self.server_sock = None

    def _load_config(self):
        if not self.config_path.exists():
            raise FileNotFoundError(f"[FATAL] Config file missing: {self.config_path}")
            
        with open(self.config_path, 'r') as f:
            config = yaml.safe_load(f)
            self.ip = config['server'].get('bind_ip', '127.0.0.3')
            self.port = config['server'].get('bind_port', 53)
            self.buffer_size = config['server'].get('buffer_size', 512)
            self.zone_file_path = self.project_root / config['data'].get('zone_file', 'zones/root.zone.json')

    def _load_zone_data(self) -> dict:
        if not self.zone_file_path.exists():
            print(f"[WARNING] Zone file not found at {self.zone_file_path}.")
            return {}
        try:
            with open(self.zone_file_path, 'r') as f:
                zone_text = f.read()
            parsed_records = RR.fromZone(zone_text)
            zone_db = {}
            for rr in parsed_records:
                name = str(rr.rname)
                rtype = rr.rtype
                if name not in zone_db: zone_db[name] = {}
                if rtype not in zone_db[name]: zone_db[name][rtype] = []
                zone_db[name][rtype].append(rr)
            print(f"[*] Successfully loaded {len(parsed_records)} records from Root BIND zone.")
            return zone_db
        except Exception as e:
            print(f"[FATAL] Failed to parse Root zone: {e}")
            sys.exit(1)

    def handle_query(self, data, addr, sock):
        try:
            request = DNSRecord.parse(data)
            qname = str(request.q.qname)
            
            reply = request.reply()
            reply.header.ra = 0 
            reply.header.aa = 1 

            tld = self.extract_tld(qname)
            
            if tld in self.zone_records and getattr(QTYPE, 'NS') in self.zone_records[tld]:
                print(f"[*] Delegating {qname} to .{tld} nameservers.")
                
                # 1. Add NS records to Authority Section
                for ns_rr in self.zone_records[tld][getattr(QTYPE, 'NS')]:
                    reply.add_auth(ns_rr)
                    
                    # 2. Find the Glue A record for this NS and add to Additional Section
                    target_ns = str(ns_rr.rdata)
                    if target_ns in self.zone_records and getattr(QTYPE, 'A') in self.zone_records[target_ns]:
                        for a_rr in self.zone_records[target_ns][getattr(QTYPE, 'A')]:
                            reply.add_ar(a_rr)
            else:
                print(f"[*] NXDOMAIN: Unknown TLD '{tld}' for query {qname}")
                reply.header.rcode = getattr(RCODE, 'NXDOMAIN')

            sock.sendto(reply.pack(), addr)
        except Exception as e:
            print(f"[ERROR] Handling query: {e}")

    def extract_tld(self, qname: str) -> str:
        parts = qname.strip(".").split(".")
        if len(parts) > 0:
            return parts[-1] + "."
        return ""

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
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
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