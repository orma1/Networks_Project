import socket
import pytest
from dnslib import DNSRecord, QTYPE, RCODE

RESOLVER_IP = '127.0.0.2'
RESOLVER_PORT = 53
TIMEOUT = 3.0

# --- Helper Function ---
def query_resolver(domain, record_type):
    """Crafts the DNS packet, sends it to our resolver, and returns the parsed response."""
    request = DNSRecord.question(domain, record_type)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(TIMEOUT)
    try:
        sock.sendto(request.pack(), (RESOLVER_IP, RESOLVER_PORT))
        response_data, _ = sock.recvfrom(4096)
        return DNSRecord.parse(response_data)
    finally:
        sock.close()


# --- The Test Matrix ---
@pytest.mark.parametrize("test_name, domain, qtype_str, expected_rcode, expected_ip", [
    (
        "Standard Local Secure Query", 
        "server1.test.homelab.", 
        "A", 
        getattr(RCODE, 'NOERROR'), 
        "192.168.1.100"
    ),
    (
        "Subdomain 2 (Should Trigger Key Cache Hit)", 
        "server2.test.homelab.", 
        "A", 
        getattr(RCODE, 'NOERROR'), 
        "192.168.1.101"
    ),
    (
        "Public Internet Fallback (DNSSEC Bypassed)", 
        "google.com.", 
        "A", 
        getattr(RCODE, 'NOERROR'), 
        None # We don't assert a specific IP for Google, just that it resolves
    ),
    (
        "Non-Existent Local Domain", 
        "doesnotexist.test.homelab.", 
        "A", 
        getattr(RCODE, 'NXDOMAIN'), 
        None
    ),
])
def test_resolver_matrix(test_name, domain, qtype_str, expected_rcode, expected_ip):
    """
    Executes the matrix of queries against the running resolver.
    """
    # 1. Send the Query
    response = query_resolver(domain, qtype_str)
    
    # 2. Assert RCODE matches our expectation
    assert response.header.rcode == expected_rcode, f"[{test_name}] Failed: Expected RCODE {expected_rcode}, got {response.header.rcode}"
    
    # 3. Assert IP matches (if we provided one)
    if expected_ip:
        found_ip = False
        for rr in response.rr:
            if rr.rtype == getattr(QTYPE, qtype_str) and str(rr.rdata) == expected_ip:
                found_ip = True
                break
        assert found_ip is True, f"[{test_name}] Failed: Expected IP {expected_ip} not found in response."

# --- Edge Case Test ---
@pytest.mark.xfail(reason="CNAME chasing not yet implemented in Resolver")
def test_cname_chasing():
    """Specific test to ensure the resolver correctly follows CNAMEs."""
    response = query_resolver("blog.test.homelab.", "A")
    assert response.header.rcode == getattr(RCODE, 'NOERROR')
    
    # We should get BOTH the CNAME record and the chased A record
    has_cname = any(rr.rtype == getattr(QTYPE, 'CNAME') for rr in response.rr)
    has_a = any(rr.rtype == getattr(QTYPE, 'A') for rr in response.rr)
    
    assert has_cname and has_a, "Resolver failed to chase CNAME to its target A record."