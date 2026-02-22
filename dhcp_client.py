import socket
import os
import random
import time
import sys
from scapy.all import *

PORT_S = 6700

class DHCPClient:
    def __init__(self):
        self.my_pid = os.getpid()
        self.client_id = ("client_id", str(self.my_pid).encode())
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # Bind to port 0 to let OS assign a unique port for multi-client testing
        self.sock.bind(("0.0.0.0", 0)) 
        self.my_port = self.sock.getsockname()[1]
        self.sock.settimeout(4) # 4 second wait for server responses
        
        self.ip = None
        self.lease_time = 0
        self.is_apipa = False

    def assign_apipa(self):
        """Generates a local 169.254.x.x address when DHCP fails."""
        self.ip = f"169.254.{random.randint(1,254)}.{random.randint(1,254)}"
        self.is_apipa = True
        self.lease_time = 10 # Check for server again every 10 seconds
        print(f"[!] DHCP failed. Assigned APIPA: {self.ip}")

    def discover_and_request(self):
        """The standard DORA process."""
        xid = random.getrandbits(32)
        print(f"[*] PID {self.my_pid} (Port {self.my_port}) searching for server...")
        
        try:
            # 1. DISCOVER
            disc = BOOTP(xid=xid)/DHCP(options=[("message-type", "discover"), self.client_id, "end"])
            self.sock.sendto(raw(disc), ("127.0.0.1", PORT_S))

            # 2. OFFER
            data, _ = self.sock.recvfrom(2048)
            offer_pkt = BOOTP(data)
            offered_ip = offer_pkt.yiaddr
            
            # Extract lease time
            opts = {opt[0]: opt[1] for opt in offer_pkt[DHCP].options if isinstance(opt, tuple)}
            self.lease_time = opts.get('lease_time', 60)

            # 3. REQUEST
            req = BOOTP(xid=xid, yiaddr=offered_ip)/DHCP(options=[("message-type", "request"), self.client_id, "end"])
            self.sock.sendto(raw(req), ("127.0.0.1", PORT_S))

            # 4. ACK
            data, _ = self.sock.recvfrom(2048)
            self.ip = offered_ip
            self.is_apipa = False
            print(f"[!] SUCCESS. Assigned IP: {self.ip} (Lease: {self.lease_time}s)")
            return True

        except socket.timeout:
            return False

    def run(self):
        """Main loop handling both active leases and APIPA retries."""
        while True:
            if self.ip is None or self.is_apipa:
                # If we don't have an IP or we are on APIPA, try to get a real DHCP lease
                success = self.discover_and_request()
                if not success and self.ip is None:
                    self.assign_apipa()
                elif not success and self.is_apipa:
                    print(f"[*] Still on APIPA ({self.ip}). Server not found, retrying in 10s...")
            
            # --- Maintenance Phase ---
            # If we are on a real DHCP IP, wait for T1 (50% lease) to renew
            # If we are on APIPA, wait 10s and loop back to try DHCP again
            wait_time = (self.lease_time / 2) if not self.is_apipa else 10
            
            try:
                time.sleep(wait_time)
                
                if not self.is_apipa:
                    print(f"[*] T1 Timer: Renewing lease for {self.ip}...")
                    xid = random.getrandbits(32)
                    renew_req = BOOTP(xid=xid, yiaddr=self.ip)/DHCP(options=[
                        ("message-type", "request"), self.client_id, "end"
                    ])
                    self.sock.sendto(raw(renew_req), ("127.0.0.1", PORT_S))
                    
                    # Wait for ACK
                    data, _ = self.sock.recvfrom(2048)
                    print(f"[*] Renewal Successful.")
            
            except socket.timeout:
                print("[!] Server lost during renewal. Falling back to APIPA logic.")
                self.assign_apipa()
            except KeyboardInterrupt:
                print("\n[!] Client exiting...")
                sys.exit(0)

if __name__ == "__main__":
    client = DHCPClient()
    client.run()