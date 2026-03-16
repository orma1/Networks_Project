import socket, os, random, time, sys, atexit, threading
from scapy.all import *

def perform_dora(sock, client_id_opt, server_addr=("127.0.0.1", 6700)):
    """Performs DISCOVER → OFFER → REQUEST → ACK. Returns (assigned_ip, lease_time, opts, xid)."""
    xid = random.getrandbits(32)
    
    # D - Discover
    disc = BOOTP(xid=xid)/DHCP(options=[("message-type", "discover"), client_id_opt, "end"])
    sock.sendto(raw(disc), server_addr)
    
    # O - Offer
    data, _ = sock.recvfrom(2048)
    offer = BOOTP(data)
    
    # R - Request
    req = BOOTP(xid=xid, yiaddr=offer.yiaddr)/DHCP(options=[("message-type", "request"), client_id_opt, "end"])
    sock.sendto(raw(req), server_addr)
    
    # A - Ack
    data, _ = sock.recvfrom(2048)
    ack = BOOTP(data)
    
    opts = {opt[0]: opt[1] for opt in ack[DHCP].options if isinstance(opt, tuple)}
    lease_time = opts.get('lease_time', 60)
    
    # ✅ RETURN THE XID SO WE CAN USE IT IN RELEASE
    return ack.yiaddr, lease_time, opts, xid


class VirtualNetworkInterface:
    def __init__(self, client_name="Device", fixed_id=None):
        self.client_name = client_name
        self.my_pid = os.getpid()
        
        # ✅ FIXED: Use fixed_id directly if provided, otherwise generate unique ID
        if fixed_id is None:
            self.unique_id = f"{client_name}-{self.my_pid}-{random.randint(1000, 9999)}"
        else:
            self.unique_id = fixed_id  # Use the fixed_id as-is for reservation lookup
            print(f"[*] Using fixed_id: {self.unique_id}")
        
        self.client_id_opt = ("client_id", self.unique_id.encode())
        
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0)) 
        self.sock.settimeout(3)
        
        self.ip = None
        self.xid = None  # ✅ STORE THE XID FOR RELEASE
        self.is_apipa = False
        self.lease_time = 60
        self.running = True
        atexit.register(self.release_ip)

    def release_ip(self):
        if self.ip and not self.is_apipa:
            print(f"\n[*] DHCP: Releasing IP {self.ip} for {self.unique_id}...")
            
            # ✅ FIXED: Include xid, better error handling
            if self.xid is None:
                self.xid = random.getrandbits(32)
            
            pkt = BOOTP(xid=self.xid, yiaddr=self.ip) / DHCP(options=[
                ("message-type", "release"), 
                self.client_id_opt, 
                "end"
            ])
            
            try:
                bytes_sent = self.sock.sendto(raw(pkt), ("127.0.0.1", 6700))
                if bytes_sent > 0:
                    print(f"[✓] RELEASE packet sent: {bytes_sent} bytes for {self.unique_id}")
                    time.sleep(0.1)  # Give server time to process
                else:
                    print(f"[!] RELEASE packet send returned 0 bytes")
            except Exception as e:
                print(f"[!] DHCP RELEASE failed for {self.unique_id}: {type(e).__name__}: {e}")
    
    def setup_network(self):
        try:
            # ✅ FIXED: Capture xid from DORA
            self.ip, self.lease_time, _, self.xid = perform_dora(self.sock, self.client_id_opt)
            
            print(f"[V] DHCP Success: {self.unique_id} assigned {self.ip} (xid={self.xid})")
            threading.Thread(target=self._maintain_lease, daemon=True).start()
            
        except Exception as e:
            self.ip = f"169.254.{random.randint(1,254)}.{random.randint(1,254)}"
            self.is_apipa = True
            print(f"[!] DHCP Failed for {self.unique_id}: {type(e).__name__}: {e}")
            print(f"[!] Using APIPA: {self.ip}")
            
        return self.ip
    
    def _maintain_lease(self):
        while self.running:
            time.sleep(self.lease_time / 2)
            if not self.is_apipa and self.ip:
                try:
                    # ✅ FIXED: Generate new xid for RENEW, include it
                    renew_xid = random.getrandbits(32)
                    req = BOOTP(xid=renew_xid, yiaddr=self.ip) / DHCP(options=[
                        ("message-type", "request"), 
                        self.client_id_opt, 
                        "end"
                    ])
                    self.sock.sendto(raw(req), ("127.0.0.1", 6700))
                    data, _ = self.sock.recvfrom(2048)
                    print(f"[*] Lease renewed for {self.unique_id}")
                except Exception as e:
                    print(f"[!] Lease renewal failed: {e}")