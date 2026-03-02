import os
import base64
import hashlib
from pathlib import Path
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from dnslib import RR, QTYPE

def load_private_key(zone_name, key_type):
    path = Path("keys") / f"{zone_name}{key_type}_private.pem"
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)

def load_public_key_b64(zone_name, key_type):
    path = Path("keys") / f"{zone_name}{key_type}_public.txt"
    with open(path, "r") as f:
        data = f.read()
        
    # Strip out that metadata prefix so we only get the Base64 key
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
    
    # Recursively find all .zone files across root, tld, and auth folders
    for zone_file in zones_dir.rglob("*.zone"):
        # Skip files that are already signed
        if str(zone_file).endswith(".signed.zone"):
            continue
            
        # 1. Deduce the Zone Name from the filename
        filename = zone_file.name
        if filename == "root.zone":
            zone_name = "."
        else:
            # 'homelab.zone' -> 'homelab.' | 'test.homelab.zone' -> 'test.homelab.'
            zone_name = filename.replace(".zone", ".")
            
        # 2. Discover Child Delegations by analyzing NS records
        child_zones = set()
        try:
            with open(zone_file, "r") as f:
                records = RR.fromZone(f.read())
                for rr in records:
                    if QTYPE[rr.rtype] == "NS":
                        if QTYPE[rr.rtype] == "NS":
                            owner = str(rr.rname)
                        # Delegation only if NS owner is below the zone
                        if owner.endswith(zone_name) and owner != zone_name:
                            child_zones.add(owner)
        except Exception as e:
            print(f"[!] Error parsing {filename} for children: {e}")
            
        # 3. Build the output path
        out_path = zone_file.with_name(filename.replace(".zone", ".signed.zone"))
        
        zones_to_sign.append((zone_file, out_path, zone_name, list(child_zones)))
        print(f"    [+] Found: {zone_name} (Children: {list(child_zones)})")
        
    return zones_to_sign

def sign_zone(input_path, output_path, zone_name, child_zones):
    print(f"[*] Signing Zone: {zone_name} from {input_path.name}")
    with open(input_path, "r") as f:
        zone_text = f.read()
        
    records = RR.fromZone(zone_text)
    
    # Load all 4 key components for the zone
    ksk_priv = load_private_key(zone_name, "KSK")
    zsk_priv = load_private_key(zone_name, "ZSK")
    ksk_pub = load_public_key_b64(zone_name, "KSK")
    zsk_pub = load_public_key_b64(zone_name, "ZSK")

    signed_lines = [
        f"$ORIGIN {zone_name}",
        "$TTL 86400",
        ""
    ]

    print(f"    -> Injecting and KSK-signing DNSKEY records")
    # 1. Publish both Public Keys (Flag 257 = KSK, Flag 256 = ZSK)
    signed_lines.append(f'@ IN TXT "DNSKEY|257|{ksk_pub}"')
    signed_lines.append(f'@ IN TXT "DNSKEY|256|{zsk_pub}"')

    # 2. KSK strictly signs the keys
    ksk_sig_ksk = sign_data(ksk_priv, f"{zone_name}|DNSKEY|257|{ksk_pub}")
    ksk_sig_zsk = sign_data(ksk_priv, f"{zone_name}|DNSKEY|256|{zsk_pub}")
    signed_lines.append(f'@ IN TXT "RRSIG|DNSKEY|257|{ksk_sig_ksk}"')
    signed_lines.append(f'@ IN TXT "RRSIG|DNSKEY|256|{ksk_sig_zsk}"')
    signed_lines.append("")

    # --- NEW: Phase 3 - The Chain of Trust (DS Records) ---
    if child_zones:
        print(f"    -> Injecting and ZSK-signing DS records for delegated zones")
        for child in child_zones:
            # 3a. Grab the child's KSK Public Key and Hash it
            child_ksk_pub = load_public_key_b64(child, "KSK")
            ds_hash = hashlib.sha256(child_ksk_pub.encode('utf-8')).hexdigest()
            
            # 3b. Publish the DS record
            signed_lines.append(f'{child} IN TXT "DS|{ds_hash}"')
            
            # 3c. The Parent's ZSK signs the Child's DS record!
            ds_data_to_sign = f"{child}|DS|{ds_hash}"
            ds_sig = sign_data(zsk_priv, ds_data_to_sign)
            signed_lines.append(f'{child} IN TXT "RRSIG|DS|{ds_sig}"')
            signed_lines.append("")
    # ------------------------------------------------------

    print(f"    -> ZSK-signing A, CNAME, and NS records")
    # 4. ZSK strictly signs the data records
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
    print(f"    [+] Saved to: {output_path}")

if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)
    
    # Dynamically build the list!
    zones_to_sign = discover_zones(project_root)
    print("-" * 40)

    for in_path, out_path, z_name, children in zones_to_sign:
        try:
            sign_zone(in_path, out_path, z_name, children)
        except FileNotFoundError as e:
            # If we haven't generated keys for a zone yet (like custom.), gracefully skip it
            print(f"    [!] Skipping {z_name}: Keys not found in /keys/ directory.")
        except Exception as e:
            print(f"    [!] Failed to sign {z_name}: {e}")
            
    print("-" * 40)
    print("[+] Zone signing process complete!")