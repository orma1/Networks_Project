import os
import base64
import hashlib
from pathlib import Path
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from dnslib import RR, QTYPE

def load_private_key(zone_name, key_type, keys_dir="keys"):
    path = Path(keys_dir) / f"{zone_name}{key_type}_private.pem"
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)

def load_public_key_b64(zone_name, key_type, keys_dir="keys"):
    path = Path(keys_dir) / f"{zone_name}{key_type}_public.txt"
    with open(path, "r") as f:
        data = f.read()
        
    if ":" in data:
        data = data.split(":")[-1]
        
    return "".join(data.split())

def sign_data(private_key, data_string):
    """Signs a string using ECDSA and SHA-256."""
    signature = private_key.sign(
        data_string.encode('utf-8'), 
        ec.ECDSA(hashes.SHA256())
    )
    return base64.b64encode(signature).decode('utf-8')

def discover_zones(project_root):
    zones_to_sign = []
    zones_dir = project_root / "zones"
    
    print("[*] Scanning directories for zone files...")
    
    for zone_file in zones_dir.rglob("*.zone"):
        if str(zone_file).endswith(".signed.zone"):
            continue
            
        filename = zone_file.name
        if filename == "root.zone":
            zone_name = "."
        else:
            zone_name = filename.replace(".zone", ".")
            
        child_zones = set()
        try:
            with open(zone_file, "r") as f:
                records = RR.fromZone(f.read())
                for rr in records:
                    if QTYPE[rr.rtype] == "NS":
                        owner = str(rr.rname)
                        if owner.endswith(zone_name) and owner != zone_name:
                            child_zones.add(owner)
        except Exception as e:
            print(f"[!] Error parsing {filename} for children: {e}")
            
        out_path = zone_file.with_name(filename.replace(".zone", ".signed.zone"))
        zones_to_sign.append((zone_file, out_path, zone_name, list(child_zones)))
        
    return zones_to_sign


def sign_zone(input_path, output_path, zone_name, child_zones, keys_dir="keys"):
    print(f"  [Signer] Began sign_zone for '{zone_name}'")
    
    with open(input_path, "r") as f:
        zone_text = f.read()
        
    records = RR.fromZone(zone_text)
    
    ksk_priv = load_private_key(zone_name, "KSK", keys_dir)
    zsk_priv = load_private_key(zone_name, "ZSK", keys_dir)
    ksk_pub = load_public_key_b64(zone_name, "KSK", keys_dir)
    zsk_pub = load_public_key_b64(zone_name, "ZSK", keys_dir)

    signed_lines = [
        f"$ORIGIN {zone_name}",
        "$TTL 86400",
        ""
    ]

    print(f"  [Signer] Injecting KSK-signed DNSKEY records...")
    signed_lines.append(f'@ IN TXT "DNSKEY|257|{ksk_pub}"')
    signed_lines.append(f'@ IN TXT "DNSKEY|256|{zsk_pub}"')

    ksk_sig_ksk = sign_data(ksk_priv, f"{zone_name}|DNSKEY|257|{ksk_pub}")
    ksk_sig_zsk = sign_data(ksk_priv, f"{zone_name}|DNSKEY|256|{zsk_pub}")
    signed_lines.append(f'@ IN TXT "RRSIG|DNSKEY|257|{ksk_sig_ksk}"')
    signed_lines.append(f'@ IN TXT "RRSIG|DNSKEY|256|{ksk_sig_zsk}"')
    signed_lines.append("")

    if child_zones:
        print(f"  [Signer] Injecting ZSK-signed DS records for delegations...")
        for child in child_zones:
            child_ksk_pub = load_public_key_b64(child, "KSK", keys_dir)
            ds_hash = hashlib.sha256(child_ksk_pub.encode('utf-8')).hexdigest()
            
            signed_lines.append(f'{child} IN TXT "DS|{ds_hash}"')
            ds_data_to_sign = f"{child}|DS|{ds_hash}"
            ds_sig = sign_data(zsk_priv, ds_data_to_sign)
            signed_lines.append(f'{child} IN TXT "RRSIG|DS|{ds_sig}"')
            signed_lines.append("")

    print(f"  [Signer] ZSK-signing standard records...")
    for rr in records:
        name = str(rr.rname)
        rtype = QTYPE[rr.rtype]
        rdata = str(rr.rdata)
        
        signed_lines.append(rr.toZone().strip())
        
        if rtype in ["A", "CNAME", "NS"]:
            data_to_sign = f"{name}|{rtype}|{rdata}"
            signature = sign_data(zsk_priv, data_to_sign)
            signed_lines.append(f'{name} IN TXT "RRSIG|{rtype}|{signature}"')
            signed_lines.append("")

    with open(output_path, "w") as f:
        f.write("\n".join(signed_lines))
    print(f"  [Signer] Saved signed zone to: {output_path.name}")

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)
    zones_to_sign = discover_zones(project_root)
    keys_directory = project_root / "keys"

    for in_path, out_path, z_name, children in zones_to_sign:
        try:
            sign_zone(in_path, out_path, z_name, children, keys_dir=str(keys_directory))
        except FileNotFoundError:
            print(f"    [!] Skipping {z_name}: Keys not found.")
        except Exception as e:
            print(f"    [!] Failed to sign {z_name}: {e}")