from enum import IntEnum
from pathlib import Path
import socket, yaml, time, threading, json, os, sys, queue
from scapy.all import *

# Ensure project root is in sys.path so dhcp.arp_probe is importable
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from dhcp.arp_probe import probe_ip_loopback, probe_ip_real

# DHCP Server Implementation with Lease Management, Reservations, and Persistence
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
        
        # Determine which IP pool to use based on the 'loopback' setting in the config
        pool = self.conf['loopback_pool'] if self.conf['loopback'] else self.conf['outside_pool']
        self.current_pool_ip, self.end_ip = pool['start_ip'], pool['end_ip']
        self.available_reclaimed_ips = []
        self.active_leases = self.load_leases()
        self.client_to_ip = {data['client_id']: ip for ip, data in self.active_leases.items()}
        self.reservations = self.conf['reservations_loopback'] if self.conf['loopback'] else self.conf['reservations_outside']
        self.persistence_file = self.conf['persistence_file']
        #if there were already leased IPs, we need to make sure the pool starts at the right place.
        pool_start = list(map(int, self.current_pool_ip.split('.')))
        for ip in self.active_leases:
            octets = list(map(int, ip.split('.')))
            if octets[:3] == pool_start[:3] and octets[3] >= pool_start[3]:
                pool_start[3] = max(pool_start[3], octets[3] + 1)
        #change the current pool accordingly
        self.current_pool_ip = ".".join(map(str, pool_start))
        #if all IPs are taken the get_next_ip will return None (untill we get back addresses)

    # Helper to save leases to disk without blocking the main thread
    def save_leases(self):
            """Saves a snapshot of leases to avoid thread-blocking during I/O."""
            try:
                with self.lock:
                    data_to_save = dict(self.active_leases)
                with open(self.persistence_file, 'w') as f:
                    json.dump(data_to_save, f, indent=4)
            except Exception as e:
                print(f"[!] Save Error: {e}")


    
    def load_leases(self):
        """Helper to load leases from disk at startup"""
        if os.path.exists(self.conf['persistence_file']):
            try:
                with open(self.conf['persistence_file'], 'r') as f: return json.load(f)
            except: return {}
        return {}
    
    def lease_cleanup_thread(self):
            """Background thread to reclaim IPs when the lease time is up."""
            while self.running:
                time.sleep(5)  # Check every 5 seconds
                now = time.time()
                expired_ips = []

                # 1. LOCK FAST: Just identify what needs to be deleted
                with self.lock:
                    expired_ips = [ip for ip, data in self.active_leases.items() if now > data['expiry']]
                    if not expired_ips:
                        continue

                # 2. PROCESS: Handle the deletions
                    for ip in expired_ips:
                        client_id = self.active_leases[ip]['client_id']
                        print(f"\n[EXPIRE] Reclaiming {ip} from {client_id}")
                        
                        if ip in self.active_leases: del self.active_leases[ip]
                        if client_id in self.client_to_ip: del self.client_to_ip[client_id]
                        
                        # Add back to reclaimed list so it can be reused immediately
                        if ip not in self.available_reclaimed_ips:
                            self.available_reclaimed_ips.append(ip)

                # 3. SAVE OUTSIDE: Writing to disk is slow; don't block the network while doing it
                try:
                    self.work_queue.put(("save", None))
                except Exception as e:
                    print(f"[!] Error saving leases: {e}")

    def _advance_pool_ip(self):
        """Advance the pool pointer and return the next candidate IP,
        skipping any reserved addresses. Must be called with self.lock held."""
        reserved_ips = set(self.reservations.values())
        while self.current_pool_ip in reserved_ips:
            octets = list(map(int, self.current_pool_ip.split('.')))
            if octets[3] <= int(self.end_ip.split('.')[3]):
                octets[3] += 1
                self.current_pool_ip = ".".join(map(str, octets))
            else:
                return None
        octets = list(map(int, self.current_pool_ip.split('.')))
        if octets[3] < int(self.end_ip.split('.')[3]):
            ip = self.current_pool_ip
            octets[3] += 1
            self.current_pool_ip = ".".join(map(str, octets))
            return ip
        return None

    def _probe_ip(self, ip):
        """ARP-like conflict check. Returns True if the IP is already in use."""
        if self.conf['loopback']:
            return probe_ip_loopback(ip)
        else:
            return probe_ip_real(ip)

    # Helper to get the next available IP for a client, considering reservations and reclaimed IPs
    def get_next_available_ip(self, client_id):
        """Returns the next available IP for client_id,
        making sure to check reservations and reclaimed IPs.
        For pool IPs, an ARP-like probe is sent first to detect conflicts
        not recorded in the lease table (e.g. after a crash)."""
        # Reservations and existing leases are trusted without probing
        with self.lock:
            if client_id in self.reservations:
                return self.reservations[client_id]
            if client_id in self.client_to_ip:
                return self.client_to_ip[client_id]

        # For pool IPs, loop until we find one that doesn't respond to the probe
        MAX_TRIES = 10
        for _ in range(MAX_TRIES):
            with self.lock:
                if self.available_reclaimed_ips:
                    candidate = self.available_reclaimed_ips.pop(0)
                else:
                    candidate = self._advance_pool_ip()
                if candidate is None:
                    return None

            # Probe outside the lock — this is a blocking network call
            if not self._probe_ip(candidate):
                print(f" |---> [POOL] Assigned {candidate} to PID: {client_id}")
                return candidate

            print(f"[ARP] ⚠️  Conflict on {candidate} — responded to probe but not in lease table. Skipping.")

        print(f"[ARP] ❌ Could not find a free IP after {MAX_TRIES} tries.")
        return None


    def sender_thread(self):
        """Background thread to send packets and save files from the work queue"""
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while self.running:
            try:
                task = self.work_queue.get(timeout=1)
                
                # Check if the task is a request to save to disk
                if task[0] == "save":
                    self.save_leases()
                else:
                    # Otherwise, it's a packet that needs to be sent
                    pkt_raw, target = task
                    send_sock.sendto(pkt_raw, target)
                    
                self.work_queue.task_done()
            except queue.Empty: continue

    
    def process_packet(self, pkt, addr):
        """Main packet processing logic for DISCOVER, REQUEST, and RELEASE messages"""
        options = pkt[DHCP].options
        msg_type = next((opt[1] for opt in options if isinstance(opt, tuple) and opt[0] == 'message-type'), None)
        # We use client_id throughout the function
        client_id = next((opt[1].decode() for opt in options if isinstance(opt, tuple) and opt[0] == 'client_id'), None)
        if not client_id:
            return

        if msg_type == DHCPMessageType.DISCOVER: # DISCOVER
            print(f"\n[DISCOVER] Received from PID: {client_id} at {addr}")
            offer_ip = self.get_next_available_ip(client_id)
            if offer_ip:
                print(f" |---> [OFFER] Sending {offer_ip} to PID: {client_id}")
                # Logic for the reply packet with all necessary options
                reply = BOOTP(op=2, yiaddr=offer_ip, xid=pkt.xid)/DHCP(options=[
                    ("message-type", "offer"), ("server_id", "127.0.0.1"),
                    ("subnet_mask", self.conf['subnet_mask']), ("router", self.conf['gateway']),
                    ("name_server", self.conf['dns_servers'][0]), ("lease_time", self.conf['lease_time']),
                    ("client_id", client_id.encode()), "end"
                ])
                # Work queue is used to avoid blocking the main thread while sending packets
                self.work_queue.put((raw(reply), (addr[0], addr[1])))

        elif msg_type == DHCPMessageType.REQUEST: # REQUEST
            print(f"\n[REQUEST] Received from PID: {client_id}")
            target_ip = pkt.yiaddr if pkt.yiaddr != "0.0.0.0" else self.client_to_ip.get(client_id)
            if not target_ip:
                print(f" |---> [NAK] No IP available for {client_id}")
                nack = BOOTP(op=2, xid=pkt.xid) / DHCP(options=[
                    ("message-type", "nak"),
                    ("server_id", "127.0.0.1"),
                    "end"
                 ])
                self.work_queue.put((raw(nack), (addr[0], addr[1])))
                return
            # Check if the requested IP is valid and can be offered to the client and update the lease information
            if target_ip:
                with self.lock:
                    self.active_leases[target_ip] = {"expiry": time.time() + self.conf['lease_time'], "client_id": client_id}
                    self.client_to_ip[client_id] = target_ip
                
                print(f" |---> [ACK] Lease Confirmed for {target_ip}")
                reply = BOOTP(op=2, yiaddr=target_ip, xid=pkt.xid)/DHCP(options=[
                    ("message-type", "ack"), ("server_id", "127.0.0.1"), ("lease_time", self.conf['lease_time']), "end"
                ])
                self.work_queue.put((raw(reply), (addr[0], addr[1])))
                
                # Save when a lease is actually created
                self.work_queue.put(("save", None))

        elif msg_type == DHCPMessageType.RELEASE: # RELEASE
            # Release logic: Remove the lease and make the IP available again
            ip_to_release = self.client_to_ip.get(client_id)
            if ip_to_release:
                with self.lock:
                    if ip_to_release in self.active_leases: del self.active_leases[ip_to_release]
                    if client_id in self.client_to_ip: del self.client_to_ip[client_id]
                    self.available_reclaimed_ips.append(ip_to_release)
                
                self.work_queue.put(("save", None)) 
                print(f" |---> Success: IP {ip_to_release} returned to the pool.")

    def run(self):
        """Start background threads for lease cleanup and packet sending"""
        threading.Thread(target=self.lease_cleanup_thread, daemon=True).start()
        threading.Thread(target=self.sender_thread, daemon=True).start()
        try: 
            # If can't bind, port is taken, likely by another instance of the server
            recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            recv_sock.bind(("0.0.0.0", self.PORT_S))
            recv_sock.settimeout(1.0)
            print(f"[*] DHCP Server active on Port {self.PORT_S}")
            try:
                while self.running:
                    try:
                        data, addr = recv_sock.recvfrom(2048)
                        pkt = BOOTP(data)
                        if DHCP in pkt: self.process_packet(pkt, addr)
                    except (socket.timeout, Exception): continue
            except KeyboardInterrupt: sys.exit(0)
        except Exception as e:
            print(f"[!] Port Taken")

if __name__ == "__main__":
    DHCPServer().run()