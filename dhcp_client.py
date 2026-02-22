import socket
from scapy.all import *
import random

PORT_S = 6700
PORT_C = 6800

def get_apipa():
    return f"169.254.{random.randint(1,254)}.{random.randint(1,254)}"

def start_client():
    # סוקט שמוגדר לאפשר Broadcast
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", PORT_C)) # הלקוח מאזין לכל מה שחוזר אליו בפורט 6800
    sock.settimeout(3)
    
    xid = random.getrandbits(32)

    # 1. DISCOVER - שליחה לכתובת Broadcast כללית
    print("[Client] Sending Broadcast DISCOVER (I don't know who the server is)...")
    disc = BOOTP(xid=xid)/DHCP(options=[("message-type", "discover"), "end"])
    
    # הלקוח שולח ל-255.255.255.255 - זה DHCP אמיתי!
    sock.sendto(raw(disc), ("255.255.255.255", PORT_S))

    try:
        # 2. Receive Offer
        data, addr = sock.recvfrom(2048)
        pkt = BOOTP(data)
        offered_ip = pkt.yiaddr
        print(f"[Client] Server {addr[0]} found me! Got Offer: {offered_ip}")

        # 3. REQUEST
        req = BOOTP(xid=xid, yiaddr=offered_ip)/DHCP(options=[("message-type", "request"), "end"])
        sock.sendto(raw(req), ("255.255.255.255", PORT_S))

        # 4. ACK
        data, addr = sock.recvfrom(2048)
        print(f"[Client] SUCCESS! My new IP is: {offered_ip}")
        return offered_ip

    except socket.timeout:
        print("[Client] No server responded to my Broadcast.")
        ip = get_apipa()
        print(f"[Client] Self-assigning APIPA address: {ip}")
        return ip

if __name__ == "__main__":
    start_client()