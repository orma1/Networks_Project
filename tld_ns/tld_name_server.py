import socket
import threading
import sys
import yaml
from pathlib import Path
from dnslib import DNSRecord, QTYPE, RR, RCODE

class LocalTLDServer:
    def __init__(self, config_filename="tld_config.yaml"):
        print("[*] Booting Local TLD Server (.homelab)...")
        
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
            self.ip = config['server'].get('bind_ip', '127.0.0.11')
            self.port = config['server'].get('bind_port', 53)
            self.buffer_size = config['server'].get('buffer_size', 512)
            self.zone_file_path = self.project_root / config['data'].get('zone_file', 'zones/tld.zone.json')
            self.zone_directory_path = self.project_root / config['data'].get('zone_directory', 'zones/auth/')

    def _load_zone_data(self) -> dict:
        # Assuming you update your __init__ to read config.data.get('zone_directory')
        zone_dir = self.zone_directory_path 
        
        if not zone_dir.exists() or not zone_dir.is_dir():
            print(f"[FATAL] Zone directory not found at {zone_dir}.")
            sys.exit(1)
            
        zone_db = {}
        loaded_files = 0
        total_records = 0

        # Loop through every .zone file in the folder
        for zone_file in zone_dir.glob("*.zone"):
            try:
                with open(zone_file, 'r') as f:
                    zone_text = f.read()
                    
                parsed_records = RR.fromZone(zone_text)
                loaded_files += 1
                total_records += len(parsed_records)
                
                # Merge into the master memory dictionary
                for rr in parsed_records:
                    name = str(rr.rname)
                    rtype = rr.rtype
                    
                    if name not in zone_db:
                        zone_db[name] = {}
                    if rtype not in zone_db[name]:
                        zone_db[name][rtype] = []
                        
                    zone_db[name][rtype].append(rr)
                    
                print(f"[*] Loaded {len(parsed_records)} records from {zone_file.name}")
                
            except Exception as e:
                print(f"[ERROR] Failed to parse {zone_file.name}: {e}")

        print(f"[*] Successfully loaded {total_records} total records across {loaded_files} zone files.")
        return zone_db

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
            reply.header.aa = 0 # TLD is not the final authority for A records
            
            domain = self.extract_domain(qname)
            
            if domain in self.zone_records and getattr(QTYPE, 'NS') in self.zone_records[domain]:
                print(f"[*] Delegating {qname} to {domain} nameservers.")
                
                # 1. Add NS records to Authority Section
                for ns_rr in self.zone_records[domain][getattr(QTYPE, 'NS')]:
                    reply.add_auth(ns_rr)
                    
                    # 2. Find the Glue A record
                    target_ns = str(ns_rr.rdata)
                    if target_ns in self.zone_records and getattr(QTYPE, 'A') in self.zone_records[target_ns]:
                        for a_rr in self.zone_records[target_ns][getattr(QTYPE, 'A')]:
                            reply.add_ar(a_rr)
            else:
                print(f"[*] NXDOMAIN: Domain '{domain}' not registered in this TLD.")
                reply.header.rcode = getattr(RCODE, 'NXDOMAIN')

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