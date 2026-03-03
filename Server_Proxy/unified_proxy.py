import socket, os, sys, yaml, signal, argparse
from flask import Flask, Response, stream_with_context, render_template_string, request
from dnslib import DNSRecord

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, '..'))
VIDEO_DIR = os.path.join(BASE_DIR, 'videos') # Used for size check
sys.path.append(PARENT_DIR)

from dhcp_helper import VirtualNetworkInterface

app = Flask(__name__)
MY_IP = None
ORIGIN_ADDR = None
PROTOCOL = "tcp"

@app.route('/')
def index():
    with open("movies.yaml", 'r', encoding='utf-8') as f:
        movies = yaml.safe_load(f)['movies']
    return render_template_string("<h1>Library ({{ p }})</h1><ul>{% for m in movies %}<li>{{ m.name }} - <a href='/play/{{ m.file }}'>Watch</a></li>{% endfor %}</ul>", movies=movies, p=PROTOCOL.upper())

@app.route('/play/<filename>')
def play(filename):
    return render_template_string("<video controls autoplay width='800'><source src='/stream/{{ f }}' type='video/mp4'></video>", f=filename)

@app.route('/stream/<filename>')
def stream(filename):
    file_path = os.path.join(VIDEO_DIR, filename)
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

    range_header = request.headers.get('Range', None)
    byte_start = 0

    if range_header:
        byte_start = int(range_header.replace('bytes=', '').split('-')[0])

    # Fixes the length jumping issue by sending 206 Partial Content
    resp = Response(
        stream_with_context(generate_video(filename, byte_start)),
        status=206 if range_header else 200,
        mimetype='video/mp4'
    )
    resp.headers.add('Content-Range', f'bytes {byte_start}-{file_size-1}/{file_size}')
    resp.headers.add('Accept-Ranges', 'bytes')
    resp.headers.add('Content-Length', str(file_size - byte_start))
    return resp

def generate_video(filename, byte_start):
    if PROTOCOL == "tcp":
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client_sock.connect(ORIGIN_ADDR)
        client_sock.sendall(f"REQ|{filename}|{byte_start}".encode())
        try:
            while True:
                chunk = client_sock.recv(32768)
                if not chunk: break
                yield chunk
        finally: client_sock.close()
    else:
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client_sock.settimeout(0.5)
        client_sock.sendto(f"REQ|{filename}|{byte_start}".encode(), ORIGIN_ADDR)
        
        expected_seq = 0
        buffer = {}
        try:
            while True:
                try:
                    data, addr = client_sock.recvfrom(20000)
                    if data.startswith(b"FIN|"): break
                    seq = int.from_bytes(data[:4], 'big')
                    client_sock.sendto(f"ACK|{seq}".encode(), addr)
                    if seq >= expected_seq:
                        buffer[seq] = data[4:]
                    while expected_seq in buffer:
                        yield buffer.pop(expected_seq)
                        expected_seq += 1
                except socket.timeout: continue
        except GeneratorExit: raise
        finally: client_sock.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", required=True)
    args = parser.parse_args()
    PROTOCOL = args.protocol

    v_net = VirtualNetworkInterface(client_name="ProxyNode")
    MY_IP = v_net.setup_network()

    try:
        ans = DNSRecord.question("www.myserver.homelab.").send("127.0.0.2", 53, timeout=2.0)
        ORIGIN_ADDR = (str(ans.get_a().rdata), 9000)
    except:
        ORIGIN_ADDR = ("127.0.0.13", 9000)

    app.run(host='0.0.0.0', port=5000, threaded=True)