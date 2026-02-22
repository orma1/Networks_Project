import base64
import os
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

def generate_key_pair(zone_name, key_type, output_dir="keys"):
    """Generates a specific key type (KSK or ZSK) for a zone."""
    print(f"[*] Generating {key_type} for zone: {zone_name}")
    
    # 1. Generate the Private Key
    private_key = ec.generate_private_key(ec.SECP256R1())
    
    # 2. Serialize Private Key
    pem_private = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    # 3. Extract and Encode Public Key
    public_numbers = private_key.public_key().public_numbers()
    x_bytes = public_numbers.x.to_bytes(32, byteorder='big')
    y_bytes = public_numbers.y.to_bytes(32, byteorder='big')
    raw_public_key = x_bytes + y_bytes
    b64_public_key = base64.b64encode(raw_public_key).decode('utf-8')

    # 4. Save Private Key
    priv_file = os.path.join(output_dir, f"{zone_name}{key_type}_private.pem")
    with open(priv_file, "wb") as f:
        f.write(pem_private)

    # 5. Save Public Key with strict RFC Flags (257 = KSK, 256 = ZSK)
    pub_file = os.path.join(output_dir, f"{zone_name}{key_type}_public.txt")
    flag = 257 if key_type == "KSK" else 256
    
    with open(pub_file, "w") as f:
        f.write(f"Zone: {zone_name}\n")
        f.write(f"Type: {key_type}\n")
        f.write(f"Flag: {flag}\n")
        f.write(f"DNSKEY Base64:\n{b64_public_key}\n")

    print(f"    [+] Saved Private: {priv_file}")
    print(f"    [+] Saved Public:  {pub_file}")

def generate_zone_keys(zone_name, output_dir="keys"):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    # Generate BOTH keys for the zone
    generate_key_pair(zone_name, "KSK", output_dir)
    generate_key_pair(zone_name, "ZSK", output_dir)
    print("-" * 40)

if __name__ == "__main__":
    # Ensure we run from the project root
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    # Generate KSK/ZSK pairs for our 3 authoritative zones
    generate_zone_keys(".")
    generate_zone_keys("homelab.")
    generate_zone_keys("custom.")
    generate_zone_keys("test.homelab.")
    generate_zone_keys("mywebsite.custom.")
    # We no longer need 'auth.test.homelab.' as the zone is just 'test.homelab.'