import base64
import hashlib
import socket
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature
from dnslib import DNSRecord, QTYPE, RCODE, RR, A
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

    @staticmethod
    def verify_dnssec_chain(domain, qtype_str, rdata_str, signature_b64, auth_ip, bind_ip, dnskey_cache, ds_cache, auth_port=53):
        """
        Verifies the signature, utilizing a local cache for DNSKEYs to avoid network spam.
        """
        print(f"\n[*] DNSSEC: Initiating Chain of Trust Verification for {domain}")
        
        # 1. Figure out the apex domain (e.g., test.homelab.)
        apex_domain = ".".join(domain.strip(".").split(".")[-2:]) + "."
        
        zsk_pub = None
        ksk_pub = None
        
        # 2. THE CACHE CHECK (Using the passed-in dictionary)
        if apex_domain in dnskey_cache:
            print(f"    [+] KEY CACHE HIT: Using stored ZSK for {apex_domain}")
            zsk_pub = dnskey_cache[apex_domain].get('ZSK')
            ksk_pub = dnskey_cache[apex_domain].get('KSK')

        else:
            # 3. THE CACHE MISS (Network Fetch)
            print(f"    -> KEY CACHE MISS: Fetching DNSKEYs for {apex_domain} from {auth_ip}...")
            key_request = DNSRecord.question(apex_domain, "TXT")
            
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(2.0)
                
                # Bind to the Resolver's IP passed from the main class
                sock.bind((bind_ip, 0))
                
                sock.sendto(key_request.pack(), (auth_ip, auth_port))
                response_data, _ = sock.recvfrom(4096)
                key_reply = DNSRecord.parse(response_data)
                
                ksk_pub = None
                zsk_pub_temp = None
                zsk_signature = None
                ksk_signature = None

                for rr in key_reply.rr:
                    if rr.rtype == getattr(QTYPE, 'TXT'):
                        txt_val = b"".join(rr.rdata.data).decode('utf-8')
                        
                        if txt_val.startswith("DNSKEY|257|"):
                            ksk_pub = txt_val.split("|")[2]
                        elif txt_val.startswith("RRSIG|DNSKEY|257|"):
                            ksk_signature = txt_val.split("|")[3]
                        elif txt_val.startswith("DNSKEY|256|"):
                            zsk_pub_temp = txt_val.split("|")[2]
                        elif txt_val.startswith("RRSIG|DNSKEY|256|"):
                            zsk_signature = txt_val.split("|")[3]
                        
                if ksk_pub and zsk_pub_temp and ksk_signature and zsk_signature:
                    print(f"    -> Verifying KSK self-signature (Integrity Check)...")
                    ksk_data_string = f"{apex_domain}|DNSKEY|257|{ksk_pub}"
                    is_ksk_valid = DNSSECValidator.verify_signature(ksk_pub, ksk_data_string, ksk_signature)
                    
                    if not is_ksk_valid:
                        print("    [!] DNSSEC BOGUS: KSK self-signature failed! Key is corrupted or forged.")
                        return False
                    print("    [+] DNSSEC SECURE: KSK self-signature is intact.")

                    print(f"    -> Verifying ZSK signature using the KSK...")
                    zsk_data_string = f"{apex_domain}|DNSKEY|256|{zsk_pub_temp}"
                    is_zsk_valid = DNSSECValidator.verify_signature(ksk_pub, zsk_data_string, zsk_signature)
                    
                    if not is_zsk_valid:
                        print("    [!] DNSSEC BOGUS: KSK did NOT sign this ZSK! Forgery detected.")
                        return False
                    print("    [+] DNSSEC SECURE: KSK successfully validated the ZSK.")

                    zsk_pub = zsk_pub_temp
                    # SAVE IT TO THE VAULT (Updates the Resolver's dictionary automatically!)
                    dnskey_cache[apex_domain] = {'KSK': ksk_pub, 'ZSK': zsk_pub}
                    print(f"    [+] Successfully fetched and cached DNSKEYs for {apex_domain}")
                else:
                    print("    [!] DNSSEC BOGUS: Missing necessary keys/signatures in Auth response!")
                    return False
                    
            except socket.timeout:
                print("    [!] DNSSEC ERROR: Timeout fetching keys.")
                return False
            except Exception as e:
                print(f"    [!] DNSSEC ERROR: {e}")
                return False
            finally:
                sock.close()
            
        # 4. Verify the KSK against the Parent's DS Hash
        if apex_domain != ".":
            if apex_domain in ds_cache:
                parent_ds_hash = ds_cache[apex_domain]
                print(f"    -> Verifying KSK against Parent DS hash...")
                
                is_ds_valid = DNSSECValidator.verify_ds_record(ksk_pub, parent_ds_hash)
                if not is_ds_valid:
                    print("    [!] DNSSEC BOGUS: KSK does not match Parent DS!")
                    return False
                print("    [+] DNSSEC SECURE: Parent DS vouches for this KSK!")
            else:
                print(f"    [!] DNSSEC BOGUS: No DS record found for {apex_domain}. Chain broken!")
                return False

        # 5. Final math on the actual record
        data_to_verify = f"{domain}|{qtype_str}|{rdata_str}"
        print(f"    -> Running ECDSA Math on: {data_to_verify}")
        
        is_valid = DNSSECValidator.verify_signature(zsk_pub, data_to_verify, signature_b64)
        
        if is_valid:
            print("    [+] DNSSEC SECURE: Cryptographic math checks out!")
            return True
        else:
            print("    [!] DNSSEC BOGUS: Cryptographic math FAILED!")
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