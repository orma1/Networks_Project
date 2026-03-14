#!/usr/bin/env python3
"""
Browser DNS Pattern Simulator
Mimics how a browser actually queries DNS when loading a page
"""

import socket
import time
import threading
from dnslib import DNSRecord
from datetime import datetime

RESOLVER_IP = "127.0.0.2"
RESOLVER_PORT = 53

results = []
lock = threading.Lock()

def query_dns(domain, qtype="A", timeout=5):
    """Send a DNS query and measure response time"""
    start_time = time.time()
    
    try:
        query = DNSRecord.question(domain, qtype)
        query_bytes = query.pack()
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(query_bytes, (RESOLVER_IP, RESOLVER_PORT))
        
        data, _ = sock.recvfrom(4096)
        response = DNSRecord.parse(data)
        sock.close()
        
        duration = time.time() - start_time
        
        with lock:
            results.append({
                'domain': domain,
                'qtype': qtype,
                'status': 'SUCCESS',
                'duration': duration
            })
        
        return True, duration
        
    except socket.timeout:
        duration = time.time() - start_time
        with lock:
            results.append({
                'domain': domain,
                'qtype': qtype,
                'status': 'TIMEOUT',
                'duration': duration
            })
        return False, duration
        
    except Exception as e:
        duration = time.time() - start_time
        with lock:
            results.append({
                'domain': domain,
                'qtype': qtype,
                'status': 'ERROR',
                'duration': duration,
                'error': str(e)
            })
        return False, duration

def simulate_page_load(page_domain):
    """Simulate all DNS queries a browser makes when loading a page"""
    print(f"\n[BROWSER SIM] Loading page: http://{page_domain}")
    print("-" * 60)
    
    # This mimics real browser behavior
    queries = [
        # Initial page load
        (page_domain, "A"),
        (page_domain, "AAAA"),
        
        # www variant
        (f"www.{page_domain}", "A"),
        (f"www.{page_domain}", "AAAA"),
        
        # mDNS probing (browsers do this)
        (f"{page_domain}.local", "A"),
        (f"{page_domain}.local", "AAAA"),
        
        # Some browsers query for these
        (page_domain, "MX"),
        (page_domain, "TXT"),
        
        # Embedded resources (simulated)
        (f"cdn.{page_domain}", "A"),
        (f"static.{page_domain}", "A"),
        (f"api.{page_domain}", "A"),
    ]
    
    threads = []
    start_time = time.time()
    
    # Launch all queries concurrently (like a browser does)
    for domain, qtype in queries:
        t = threading.Thread(target=query_dns, args=(domain, qtype, 3))
        threads.append(t)
        t.start()
    
    # Wait for all queries to complete
    for t in threads:
        t.join()
    
    total_time = time.time() - start_time
    
    # Analyze results
    successful = sum(1 for r in results if r['status'] == 'SUCCESS')
    timeouts = sum(1 for r in results if r['status'] == 'TIMEOUT')
    errors = sum(1 for r in results if r['status'] == 'ERROR')
    
    print(f"\n  Page load completed in {total_time:.2f}s")
    print(f"  Queries: {len(queries)} total")
    print(f"    ✓ Success: {successful}")
    print(f"    ⏱ Timeout: {timeouts}")
    print(f"    ✗ Error:   {errors}")
    
    # Show slow queries
    slow_queries = [r for r in results if r['duration'] > 1.0]
    if slow_queries:
        print(f"\n  ⚠️  Slow queries (>1s):")
        for r in slow_queries:
            print(f"    - {r['domain']} ({r['qtype']}): {r['duration']:.2f}s - {r['status']}")
    
    # Show timeouts
    timeout_queries = [r for r in results if r['status'] == 'TIMEOUT']
    if timeout_queries:
        print(f"\n  ⚠️  Timed out queries:")
        for r in timeout_queries:
            print(f"    - {r['domain']} ({r['qtype']})")

def simulate_multiple_tabs():
    """Simulate opening multiple tabs (browsers do concurrent DNS)"""
    print("\n" + "="*60)
    print("[BROWSER SIM] Opening 3 tabs simultaneously...")
    print("="*60)
    
    domains = [
        "media.homelab",
        "originserver.homelab",
        "test.homelab"
    ]
    
    threads = []
    for domain in domains:
        t = threading.Thread(target=simulate_page_load, args=(domain,))
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join()

def continuous_browsing_test():
    """Simulate continuous browsing (page after page)"""
    print("\n" + "="*60)
    print("[BROWSER SIM] Continuous browsing test (5 page loads)")
    print("="*60)
    
    for i in range(5):
        global results
        results = []  # Clear results for each iteration
        
        print(f"\n--- Page Load {i+1}/5 ---")
        simulate_page_load("media.homelab")
        time.sleep(2)  # Small delay between page loads

def main():
    print("\n" + "="*60)
    print(" BROWSER DNS PATTERN SIMULATOR")
    print(f" Target Resolver: {RESOLVER_IP}:{RESOLVER_PORT}")
    print(f" Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    tests = [
        ("Single Page Load", lambda: simulate_page_load("media.homelab")),
        ("Multiple Tabs", simulate_multiple_tabs),
        ("Continuous Browsing", continuous_browsing_test),
    ]
    
    for test_name, test_func in tests:
        global results
        results = []
        
        print(f"\n{'='*60}")
        print(f"TEST: {test_name}")
        print(f"{'='*60}")
        
        try:
            test_func()
        except KeyboardInterrupt:
            print("\n\n⚠️  Test interrupted by user")
            break
        except Exception as e:
            print(f"\n⚠️  Test failed: {e}")
            import traceback
            traceback.print_exc()
        
        time.sleep(1)
    
    print("\n" + "="*60)
    print(" SIMULATION COMPLETE")
    print("="*60)

if __name__ == "__main__":
    main()
