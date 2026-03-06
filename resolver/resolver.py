
import socket
import threading

# --- Local Imports ---
from config_loader import ConfigLoader
from dns_cache import DNSCache
from dnslib import DNSRecord, QTYPE, RCODE, RR, A
from forwarder import Forwarder
from dnssec_validator import DNSSECValidator

class Resolver:
    def __init__(self, config, dnssec_enabled=False):
        """ Receives the config object from the Manager """
        self.dnssec_enabled = dnssec_enabled
        self.config = config
        self.cache = DNSCache(
            filename=self.config.cache_file_path,
            capacity=self.config.cache_capacity,
            save_interval=self.config.save_interval
        )
        self.dnskey_cache = {}
        self.ds_cache = {}
        self.forwarder = Forwarder(timeout=self.config.timeout)
        self.running = False
        self.server_socket = None

    def _resolve_iterative(self, request, target_domain):
        current_ip = self.config.root_server_ip
        current_port = self.config.root_server_port
        visited_ips = set() # [EDGE CASE 7] Loop Detection
        
        qtype = request.q.qtype

        for jump in range(10): # Cap at 10 jumps
            if current_ip in visited_ips:
                print(f"    [ERROR] Routing loop detected at {current_ip}. Aborting.")
                break
            visited_ips.add(current_ip)

            print(f"    [Iterative Jump {jump+1}] Asking {current_ip} for {target_domain}")
            
            try:
                raw_response = self.forwarder.send_query(current_ip, current_port, request.pack())
                response = DNSRecord.parse(raw_response)
                
                # [EDGE CASE 2 & 12] Distinguish RCODEs properly
                if response.header.rcode != getattr(RCODE, 'NOERROR'):
                    rcode_val = response.header.rcode
                    if rcode_val == getattr(RCODE, 'NXDOMAIN'):
                        print(f"    [*] NXDOMAIN from {current_ip}. Domain does not exist.")
                        return raw_response, current_ip
                    elif rcode_val in (getattr(RCODE, 'SERVFAIL'), getattr(RCODE, 'REFUSED')):
                        print(f"    [*] Server {current_ip} returned {RCODE.get(rcode_val)}.")
                        if jump == 0:
                            print("    [*] Local Root failed. Triggering public fallback.")
                            return None, None
                        break # Abort current path
                    else:
                        print(f"    [*] Server {current_ip} returned unexpected error: {rcode_val}.")
                        return raw_response, current_ip
                
                # [EDGE CASE 1 & 6] We got an Answer, but is it the right one?
                if len(response.rr) > 0:
                    has_exact_match = any(rr.rtype == qtype for rr in response.rr)
                    has_cname = any(rr.rtype == getattr(QTYPE, 'CNAME') for rr in response.rr)
                    
                    if has_exact_match:
                        print(f"    [*] Final exact answer found at {current_ip}!")
                        return raw_response, current_ip
                    elif has_cname:
                        print(f"    [*] CNAME redirect detected at {current_ip}. Returning to client.")
                        return raw_response, current_ip
                    else:
                        print(f"    [*] Answer section present, but QTYPE mismatch.")
                        return raw_response, current_ip
                
                # [EDGE CASE 9] Authority-SOA NODATA Handling
                has_soa = any(auth_rr.rtype == getattr(QTYPE, 'SOA') for auth_rr in response.auth)
                if len(response.rr) == 0 and has_soa:
                    print(f"    [*] Authoritative NODATA (SOA) received. Record type does not exist.")
                    return raw_response, current_ip

                # [EDGE CASE 3] Delegation
                has_ns_delegation = any(auth_rr.rtype == getattr(QTYPE, 'NS') for auth_rr in response.auth)
                
                if has_ns_delegation:
                    if getattr(self, 'dnssec_enabled', False):
                        for auth_rr in response.auth:
                            if auth_rr.rtype == getattr(QTYPE, 'TXT'):
                                txt_data = b"".join(auth_rr.rdata.data).decode('utf-8')
                                if txt_data.startswith("DS|"):
                                    child_domain = str(auth_rr.rname)
                                    ds_hash = txt_data.split("|")[1]
                                    self.ds_cache[child_domain] = ds_hash
                                    print(f"    [+] DNSSEC: Captured DS hash for {child_domain}.")
                    
                    # [EDGE CASE 5] Extract ALL available IPs, but we'll try the first one for now
                    glue_ips = [str(ar.rdata) for ar in response.ar if ar.rtype == getattr(QTYPE, 'A')]
                    
                    if glue_ips:
                        next_ip = glue_ips[0]
                        print(f"    [*] Delegation received. Moving to: {next_ip}")
                        current_ip = next_ip
                        continue
                    else:
                        # [EDGE CASE 4] Delegation without glue
                        print("    [ERROR] Delegation missing glue record. Recursive glue lookup not implemented.")
                        break
                        
            # [EDGE CASE 3] Distinguish specific network failures
            except socket.timeout:
                print(f"    [ERROR] Timeout communicating with {current_ip}.")
                if jump == 0:
                    return None, None # Fallback to public if root is dead
                break
            except Exception as e:
                print(f"    [ERROR] Packet parsing/iteration failed at {current_ip}: {e}")
                break
                
        print("    [ERROR] Resolution failed (Max jumps, loops, or missing data).")
        # [EDGE CASE 10] Preserve original request ID and QR flag
        error_reply = request.reply()
        error_reply.header.rcode = getattr(RCODE, 'SERVFAIL')
        return error_reply.pack(), None

    def handle_query(self, data, addr, socket_ref):
        try:
            request = DNSRecord.parse(data)
            if request.header.qr == 1:
                print("[WARNING] Received a DNS response on the resolver. Ignoring.")
                return

            qname:str = str(request.q.qname)
            qtype:int = request.q.qtype
            
            # --- STEP 1: CACHE LOOKUP ---
            cached_response = self.cache.get(qname, qtype)
            if cached_response:
                print(f"[CACHE HIT] {qname}")
                reply = cached_response
                reply.header.id = request.header.id
                reply.header.aa = 0 # Cache is not authoritative
                socket_ref.sendto(reply.pack(), addr)
                return

            print(f"[CACHE MISS] Resolving {qname}...")
            # --- STEP 2: ROUTING & RESOLUTION ---
            try:
                # 1. Ask the Local DNS Tree first
                upstream_data, auth_ip = self._resolve_iterative(request, qname)
                
                # 2. If the Local Root returned None, fallback to the Public Internet
                if upstream_data is None:
                    target_ip = self.config.public_forwarder
                    target_port = self.config.public_port
                    print(f"[*] Forwarding to Public DNS ({target_ip})...")
                    upstream_data = self.forwarder.send_query(target_ip, target_port, request.pack())
                    auth_ip = None
                
                # --- STEP 3: Validate Keys ---
                real_reply = DNSRecord.parse(upstream_data)
                if getattr(self, 'dnssec_enabled', False) and auth_ip is not None:
                    target_ip_ans = None
                    rrsig_b64 = None
                    actual_record_name = qname # Default to the query name
                    
                    # 1. Separate the IP from the Signature
                    for rr in real_reply.rr:
                        if rr.rtype == getattr(QTYPE, 'A'):
                            target_ip_ans = str(rr.rdata)
                            # CRITICAL FIX: Grab the actual name of the A record (e.g., server1.test.homelab.)
                            actual_record_name = str(rr.rname) 
                        elif rr.rtype == getattr(QTYPE, 'TXT'):
                            txt_val = b"".join(rr.rdata.data).decode('utf-8')
                            if txt_val.startswith("RRSIG|A|"):
                                rrsig_b64 = txt_val.split("|")[2]
                    
                    # 2. If we found both, validate them!
                    if target_ip_ans and rrsig_b64:
                        # Use actual_record_name instead of qname so the CNAME math lines up!
                        is_secure = self.verify_dnssec_chain(actual_record_name, "A", target_ip_ans, rrsig_b64, auth_ip)
                        
                        if not is_secure:
                            print(f"[*] SECURE RESOLUTION ABORTED: {qname} failed validation.")
                            error_reply = request.reply()
                            error_reply.header.rcode = getattr(RCODE, 'SERVFAIL')
                            socket_ref.sendto(error_reply.pack(), addr)
                            return # Exit out completely
                    else:
                        print("[!] Warning: DNSSEC enabled but missing RRSIG. Assuming insecure.")

                # --- STEP 4: CACHE & REPLY ---
                # Fix Flags
                real_reply.header.aa = 0
                real_reply.header.ra = 1 
                if real_reply.header.rcode == 0 and getattr(self, 'dnssec_enabled', False) and auth_ip is not None:
                    # If is_secure is True from Step 3, we successfully did the math
                    real_reply.header.ad = 1 if is_secure else 0
                else:
                    real_reply.header.ad = 0

                # Only cache successful responses (NOERROR)
                if real_reply.header.rcode == 0:
                    self.cache.put(qname, qtype, real_reply, self.config.default_ttl)    
                
                # Send the final reply
                socket_ref.sendto(real_reply.pack(), addr)

            except socket.timeout:
                print(f"[TIMEOUT] Upstream server did not respond.")
            except Exception as e:
                print(f"[ERROR] Upstream query failed: {e}")

        except Exception as e:
            print(f"[ERROR] Handling packet: {e}")

    def _listening_loop(self):
        """
        The Dedicated Listener Thread.
        Only job: Wait for packets and spawn workers.
        """
        print(f"[*] Listener thread started on {self.config.bind_ip}:{self.config.bind_port}")
        
        while self.running:
            try:
                data, addr = self.server_socket.recvfrom(self.config.buffer_size)
                # Spawn a worker thread to handle the specific logic
                worker = threading.Thread(target=self.handle_query, args=(data, addr, self.server_socket))
                worker.daemon = True
                worker.start()
                
            except OSError:
                # Socket was closed during shutdown
                break
            except Exception as e:
                print(f"[ERROR] Listener loop: {e}")

    def verify_dnssec_chain(self, domain, qtype_str, rdata_str, signature_b64, auth_ip, auth_port=53):
        """
        Verifies the signature, utilizing a local cache for DNSKEYs to avoid network spam.
        """
        print(f"\n[*] DNSSEC: Initiating Chain of Trust Verification for {domain}")
        
        # 1. Figure out the apex domain (e.g., test.homelab.)
        apex_domain = ".".join(domain.strip(".").split(".")[-2:]) + "."
        
        zsk_pub = None
        ksk_pub = None
        
        # 2. THE CACHE CHECK
        if apex_domain in self.dnskey_cache:
            print(f"    [+] KEY CACHE HIT: Using stored ZSK for {apex_domain}")
            zsk_pub = self.dnskey_cache[apex_domain].get('ZSK')
            ksk_pub = self.dnskey_cache[apex_domain].get('KSK')

        else:
            # 3. THE CACHE MISS (Network Fetch)
            print(f"    -> KEY CACHE MISS: Fetching DNSKEYs for {apex_domain} from {auth_ip}...")
            key_request = DNSRecord.question(apex_domain, "TXT")
            
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(2.0)
                sock.sendto(key_request.pack(), (auth_ip, auth_port))
                response_data, _ = sock.recvfrom(4096)
                key_reply = DNSRecord.parse(response_data)
                
                ksk_pub = None
                zsk_pub_temp = None
                zsk_signature = None
                ksk_signature = None

                for rr in key_reply.rr:
                    if rr.rtype == getattr(QTYPE, 'TXT'):
                        # Safely join the raw byte chunks and decode them
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
                    # SAVE IT TO THE VAULT!
                    self.dnskey_cache[apex_domain] = {'KSK': ksk_pub, 'ZSK': zsk_pub}
                    print(f"    [+] Successfully fetched and cached DNSKEYs for {apex_domain}")
                else:
                    print("    [!] DNSSEC BOGUS: No ZSK found in Auth response!")
                    return False
                    
                    
            except socket.timeout:
                print("    [!] DNSSEC ERROR: Timeout fetching keys.")
                return False
            except Exception as e:
                print(f"    [!] DNSSEC ERROR: {e}")
                return False
            finally:
                sock.close()
            
        # Notice this is outdented! It runs whether we hit the cache OR fetched from the network!
        if apex_domain != ".":
            if apex_domain in self.ds_cache:
                parent_ds_hash = self.ds_cache[apex_domain]
                print(f"    -> Verifying KSK against Parent DS hash...")
                
                is_ds_valid = DNSSECValidator.verify_ds_record(ksk_pub, parent_ds_hash)
                if not is_ds_valid:
                    print("    [!] DNSSEC BOGUS: KSK does not match Parent DS!")
                    return False
                print("    [+] DNSSEC SECURE: Parent DS vouches for this KSK!")
            else:
                print(f"    [!] DNSSEC BOGUS: No DS record found for {apex_domain}. Chain broken!")
                return False

        # --- Phase 7: Do the actual math on the IP address! ---
        data_to_verify = f"{domain}|{qtype_str}|{rdata_str}"
        print(f"    -> Running ECDSA Math on: {data_to_verify}")
        
        is_valid = DNSSECValidator.verify_signature(zsk_pub, data_to_verify, signature_b64)
        
        if is_valid:
            print("    [+] DNSSEC SECURE: Cryptographic math checks out!")
            return True
        else:
            print("    [!] DNSSEC BOGUS: Cryptographic math FAILED!")
            return False

    def start(self):
        """
        Main entry point. Sets up the socket and starts the listener thread.
        """
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, 'SO_REUSEPORT'):
                self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            self.server_socket.bind((self.config.bind_ip, self.config.bind_port))
            self.running = True


            # Start the Listener in a SEPARATE thread
            listener_thread = threading.Thread(target=self._listening_loop)
            listener_thread.daemon = True # Dies when main thread dies
            listener_thread.start()

        except OSError as e:
            print(f"[FATAL] Could not bind port: {e}")

    def stop(self):
        """Clean shutdown to ensure cache saves."""
        self.running = False
        
        # Save Cache
        if hasattr(self, 'cache'):
            self.cache.stop()
        
        # Close socket to unblock the listener thread
        if self.server_socket:
            self.server_socket.close()
