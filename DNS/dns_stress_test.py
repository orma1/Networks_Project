#!/usr/bin/env python3
"""
DNS Resolver Stress Test
Tests the resolver under various load conditions to find breaking points
"""

import socket
import time
import threading
from dnslib import DNSRecord
from datetime import datetime
import sys

RESOLVER_IP = "127.0.0.2"
RESOLVER_PORT = 53

# Test counters
successful_queries = 0
failed_queries = 0
timeout_queries = 0
lock = threading.Lock()

def query_dns(domain, qtype="A", timeout=5):
    """Send a single DNS query and return result"""
    global successful_queries, failed_queries, timeout_queries
    
    try:
        # Create DNS query
        query = DNSRecord.question(domain, qtype)
        query_bytes = query.pack()
        
        # Send query
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(query_bytes, (RESOLVER_IP, RESOLVER_PORT))
        
        # Receive response
        data, _ = sock.recvfrom(4096)
        response = DNSRecord.parse(data)
        sock.close()
        
        with lock:
            successful_queries += 1
        
        return True, response
        
    except socket.timeout:
        with lock:
            timeout_queries += 1
        return False, "TIMEOUT"
    except Exception as e:
        with lock:
            failed_queries += 1
        return False, str(e)

def print_stats():
    """Print current statistics"""
    total = successful_queries + failed_queries + timeout_queries
    if total == 0:
        return
    
    success_rate = (successful_queries / total) * 100
    timeout_rate = (timeout_queries / total) * 100
    fail_rate = (failed_queries / total) * 100
    
    print(f"\n{'='*60}")
    print(f"Total Queries: {total}")
    print(f"  ✓ Successful: {successful_queries} ({success_rate:.1f}%)")
    print(f"  ⏱ Timeouts:   {timeout_queries} ({timeout_rate:.1f}%)")
    print(f"  ✗ Failed:     {failed_queries} ({fail_rate:.1f}%)")
    print(f"{'='*60}\n")

# ══════════════════════════════════════════════════════════════════════════════
# TEST 1: Basic Connectivity
# ══════════════════════════════════════════════════════════════════════════════
def test_basic_connectivity():
    """Test if resolver is responding at all"""
    print("\n[TEST 1] Basic Connectivity Test")
    print("-" * 60)
    
    domains = [
        "media.homelab",
        "www.media.homelab",
        "originserver.homelab",
        "test.homelab"
    ]
    
    for domain in domains:
        success, result = query_dns(domain, timeout=2)
        status = "✓" if success else "✗"
        print(f"{status} {domain}: {result if not success else 'OK'}")
    
    print_stats()
    return successful_queries > 0

# ══════════════════════════════════════════════════════════════════════════════
# TEST 2: Sequential Load
# ══════════════════════════════════════════════════════════════════════════════
def test_sequential_load():
    """Test resolver with sequential queries"""
    print("\n[TEST 2] Sequential Load Test (100 queries)")
    print("-" * 60)
    
    start_time = time.time()
    
    for i in range(100):
        query_dns("media.homelab", timeout=2)
        if (i + 1) % 20 == 0:
            print(f"  Progress: {i + 1}/100 queries sent...")
    
    duration = time.time() - start_time
    qps = 100 / duration
    
    print(f"\n  Completed in {duration:.2f}s ({qps:.1f} queries/sec)")
    print_stats()

# ══════════════════════════════════════════════════════════════════════════════
# TEST 3: Concurrent Load
# ══════════════════════════════════════════════════════════════════════════════
def concurrent_worker(num_queries, domain, thread_id):
    """Worker thread for concurrent testing"""
    for i in range(num_queries):
        query_dns(domain, timeout=2)

def test_concurrent_load():
    """Test resolver with concurrent queries"""
    print("\n[TEST 3] Concurrent Load Test (10 threads × 10 queries)")
    print("-" * 60)
    
    threads = []
    start_time = time.time()
    
    for i in range(10):
        t = threading.Thread(target=concurrent_worker, args=(10, "media.homelab", i))
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join()
    
    duration = time.time() - start_time
    qps = 100 / duration
    
    print(f"\n  Completed in {duration:.2f}s ({qps:.1f} queries/sec)")
    print_stats()

