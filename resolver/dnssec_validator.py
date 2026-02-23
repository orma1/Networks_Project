import base64
import hashlib
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature

class DNSSECValidator:
    @staticmethod
    def load_public_key(b64_key: str):
        """Reconstructs the ECDSA Public Key object from a Base64 string."""
        raw_key_bytes = base64.b64decode(b64_key)
        
        # True DNSSEC (RFC 6605) strips the ASN.1 headers and the uncompressed 
        # point identifier to save packet space, leaving exactly 64 bytes.
        if len(raw_key_bytes) == 64:
            # Reconstruct the uncompressed EC point by prepending the 0x04 byte
            full_point = b"\x04" + raw_key_bytes
            return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), full_point)
        else:
            # Fallback in case it IS a standard PEM format
            pem_data = f"-----BEGIN PUBLIC KEY-----\n{b64_key}\n-----END PUBLIC KEY-----"
            return serialization.load_pem_public_key(pem_data.encode('utf-8'))

    @staticmethod
    def verify_signature(pub_key_b64: str, data_string: str, signature_b64: str) -> bool:
        """
        The core ECDSA math: Verifies that the data_string was signed 
        by the private key corresponding to pub_key_b64.
        """
        try:
            public_key = DNSSECValidator.load_public_key(pub_key_b64)
            signature_bytes = base64.b64decode(signature_b64)
            
            # The verify method will fail silently if successful, 
            # and raise an InvalidSignature exception if the math fails.
            public_key.verify(
                signature_bytes,
                data_string.encode('utf-8'),
                ec.ECDSA(hashes.SHA256())
            )
            return True
            
        except InvalidSignature:
            print("[!] DNSSEC BOGUS: Cryptographic signature mismatch!")
            return False
        except Exception as e:
            print(f"[!] DNSSEC ERROR: Could not parse signature data: {e}")
            return False

    @staticmethod
    def verify_ds_record(child_ksk_pub_b64: str, parent_ds_hash_hex: str) -> bool:
        """
        The Chain of Trust proof: Hashes the child's KSK and checks if it 
        matches the parent's published DS record.
        """
        try:
            calculated_hash = hashlib.sha256(child_ksk_pub_b64.encode('utf-8')).hexdigest()
            if calculated_hash == parent_ds_hash_hex:
                return True
            else:
                print(f"[!] DNSSEC BOGUS: DS hash mismatch!")
                print(f"    Expected: {parent_ds_hash_hex}")
                print(f"    Got:      {calculated_hash}")
                return False
        except Exception as e:
            print(f"[!] DNSSEC ERROR: Failed to verify DS record: {e}")
            return False

# --- Quick Local Test ---
if __name__ == "__main__":
    print("[*] Running local validator test...")
    # This proves the math engine works independently of the network!
    dummy_priv = ec.generate_private_key(ec.SECP256R1())
    dummy_pub = dummy_priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8').split('\n')[1:-2]
    
    dummy_pub_b64 = "".join(dummy_pub)
    
    test_data = "server1.test.homelab.|A|192.168.1.102"
    sig = dummy_priv.sign(test_data.encode(), ec.ECDSA(hashes.SHA256()))
    sig_b64 = base64.b64encode(sig).decode()
    
    is_valid = DNSSECValidator.verify_signature(dummy_pub_b64, test_data, sig_b64)
    print(f"[*] Signature Verification Result: {'PASS' if is_valid else 'FAIL'}")