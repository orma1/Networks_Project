import os
import base64
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from dnslib import RR, QTYPE

def load_private_key(zone_name):
    path = os.path.join("keys", f"{zone_name}private.pem")
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)

def load_public_key_b64(zone_name):
    path = os.path.join("keys", f"{zone_name}public.txt")
    with open(path, "r") as f:
        lines = f.readlines()
        return lines[-1].strip()

def sign_data(private_key, data_string):
    """Signs a string using ECDSA and SHA-256."""
    signature = private_key.sign(
        data_string.encode('utf-8'), 
        ec.ECDSA(hashes.SHA256())
    )
    return base64.b64encode(signature).decode('utf-8')

def sign_zone():
    zone_name = "test.homelab."
    input_file = "zones/auth.zone"
    output_file = "zones/auth.signed.zone"
    
    print(f"[*] Loading BIND zone: {input_file}")
    with open(input_file, "r") as f:
        zone_text = f.read()
        
    # Let dnslib parse the text so we have clean objects to work with
    records = RR.fromZone(zone_text)
    
    print(f"[*] Loading ECDSA keys for {zone_name}")
    priv_key = load_private_key(zone_name)
    pub_key_b64 = load_public_key_b64(zone_name)

    # We rebuild the file line by line
    signed_lines = [
        f"$ORIGIN {zone_name}",
        "$TTL 86400",
        ""
    ]

    print(f"[*] Injecting and signing DNSKEY for {zone_name}")
    # 1. Publish the Public Key
    signed_lines.append(f'@ IN TXT "DNSKEY|{pub_key_b64}"')
    
    # 2. Sign the Public Key
    dnskey_sig = sign_data(priv_key, f"{zone_name}|DNSKEY|{pub_key_b64}")
    signed_lines.append(f'@ IN TXT "RRSIG|DNSKEY|{dnskey_sig}"')
    signed_lines.append("")

    # 3. Loop through original records and sign them
    for rr in records:
        name = str(rr.rname)
        rtype = QTYPE[rr.rtype]
        rdata = str(rr.rdata)
        
        # Keep the original record exactly as it was
        signed_lines.append(rr.toZone().strip())
        
        # We only sign A and CNAME records for this implementation
        if rtype in ["A", "CNAME"]:
            print(f"    -> Signing {rtype} record for {name}")
            data_to_sign = f"{name}|{rtype}|{rdata}"
            signature = sign_data(priv_key, data_to_sign)
            
            # Attach the signature right beneath it
            signed_lines.append(f'{name} IN TXT "RRSIG|{rtype}|{signature}"')
            signed_lines.append("") # blank line for readability

    print(f"[*] Saving signed zone to: {output_file}")
    with open(output_file, "w") as f:
        f.write("\n".join(signed_lines))
        
    print("-" * 40)
    print("[+] Zone signing complete!")

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sign_zone()