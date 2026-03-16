import sys
from enum import IntEnum
from pathlib import Path
import socket, yaml, time, threading, json, os, sys, queue
from scapy.all import *

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from dhcp.arp_probe import probe_ip_loopback, probe_ip_real
BASE_DIR = os.path.dirname(__file__)

class DHCPMessageType(IntEnum):
    DISCOVER = 1
    REQUEST  = 3
    RELEASE  = 7

class DHCPServer:
    def __init__(self):
        self.project_root = Path(__file__).resolve().parent.parent
        self.config_path = self.project_root / "dhcp" / "configs" / "dhcp_config.yaml"
        with open(self.config_path, 'r') as f:
            self.conf = yaml.safe_load(f)['dhcp_server']
        self.PORT_S, self.running = 6700, True
        self.lock, self.work_queue = threading.Lock(), queue.Queue()
        
        # Determine which IP pool to use based on the 'loopback' setting
        pool = self.conf['loopback_pool'] if self.conf['loopback'] else self.conf['outside_pool']
        self.pool_start = pool['start_ip']
        self.pool_end = pool['end_ip']
        self.current_pool_ip = pool['start_ip']
        self.end_ip = pool['end_ip']
        
        self.available_reclaimed_ips = []
        self.active_leases = self.load_leases()
        self.client_to_ip = {data['client_id']: ip for ip, data in self.active_leases.items()}
        self.reservations = self.conf['reservations_loopback'] if self.conf['loopback'] else self.conf['reservations_outside']
        self.persistence_file = os.path.join(BASE_DIR, self.conf['persistence_file'])
        
        # ✅ FIXED: Properly initialize pool pointer to skip only CURRENTLY LEASED IPs
        self._init_pool_pointer()

    def _init_pool_pointer(self):
        """Initialize pool pointer to skip currently active leases only."""
        pool_octets = list(map(int, self.pool_start.split('.')))
        end_octets = list(map(int, self.pool_end.split('.')))
        max_octet = end_octets[3]
        
        # Find the highest leased IP in the pool
        highest_leased_octet = pool_octets[3] - 1
        
        for ip in self.active_leases:
            ip_octets = list(map(int, ip.split('.')))
            # Check if IP is in this pool
            if ip_octets[:3] == pool_octets[:3] and pool_octets[3] <= ip_octets[3] <= max_octet:
                highest_leased_octet = max(highest_leased_octet, ip_octets[3])
        
        # Set pool pointer to the next IP after the highest leased one
        if highest_leased_octet >= pool_octets[3]:
            pool_octets[3] = min(highest_leased_octet + 1, max_octet)
        
        self.current_pool_ip = ".".join(map(str, pool_octets))
        print(f"[DHCP] Pool initialized: {self.pool_start} to {self.pool_end}, next: {self.current_pool_ip}")

    def save_leases(self):
        """Save leases to disk without blocking."""
        try:
            with self.lock:
                data_to_save = dict(self.active_leases)
            with open(self.persistence_file, 'w') as f:
                json.dump(data_to_save, f, indent=4)
        except Exception as e:
            print(f"[!] Save Error: {e}")

    def load_leases(self):
        """Load leases from disk at startup."""
        if os.path.exists(self.conf['persistence_file']):
            try:
                with open(self.conf['persistence_file'], 'r') as f:
                    leases = json.load(f)
                    print(f"[DHCP] Loaded {len(leases)} leases from disk")
                    return leases
            except Exception as e:
                print(f"[!] Load Error: {e}")
        return {}

    def lease_cleanup_thread(self):
        """Background thread to reclaim IPs when lease time expires."""
        while self.running:
            time.sleep(5)
            now = time.time()
            expired_ips = []

            with self.lock:
                expired_ips = [ip for ip, data in self.active_leases.items() if now > data['expiry']]
                if not expired_ips:
                    continue

                for ip in expired_ips:
                    client_id = self.active_leases[ip].get('client_id', 'unknown')
                    print(f"\n[EXPIRE] 🗑️  Reclaiming {ip} from {client_id}")
                    
                    if ip in self.active_leases:
                        del self.active_leases[ip]
                    if client_id in self.client_to_ip:
                        del self.client_to_ip[client_id]
                    
                    # ✅ Add back to reclaimed list for reuse
                    if ip not in self.available_reclaimed_ips and ip not in self.reservations.values():
                        self.available_reclaimed_ips.append(ip)

            try:
                self.work_queue.put(("save", None))
            except Exception as e:
                print(f"[!] Error saving leases: {e}")

    def _advance_pool_ip(self):
        """Get next IP from pool, skipping reserved IPs. Called with lock held."""
        reserved_ips = set(self.reservations.values())
        end_octet = int(self.pool_end.split('.')[3])
        
        while True:
            octets = list(map(int, self.current_pool_ip.split('.')))
            
            # Check if we've exceeded the pool range
            if octets[3] > end_octet:
                print(f"[DHCP] Pool exhausted, wrapping around")
                self.current_pool_ip = self.pool_start
                return None
            
            # Check if current IP is reserved
            if self.current_pool_ip in reserved_ips:
                octets[3] += 1
                self.current_pool_ip = ".".join(map(str, octets))
                continue
            
            # Return this IP and advance pointer
            ip_to_return = self.current_pool_ip
            octets[3] += 1
            self.current_pool_ip = ".".join(map(str, octets))
            
            return ip_to_return

    def _probe_ip(self, ip):
        """ARP-like conflict check. Returns True if IP is in use."""
        if self.conf['loopback']:
            return probe_ip_loopback(ip)
        else:
            return probe_ip_real(ip)

    def get_next_available_ip(self, client_id):
        """Get next available IP for client, checking reservations and reclaimed IPs."""
        # ✅ CHECK RESERVATIONS FIRST (no probing needed)
        if client_id in self.reservations:
            reserved_ip = self.reservations[client_id]
            print(f"[DHCP] 🎯 Reserved IP for {client_id}: {reserved_ip}")
            return reserved_ip
        
        # Check if client already has an IP
        with self.lock:
            if client_id in self.client_to_ip:
                existing_ip = self.client_to_ip[client_id]
                print(f"[DHCP] Existing lease for {client_id}: {existing_ip}")
                return existing_ip

        # For pool IPs, try reclaimed first, then new ones
        MAX_TRIES = 10
        for attempt in range(MAX_TRIES):
            with self.lock:
                # Try reclaimed IPs first
                if self.available_reclaimed_ips:
                    candidate = self.available_reclaimed_ips.pop(0)
                    print(f"[DHCP] Using reclaimed IP: {candidate}")
                else:
                    candidate = self._advance_pool_ip()
                
                
                if candidate is None:
                    print(f"[DHCP] ❌ No IPs available in pool")
                    return None

            # Probe outside the lock (blocking network call)
            if not self._probe_ip(candidate):
                print(f"[DHCP] ✅ Assigned pool IP {candidate} to {client_id} (attempt {attempt + 1}/{MAX_TRIES})")
                return candidate

            print(f"[DHCP] ⚠️  Conflict on {candidate} — responded to probe. Skipping.")

        print(f"[DHCP] ❌ Could not find free IP after {MAX_TRIES} attempts")
        return None

    def sender_thread(self):
        """Background thread for packet sending and file I/O."""
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while self.running:
            try:
                task = self.work_queue.get(timeout=1)
                
                if task[0] == "save":
                    self.save_leases()
                else:
                    pkt_raw, target = task
                    send_sock.sendto(pkt_raw, target)
                    
                self.work_queue.task_done()
            except queue.Empty:
                continue

    def process_packet(self, pkt, addr):
        """Main packet processing for DISCOVER, REQUEST, RELEASE."""
        options = pkt[DHCP].options
        msg_type = next((opt[1] for opt in options if isinstance(opt, tuple) and opt[0] == 'message-type'), None)
        client_id = next((opt[1].decode() if isinstance(opt[1], bytes) else opt[1] 
                         for opt in options if isinstance(opt, tuple) and opt[0] == 'client_id'), None)
        
        if not client_id:
            print(f"[DHCP] ⚠️  Packet without client_id from {addr}")
            return

        if msg_type == DHCPMessageType.DISCOVER:
            print(f"\n[DISCOVER] 📥 From {client_id} at {addr}")
            offer_ip = self.get_next_available_ip(client_id)
            
            if offer_ip:
                print(f"[OFFER] 📤 Sending {offer_ip} to {client_id}")
                reply = BOOTP(op=2, yiaddr=offer_ip, xid=pkt.xid) / DHCP(options=[
                    ("message-type", "offer"), 
                    ("server_id", "127.0.0.1"),
                    ("subnet_mask", self.conf['subnet_mask']), 
                    ("router", self.conf['gateway']),
                    ("name_server", self.conf['dns_servers'][0]), 
                    ("lease_time", self.conf['lease_time']),
                    ("client_id", client_id.encode()), 
                    "end"
                ])
                self.work_queue.put((raw(reply), (addr[0], addr[1])))

        elif msg_type == DHCPMessageType.REQUEST:
            print(f"\n[REQUEST] 🤝 From {client_id}")
            target_ip = pkt.yiaddr if pkt.yiaddr != "0.0.0.0" else None
            
            if not target_ip:
                with self.lock:
                    target_ip = self.client_to_ip.get(client_id)
            
            if not target_ip:
                print(f"[NAK] ❌ No IP for {client_id}")
                nack = BOOTP(op=2, xid=pkt.xid) / DHCP(options=[
                    ("message-type", "nak"),
                    ("server_id", "127.0.0.1"),
                    "end"
                ])
                self.work_queue.put((raw(nack), (addr[0], addr[1])))
                return

            # Lease the IP
            with self.lock:
                self.active_leases[target_ip] = {
                    "expiry": time.time() + self.conf['lease_time'], 
                    "client_id": client_id
                }
                self.client_to_ip[client_id] = target_ip

            print(f"[ACK] ✅ Lease confirmed for {target_ip} → {client_id}")
            reply = BOOTP(op=2, yiaddr=target_ip, xid=pkt.xid) / DHCP(options=[
                ("message-type", "ack"), 
                ("server_id", "127.0.0.1"), 
                ("lease_time", self.conf['lease_time']), 
                "end"
            ])
            self.work_queue.put((raw(reply), (addr[0], addr[1])))
            self.work_queue.put(("save", None))

        elif msg_type == DHCPMessageType.RELEASE:
            try:
                self.release_ip(client_id, pkt.yiaddr)
            except Exception as e:
                print(f"[!] Error processing RELEASE from {client_id}: {type(e).__name__}: {e}")
            # Save outside the lock
            self.work_queue.put(("save", None))


    def release_ip(self, client_id, ip):
            print(f"\n[RELEASE] 🔄 From {client_id}")
            
            # ✅ FIXED: Proper RELEASE handling with error checking
            ip_to_release = None
            with self.lock:
                ip_to_release = self.client_to_ip.get(client_id)
                
                if ip_to_release:
                    # Verify it matches the yiaddr in the packet
                    if pkt.yiaddr and pkt.yiaddr != ip_to_release:
                        print(f"[RELEASE] ⚠️  IP mismatch: packet says {pkt.yiaddr}, table says {ip_to_release}")
                    
                    # Remove from active leases and mappings
                    if ip_to_release in self.active_leases:
                        del self.active_leases[ip_to_release]
                    del self.client_to_ip[client_id]
                    
                    # Add to reclaimed pool
                    if ip_to_release not in self.available_reclaimed_ips:
                        self.available_reclaimed_ips.append(ip_to_release)
                    
                    print(f"[RELEASE] ✅ Success: {ip_to_release} released from {client_id}")
                else:
                    print(f"[RELEASE] ⚠️  Unknown client {client_id} - no IP to release")

    def run(self):
        """Start DHCP server."""
        threading.Thread(target=self.lease_cleanup_thread, daemon=True).start()
        threading.Thread(target=self.sender_thread, daemon=True).start()
        
        try:
            recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            recv_sock.bind(("0.0.0.0", self.PORT_S))
            recv_sock.settimeout(1.0)
            print(f"\n{'='*60}")
            print(f"[*] DHCP Server active on Port {self.PORT_S}")
            print(f"[*] Loopback Mode: {self.conf['loopback']}")
            print(f"[*] Pool: {self.pool_start} to {self.pool_end}")
            print(f"{'='*60}\n")
            
            try:
                while self.running:
                    try:
                        data, addr = recv_sock.recvfrom(2048)
                        pkt = BOOTP(data)
                        if DHCP in pkt:
                            self.process_packet(pkt, addr)
                    except (socket.timeout, Exception):
                        continue
            except KeyboardInterrupt:
                print(f"\n[*] Shutting down DHCP server...")
                self.running = False
                sys.exit(0)
        except Exception as e:
            print(f"[!] Failed to bind port {self.PORT_S}: {e}")
            print(f"[!] Another DHCP server might be running")

if __name__ == "__main__":
    DHCPServer().run()