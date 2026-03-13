import subprocess
import os
import threading
import uvicorn
import ipaddress 
import traceback
import yaml
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Union
from pathlib import Path
from dnslib import RR, QTYPE

# 1. Path Resolution for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 2. Clean Import
from DNS.dnssec_tools.keygen import generate_zone_keys 
from DNS.dnssec_tools.zone_signer import sign_zone

# ==========================================
# 0. SETUP & PATHS
# ==========================================
app = FastAPI(title="DNS Resolver API", debug=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DEBUG HANDLER ---
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    print(f"\n[CRITICAL ERROR] 💥 Failed handling {request.method} {request.url}")
    print("="*60)
    traceback.print_exc()
    print("="*60)
    
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"Unhandled Server Crash: {str(exc)}",
            "type": str(type(exc).__name__)
        }
    )
# --------------------------------

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(CURRENT_DIR, "zones")):
    ZONE_DIR = os.path.join(CURRENT_DIR, "zones")
else:
    ZONE_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "zones"))
CONFIG_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "configs"))

# ==========================================
# 1. PYDANTIC MODELS
# ==========================================
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

class ARecordUpdate(BaseModel):
    name: str
    ip: str
    ttl: Optional[int] = None

class ServerSection(BaseModel):
    bind_ip: str
    bind_port: int
    buffer_size: int

class NameServerDataSection(BaseModel):
    zone_directory: str

class NameServerConfig(BaseModel):
    server: ServerSection
    data: NameServerDataSection

class ResolverUpstreamSection(BaseModel):
    root_server_ip: str
    root_server_port: int
    public_forwarder: str
    public_port: int

class ResolverBehaviorSection(BaseModel):
    default_ttl: int
    timeout: float
    enable_logging: bool

class ResolverStorageSection(BaseModel):
    cache_file: str
    save_interval: int
    cache_capacity: int

class ResolverConfig(BaseModel):
    server: ServerSection
    upstream: ResolverUpstreamSection
    behavior: ResolverBehaviorSection
    storage: ResolverStorageSection

ConfigPayload = Union[ResolverConfig, NameServerConfig]

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

# --- SECURITY FIX: Path Traversal Prevention ---
def get_safe_zone_path(server_name: str, zone_name: str = None) -> str:
    """
    Constructs and strictly validates a path within the ZONE_DIR.
    Raises ValueError if path traversal is detected.
    """
    if zone_name:
        target_path = os.path.abspath(os.path.join(ZONE_DIR, server_name.lower(), f"{zone_name.lower()}.zone"))
    else:
        target_path = os.path.abspath(os.path.join(ZONE_DIR, server_name.lower()))
    
    # The resolved path MUST still start with the absolute ZONE_DIR path
    if not target_path.startswith(os.path.abspath(ZONE_DIR)):
        raise ValueError("Invalid path: Traversal attempt detected.")
    
    return target_path

def get_safe_config_path(server_name: str) -> str:
    """
    Constructs and strictly validates a path within the CONFIG_DIR.
    Raises ValueError if path traversal is detected.
    """
    target_path = os.path.abspath(os.path.join(CONFIG_DIR, f"{server_name.lower()}_config.yaml"))
    
    if not target_path.startswith(os.path.abspath(CONFIG_DIR)):
        raise ValueError("Invalid config path: Traversal attempt detected.")
        
    return target_path
# -----------------------------------------------

def validate_dns_records(records: List[DnsRecord]):
    for idx, rec in enumerate(records):
        rtype = rec.type.upper()
        data = rec.data.strip()
        name = rec.name.strip()

        try:
            if rtype == "A":
                ipaddress.IPv4Address(data) 
            elif rtype == "AAAA":
                ipaddress.IPv6Address(data)
            elif rtype in ["NS", "CNAME"]:
                if " " in data:
                    raise ValueError(f"Contains invalid spaces.")
            elif rtype == "SOA":
                parts = data.split()
                if len(parts) != 7:
                    raise ValueError(f"Must have exactly 7 fields. Got {len(parts)}.")
                for num_part in parts[2:]:
                    if not num_part.isdigit():
                        raise ValueError(f"SOA timers/serials must be numbers. Found: '{num_part}'")
        except ipaddress.AddressValueError:
            raise ValueError(f"Row {idx + 1} (Name: {name}): Invalid {rtype} address format -> '{data}'")
        except ValueError as e:
            raise ValueError(f"Row {idx + 1} (Name: {name}): {str(e)}")

