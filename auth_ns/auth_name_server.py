import socket
import threading
import sys
import json
import yaml
from pathlib import Path
from dnslib import DNSRecord, QTYPE, RR, A, CNAME, RCODE

class LocalAuthServer:
    def __init__(self, config_filename="auth_config.yaml"):
        print("[*] Booting Local Authoritative Server (test.homelab)...")
        
        self.project_root = Path(__file__).resolve().parent.parent
        self.config_path = self.project_root / "configs" / config_filename
        
        self._load_config()
        self.zone_records = self._load_zone_data()
        
        self.running = False
        self.server_sock = None

    def _load_config(self):
        if not self.config_path.exists():
            raise FileNotFoundError(f"[FATAL] Config file missing: {self.config_path}")
            
        with open(self.config_path, 'r') as f:
            config = yaml.safe_load(f)
            self.ip = config['server'].get('bind_ip', '127.0.0.12')
            self.port = config['server'].get('bind_port', 53)
            self.buffer_size = config['server'].get('buffer_size', 512)
            self.zone_file_path = self.project_root / config['data'].get('zone_file', 'zones/auth.zone.json')

    def _load_zone_data(self) -> dict:
        if not self.zone_file_path.exists():
            print(f"[WARNING] Zone file not found at {self.zone_file_path}.")
            return {}
            
        try:
            with open(self.zone_file_path, 'r') as f:
                zone_text = f.read()
                
            # dnslib magic: Parses the entire BIND file into RR objects
            parsed_records = RR.fromZone(zone_text)
            
            # Organize them into a dictionary for instant lookups:
            # { "www.test.homelab.": { QTYPE.A: [RR1, RR2], QTYPE.CNAME: [RR3] } }
            zone_db = {}
            for rr in parsed_records:
                name = str(rr.rname)
                rtype = rr.rtype
                
                if name not in zone_db:
                    zone_db[name] = {}
                if rtype not in zone_db[name]:
                    zone_db[name][rtype] = []
                    
                zone_db[name][rtype].append(rr)
                
            print(f"[*] Successfully loaded {len(parsed_records)} records from BIND zone file.")
            return zone_db
            
        except Exception as e:
            print(f"[FATAL] Failed to parse BIND zone file: {e}")
            sys.exit(1)

    def handle_query(self, data, addr, sock):
        try:
            request = DNSRecord.parse(data)
            qname = str(request.q.qname)
            qtype = request.q.qtype
            
            reply = request.reply()
            reply.header.ra = 0 
            reply.header.aa = 1 
            
            # Use our new zone_records (renamed from a_records)
            if qname in self.zone_records:
                node = self.zone_records[qname]
                
                # CASE 1: They asked for a specific record type (like A or NS)
                if qtype in node:
                    for rr in node[qtype]:
                        reply.add_answer(rr)
                        print(f"[*] ANSWER: Appended {QTYPE[qtype]} record for {qname}")
                
                # CASE 2: CNAME resolution (They asked for A, but we only have CNAME)
                elif getattr(QTYPE, 'CNAME') in node and qtype == getattr(QTYPE, 'A'):
                    for cname_rr in node[getattr(QTYPE, 'CNAME')]:
                        reply.add_answer(cname_rr)
                        
                        # CNAME Chasing: Do we also have the A record for the target?
                        target_name = str(cname_rr.rdata)
                        if target_name in self.zone_records and getattr(QTYPE, 'A') in self.zone_records[target_name]:
                            for target_a_rr in self.zone_records[target_name][getattr(QTYPE, 'A')]:
                                reply.add_answer(target_a_rr)
                                print(f"[*] CNAME CHASE: Appended A record for {target_name}")
                
                # CASE 3: The name exists, but not the requested type
                else:
                    print(f"[*] NODATA: Name '{qname}' exists, but no {QTYPE[qtype]} records.")
            else:
                print(f"[*] NXDOMAIN: Domain '{qname}' does not exist.")
                reply.header.rcode = getattr(RCODE, 'NXDOMAIN')
            
            sock.sendto(reply.pack(), addr)

        except Exception as e:
            print(f"[ERROR] Handling query: {e}")

    def _listening_loop(self):
        print(f"[*] Authoritative Server Active on {self.ip}:{self.port}")
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
            
            while self.running:
                threading.Event().wait(1)
                
        except KeyboardInterrupt:
            self.stop()
        except OSError as e:
            print(f"[FATAL] Could not bind Auth Server: {e}")

    def stop(self):
        print("\n[*] Shutting down Authoritative Server...")
        self.running = False
        if self.server_sock:
            self.server_sock.close()
        sys.exit(0)

if __name__ == "__main__":
    auth = LocalAuthServer()
    auth.start()