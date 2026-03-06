import socket
import threading
import sys
import argparse
import yaml
from pathlib import Path
from dnslib import DNSRecord, QTYPE, RR, RCODE

class LocalAuthServer:
    def __init__(self, config_filename="auth_config.yaml", dnssec_enabled=False):
        print("[*] Booting Local Authoritative Server (test.homelab)...")
        self.dnssec_enabled = dnssec_enabled

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
            is_signed_file = str(zone_file).endswith(".signed.zone")
            
            # If DNSSEC is ON, skip standard files
            if self.dnssec_enabled and not is_signed_file:
                continue
            # If DNSSEC is OFF, skip signed files
            if not self.dnssec_enabled and is_signed_file:
                continue
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

                    if getattr(self, 'dnssec_enabled', False) and getattr(QTYPE, 'TXT') in node:
                        for txt_rr in node[getattr(QTYPE, 'TXT')]:
                            txt_data = str(txt_rr.rdata).strip('"')
                            # If the TXT record is an RRSIG for the record type we just answered, attach it!
                            if txt_data.startswith(f"RRSIG|{QTYPE[qtype]}|"):
                                reply.add_answer(txt_rr)
                                print(f"    [+] DNSSEC: Attached RRSIG for {QTYPE[qtype]}")
                
                # CASE 2: CNAME resolution (They asked for A, but we only have CNAME)
                elif getattr(QTYPE, 'CNAME') in node and qtype == getattr(QTYPE, 'A'):
                    for cname_rr in node[getattr(QTYPE, 'CNAME')]:
                        reply.add_answer(cname_rr)

                        # Attach CNAME Signature
                        if getattr(self, 'dnssec_enabled', False) and getattr(QTYPE, 'TXT') in node:
                            for txt_rr in node[getattr(QTYPE, 'TXT')]:
                                if str(txt_rr.rdata).strip('"').startswith("RRSIG|CNAME|"):
                                    reply.add_answer(txt_rr)
                                    print(f"    [+] DNSSEC: Attached RRSIG for CNAME")
                                    
                        # CNAME Chasing: Do we also have the A record for the target?
                        target_name = str(cname_rr.rdata)
                        if target_name in self.zone_records and getattr(QTYPE, 'A') in self.zone_records[target_name]:
                            target_node = self.zone_records[target_name]
                            for target_a_rr in target_node[getattr(QTYPE, 'A')]:
                                reply.add_answer(target_a_rr)
                                print(f"[*] CNAME CHASE: Appended A record for {target_name}")

                            # Attach Chased A Record Signature
                            if getattr(self, 'dnssec_enabled', False) and getattr(QTYPE, 'TXT') in target_node:
                                for txt_rr in target_node[getattr(QTYPE, 'TXT')]:
                                    if str(txt_rr.rdata).strip('"').startswith("RRSIG|A|"):
                                        reply.add_answer(txt_rr)
                                        print(f"    [+] DNSSEC: Attached RRSIG for Chased A record")
                
                # CASE 3: The name exists, but not the requested type
                else:
                    print(f"[*] NODATA: Name '{qname}' exists, but no {QTYPE[qtype]} records.")
            else:
                print(f"[*] NXDOMAIN: Domain '{qname}' does not exist.")
                reply.header.rcode = getattr(RCODE, 'NXDOMAIN')
            
            # --- NEW: ADD SOA RECORD FOR NXDOMAIN AND NODATA ---
            # If we didn't add any answers, we must provide the SOA record in the Authority section
            if len(reply.rr) == 0:
                parts = qname.strip(".").split(".")
                # Climb up the domain tree (x.test.homelab. -> test.homelab. -> homelab.)
                for i in range(len(parts)):
                    apex = ".".join(parts[i:]) + "."
                    if apex in self.zone_records and getattr(QTYPE, 'SOA') in self.zone_records[apex]:
                        # 1. Add the SOA record
                        for soa_rr in self.zone_records[apex][getattr(QTYPE, 'SOA')]:
                            reply.add_auth(soa_rr)
                            print(f"    [*] Attached SOA record for {apex} to Authority section.")
                        
                        # 2. Add the RRSIG for the SOA record (if DNSSEC is enabled)
                        if getattr(self, 'dnssec_enabled', False) and getattr(QTYPE, 'TXT') in self.zone_records[apex]:
                            for txt_rr in self.zone_records[apex][getattr(QTYPE, 'TXT')]:
                                if str(txt_rr.rdata).strip('"').startswith("RRSIG|SOA|"):
                                    reply.add_auth(txt_rr)
                                    print(f"    [+] DNSSEC: Attached RRSIG for SOA record")
                        break

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
        if hasattr(socket, 'SO_REUSEPORT'):
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--dnssec', action='store_true')
    args = parser.parse_args()
    auth = LocalAuthServer(dnssec_enabled=args)
    auth.start()