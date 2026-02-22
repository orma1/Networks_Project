import base64
import os
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

def generate_zone_keys(zone_name, output_dir="keys"):
    """
    Generates an ECDSA private/public key pair for a DNS zone.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    print(f"[*] Generating ECDSA P-256 keys for zone: {zone_name}")

    # 1. Generate the Private Key
    private_key = ec.generate_private_key(ec.SECP256R1())

    # 2. Serialize Private Key to PEM format (to save to disk)
    pem_private = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    )

    # 3. Extract the Public Key (The DNSKEY)
    public_key = private_key.public_key()
    
    # In DNSSEC, ECDSA public keys are stored as raw bytes (X + Y coordinates)
    # We grab the raw numbers and encode them in Base64 for the DNSKEY record
    public_numbers = public_key.public_numbers()
    x_bytes = public_numbers.x.to_bytes(32, byteorder='big')
    y_bytes = public_numbers.y.to_bytes(32, byteorder='big')
    raw_public_key = x_bytes + y_bytes
    
    b64_public_key = base64.b64encode(raw_public_key).decode('utf-8')

    # 4. Save the Private Key
    priv_file = os.path.join(output_dir, f"{zone_name}private.pem")
    with open(priv_file, "wb") as f:
        f.write(pem_private)

    # 5. Save the Public Key Info
    pub_file = os.path.join(output_dir, f"{zone_name}public.txt")
    with open(pub_file, "w") as f:
        f.write(f"Zone: {zone_name}\n")
        f.write(f"Algorithm: 13 (ECDSA Curve P-256 with SHA-256)\n")
        f.write(f"DNSKEY Base64:\n{b64_public_key}\n")

    print(f"    [+] Saved Private Key: {priv_file}")
    print(f"    [+] Saved Public Key:  {pub_file}")
    print("-" * 40)

if __name__ == "__main__":
    # Let's generate keys for our 3 authoritative zones!
    generate_zone_keys("homelab.")         # For the Root
    generate_zone_keys("test.homelab.")    # For the TLD
    generate_zone_keys("auth.test.homelab.") # For the Auth (the zone itself)