# ══════════════════════════════════════════════════════════════════════════════
# TEST 4: Different Query Types
# ══════════════════════════════════════════════════════════════════════════════
def test_query_types():
    """Test different query types"""
    print("\n[TEST 4] Query Type Test")
    print("-" * 60)
    
    test_cases = [
        ("media.homelab", "A", "A Record"),
        ("www.media.homelab", "A", "A via CNAME"),
        ("media.homelab", "AAAA", "AAAA Record"),
        ("media.homelab", "SOA", "SOA Record"),
        ("media.homelab", "NS", "NS Record"),
        ("5.0.0.127.in-addr.arpa", "PTR", "Reverse DNS (PTR)"),
    ]
    
    for domain, qtype, description in test_cases:
        success, result = query_dns(domain, qtype, timeout=2)
        status = "✓" if success else "✗"
        print(f"{status} {description}: {domain}")
    
    print_stats()

# ══════════════════════════════════════════════════════════════════════════════
# TEST 5: Rapid Fire (Find Breaking Point)
# ══════════════════════════════════════════════════════════════════════════════
def test_rapid_fire():
    """Rapid fire queries to find breaking point"""
    print("\n[TEST 5] Rapid Fire Test (finding breaking point)")
    print("-" * 60)
    
    batch_size = 50
    max_batches = 10
    
    for batch in range(max_batches):
        print(f"\n  Batch {batch + 1}/{max_batches} ({batch_size} queries)...")
        
        threads = []
        for i in range(batch_size):
            t = threading.Thread(target=query_dns, args=("media.homelab", "A", 2))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # Check if resolver is still responsive
        success, _ = query_dns("media.homelab", timeout=1)
        if not success:
            print(f"\n  ⚠️  Resolver stopped responding after batch {batch + 1}")
            break
        
        time.sleep(0.5)  # Small delay between batches
    
    print_stats()

# ══════════════════════════════════════════════════════════════════════════════
# TEST 6: NXDOMAIN Flood
# ══════════════════════════════════════════════════════════════════════════════
def test_nxdomain_flood():
    """Test with non-existent domains"""
    print("\n[TEST 6] NXDOMAIN Flood Test")
    print("-" * 60)
    
    for i in range(50):
        fake_domain = f"nonexistent{i}.homelab"
        query_dns(fake_domain, timeout=2)
        if (i + 1) % 10 == 0:
            print(f"  Progress: {i + 1}/50 NXDOMAIN queries...")
    
    print_stats()

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    global successful_queries, failed_queries, timeout_queries
    
    print("\n" + "="*60)
    print(" DNS RESOLVER STRESS TEST")
    print(f" Target: {RESOLVER_IP}:{RESOLVER_PORT}")
    print(f" Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    tests = [
        ("Basic Connectivity", test_basic_connectivity),
        ("Sequential Load", test_sequential_load),
        ("Concurrent Load", test_concurrent_load),
        ("Query Types", test_query_types),
        ("Rapid Fire", test_rapid_fire),
        ("NXDOMAIN Flood", test_nxdomain_flood),
    ]
    
    for test_name, test_func in tests:
        # Reset counters for each test
        successful_queries = 0
        failed_queries = 0
        timeout_queries = 0
        
        try:
            result = test_func()
            
            # If basic connectivity fails, stop
            if test_name == "Basic Connectivity" and not result:
                print("\n⚠️  Resolver not responding. Stopping tests.")
                break
                
        except KeyboardInterrupt:
            print("\n\n⚠️  Test interrupted by user")
            break
        except Exception as e:
            print(f"\n⚠️  Test failed with error: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "="*60)
    print(" STRESS TEST COMPLETE")
    print("="*60)

if __name__ == "__main__":
    main()