import socket, os

def tcp_client_stream(filename: str, byte_start: int, server_addr: tuple):
    print(f"[TCP] Connecting to {server_addr} for {filename}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    try:
        sock.connect(server_addr)
        sock.sendall(f"REQ|{filename}|{byte_start}\n".encode())
        # Skip header
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(1024)
            if not chunk: break
            buf += chunk
        
        while True:
            data = sock.recv(65536)
            if not data: break
            yield data
    except Exception as e:
        print(f"[TCP Client Error] {e}")
    finally:
        sock.close()

def handle_tcp_server_connection(conn, addr, video_dir):
    try:
        data = conn.recv(1024).decode().strip()
        if not data.startswith("REQ|"): return
        parts = data.split("|")
        filepath = os.path.join(video_dir, parts[1])
        if not os.path.exists(filepath):
            conn.sendall(b"ERR|404\n")
            return
        conn.sendall(f"OK|{os.path.getsize(filepath)}\n".encode())
        with open(filepath, "rb") as f:
            f.seek(int(parts[2]))
            while True:
                chunk = f.read(65536)
                if not chunk: break
                conn.sendall(chunk)
    finally:
        conn.close()