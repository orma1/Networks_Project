import socket, os, sys, yaml, signal
from flask import Flask, Response, stream_with_context, render_template_string

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.abspath(os.path.join(BASE_DIR, '..'))
sys.path.append(PARENT_DIR)

from dhcp_helper import VirtualNetworkInterface

app = Flask(__name__)

# --- Global Variables ---
MY_IP = None
ORIGIN_ADDR = None
MOVIES_LIST = []

HTML_MENU = """
<html><body style='font-family: Arial;'>
    <h1>🎬 TCP Video Library</h1>
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
    # Create a TCP Socket
    client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client_sock.connect(ORIGIN_ADDR)
        # Send the request (TCP handles reliability)
        client_sock.sendall(f"REQ|{filename}".encode())
        
        while True:
            # TCP provides a continuous stream of data
            chunk = client_sock.recv(16384) # 16KB chunks
            if not chunk:
                break
            yield chunk
    except Exception as e:
        print(f"[!] Streaming error: {e}")
    finally:
        client_sock.close()

def shutdown_handler(signum, frame):
    print("\n[*] Proxy shutting down...")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown_handler)

    v_net = VirtualNetworkInterface(client_name="ProxyNode")
    MY_IP = v_net.setup_network()

    with open("./RUDP/movies.yaml", 'r' , encoding='utf-8') as f:
        conf = yaml.safe_load(f)
        MOVIES_LIST = conf['movies']
        # Ensure this matches the Server's IP/Port
        ORIGIN_ADDR = ("127.0.0.12", 9000) 

    print(f"[*] Proxy UI at http://{MY_IP}:5000")
    app.run(host='0.0.0.0', port=5000, threaded=True, use_reloader=False)