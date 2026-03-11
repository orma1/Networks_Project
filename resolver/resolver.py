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
        self.cache_lock = threading.Lock()
        self.in_flight_queries = set()
        self.lock = threading.Lock()

    # --- NEW HELPER: Detect Client's DO Bit ---
    def wants_dnssec(self, request):
        """Checks the EDNS0 OPT record to see if the client requested DNSSEC (DO bit)."""
        for rr in request.ar:
            if rr.rtype == getattr(QTYPE, 'OPT'):
                # The DO bit is the highest bit (0x8000) of the 32-bit TTL field
                if (rr.ttl & 0x8000) != 0:
                    return True
        return False

    # --- NEW HELPER: Strip bloated records ---
    def strip_dnssec_records(self, reply):
        """Strips custom DNSSEC TXT records from the reply payload."""
        def is_dnssec_txt(rr):
            if rr.rtype == getattr(QTYPE, 'TXT'):
                try:
                    txt_val = b"".join(rr.rdata.data).decode('utf-8').strip('"')
                    if txt_val.startswith(("RRSIG|", "DNSKEY|", "DS|")):
                        return True
                except Exception:
                    pass
            return False

        reply.rr = [rr for rr in reply.rr if not is_dnssec_txt(rr)]
        reply.auth = [rr for rr in reply.auth if not is_dnssec_txt(rr)]
        reply.ar = [rr for rr in reply.ar if not is_dnssec_txt(rr)]

    def _resolve_iterative(self, request, target_domain):
        current_ip = self.config.root_server_ip
        current_port = self.config.root_server_port
        visited_ips = set() 
        
        qtype = request.q.qtype

        for jump in range(10): 
            if current_ip in visited_ips:
                print(f"    [ERROR] Routing loop detected at {current_ip}. Aborting.")
                break
            visited_ips.add(current_ip)

            print(f"    [Iterative Jump {jump+1}] Asking {current_ip} for {target_domain}")
            
            try:
                raw_response = self.forwarder.send_query(current_ip, current_port, request.pack())
                response = DNSRecord.parse(raw_response)
                
                if response.header.rcode != getattr(RCODE, 'NOERROR'):
                    rcode_val = response.header.rcode
                    if rcode_val == getattr(RCODE, 'NXDOMAIN'):
                        print(f"    [*] NXDOMAIN from {current_ip}. Domain does not exist.")
                        if jump == 0:
                            return None,None
                        return raw_response, current_ip
                    elif rcode_val in (getattr(RCODE, 'SERVFAIL'), getattr(RCODE, 'REFUSED')):
                        print(f"    [*] Server {current_ip} returned {RCODE.get(rcode_val)}.")
                        if jump == 0:
                            print("    [*] Local Root failed. Triggering public fallback.")
                            return None, None
                        break 
                    else:
                        print(f"    [*] Server {current_ip} returned unexpected error: {rcode_val}.")
                        return raw_response, current_ip
                
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
                
                has_soa = any(auth_rr.rtype == getattr(QTYPE, 'SOA') for auth_rr in response.auth)
                if len(response.rr) == 0 and has_soa:
                    print(f"    [*] Authoritative NODATA (SOA) received. Record type does not exist.")
                    return raw_response, current_ip

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
                    
                    glue_ips = [str(ar.rdata) for ar in response.ar if ar.rtype == getattr(QTYPE, 'A')]
                    
                    if glue_ips:
                        next_ip = glue_ips[0]
                        print(f"    [*] Delegation received. Moving to: {next_ip}")
                        current_ip = next_ip
                        continue
                    else:
                        print("    [ERROR] Delegation missing glue record. Recursive glue lookup not implemented.")
                        break
                        
            except socket.timeout:
                print(f"    [ERROR] Timeout communicating with {current_ip}.")
                if jump == 0:
                    return None, None 
                break
            except Exception as e:
                print(f"    [ERROR] Packet parsing/iteration failed at {current_ip}: {e}")
                break
                
        print("    [ERROR] Resolution failed (Max jumps, loops, or missing data).")
        error_reply = request.reply()
        error_reply.header.rcode = getattr(RCODE, 'SERVFAIL')
        return error_reply.pack(), None

    def _handle_cache_lookup(self, request, qname, qtype, wants_dnssec, addr, socket_ref):
            # 2. PROTECT CACHE READS
            with self.cache_lock:
                cached_response = self.cache.get(qname, qtype)
                if cached_response:
                    reply = DNSRecord.parse(cached_response.pack())
                    reply.header.id = request.header.id
                    reply.header.aa = 0 
                    if not wants_dnssec:
                        self.strip_dnssec_records(reply)
                    socket_ref.sendto(reply.pack(), addr)
                    return True
            return False

    def _fetch_upstream(self, request, qname):
        upstream_data, auth_ip = self._resolve_iterative(request, qname)
        if upstream_data is None:
            target_ip = self.config.public_forwarder
            target_port = getattr(self.config, 'public_port', 53)
            print(f"[*] Forwarding to Public DNS ({target_ip})...")
            upstream_data = self.forwarder.send_query(target_ip, target_port, request.pack())
            auth_ip = None 
        return DNSRecord.parse(upstream_data), auth_ip

    def _validate_dnssec(self, request, real_reply, qname, auth_ip, addr, socket_ref):
        client_requested_dnssec = self.wants_dnssec(request)
        checking_disabled = (request.header.cd == 1)
        should_validate = (getattr(self, 'dnssec_enabled', False) or client_requested_dnssec) and not checking_disabled

        if not should_validate:
            if checking_disabled:
                print(f"[*] Client sent CD=1 (Checking Disabled). Bypassing DNSSEC validation for {qname}.")
            return False

        if auth_ip is None:
            return False 

        target_ip_ans, rrsig_b64, actual_record_name = None, None, qname 

        for rr in real_reply.rr:
            if rr.rtype == getattr(QTYPE, 'A'):
                target_ip_ans = str(rr.rdata)
                actual_record_name = str(rr.rname) 
            elif rr.rtype == getattr(QTYPE, 'TXT'):
                txt_val = b"".join(rr.rdata.data).decode('utf-8')
                if txt_val.startswith("RRSIG|A|"):
                    rrsig_b64 = txt_val.split("|")[2]

        if target_ip_ans and rrsig_b64:
            is_secure = DNSSECValidator.verify_dnssec_chain(
                domain=actual_record_name, qtype_str="A", rdata_str=target_ip_ans, 
                signature_b64=rrsig_b64, auth_ip=auth_ip, bind_ip=self.config.bind_ip,
                dnskey_cache=self.dnskey_cache, ds_cache=self.ds_cache
            )
            
        with self.cache_lock:
            if target_ip_ans and rrsig_b64:
                is_secure = DNSSECValidator.verify_dnssec_chain(
                    domain=actual_record_name, qtype_str="A", rdata_str=target_ip_ans, 
                    signature_b64=rrsig_b64, auth_ip=auth_ip, bind_ip=self.config.bind_ip,
                    dnskey_cache=self.dnskey_cache, ds_cache=self.ds_cache
                )
        
                if not is_secure:
                    print(f"[*] SECURE RESOLUTION ABORTED: {qname} failed validation.")
                    error_reply = request.reply()
                    error_reply.header.rcode = getattr(RCODE, 'SERVFAIL')
                    socket_ref.sendto(error_reply.pack(), addr)
                    return None 
                return True
            else:
                print("[!] Warning: DNSSEC validation required but missing RRSIG. Assuming insecure.")
                return False

    def _finalize_and_reply(self, real_reply, qname, qtype, is_secure, wants_dnssec, addr, socket_ref):
        """Sets final flags, updates the cache, and sends the packet back to the client."""
        real_reply.header.aa = 0
        real_reply.header.ra = 1 
        
        if real_reply.header.rcode == 0 and is_secure:
            real_reply.header.ad = 1
        else:
            real_reply.header.ad = 0

        with self.cache_lock:
            if real_reply.header.rcode == 0:
                cache_clone = DNSRecord.parse(real_reply.pack())
                self.cache.put(qname, qtype, cache_clone, getattr(self.config, 'default_ttl', 60))

        # 1. Store the fully bloated, signed record in cache by creating a clone
        if real_reply.header.rcode == 0:
            cache_clone = DNSRecord.parse(real_reply.pack())
            self.cache.put(qname, qtype, cache_clone, getattr(self.config, 'default_ttl', 60))    
        
        # 2. Filter the record right before returning it to the user
        if not wants_dnssec:
            self.strip_dnssec_records(real_reply)
        socket_ref.sendto(real_reply.pack(), addr)

        

        

    def handle_query(self, data, addr, socket_ref):
        print(f"[DEBUG] Thread {threading.get_ident()} started processing {addr}")

        
        try:
            
            request = DNSRecord.parse(data)
            if request.header.qr == 1:
                print("[WARNING] Received a DNS response on the resolver. Ignoring.")
                return

            qname = str(request.q.qname)
            qtype = request.q.qtype

            with self.lock:
                if qname in self.in_flight_queries:
                    print(f"[DEBUG] Dropping duplicate request for {qname}")
                    return # Ignore this packet
                self.in_flight_queries.add(qname)
            
            # Identify if client explicitly asked for DNSSEC
            client_wants_dnssec = self.wants_dnssec(request)

            # --- STEP 1: CACHE LOOKUP ---
            if self._handle_cache_lookup(request, qname, qtype, client_wants_dnssec, addr, socket_ref):
                return 

            print(f"[CACHE MISS] Resolving {qname}...")

            # --- STEP 2: ROUTING & RESOLUTION ---
            try:
                real_reply, auth_ip = self._fetch_upstream(request, qname)
                
                # --- STEP 3: DNSSEC VALIDATION ---
                is_secure = self._validate_dnssec(request, real_reply, qname, auth_ip, addr, socket_ref)
                
                if is_secure is None:
                    return # Packet dropped due to BOGUS signature.

                # --- STEP 4: CACHE & REPLY ---
                self._finalize_and_reply(real_reply, qname, qtype, is_secure, client_wants_dnssec, addr, socket_ref)

            except socket.timeout:
                print(f"[TIMEOUT] Upstream server did not respond.")
            except Exception as e:
                print(f"[ERROR] Upstream query failed: {e}")

        except Exception as e:
            print(f"[ERROR] Handling packet: {e}")

    def _listening_loop(self):
        print(f"[*] Listener thread started on {self.config.bind_ip}:{self.config.bind_port}")
        while self.running:
            try:
                data, addr = self.server_socket.recvfrom(self.config.buffer_size)
                worker = threading.Thread(target=self.handle_query, args=(data, addr, self.server_socket))
                worker.daemon = True
                worker.start()
            except OSError:
                break
            except Exception as e:
                print(f"[ERROR] Listener loop: {e}")

    def start(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, 'SO_REUSEPORT'):
                self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            self.server_socket.bind((self.config.bind_ip, self.config.bind_port))
            self.running = True

            listener_thread = threading.Thread(target=self._listening_loop)
            listener_thread.daemon = True
            listener_thread.start()

        except OSError as e:
            print(f"[FATAL] Could not bind port: {e}")

    def stop(self):
        self.running = False
        if hasattr(self, 'cache'):
            self.cache.stop()
        if self.server_socket:
            self.server_socket.close()