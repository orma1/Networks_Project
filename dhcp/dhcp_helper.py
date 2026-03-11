import socket, os, random, time, sys, atexit, threading
from scapy.all import *

class VirtualNetworkInterface:
    def __init__(self, client_name="Device", fixed_id=None):
        self.client_name = client_name
        self.my_pid = os.getpid()
        # מזהה ייחודי שמשלב שם, PID ומספר רנדומלי למניעת כפילויות ב-Pool
        if fixed_id is None:
            self.unique_id = f"{client_name}-{self.my_pid}-{random.randint(1000, 9999)}"
            self.client_id_opt = ("client_id", self.unique_id.encode())
        else:
            self.unique_id = fixed_id
            self.client_id_opt = ("client_id", self.unique_id.encode())
        # print(f"[*] VirtualNetworkInterface initialized with ID: {self.unique_id}")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0)) 
        self.sock.settimeout(3)
        
        self.ip = None
        self.is_apipa = False
        self.lease_time = 60
        self.running = True
        atexit.register(self.release_ip)

    def release_ip(self):
        if self.ip and not self.is_apipa:
            print(f"\n[*] DHCP: Releasing IP {self.ip} for {self.unique_id}...")
            pkt = BOOTP(yiaddr=self.ip)/DHCP(options=[("message-type", "release"), self.client_id_opt, "end"])
            try:
                self.sock.sendto(raw(pkt), ("127.0.0.1", 6700))
            except: pass

    def setup_network(self):
        # print(f"[*] DHCP: {self.unique_id} searching for server on port 6700...")
        try:
            xid = random.getrandbits(32)
            # D - Discover
            disc = BOOTP(xid=xid)/DHCP(options=[("message-type", "discover"), self.client_id_opt, "end"])
            self.sock.sendto(raw(disc), ("127.0.0.1", 6700))
            
            # O - Offer
            data, _ = self.sock.recvfrom(2048)
            offer = BOOTP(data)
            
            # R - Request
            req = BOOTP(xid=xid, yiaddr=offer.yiaddr)/DHCP(options=[("message-type", "request"), self.client_id_opt, "end"])
            self.sock.sendto(raw(req), ("127.0.0.1", 6700))
            
            # A - Ack
            data, _ = self.sock.recvfrom(2048)
            ack = BOOTP(data)
            
            opts = {opt[0]: opt[1] for opt in ack[DHCP].options if isinstance(opt, tuple)}
            self.lease_time = opts.get('lease_time', 60)
            self.ip = ack.yiaddr
            print(f"[V] DHCP Success: {self.unique_id} assigned {self.ip}")
            
            threading.Thread(target=self._maintain_lease, daemon=True).start()
            
        except Exception:
            self.ip = f"169.254.{random.randint(1,254)}.{random.randint(1,254)}"
            self.is_apipa = True
            print(f"[!] DHCP Failed, using APIPA: {self.ip}")
            
        return self.ip

    def _maintain_lease(self):
        while self.running:
            time.sleep(self.lease_time / 2)
            if not self.is_apipa and self.ip:
                try:
                    req = BOOTP(yiaddr=self.ip)/DHCP(options=[("message-type", "request"), self.client_id_opt, "end"])
                    self.sock.sendto(raw(req), ("127.0.0.1", 6700))
                    self.sock.recvfrom(2048)
                except: pass