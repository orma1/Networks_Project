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
                data = json.load(f)
                print(f"[*] Successfully loaded {len(data)} A-records.")
                return data
        except json.JSONDecodeError as e:
            print(f"[FATAL] Invalid JSON in zone file: {e}")
            sys.exit(1)

    def handle_query(self, data, addr, sock):
        try:
            request = DNSRecord.parse(data)
            qname = str(request.q.qname)
            qtype = request.q.qtype
            
            reply = request.reply()
            reply.header.ra = 0 
            reply.header.aa = 1 
            
            # --- ANSWER LOGIC ---
            if qname in self.zone_records:
                node = self.zone_records[qname] # E.g., {"A": "192.168.1.100"}
                
                # CASE 1: They specifically asked for a CNAME
                if qtype == getattr(QTYPE, 'CNAME') and "CNAME" in node:
                    cname_target = node["CNAME"]
                    print(f"[*] ANSWER: {qname} (CNAME) -> {cname_target}")
                    reply.add_answer(RR(qname, QTYPE.CNAME, rdata=CNAME(cname_target), ttl=300))
                
                # CASE 2: They asked for an A record
                elif qtype == getattr(QTYPE, 'A'):
                    if "A" in node:
                        # Standard A record response
                        ip_address = node["A"]
                        print(f"[*] ANSWER: {qname} (A) -> {ip_address}")
                        reply.add_answer(RR(qname, QTYPE.A, rdata=A(ip_address), ttl=300))
                        
                    elif "CNAME" in node:
                        # CNAME RESOLUTION!
                        cname_target = node["CNAME"]
                        print(f"[*] ALIAS FOUND: {qname} is a CNAME for {cname_target}")
                        
                        # Add the CNAME record to the answer
                        reply.add_answer(RR(qname, QTYPE.CNAME, rdata=CNAME(cname_target), ttl=300))
                        
                        # PRO MOVE: Do we also host the A record for the target? If so, attach it!
                        if cname_target in self.zone_records and "A" in self.zone_records[cname_target]:
                            target_ip = self.zone_records[cname_target]["A"]
                            print(f"[*] CNAME CHASE: Piggybacking A record for {cname_target} -> {target_ip}")
                            reply.add_answer(RR(cname_target, QTYPE.A, rdata=A(target_ip), ttl=300))
                    else:
                        print(f"[*] NODATA: Name '{qname}' exists, but no A/CNAME records.")
                        
                else:
                    print(f"[*] NODATA: Name '{qname}' exists, but unsupported QTYPE {qtype}.")
                    
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