# ==========================================
# 3. CORE ZONE ENDPOINTS
# ==========================================
@app.get("/api/zones/list/{tier}")
async def list_zone_files(tier: str):
    try:
        tier_dir = get_safe_zone_path(tier)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not os.path.exists(tier_dir):
        return []
    
    zones = []
    for file in os.listdir(tier_dir):
        if file.endswith(".zone") and not file.endswith(".signed.zone"):
            zones.append(file.replace(".zone", ""))
    return zones

@app.get("/api/zone/{server_name}/{zone_name}", response_model=ZoneData)
async def get_zone(server_name: str, zone_name: str):
    try:
        file_path = get_safe_zone_path(server_name, zone_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Zone file '{zone_name}.zone' not found in '{server_name}' tier.")

    zone_data = {"origin": "", "defaultTtl": 86400, "records": []}

    try:
        # First pass: Extract just the $ORIGIN and $TTL directives
        with open(file_path, "r") as f:
            raw_zone_text = f.read()
            
        for line in raw_zone_text.splitlines():
            line = line.strip()
            if line.startswith("$ORIGIN"):
                zone_data["origin"] = line.split()[1]
            elif line.startswith("$TTL"):
                zone_data["defaultTtl"] = int(line.split()[1])

        parsed_records = RR.fromZone(raw_zone_text)
        
        record_id_counter = 1
        for record in parsed_records:
            rdata_str = str(record.rdata)
            
            if record.rtype == 16: # 16 is the integer for TXT
                if rdata_str.startswith("b'") or rdata_str.startswith('b"'):
                    rdata_str = rdata_str[2:-1]
                
                if not rdata_str.startswith('"'):
                    rdata_str = f'"{rdata_str}"'

            record_dict = {
                "id": str(record_id_counter), 
                "name": str(record.rname), 
                "ttl": record.ttl,
                "class": "IN", 
                "type": QTYPE.get(record.rtype), 
                "data": rdata_str
            }
            zone_data["records"].append(record_dict)
            record_id_counter += 1

        return zone_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse zone: {str(e)}")

@app.post("/api/zone/{server_name}/{zone_name}")
async def save_zone(server_name: str, zone_name: str, payload: ZoneData):
    try:
        tier_dir = get_safe_zone_path(server_name)
        file_path = get_safe_zone_path(server_name, zone_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        os.makedirs(tier_dir, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create zone directory: {str(e)}")

    try:
        validate_dns_records(payload.records)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        # 1. Write the raw file
        with open(file_path, "w") as f:
            f.write(f"$ORIGIN {payload.origin}\n")
            f.write(f"$TTL {payload.defaultTtl}\n\n")
            for rec in payload.records:
                ttl_str = f"{rec.ttl}\t" if rec.ttl is not None else ""
                line = f"{rec.name.ljust(19)} {ttl_str}{rec.record_class}\t{rec.type.ljust(4)}\t{rec.data}\n"
                f.write(line)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to write zone file: {str(e)}")

    # 2. Targeted DNSSEC Re-Signing (non-fatal — zone file is already saved)
    try:
        print(f"[FastAPI] Regenerating keys and securely signing zone for {zone_name} in {server_name}...")

        fqdn = "." if zone_name.lower() == "root" else f"{zone_name.lower()}."
        child_zones = [rec.name for rec in payload.records if rec.type == "NS" and rec.name.endswith(fqdn) and rec.name != fqdn]
        keys_dir = os.path.join(project_root, "keys")

        # Check for missing keys and generate if needed
        ksk_priv_path = os.path.join(keys_dir, f"{fqdn}KSK_private.pem")
        if not os.path.exists(ksk_priv_path):
            generate_zone_keys(fqdn, output_dir=keys_dir)

        for child in child_zones:
            child_ksk_path = os.path.join(keys_dir, f"{child}KSK_private.pem")
            if not os.path.exists(child_ksk_path):
                generate_zone_keys(child, output_dir=keys_dir)

        in_path = Path(file_path)
        out_path = Path(file_path.replace(".zone", ".signed.zone"))
        sign_zone(in_path, out_path, fqdn, child_zones, keys_dir=keys_dir)

        return {"success": True, "message": "Zone saved and securely signed."}

    except Exception as e:
        traceback.print_exc()
        print(f"[FastAPI] Warning: DNSSEC signing failed for {zone_name}: {e}")
        return {"success": True, "message": "Zone saved (DNSSEC signing failed — see server logs)."}

@app.delete("/api/zone/{server_name}/{zone_name}")
async def delete_zone(server_name: str, zone_name: str):
    try:
        file_path = get_safe_zone_path(server_name, zone_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Zone file not found.")

    try:
        os.remove(file_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete zone file: {str(e)}")

    # Remove the signed zone file if present — non-fatal
    signed_path = file_path.replace(".zone", ".signed.zone")
    if os.path.exists(signed_path):
        try:
            os.remove(signed_path)
        except Exception as e:
            print(f"[FastAPI] Warning: could not remove signed zone file: {e}")

    print(f"[FastAPI] Successfully deleted zone: {zone_name}")
    return {"success": True, "message": f"Zone {zone_name} deleted."}

# ==========================================
# 4. SPECIFIC RECORD ENDPOINTS
# ==========================================
@app.get("/api/zone/{server_name}/{zone_name}/records/a")
async def get_a_records(server_name: str, zone_name: str):
    zone_data = await get_zone(server_name, zone_name)
    a_records = [rec for rec in zone_data["records"] if rec["type"] == "A"]
    return {"server_name": server_name, "zone_name": zone_name, "a_records": a_records}

@app.post("/api/zone/{server_name}/{zone_name}/records/a/{record_id}")
async def update_a_record(server_name: str, zone_name: str, record_id: str, payload: ARecordUpdate):
    zone_data = await get_zone(server_name, zone_name)
    record_found = False
    
    for rec in zone_data["records"]:
        if rec["type"] == "A" and rec["id"] == record_id:
            rec["name"] = payload.name 
            rec["data"] = payload.ip
            if payload.ttl is not None: 
                rec["ttl"] = payload.ttl
            record_found = True
            break
            
    if not record_found:
        existing_ids = [int(r["id"]) for r in zone_data["records"] if r["id"].isdigit()]
        next_id = str(max(existing_ids) + 1) if existing_ids else "1"
        new_record = {
            "id": next_id, 
            "name": payload.name, 
            "class": "IN", 
            "type": "A", 
            "ttl": payload.ttl, 
            "data": payload.ip
        }
        zone_data["records"].append(new_record)
        
    zone_payload = ZoneData(**zone_data)
    await save_zone(server_name, zone_name, zone_payload)
    
    action = "Updated" if record_found else "Created"
    return {"success": True, "message": f"A record '{payload.name}' {action} successfully with IP {payload.ip}."}

# ==========================================
# 5. SERVER RUNNER & CONFIG ENDPOINTS 
# ==========================================
@app.get("/api/config/{server_name}")
async def get_server_config(server_name: str):
    try:
        file_path = get_safe_config_path(server_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Config file not found.")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            raw_content = f.read()
        if not raw_content.strip():
            raise HTTPException(status_code=500, detail="File is empty.")

        raw_yaml_dict = yaml.safe_load(raw_content)

        if server_name.lower() == "resolver":
            validated_config = ResolverConfig(**raw_yaml_dict)
        else:
            validated_config = NameServerConfig(**raw_yaml_dict)
        return validated_config
    except Exception as e:
        traceback.print_exc() 
        raise HTTPException(status_code=500, detail=f"Backend crash: {str(e)}")

@app.post("/api/config/{server_name}")
async def save_server_config(server_name: str, payload: ConfigPayload):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    
    try:
        file_path = get_safe_config_path(server_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    try:
        config_dict = payload.model_dump() 
        with open(file_path, "w") as f:
            yaml.safe_dump(config_dict, f, default_flow_style=False, sort_keys=False)
        return {"success": True, "message": "Configuration saved successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write YAML config: {str(e)}")

class APIServer:
    def __init__(self, resolver, host="127.0.0.1", port=8000):
        app.state.resolver = resolver
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