from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
import subprocess
import os
import threading
import uvicorn

app = FastAPI(title="DNS Resolver API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allows Electron and Swagger to connect from anywhere
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 1. Pydantic Models ---
class DnsRecord(BaseModel):
    id: str
    name: str
    record_class: str = Field(default="IN", alias="class") 
    type: str
    ttl: Optional[int] = None
    data: str

class ZoneData(BaseModel):
    origin: str
    defaultTtl: int
    records: List[DnsRecord]

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

if os.path.exists(os.path.join(CURRENT_DIR, "zones")):
    ZONE_DIR = os.path.join(CURRENT_DIR, "zones")
else:
    ZONE_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "zones"))

# --- NEW: Smart File Finder ---
def find_zone_file(server_name: str) -> str:
    target_filename = f"{server_name.lower()}.zone"
    
    # Check if it's directly in the zones folder first
    direct_path = os.path.join(ZONE_DIR, target_filename)
    if os.path.exists(direct_path):
        return direct_path

    # If not, scan the subdirectories (auth, root, tld)
    for root_dir, _, files in os.walk(ZONE_DIR):
        if target_filename in files:
            return os.path.join(root_dir, target_filename)
            
    return None

# --- 2. GET Endpoint ---
@app.get("/api/zone/{server_name}", response_model=ZoneData)
async def get_zone(server_name: str):
    # Use the new smart finder!
    file_path = find_zone_file(server_name)
    
    if not file_path:
        raise HTTPException(status_code=404, detail=f"Zone file '{server_name}.zone' not found in any subfolder.")

    zone_data = {
        "origin": "",
        "defaultTtl": 86400,
        "records": []
    }

    try:
        with open(file_path, "r") as f:
            lines = f.readlines()

        record_id_counter = 1

        for line in lines:
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            
            if line.startswith("$ORIGIN"):
                zone_data["origin"] = line.split()[1]
                continue
            if line.startswith("$TTL"):
                zone_data["defaultTtl"] = int(line.split()[1])
                continue

            parts = line.split()
            if len(parts) >= 4:
                has_ttl = parts[1].isdigit()
                name = parts[0]
                record_ttl = int(parts[1]) if has_ttl else None
                record_class = parts[2] if has_ttl else parts[1]
                record_type = parts[3] if has_ttl else parts[2]
                
                data_start_index = 4 if has_ttl else 3
                data = " ".join(parts[data_start_index:])
                
                record = {
                    "id": str(record_id_counter),
                    "name": name,
                    "ttl": record_ttl,
                    "class": record_class,
                    "type": record_type,
                    "data": data
                }
                zone_data["records"].append(record)
                record_id_counter += 1

        return zone_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse zone: {str(e)}")
    
# --- 3. POST Endpoint ---
@app.post("/api/zone/{server_name}")
async def save_zone(server_name: str, payload: ZoneData):
    # Use the smart finder to overwrite the correct file!
    file_path = find_zone_file(server_name)
    
    if not file_path:
        # If it's a brand new zone, default to saving it directly in ZONE_DIR
        file_path = os.path.join(ZONE_DIR, f"{server_name.lower()}.zone")

    try:
        with open(file_path, "w") as f:
            f.write(f"$ORIGIN {payload.origin}\n")
            f.write(f"$TTL {payload.defaultTtl}\n\n")

            for rec in payload.records:
                ttl_str = f"{rec.ttl}\t" if rec.ttl is not None else ""
                line = f"{rec.name.ljust(19)} {ttl_str}{rec.record_class}\t{rec.type.ljust(4)}\t{rec.data}\n"
                f.write(line)

        print(f"[FastAPI] Regenerating keys and reloading zone for {server_name}...")
        return {"success": True, "message": "Zone saved and regenerated."}

    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Failed to run system command: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save zone: {str(e)}")

# --- FOLDER SCANNER ENDPOINT ---
@app.get("/api/zones/list/{tier}")
async def list_zone_files(tier: str):
    # Translates "Root" -> "root" and maps it to your ZONE_DIR
    tier_dir = os.path.join(ZONE_DIR, tier.lower())
    
    if not os.path.exists(tier_dir):
        return []
    
    zones = []
    for file in os.listdir(tier_dir):
        # Grab only the base zone files, ignoring the signed ones
        if file.endswith(".zone") and not file.endswith(".signed.zone"):
            zones.append(file.replace(".zone", ""))
            
    return zones

# --- 4. API Server Class ---
resolver_ref = None

class APIServer:
    def __init__(self, resolver, host="127.0.0.1", port=8000):
        global resolver_ref
        resolver_ref = resolver
        self.host = host
        self.port = port
        self.thread = None

    def start(self):
        print(f"[*] API Server booting on http://{self.host}:{self.port}")
        self.thread = threading.Thread(
            target=uvicorn.run, 
            args=(app,), 
            kwargs={"host": self.host, "port": self.port, "log_level": "critical"}
        )
        self.thread.daemon = True
        self.thread.start()
        
    def stop(self):
        print("[*] API Server shutting down...")