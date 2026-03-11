import socket, os, sys, yaml, time
from flask import Flask, Response, stream_with_context, render_template, request, redirect
from dnslib import DNSRecord
import random

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(BASE_DIR, 'videos') 
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, '..'))
sys.path.append(PARENT_DIR)

from dhcp.dhcp_helper import VirtualNetworkInterface

app = Flask(__name__)
MY_IP, WEB_SERVER_ADDR = None, None
PROTOCOL = "rudp"
QUALITY_DISPLAY = "Auto"
SERVER_PORT = 9000
packet_loss_value = 0  

@app.route('/')
def index():
    with open("movies.yaml", 'r', encoding='utf-8') as f:
        movies = yaml.safe_load(f)['movies']
    return render_template('index.html', movies=movies, protocol=PROTOCOL.upper(), quality=QUALITY_DISPLAY, packet_loss=packet_loss_value)

@app.route('/switch_protocol', methods=['POST'])
def switch_protocol():
    global PROTOCOL
    PROTOCOL = "rudp" if PROTOCOL == "tcp" else "tcp"
    return redirect('/')


@app.route('/set_loss', methods=['POST'])
def set_loss():
    global packet_loss_value
    packet_loss_value = int(request.form.get('loss_percent', 0))
    # Return to the previous page
    return redirect(request.referrer or '/')

@app.route('/play/<filename>')
def play(filename):
    selected_q = request.args.get('force_quality', 'auto')
    return render_template('index.html', video_file=filename, protocol=PROTOCOL.upper(), 
                           selected_quality=selected_q, quality=QUALITY_DISPLAY, packet_loss=packet_loss_value)

@app.route('/stream/<filename>')
def stream(filename):
    selected_q = request.args.get('force_quality', 'auto')
    current_q = "720" if selected_q == "auto" else selected_q
    target_file = filename.replace(".mp4", f"_{current_q}.mp4")
    file_path = os.path.join(VIDEO_DIR, target_file)
    
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
    range_header = request.headers.get('Range', 'bytes=0-')
    byte_start = int(range_header.replace('bytes=', '').split('-')[0])

    resp = Response(
        stream_with_context(generate_video(target_file, byte_start)),
        status=206,
        mimetype='video/mp4'
    )
    resp.headers.add('Content-Range', f'bytes {byte_start}-{file_size-1}/{file_size}')
    resp.headers.add('Accept-Ranges', 'bytes')
    resp.headers.add('Content-Length', str(file_size - byte_start))
    return resp

def generate_video(target_file, byte_start):
    global QUALITY_DISPLAY
    selected_q = request.args.get('force_quality', 'auto')
    
        
    # For TCP
    if PROTOCOL == "tcp":
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:                
            client_sock.connect(WEB_SERVER_ADDR)
            if packet_loss_value > 0 and random.random() < (packet_loss_value / 100):
                client_sock.sendall(f"DROP|{target_file}|{byte_start}".encode())
            else:
                client_sock.sendall(f"REQ|{target_file}|{byte_start}".encode())
                QUALITY_DISPLAY = "Fixed (TCP)"
                while True:
                    chunk = client_sock.recv(65535)
                    if not chunk: break
                    yield chunk
        except GeneratorExit: pass
        finally: client_sock.close()
    # For RUDP
    else:
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client_sock.settimeout(1.5) # Increased for stability
        if packet_loss_value > 0 and random.random() < (packet_loss_value / 100):
            client_sock.sendto(f"DROP|{target_file}|{byte_start}".encode(), WEB_SERVER_ADDR)
        else:
            client_sock.sendto(f"REQ|{target_file}|{byte_start}".encode(), WEB_SERVER_ADDR)
            
            expected_seq, buffer, bytes_received, start_time = 0, {}, 0, time.time()
            try:
                while True:
                    try:
                        data, addr = client_sock.recvfrom(65535)
                        if data.startswith(b"ALIVE|"): continue
                        if data.startswith(b"FIN|"): break
                        
                        seq = int.from_bytes(data[:4], 'big')
                        client_sock.sendto(f"ACK|{seq}".encode(), addr)
                        
                        if seq >= expected_seq:
                            buffer[seq] = data[4:]
                            bytes_received += len(data)

                        # DASH Bitrate Logic
                        elapsed = time.time() - start_time
                        if elapsed > 2.0:
                            mbps = (bytes_received * 8) / (elapsed * 1024 * 1024)
                            if selected_q == "auto":
                                if mbps < 1.0: QUALITY_DISPLAY = "480p (Low)"
                                elif mbps < 3.5: QUALITY_DISPLAY = "720p (Med)"
                                else: QUALITY_DISPLAY = "1080p (High)"
                            else:
                                QUALITY_DISPLAY = f"{selected_q}p (Forced)"
                            start_time, bytes_received = time.time(), 0

                        while expected_seq in buffer:
                            yield buffer.pop(expected_seq)
                            expected_seq += 1
                    except socket.timeout: 
                        continue
            except GeneratorExit: pass
            finally: client_sock.close()

if __name__ == "__main__":
    v_net = VirtualNetworkInterface(client_name="ProxyNode")
    MY_IP = v_net.setup_network()
    try:
        ans = DNSRecord.question("originserver.homelab.").send("127.0.0.2", 53, timeout=2.0)
        reply = DNSRecord.parse(ans)
        WEB_SERVER_ADDR = None
        for rr in reply.rr:
            if rr.rtype == 1:  # A record
                WEB_SERVER_ADDR = (str(rr.rdata), SERVER_PORT) 
        print(f"[*] Origin server address set to: {WEB_SERVER_ADDR}")
    except:
        print("[!] Failed to resolve origin server IP via DNS. Defaulting to 127.0.0.13:9000")
        WEB_SERVER_ADDR = ("127.0.0.13", SERVER_PORT)
    app.run(host='0.0.0.0', port=5000, threaded=True)