import socket, os, random, time, sys
from scapy.all import *

class DHCPClient:
    def __init__(self):
        self.my_pid = os.getpid()
        # self.my_pid = 6868 # For testing reservation logic with a fixed PID
        self.client_id = ("client_id", str(self.my_pid).encode())
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Bind to a random port to avoid conflicts with multiple clients on the same machine
        self.sock.bind(("0.0.0.0", 0))
        self.sock.settimeout(3)
        self.ip, self.is_apipa, self.lease = None, False, 60
        # Ensure we release the IP on exit
        atexit.register(self.release_ip)

    # APIPA logic: If no DHCP server responds, assign a random IP in the 169.254.x.x range
    def get_apipa_ip(self):
        self.ip = f"169.254.{random.randint(1,254)}.{random.randint(1,254)}"
        print(f"[!] New APIPA: {self.ip}")
        self.is_apipa = True

    # Helper to print details from DHCP OFFER/ACK
    def print_details(self, pkt):
        opts = {opt[0]: opt[1] for opt in pkt[DHCP].options if isinstance(opt, tuple)}
        print(f"\n[+] NEW CONFIG: IP={pkt.yiaddr} | SUBNET={opts.get('subnet_mask')} | GW={opts.get('router')} |DNS={opts.get('name_server')}")

    # Release IP back to the server when the client exits
    def release_ip(self):
        """Sends DHCP Message Type 7 (Release) to the server."""
        if self.ip and not self.is_apipa:
            print(f"\n[*] Sending Release for {self.ip}...")
            pkt = BOOTP(yiaddr=self.ip)/DHCP(options=[("message-type", "release"), self.client_id, "end"])
            try:
                self.sock.sendto(raw(pkt), ("127.0.0.1", 6700))
            except: pass

    def run(self):
        while True:
            # If we don't have an IP or we're currently using APIPA, try to get a DHCP lease
            if self.ip is None or self.is_apipa:
                if self.is_apipa is False:
                    print(f"[*] PID {self.my_pid} searching for DHCP server...")
                try:
                    # Generate a random transaction ID for each client instance
                    xid = random.getrandbits(32)
                    disc = BOOTP(xid=xid)/DHCP(options=[("message-type", "discover"), self.client_id, "end"])
                    self.sock.sendto(raw(disc), ("127.0.0.1", 6700))
                    
                    # Try to get OFFER
                    data, _ = self.sock.recvfrom(2048)
                    offer = BOOTP(data)
                    self.print_details(offer)
                    
                    # Send REQUEST for the offered IP
                    req = BOOTP(xid=xid, yiaddr=offer.yiaddr)/DHCP(options=[("message-type", "request"), self.client_id, "end"])
                    self.sock.sendto(raw(req), ("127.0.0.1", 6700))
                    
                    # Try to get ACK
                    # FIXED: Changed 'lease_index' logic to correctly find the index of the lease_time option
                    lease_index = next(i for i, opt in enumerate(offer[DHCP].options) if isinstance(opt, tuple) and opt[0] == "lease_time")
                    lease = offer.getlayer(DHCP).options[lease_index]
                    data, _ = self.sock.recvfrom(2048)
                    self.ip, self.is_apipa = offer.yiaddr, False

                    # Logic to extract lease time from the ACK (if present) and print details
                    if lease[0] == "lease_time":
                        self.lease = lease[1]
                    print(f"[*] PID {self.my_pid} successfully bound to {self.ip}")
                    self.is_apipa = False
                
                # No response from server, fallback to APIPA
                except (socket.timeout, ConnectionResetError, OSError):
                    if self.is_apipa is False:
                        print("[!] Server not found.")
                        self.get_apipa_ip()

            # Wait for half the lease time before renewing (or 10s if using APIPA)
            time.sleep(10 if self.is_apipa else (self.lease / 2))

            # As long as we're not using APIPA, try to renew the lease
            if not self.is_apipa:
                try:
                    print(f"[*] Renewing {self.ip}...")
                    req = BOOTP(yiaddr=self.ip)/DHCP(options=[("message-type", "request"), self.client_id, "end"])
                    self.sock.sendto(raw(req), ("127.0.0.1", 6700))
                    self.sock.recvfrom(2048)

                # If renewal fails, revert to APIPA logic
                except Exception:
                    print("[!] Renewal failed. Reverting to APIPA logic.")
                    self.get_apipa_ip()
                    self.ip, self.is_apipa = None, True

if __name__ == "__main__":
    try: DHCPClient().run()
    except KeyboardInterrupt: sys.exit(0)