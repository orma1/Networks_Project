import socket
import yaml
import time
import threading
import sys
from scapy.all import *

# קבועים למניעת מספרי קסם
DHCP_DISCOVER = 1
DHCP_OFFER = 2
DHCP_REQUEST = 3
DHCP_ACK = 5

def increment_ip(ip, end_ip):
    octets = list(map(int, ip.split('.')))
    for i in range(3, -1, -1):
        octets[i] += 1
        if octets[i] <= 255:
            break
        octets[i] = 0
    return '.'.join(map(str, octets))

def load_config():
    with open('config.yaml', 'r') as file:
        return yaml.safe_load(file)['dhcp_server']

class DHCPServer:
    def __init__(self):
        self.conf_data = load_config()
        self.PORT_S = 6700
        self.PORT_C = 6800
        self.lease_time = self.conf_data['lease_time']
        
        pool_type = 'loopback_pool' if self.conf_data['loopback'] else 'outside_pool'
        self.current_ip = self.conf_data[pool_type]['start_ip']
        self.end_ip = self.conf_data[pool_type]['end_ip']
        
        self.available_ips = [self.current_ip] 
        self.active_leases = {} 
        self.lock = threading.Lock()
        self.running = True # דגל לשליטה על סגירת השרת

    def lease_cleanup_thread(self):
        """Thread נפרד לניהול פגי תוקף של כתובות"""
        while self.running:
            time.sleep(1)
            now = time.time()
            with self.lock:
                expired_ips = [ip for ip, expiry in self.active_leases.items() if now > expiry]
                for ip in expired_ips:
                    print(f"\n[Thread] Lease EXPIRED for {ip}. Returning to pool.")
                    self.available_ips.append(ip)
                    del self.active_leases[ip]

    def listen_thread(self):
        """Thread נפרד להאזנה לחבילות DHCP (מונע חסימה של ה-Main Thread)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1) # מאפשר ל-Socket לבדוק את ה-running flag מדי פעם
        sock.bind(("0.0.0.0", self.PORT_S))
        
        print(f"[*] DHCP Listener started on port {self.PORT_S}...")

        while self.running:
            try:
                data, addr = sock.recvfrom(2048)
                pkt = BOOTP(data)
                if DHCP in pkt:
                    self.handle_packet(pkt, addr, sock)
            except socket.timeout:
                continue # ה-Timeout מאפשר ללולאה לבדוק אם self.running עדיין True
            except Exception as e:
                if self.running: print(f"[!] Error: {e}")

    def handle_packet(self, pkt, addr, sock):
        """לוגיקת הטיפול בחבילה (נקראת מה-Listen Thread)"""
        msg_type = pkt[DHCP].options[0][1]
        xid = pkt.xid

        if msg_type == DHCP_DISCOVER:
            with self.lock:
                offer_ip = self.available_ips.pop(0) if self.available_ips else None
                if not offer_ip and self.current_ip != self.end_ip:
                    self.current_ip = increment_ip(self.current_ip, self.end_ip)
                    offer_ip = self.current_ip
                
                if offer_ip: self.available_ips.insert(0, offer_ip)

            if offer_ip:
                print(f"[Server] Offering {offer_ip}")
                reply = BOOTP(op=2, yiaddr=offer_ip, xid=xid)/DHCP(options=[
                    ("message-type", "offer"), ("server_id", "127.0.0.1"),
                    ("lease_time", self.lease_time), "end"
                ])
                sock.sendto(raw(reply), (addr[0], self.PORT_C))

        elif msg_type == DHCP_REQUEST:
            with self.lock:
                # במימוש זה אנחנו לוקחים את ה-yiaddr מהחבילה
                requested_ip = pkt.yiaddr if pkt.yiaddr != "0.0.0.0" else self.available_ips[0]
                self.active_leases[requested_ip] = time.time() + self.lease_time
                if requested_ip in self.available_ips: self.available_ips.remove(requested_ip)

            print(f"[Server] ACK for {requested_ip}")
            reply = BOOTP(op=2, yiaddr=requested_ip, xid=xid)/DHCP(options=[
                ("message-type", "ack"), ("lease_time", self.lease_time), "end"
            ])
            sock.sendto(raw(reply), (addr[0], self.PORT_C))

    def run(self):
        """פונקציית ההפעלה הראשית שמנהלת את ה-Threads ואת ה-CTRL+C"""
        t1 = threading.Thread(target=self.lease_cleanup_thread, daemon=True)
        t2 = threading.Thread(target=self.listen_thread, daemon=True)
        
        t1.start()
        t2.start()

        print("\n" + "="*40)
        print(" DHCP SERVER IS RUNNING ")
        print(" Press CTRL+C to stop the server safely ")
        print("="*40 + "\n")

        try:
            while True:
                time.sleep(0.5) # ה-Main Thread פשוט נח ומחכה ל-Keyboard Interrupt
        except KeyboardInterrupt:
            print("\n[!] CTRL+C detected. Shutting down server...")
            self.running = False
            time.sleep(1) # זמן קצר לסגירת Threads
            sys.exit(0)

if __name__ == "__main__":
    server = DHCPServer()
    server.run()