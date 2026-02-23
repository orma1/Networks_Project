import socket, yaml, time, threading, json, os, sys, queue
from scapy.all import *

class DHCPServer:
    def __init__(self):
        with open('config.yaml', 'r') as f:
            self.conf = yaml.safe_load(f)['dhcp_server']
        self.PORT_S, self.running = 6700, True
        self.lock, self.work_queue = threading.Lock(), queue.Queue()
        
        pool = self.conf['loopback_pool'] if self.conf['loopback'] else self.conf['outside_pool']
        self.current_pool_ip, self.end_ip = pool['start_ip'], pool['end_ip']
        self.available_reclaimed_ips = []
        self.active_leases = self.load_leases()
        self.client_to_ip = {data['client_id']: ip for ip, data in self.active_leases.items()}
        self.reservations = self.conf['reservations_loopback'] if self.conf['loopback'] else self.conf['reservations_outside']
        self.persistence_file = self.conf['persistence_file']

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
                with self.lock:
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
                    self.save_leases()
                except Exception as e:
                    print(f"[!] Error saving leases: {e}")

    def get_next_available_ip(self, client_id):
        with self.lock:
            if client_id in self.reservations:
                return self.reservations[client_id]
            if client_id in self.client_to_ip:
                return self.client_to_ip[client_id]
            if self.available_reclaimed_ips:
                return self.available_reclaimed_ips.pop(0)
            
            # Simple IP increment logic
            octets = list(map(int, self.current_pool_ip.split('.')))
            if octets[3] < int(self.end_ip.split('.')[3]):
                assigned = self.current_pool_ip
                octets[3] += 1
                self.current_pool_ip = ".".join(map(str, octets))
                print(f" |---> [POOL] Assigned {assigned} to PID: {client_id}")
                return assigned
            return None

    def sender_thread(self):
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while self.running:
            try:
                pkt_raw, target = self.work_queue.get(timeout=1)
                send_sock.sendto(pkt_raw, target)
                self.work_queue.task_done()
            except queue.Empty: continue

    def process_packet(self, pkt, addr):
        options = pkt[DHCP].options
        msg_type = next((opt[1] for opt in options if isinstance(opt, tuple) and opt[0] == 'message-type'), None)
        # We use client_id throughout the function
        client_id = next((opt[1].decode() for opt in options if isinstance(opt, tuple) and opt[0] == 'client_id'), None)
        
        if not client_id:
            return

        if msg_type == 1: # DISCOVER
            print(f"\n[DISCOVER] Received from PID: {client_id} at {addr}")
            offer_ip = self.get_next_available_ip(client_id)
            if offer_ip:
                print(f" |---> [OFFER] Sending {offer_ip} to PID: {client_id}")
                reply = BOOTP(op=2, yiaddr=offer_ip, xid=pkt.xid)/DHCP(options=[
                    ("message-type", "offer"), ("server_id", "127.0.0.1"),
                    ("subnet_mask", self.conf['subnet_mask']), ("router", self.conf['gateway']),
                    ("name_server", self.conf['dns_servers'][0]), ("lease_time", self.conf['lease_time']),
                    ("client_id", client_id.encode()), "end"
                ])
                self.work_queue.put((raw(reply), (addr[0], addr[1])))

        elif msg_type == 3: # REQUEST
            print(f"\n[REQUEST] Received from PID: {client_id}")
            target_ip = pkt.yiaddr if pkt.yiaddr != "0.0.0.0" else self.client_to_ip.get(client_id)
            
            if target_ip:
                with self.lock:
                    self.active_leases[target_ip] = {"expiry": time.time() + self.conf['lease_time'], "client_id": client_id}
                    self.client_to_ip[client_id] = target_ip
                
                print(f" |---> [ACK] Lease Confirmed for {target_ip}")
                reply = BOOTP(op=2, yiaddr=target_ip, xid=pkt.xid)/DHCP(options=[
                    ("message-type", "ack"), ("server_id", "127.0.0.1"), ("lease_time", self.conf['lease_time']), "end"
                ])
                self.work_queue.put((raw(reply), (addr[0], addr[1])))
                
                # ADDED: Save when a lease is actually created
                self.save_leases()

        elif msg_type == 7: # RELEASE
            # FIXED: Changed 'cid' to 'client_id' to match the variable above
            ip_to_release = self.client_to_ip.get(client_id)
            if ip_to_release:
                with self.lock:
                    if ip_to_release in self.active_leases: del self.active_leases[ip_to_release]
                    if client_id in self.client_to_ip: del self.client_to_ip[client_id]
                    self.available_reclaimed_ips.append(ip_to_release)
                
                self.save_leases() 
                print(f" |---> Success: IP {ip_to_release} returned to the pool.")

    def run(self):
        threading.Thread(target=self.lease_cleanup_thread, daemon=True).start()
        threading.Thread(target=self.sender_thread, daemon=True).start()
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

if __name__ == "__main__":
    DHCPServer().run()