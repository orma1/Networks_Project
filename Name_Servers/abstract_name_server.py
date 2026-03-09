import socket
import threading
import sys
import yaml
from abc import ABC, abstractmethod
from pathlib import Path
from dnslib import RR

class AbstractNameServer(ABC):
    def __init__(self, server_name, default_ip, config_filename, dnssec_enabled=False):
        print(f"[*] Booting {server_name}...")
        self.dnssec_enabled = dnssec_enabled
        
        # Path Resolution
        self.project_root = Path(__file__).resolve().parent.parent
        self.config_path = self.project_root / "configs" / config_filename
        
        # Load Config and Zone Data
        self._load_config(default_ip)
        self.zone_records = self._load_zone_data()
        
        self.running = False
        self.server_sock = None

    def _load_config(self, default_ip):
        if not self.config_path.exists():
            raise FileNotFoundError(f"[FATAL] Config file missing: {self.config_path}")
            
        with open(self.config_path, 'r') as f:
            config = yaml.safe_load(f)
            self.ip = config['server'].get('bind_ip', default_ip)
            self.port = config['server'].get('bind_port', 53)
            self.buffer_size = config['server'].get('buffer_size', 512)
            self.zone_directory_path = self.project_root / config['data'].get('zone_directory', 'zones/auth/')

    def _load_zone_data(self) -> dict:
        zone_dir = self.zone_directory_path 
        if not zone_dir.exists() or not zone_dir.is_dir():
            print(f"[FATAL] Zone directory not found at {zone_dir}.")
            sys.exit(1)
            
        zone_db = {}
        loaded_files = 0
        total_records = 0

        for zone_file in zone_dir.glob("*.zone"):
            is_signed_file = zone_file.name.endswith(".signed.zone")
            
            # Filter logic for DNSSEC
            if self.dnssec_enabled and not is_signed_file:
                continue
            if not self.dnssec_enabled and is_signed_file:
                continue

            try:
                with open(zone_file, 'r') as f:
                    zone_text = f.read()
                    
                parsed_records = RR.fromZone(zone_text)
                loaded_files += 1
                total_records += len(parsed_records)
                
                for rr in parsed_records:
                    name = str(rr.rname)
                    rtype = rr.rtype
                    zone_db.setdefault(name, {}).setdefault(rtype, []).append(rr)
                    
                print(f"[*] Loaded {len(parsed_records)} records from {zone_file.name}")
            except Exception as e:
                print(f"[ERROR] Failed to parse {zone_file.name}: {e}")

        print(f"[*] Successfully loaded {total_records} total records across {loaded_files} files.")
        return zone_db

    @abstractmethod
    def handle_query(self, data, addr, sock):
        """Each server type implements its own resolution logic here."""
        pass

    def _listening_loop(self):
        print(f"[*] Server Active on {self.ip}:{self.port}")
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
            print(f"[FATAL] Could not bind server: {e}")

    def stop(self):
        print(f"\n[*] Shutting down server...")
        self.running = False
        if self.server_sock:
            self.server_sock.close()
        sys.exit(0)