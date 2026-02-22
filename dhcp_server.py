import socket
import yaml
import time
import threading
import json
import os
import sys
import queue
from scapy.all import *

# DHCP Constants
DHCP_DISCOVER = 1
DHCP_OFFER = 2
DHCP_REQUEST = 3
DHCP_ACK = 5

def increment_ip(ip):
    """Calculates the next IP address in the sequence."""
    octets = list(map(int, ip.split('.')))
    for i in range(3, -1, -1):
        octets[i] += 1
        if octets[i] <= 255:
            break
        octets[i] = 0
    return '.'.join(map(str, octets))

class DHCPServer:
    def __init__(self):
        # 1. Load Config
        with open('config.yaml', 'r') as f:
            self.conf = yaml.safe_load(f)['dhcp_server']
        
        self.PORT_S = 6700
        self.running = True
        self.lock = threading.Lock()
        self.work_queue = queue.Queue()
        
        # 2. IP Pool Setup
        pool = self.conf['loopback_pool'] if self.conf['loopback'] else self.conf['outside_pool']
        self.current_pool_ip = pool['start_ip']
        self.end_ip = pool['end_ip']
        self.available_reclaimed_ips = []
        
        # 3. State & Persistence
        self.persistence_file = self.conf['persistence_file']
        self.active_leases = self.load_leases() # {IP: {"expiry": t, "client_id": pid}}
        self.client_to_ip = {data['client_id']: ip for ip, data in self.active_leases.items()}

    def load_leases(self):
        if os.path.exists(self.persistence_file):
            try:
                with open(self.persistence_file, 'r') as f:
                    return json.load(f)
            except: return {}
        return {}

    def save_leases(self):
        with open(self.persistence_file, 'w') as f:
            json.dump(self.active_leases, f)

    def lease_cleanup_thread(self):
        """Reclaims expired IPs in the background."""
        while self.running:
            time.sleep(2)
            now = time.time()
            with self.lock:
                expired = [ip for ip, data in self.active_leases.items() if now > data['expiry']]
                for ip in expired:
                    print(f"\n[Cleanup] Lease for {ip} expired.")
                    cid = self.active_leases[ip]['client_id']
                    if cid in self.client_to_ip: del self.client_to_ip[cid]
                    self.available_reclaimed_ips.append(ip)
                    del self.active_leases[ip]
                if expired: self.save_leases()

    def sender_thread(self):
        """Dedicated thread for sending packets."""
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while self.running:
            try:
                # Task: (packet_raw, (target_ip, target_port))
                pkt_raw, target = self.work_queue.get(timeout=1)
                send_sock.sendto(pkt_raw, target)
                self.work_queue.task_done()
            except queue.Empty:
                continue

    def get_next_available_ip(self, client_id):
        """Core logic to ensure unique sequential IP assignment."""
        with self.lock:
            # 1. Check if they already have an active lease
            if client_id in self.client_to_ip:
                return self.client_to_ip[client_id]
            
            # 2. Check if we have a reclaimed IP from a past expiration
            if self.available_reclaimed_ips:
                return self.available_reclaimed_ips.pop(0)
            
            # 3. Assign next new IP from pool
            assigned = self.current_pool_ip
            if assigned != self.end_ip:
                self.current_pool_ip = increment_ip(self.current_pool_ip)
            else:
                assigned = None # Pool exhausted
            return assigned

    def process_packet(self, pkt, addr):
        """Processes incoming DHCP messages."""
        options = pkt[DHCP].options
        msg_type = next((opt[1] for opt in options if isinstance(opt, tuple) and opt[0] == 'message-type'), None)
        client_id = next((opt[1].decode() for opt in options if isinstance(opt, tuple) and opt[0] == 'client_id'), None)
        
        if not client_id: return
        xid = pkt.xid

        if msg_type == DHCP_DISCOVER:
            offer_ip = self.get_next_available_ip(client_id)
            if not offer_ip: 
                print("[!] No IPs left in pool!")
                return
            
            print(f"[Listener] DISCOVER from PID {client_id}. Offering {offer_ip}")
            reply = BOOTP(op=2, yiaddr=offer_ip, xid=xid)/DHCP(options=[
                ("message-type", "offer"), ("server_id", "127.0.0.1"),
                ("lease_time", self.conf['lease_time']), ("client_id", client_id.encode()),
                ("subnet_mask", self.conf['subnet_mask']), ("router", self.conf['gateway']),
                ("name_server", self.conf['dns_servers'][0]), "end"
            ])
            self.work_queue.put((raw(reply), (addr[0], addr[1])))

        elif msg_type == DHCP_REQUEST:
            # For Request, we finalize the lease
            target_ip = pkt.yiaddr if pkt.yiaddr != "0.0.0.0" else self.client_to_ip.get(client_id)
            if not target_ip: return

            with self.lock:
                self.active_leases[target_ip] = {
                    "expiry": time.time() + self.conf['lease_time'],
                    "client_id": client_id
                }
                self.client_to_ip[client_id] = target_ip
                self.save_leases()

            print(f"[Listener] REQUEST from PID {client_id}. Sending ACK for {target_ip}")
            reply = BOOTP(op=2, yiaddr=target_ip, xid=xid)/DHCP(options=[
                ("message-type", "ack"), ("server_id", "127.0.0.1"),
                ("lease_time", self.conf['lease_time']), "end"
            ])
            self.work_queue.put((raw(reply), (addr[0], addr[1])))

    def run(self):
        """Starts threads and manages the main loop for CTRL+C support."""
        # Start background threads
        threading.Thread(target=self.lease_cleanup_thread, daemon=True).start()
        threading.Thread(target=self.sender_thread, daemon=True).start()

        # Listener socket with timeout to allow CTRL+C checks
        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        recv_sock.bind(("0.0.0.0", self.PORT_S))
        recv_sock.settimeout(1.0) 

        print(f"[*] DHCP Server active on port {self.PORT_S}")
        print("[*] Monitoring leases and sequential IP pool...")
        print("[*] Press CTRL+C to stop.")

        try:
            while self.running:
                try:
                    data, addr = recv_sock.recvfrom(2048)
                    pkt = BOOTP(data)
                    if DHCP in pkt:
                        self.process_packet(pkt, addr)
                except socket.timeout:
                    continue # This loop tick allows KeyboardInterrupt to trigger
        except KeyboardInterrupt:
            print("\n[!] CTRL+C detected. Saving state and exiting...")
            self.running = False
            sys.exit(0)

if __name__ == "__main__":
    DHCPServer().run()