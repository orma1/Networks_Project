import subprocess
import os
import threading
import uvicorn
import ipaddress 
import traceback
import yaml
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Union
from pathlib import Path

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
app = FastAPI(title="DNS Resolver API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(CURRENT_DIR, "zones")):
    ZONE_DIR = os.path.join(CURRENT_DIR, "zones")
else:
    ZONE_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "zones"))

CONFIG_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "configs"))

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
    tier_dir = os.path.join(ZONE_DIR, tier.lower())
    if not os.path.exists(tier_dir):
        return []
    
    zones = []
    for file in os.listdir(tier_dir):
        if file.endswith(".zone") and not file.endswith(".signed.zone"):
            zones.append(file.replace(".zone", ""))
    return zones

@app.get("/api/zone/{server_name}/{zone_name}", response_model=ZoneData)
async def get_zone(server_name: str, zone_name: str):
    file_path = os.path.join(ZONE_DIR, server_name.lower(), f"{zone_name.lower()}.zone")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Zone file '{zone_name}.zone' not found in '{server_name}' tier.")

    zone_data = {"origin": "", "defaultTtl": 86400, "records": []}

    try:
        with open(file_path, "r") as f:
            lines = f.readlines()

        record_id_counter = 1
        for line in lines:
            line = line.strip()
            if not line or line.startswith(";"): continue
            
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
                    "id": str(record_id_counter), "name": name, "ttl": record_ttl,
                    "class": record_class, "type": record_type, "data": data
                }
                zone_data["records"].append(record)
                record_id_counter += 1

        return zone_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse zone: {str(e)}")

@app.post("/api/zone/{server_name}/{zone_name}")
async def save_zone(server_name: str, zone_name: str, payload: ZoneData):
    tier_dir = os.path.join(ZONE_DIR, server_name.lower())
    os.makedirs(tier_dir, exist_ok=True)
    file_path = os.path.join(tier_dir, f"{zone_name.lower()}.zone")

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

        print(f"[FastAPI] Regenerating keys and securely signing zone for {zone_name} in {server_name}...")

        # 2. Targeted DNSSEC Re-Signing
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

        # 3. Call the imported Signer function
        in_path = Path(file_path)
        out_path = Path(file_path.replace(".zone", ".signed.zone"))
        
        sign_zone(in_path, out_path, fqdn, child_zones, keys_dir=keys_dir)

        return {"success": True, "message": "Zone saved and securely signed."}

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to save and sign zone: {str(e)}")

@app.delete("/api/zone/{server_name}/{zone_name}")
async def delete_zone(server_name: str, zone_name: str):
    file_path = os.path.join(ZONE_DIR, server_name.lower(), f"{zone_name.lower()}.zone")
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Zone file not found.")

    try:
        os.remove(file_path)
        signed_path = file_path.replace(".zone", ".signed.zone")
        if os.path.exists(signed_path):
            os.remove(signed_path)
            
        print(f"[FastAPI] Successfully deleted zone: {zone_name}")
        return {"success": True, "message": f"Zone {zone_name} deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete zone files: {str(e)}")

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
resolver_ref = None

@app.get("/api/config/{server_name}")
async def get_server_config(server_name: str):
    file_path = os.path.join(CONFIG_DIR, f"{server_name.lower()}_config.yaml") 
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
    file_path = os.path.join(CONFIG_DIR, f"{server_name.lower()}_config.yaml")
    try:
        config_dict = payload.model_dump() 
        with open(file_path, "w") as f:
            yaml.safe_dump(config_dict, f, default_flow_style=False, sort_keys=False)
        return {"success": True, "message": "Configuration saved successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write YAML config: {str(e)}")

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