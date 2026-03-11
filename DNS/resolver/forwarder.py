import socket

class Forwarder:
    """
    Handles the raw network transport for DNS queries.
    Responsible for:
    1. Creating UDP sockets to forward queries to the configured Public DNS server
    2. Sending binary packets
    3. Waiting for responses (with timeout) from Public DNS server
    4. Closing the connection when a response is received or timeout occurs.
    """
    def __init__(self, timeout: float = 2.0):
        # Clean and simple: only the timeout matters now.
        self.timeout = timeout

    def send_query(self, target_ip: str, target_port: int, raw_data: bytes) -> bytes:
        """
        Sends a raw DNS query to a specific server and returns the raw response.
        Raises socket.timeout or socket.error if something goes wrong.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        
        try:
            sock.sendto(raw_data, (target_ip, target_port))
            response_data, _ = sock.recvfrom(4096)
            return response_data
        finally:
            sock.close()