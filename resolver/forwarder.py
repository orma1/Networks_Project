import socket
class Forwarder:
    """
    Handles the raw network transport for DNS queries.
    Responsible for:
    1. Creating UDP sockets to forward queries to the configured Public DNS server
    2. Sending binary packets
    3. Waiting for responses (with timeout) from Public DNS server
    4. closing the connection when recived a response or timeout occurs.
    """
    def __init__(self, local_ip: str = '127.0.0.2', timeout: float = 2.0):
        self.local_ip = local_ip
        self.timeout = timeout

    def send_query(self, target_ip: str, target_port: int, raw_data: bytes) -> bytes:
        """
        Sends a raw DNS query to a specific server and returns the raw response.
        Raises socket.timeout or socket.error if something goes wrong.
        """
        # Create a new socket for *this specific transaction*
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.local_ip, 0))
        sock.settimeout(self.timeout)
        
        try:
            # Send
            sock.sendto(raw_data, (target_ip, target_port))
            
            # Receive
            # 4096 is the safe buffer size we agreed on
            response_data, _ = sock.recvfrom(4096)
            
            return response_data
            
        finally:
            # Ensure the socket is ALWAYS closed, even if we crash/timeout
            sock.close()