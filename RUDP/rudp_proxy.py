import socket, os, sys, threading, yaml, signal
from flask import Flask, Response, stream_with_context, render_template_string

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, '..'))
sys.path.append(PARENT_DIR)

from dhcp_helper import VirtualNetworkInterface

app = Flask(__name__)

# --- משתנים גלובליים ---
MY_IP = None
ORIGIN_ADDR = None
MOVIES_LIST = []

# --- תבניות HTML ---
HTML_MENU = """
<html><body style='font-family: Arial;'>
    <h1>🎬 RUDP Video Library</h1>
    <ul>{% for m in movies %}
        <li><b>{{ m.name }}</b> - <a href='/play/{{ m.file }}'>Watch Now</a></li>
    {% endfor %}</ul>
</body></html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_MENU, movies=MOVIES_LIST)

@app.route('/play/<filename>')
def play(filename):
    return render_template_string("""
        <html><body>
            <h2>Playing: {{ f }}</h2>
            <video controls autoplay width='800'><source src='/stream/{{ f }}' type='video/mp4'></video>
            <br><a href='/'>Back to Menu</a>
        </body></html>
    """, f=filename)

@app.route('/stream/<filename>')
def stream(filename):
    return Response(stream_with_context(generate_video(filename)), mimetype='video/mp4')

def generate_video(filename):
    client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_sock.bind((MY_IP, 0))
    client_sock.settimeout(2.0)
    
    client_sock.sendto(f"REQ|{filename}".encode(), ORIGIN_ADDR)
    
    expected_seq = 0
    while True:
        try:
            data, addr = client_sock.recvfrom(40000)
            if data.startswith(b"FIN|"): break
            
            seq = int.from_bytes(data[:4], 'big')
            payload = data[4:]
            
            if seq == expected_seq:
                client_sock.sendto(f"ACK|{seq}".encode(), addr)
                expected_seq += 1
                yield payload
            elif seq < expected_seq:
                client_sock.sendto(f"ACK|{seq}".encode(), addr)
        except socket.timeout: continue
    client_sock.close()

def shutdown_handler(signum, frame):
    print("\n[*] Proxy shutting down...")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown_handler)

    # 1. רשת
    v_net = VirtualNetworkInterface(client_name="ProxyNode")
    MY_IP = v_net.setup_network()

    # 2. קונפיגורציה
    config_path = os.path.join(PARENT_DIR, 'config.yaml')
    with open("movies.yaml", 'r' , encoding='utf-8') as f:
        conf = yaml.safe_load(f)
        MOVIES_LIST = conf['movies']
        # TODO: Get the origin server IP from DNS. For now, we assume it's in the config.
        ORIGIN_ADDR = ("127.0.0.13", 9000)  # Hardcoded for testing

    print(f"[*] Proxy UI at http://{MY_IP}:5000")
    app.run(host='0.0.0.0', port=5000, threaded=True, use_reloader=False)