
import socket
import threading
import time
import sys

# --- Local Imports ---
from config_loader import ConfigLoader
from dns_cache import DNSCache
from dnslib import DNSRecord, QTYPE, RCODE, RR, A
from forwarder import Forwarder

class Resolver:
    def __init__(self, config):
        """ Receives the config object from the Manager """
        self.config = config
        self.cache = DNSCache(
            filename=self.config.cache_file_path,
            capacity=self.config.cache_capacity,
            save_interval=self.config.save_interval
        )
        self.forwarder = Forwarder(timeout=self.config.timeout)
        self.running = False
        self.server_socket = None

    def _resolve_iterative(self, request, target_domain):
        current_ip = self.config.root_server_ip
        current_port = self.config.root_server_port
        
        for jump in range(10): # Cap at 10 jumps
            print(f"    [Iterative Jump {jump+1}] Asking {current_ip} for {target_domain}")
            
            try:
                raw_response = self.forwarder.send_query(current_ip, current_port, request.pack())
                response = DNSRecord.parse(raw_response)
                
                # CASE 1: We got an Answer!
                if len(response.rr) > 0:
                    print(f"    [*] Final answer found at {current_ip}!")
                    return raw_response
                
                # CASE 2: We got an Error (NXDOMAIN or NODATA)
                if response.header.rcode != 0:
                    if jump == 0:
                        # The Local Root rejected the TLD (e.g., asked for google.com).
                        # Return None to trigger the public forwarder fallback in handle_query.
                        print(f"    [*] Local Root does not know this TLD. Triggering public fallback.")
                        return None
                    else:
                        # A lower local server (TLD/Auth) rejected the subdomain.
                        # This is a true local error. Return it to the client.
                        print(f"    [*] Local server {current_ip} returned error code {response.header.rcode}.")
                        return raw_response
                
                # CASE 3: We got a Delegation
                if len(response.auth) > 0:
                    next_ip = None
                    for ar in response.ar: # Look in the Additional section for the IP
                        if ar.rtype == getattr(QTYPE, 'A'):
                            next_ip = str(ar.rdata)
                            break
                    
                    if next_ip:
                        print(f"    [*] Delegation received. Moving to: {next_ip}")
                        current_ip = next_ip
                        continue
                    else:
                        print("    [ERROR] Delegation missing glue record (IP).")
                        break
                        
            except Exception as e:
                print(f"    [ERROR] Iteration failed at {current_ip}: {e}")
                break
                
        print("    [ERROR] Resolution failed (Max jumps or missing data).")
        error_reply = request.reply()
        error_reply.header.rcode = getattr(RCODE, 'SERVFAIL')
        return error_reply.pack()

    def handle_query(self, data, addr, socket_ref):
        try:
            request = DNSRecord.parse(data)
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
                upstream_data = self._resolve_iterative(request, qname)
                
                # 2. If the Local Root returned None, fallback to the Public Internet
                if upstream_data is None:
                    target_ip = self.config.public_forwarder
                    target_port = self.config.public_port
                    print(f"[*] Forwarding to Public DNS ({target_ip})...")
                    upstream_data = self.forwarder.send_query(target_ip, target_port, request.pack())

                # --- STEP 3: CACHE & REPLY ---
                real_reply = DNSRecord.parse(upstream_data)
                
                # Fix Flags
                real_reply.header.aa = 0 
                real_reply.header.ra = 1 
                
                # Only cache successful responses (NOERROR)
                if real_reply.header.rcode == 0:
                    self.cache.put(qname, qtype, real_reply, self.config.default_ttl)    
                
                # Send the final reply
                socket_ref.sendto(upstream_data, addr)

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

    def start(self):
        """
        Main entry point. Sets up the socket and starts the listener thread.
        """
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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


# --- GLOBAL INSTANCE FOR SIGNAL HANDLING ---
# resolver = None

# def signal_handler(sig, frame):
#     """ Catches Ctrl+C (SIGINT) and calls stop() """
#     if resolver:
#         resolver.stop()

# if __name__ == "__main__":
#     try:
#         resolver = Resolver()
#         resolver.start()
#     except KeyboardInterrupt:
#         # Allow Ctrl+C to stop the server and save the cache
#         print("\n[*] Stopping server...")
#         resolver.stop()