import socket

class RUDPReceiver:
    def __init__(self, server_ip="127.0.0.1", server_port=9000):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server_addr = (server_ip, server_port)
        # הלקוח לא עושה bind לפורט ספציפי, ה-OS ייתן לו אחד פנוי

    def request_video(self, filename):
        print(f"[*] Requesting {filename} from {self.server_addr}...")
        self.sock.sendto(filename.encode(), self.server_addr)

        output_name = "downloaded_" + filename
        with open(output_name, "wb") as f:
            expected_seq = 0
            while True:
                try:
                    data, addr = self.sock.recvfrom(8192) # באפר גדול יותר לוידאו
                    
                    if data == b"DONE":
                        print("[*] Transfer complete!")
                        break
                    
                    if data.startswith(b"ERROR"):
                        print(f"[!] Server error: {data.decode()}")
                        break

                    seq_num = int.from_bytes(data[:4], 'big')
                    payload = data[4:]

                    if seq_num == expected_seq:
                        f.write(payload)
                        self.sock.sendto(f"ACK{seq_num}".encode(), addr)
                        expected_seq += 1
                    else:
                        # שליחת ACK חוזר למקרה שהקודם אבד
                        self.sock.sendto(f"ACK{seq_num}".encode(), addr)
                except Exception as e:
                    print(f"[!] Error: {e}")
                    break
        print(f"[*] Saved as {output_name}")

if __name__ == "__main__":
    client = RUDPReceiver()
    client.request_video("movie.mp4") # וודא שיש קובץ כזה בתיקייה של השרת