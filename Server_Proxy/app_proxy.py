import os, yaml, socket, uvicorn, time, threading
from fastapi import FastAPI, Request, Form
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from proto_tcp import tcp_client_stream
from proto_rudp import rudp_client_stream

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI()
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Load Config with Fix
with open(os.path.join(BASE_DIR, "proxy_config.yaml"), "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

PROTOCOL = config.get("protocol", "rudp")
SERVER_IP = "127.0.0.1" # Default, will be updated by resolve logic

class Quality:
    def __init__(self): self.b, self.q = 0, "High"; self.l = threading.Lock()
    def add_bytes(self, n): 
        with self.l: self.b += n
    def get(self):
        with self.l:
            if self.b < 500000: self.q = "Low"
            else: self.q = "High"
            self.b = 0
            return self.q

qs = Quality()

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "protocol": PROTOCOL})

@app.get("/video")
async def video(request: Request):
    port = 8000 if PROTOCOL == "tcp" else 9000
    if PROTOCOL == "tcp":
        return StreamingResponse(tcp_client_stream("video.mp4", 0, (SERVER_IP, port)), media_type="video/mp4")
    else:
        gen, _ = rudp_client_stream("video.mp4", 0, (SERVER_IP, port), qs)
        return StreamingResponse(gen, media_type="video/mp4")

@app.post("/switch_protocol")
async def switch(request: Request):
    global PROTOCOL
    data = await request.json()
    PROTOCOL = data.get("protocol", "rudp")
    print(f"[*] Switched to {PROTOCOL}")
    return {"status": "ok"}

@app.post("/set_loss")
async def set_loss(request: Request):
    # This just acknowledges the UI button for now
    return {"status": "ok"}

@app.get("/metrics")
async def metrics():
    return {"protocol": PROTOCOL.upper(), "quality": qs.get()}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.30", port=5000)