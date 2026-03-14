"""
STREAMING PROTOCOL INTERFACES
═════════════════════════════════════════════════════════════

Defines abstract interfaces (protocols) for streaming components.
These establish contracts between server and proxy without coupling them.

This follows the Dependency Inversion Principle:
- High-level modules (StreamOrchestrator, RequestDispatcher) depend on abstractions
- Low-level modules (TCPClient, RUDPSession) implement these abstractions
- Both can evolve independently as long as they honor the contract

Python's typing.Protocol provides structural subtyping (duck typing with type hints).
No need to explicitly inherit - just implement the required methods.
"""

from typing import Protocol, Generator, Optional, Tuple, Dict, Any
from dataclasses import dataclass
from enum import Enum


# ══════════════════════════════════════════════════════════════════════════════
# PROTOCOL ENUMS & DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

class TransportProtocol(Enum):
    """Available transport protocols."""
    TCP = "tcp"
    RUDP = "rudp"


class StreamState(Enum):
    """Current state of a streaming session."""
    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STREAMING = "streaming"
    PAUSED = "paused"
    ERROR = "error"
    CLOSED = "closed"


@dataclass
class StreamRequest:
    """Request to stream a file."""
    filename: str
    byte_start: int = 0
    byte_end: Optional[int] = None
    protocol: TransportProtocol = TransportProtocol.RUDP
    quality: str = "auto"  # For DASH: "auto", "480", "720", "1080"
    
    def __post_init__(self):
        """Validate request parameters."""
        if self.byte_start < 0:
            raise ValueError("byte_start must be non-negative")
        if self.byte_end is not None and self.byte_end < self.byte_start:
            raise ValueError("byte_end must be >= byte_start")


@dataclass
class StreamMetadata:
    """Metadata about a stream."""
    file_size: int
    remote_window: int
    content_type: str = "video/mp4"
    supports_range: bool = True
    
    def __post_init__(self):
        """Validate metadata."""
        if self.file_size < 0:
            raise ValueError("file_size must be non-negative")
        if self.remote_window < 0:
            raise ValueError("remote_window must be non-negative")


@dataclass
class StreamMetrics:
    """Real-time metrics for a streaming session."""
    bytes_transferred: int = 0
    packets_sent: int = 0
    packets_received: int = 0
    packets_lost: int = 0
    packets_retransmitted: int = 0
    packets_out_of_order: int = 0
    duplicate_packets: int = 0
    current_throughput_mbps: float = 0.0
    average_throughput_mbps: float = 0.0
    current_quality: str = "unknown"
    connection_state: StreamState = StreamState.IDLE
    error_message: str = ""
    
    def loss_rate(self) -> float:
        """Calculate packet loss rate."""
        total = self.packets_sent or self.packets_received
        if total == 0:
            return 0.0
        return self.packets_lost / total
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "bytes_transferred": self.bytes_transferred,
            "packets_sent": self.packets_sent,
            "packets_received": self.packets_received,
            "packets_lost": self.packets_lost,
            "packets_retransmitted": self.packets_retransmitted,
            "packets_out_of_order": self.packets_out_of_order,
            "duplicate_packets": self.duplicate_packets,
            "current_throughput_mbps": self.current_throughput_mbps,
            "average_throughput_mbps": self.average_throughput_mbps,
            "current_quality": self.current_quality,
            "connection_state": self.connection_state.value,
            "error_message": self.error_message,
            "loss_rate": self.loss_rate(),
        }


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT-SIDE INTERFACES (PROXY)
# ══════════════════════════════════════════════════════════════════════════════

class StreamingClient(Protocol):
    """
    Interface for streaming clients (proxy-side fetchers).
    
    Implementations: TCPClient, RUDPClient
    
    Responsibilities:
    - Connect to origin server
    - Request file streams
    - Receive and reassemble data
    - Report metrics
    
    Does NOT handle:
    - HTTP layer concerns
    - Quality selection (that's QualitySelector's job)
    - Serving to browser (that's HTTPHandler's job)
    """
    
    def connect(self, server_addr: Tuple[str, int], request: StreamRequest) -> StreamMetadata:
        """
        Establish connection and negotiate stream.
        
        Args:
            server_addr: (host, port) of origin server
            request: Stream request details
            
        Returns:
            Metadata about the stream (file size, etc)
            
        Raises:
            ConnectionError: If connection fails
            TimeoutError: If server doesn't respond
            ValueError: If request is invalid
            
        Example:
            >>> client = RUDPClient()
            >>> request = StreamRequest(filename="video.mp4", byte_start=0)
            >>> metadata = client.connect(("127.0.0.1", 9000), request)
            >>> print(f"File size: {metadata.file_size}")
        """
        ...
    
    def stream(self) -> Generator[bytes, None, None]:
        """
        Stream data chunks.
        
        Yields:
            Data chunks as received and reassembled
            
        Raises:
            RuntimeError: If not connected
            IOError: On network errors
            
        Example:
            >>> for chunk in client.stream():
            ...     process_chunk(chunk)
        """
        ...
    
    def get_metrics(self) -> StreamMetrics:
        """
        Get real-time streaming metrics.
        
        Returns:
            Current metrics snapshot
            
        Example:
            >>> metrics = client.get_metrics()
            >>> print(f"Throughput: {metrics.current_throughput_mbps:.2f} Mbps")
        """
        ...
    
    def close(self) -> None:
        """
        Close connection and cleanup resources.
        
        Should be idempotent (safe to call multiple times).
        
        Example:
            >>> client.close()
        """
        ...


class QualitySelector(Protocol):
    """
    Interface for adaptive bitrate quality selection.
    
    Implementations: DASHQualitySelector, FixedQualitySelector
    
    Responsibilities:
    - Monitor throughput
    - Select appropriate quality level
    - Adapt to network conditions
    
    Does NOT handle:
    - Actual data fetching
    - File resolution/mapping
    """
    
    def select_quality(self, throughput_mbps: float, current_quality: str) -> str:
        """
        Select quality based on measured throughput.
        
        Args:
            throughput_mbps: Recent measured throughput
            current_quality: Current quality level
            
        Returns:
            New quality level ("480", "720", "1080", etc)
            
        Example:
            >>> selector = DASHQualitySelector()
            >>> quality = selector.select_quality(throughput_mbps=5.2, current_quality="720")
            >>> print(quality)  # "1080"
        """
        ...
    
    def update_metrics(self, bytes_received: int, elapsed_seconds: float) -> None:
        """
        Update throughput measurements.
        
        Args:
            bytes_received: Bytes received in measurement window
            elapsed_seconds: Time elapsed
            
        Example:
            >>> selector.update_metrics(bytes_received=1048576, elapsed_seconds=1.0)
        """
        ...
    
    def get_current_quality(self) -> str:
        """
        Get current selected quality.
        
        Returns:
            Current quality level
            
        Example:
            >>> selector.get_current_quality()
            "720"
        """
        ...
    
    def reset(self) -> None:
        """
        Reset quality selector state.
        
        Example:
            >>> selector.reset()
        """
        ...


class StreamOrchestrator(Protocol):
    """
    Interface for coordinating fetching and serving (proxy-side).
    
    Implementations: VideoStreamOrchestrator
    
    Responsibilities:
    - Protocol selection (TCP vs RUDP)
    - Client lifecycle management
    - Quality adaptation coordination
    - Error recovery
    
    Does NOT handle:
    - HTTP request/response details
    - Low-level socket operations
    """
    
    def fetch_stream(
        self,
        request: StreamRequest,
        server_addr: Tuple[str, int]
    ) -> Tuple[Generator[bytes, None, None], StreamMetrics]:
        """
        Fetch stream from origin server.
        
        Args:
            request: Stream request details
            server_addr: Origin server address
            
        Returns:
            Tuple of (data_generator, metrics)
            
        Raises:
            ConnectionError: If cannot connect to server
            ValueError: If request is invalid
            
        Example:
            >>> orchestrator = VideoStreamOrchestrator()
            >>> request = StreamRequest(filename="video.mp4")
            >>> generator, metrics = orchestrator.fetch_stream(
            ...     request, ("127.0.0.1", 9000)
            ... )
            >>> for chunk in generator:
            ...     yield chunk
        """
        ...
    
    def switch_protocol(self, new_protocol: TransportProtocol) -> None:
        """
        Switch transport protocol for future streams.
        
        Args:
            new_protocol: Protocol to switch to
            
        Example:
            >>> orchestrator.switch_protocol(TransportProtocol.TCP)
        """
        ...


# ══════════════════════════════════════════════════════════════════════════════
# SERVER-SIDE INTERFACES
# ══════════════════════════════════════════════════════════════════════════════

class StreamingServer(Protocol):
    """
    Interface for streaming servers (origin-side handlers).
    
    Implementations: TCPHandler, RUDPSession
    
    Responsibilities:
    - Accept client connections
    - Serve file data
    - Manage flow control
    - Track session metrics
    
    Does NOT handle:
    - Request dispatching
    - File location/access (that's FileRepository's job)
    - Multi-session coordination (that's SessionManager's job)
    """
    
    def handle_request(
        self,
        request: StreamRequest,
        client_addr: Tuple[str, int]
    ) -> None:
        """
        Handle streaming request from client.
        
        Args:
            request: Client's stream request
            client_addr: Client's network address
            
        Raises:
            FileNotFoundError: If requested file doesn't exist
            PermissionError: If file access denied
            IOError: On network/disk errors
            
        Example:
            >>> handler = RUDPSession(filepath="/path/to/video.mp4")
            >>> request = StreamRequest(filename="video.mp4", byte_start=0)
            >>> handler.handle_request(request, ("127.0.0.1", 50000))
        """
        ...
    
    def get_metrics(self) -> StreamMetrics:
        """
        Get session metrics.
        
        Returns:
            Current session metrics
            
        Example:
            >>> metrics = handler.get_metrics()
            >>> print(f"Sent: {metrics.packets_sent}, Lost: {metrics.packets_lost}")
        """
        ...
    
    def close(self) -> None:
        """
        Close session and cleanup.
        
        Should be idempotent.
        
        Example:
            >>> handler.close()
        """
        ...


class SessionManager(Protocol):
    """
    Interface for managing multiple streaming sessions (server-side).
    
    Implementations: RUDPSessionManager, SimpleSessionManager
    
    Responsibilities:
    - Track active sessions
    - Clean up dead sessions
    - Provide session statistics
    
    Does NOT handle:
    - Actual data transfer
    - Protocol-specific logic
    """
    
    def create_session(
        self,
        session_id: str,
        protocol: TransportProtocol,
        client_addr: Tuple[str, int]
    ) -> StreamingServer:
        """
        Create new streaming session.
        
        Args:
            session_id: Unique session identifier
            protocol: Transport protocol to use
            client_addr: Client's address
            
        Returns:
            New session handler
            
        Raises:
            ValueError: If session_id already exists
            
        Example:
            >>> manager = RUDPSessionManager()
            >>> session = manager.create_session(
            ...     "session_123", TransportProtocol.RUDP, ("127.0.0.1", 50000)
            ... )
        """
        ...
    
    def get_session(self, session_id: str) -> Optional[StreamingServer]:
        """
        Get existing session by ID.
        
        Args:
            session_id: Session identifier
            
        Returns:
            Session handler or None if not found
            
        Example:
            >>> session = manager.get_session("session_123")
        """
        ...
    
    def close_session(self, session_id: str) -> None:
        """
        Close and remove session.
        
        Args:
            session_id: Session to close
            
        Example:
            >>> manager.close_session("session_123")
        """
        ...
    
    def get_active_sessions(self) -> Dict[str, StreamMetrics]:
        """
        Get metrics for all active sessions.
        
        Returns:
            Dictionary mapping session_id → metrics
            
        Example:
            >>> active = manager.get_active_sessions()
            >>> for session_id, metrics in active.items():
            ...     print(f"{session_id}: {metrics.bytes_transferred} bytes")
        """
        ...
    
    def cleanup_idle_sessions(self, timeout_seconds: float) -> int:
        """
        Close sessions idle longer than timeout.
        
        Args:
            timeout_seconds: Idle timeout threshold
            
        Returns:
            Number of sessions closed
            
        Example:
            >>> closed = manager.cleanup_idle_sessions(timeout_seconds=30.0)
            >>> print(f"Cleaned up {closed} idle sessions")
        """
        ...


class FileRepository(Protocol):
    """
    Interface for file access abstraction (server-side).
    
    Implementations: LocalFileRepository, CachingFileRepository
    
    Responsibilities:
    - Locate files
    - Provide file metadata
    - Handle file access
    
    Does NOT handle:
    - Network transfer
    - Protocol logic
    """
    
    def get_file_path(self, filename: str, quality: str = "auto") -> str:
        """
        Resolve filename to full path.
        
        Args:
            filename: Requested filename (e.g., "video.mp4")
            quality: Quality variant ("480", "720", "1080")
            
        Returns:
            Full filesystem path
            
        Raises:
            FileNotFoundError: If file doesn't exist
            
        Example:
            >>> repo = LocalFileRepository(base_dir="/videos")
            >>> path = repo.get_file_path("video.mp4", quality="720")
            >>> print(path)  # "/videos/video_720.mp4"
        """
        ...
    
    def get_file_size(self, filename: str, quality: str = "auto") -> int:
        """
        Get file size without opening.
        
        Args:
            filename: Requested filename
            quality: Quality variant
            
        Returns:
            File size in bytes
            
        Raises:
            FileNotFoundError: If file doesn't exist
            OSError: On filesystem errors
            
        Example:
            >>> size = repo.get_file_size("video.mp4", quality="720")
            >>> print(f"File size: {size:,} bytes")
        """
        ...
    
    def file_exists(self, filename: str, quality: str = "auto") -> bool:
        """
        Check if file exists.
        
        Args:
            filename: Requested filename
            quality: Quality variant
            
        Returns:
            True if file exists and is accessible
            
        Example:
            >>> if repo.file_exists("video.mp4", quality="720"):
            ...     print("File available")
        """
        ...


# ══════════════════════════════════════════════════════════════════════════════
# UTILITY INTERFACES
# ══════════════════════════════════════════════════════════════════════════════

class MetricsCollector(Protocol):
    """
    Interface for collecting and aggregating metrics.
    
    Implementations: PrometheusCollector, SimpleMetricsCollector
    
    Responsibilities:
    - Collect metrics from sessions
    - Aggregate statistics
    - Provide reporting
    
    Does NOT handle:
    - Business logic
    - Session management
    """
    
    def record_metric(self, name: str, value: float, labels: Dict[str, str] = None) -> None:
        """
        Record a single metric value.
        
        Args:
            name: Metric name (e.g., "bytes_transferred")
            value: Metric value
            labels: Optional labels/tags (e.g., {"protocol": "rudp"})
            
        Example:
            >>> collector = SimpleMetricsCollector()
            >>> collector.record_metric(
            ...     "bytes_transferred", 1048576, {"protocol": "rudp"}
            ... )
        """
        ...
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get aggregated metrics summary.
        
        Returns:
            Dictionary of aggregated metrics
            
        Example:
            >>> summary = collector.get_summary()
            >>> print(summary["total_bytes_transferred"])
        """
        ...


# ══════════════════════════════════════════════════════════════════════════════
# TESTING & VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def validate_streaming_client(client: StreamingClient) -> bool:
    """
    Validate that an object implements StreamingClient protocol.
    
    Args:
        client: Object to validate
        
    Returns:
        True if implements protocol correctly
        
    Example:
        >>> from rudp_client import RUDPClient
        >>> client = RUDPClient()
        >>> assert validate_streaming_client(client)
    """
    required_methods = ["connect", "stream", "get_metrics", "close"]
    return all(hasattr(client, method) and callable(getattr(client, method))
               for method in required_methods)


def validate_streaming_server(server: StreamingServer) -> bool:
    """
    Validate that an object implements StreamingServer protocol.
    
    Args:
        server: Object to validate
        
    Returns:
        True if implements protocol correctly
        
    Example:
        >>> from rudp_session import RUDPSession
        >>> session = RUDPSession(filepath="/path/to/video.mp4")
        >>> assert validate_streaming_server(session)
    """
    required_methods = ["handle_request", "get_metrics", "close"]
    return all(hasattr(server, method) and callable(getattr(server, method))
               for method in required_methods)


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Running streaming_interfaces self-tests...")
    
    # Test data classes
    request = StreamRequest(filename="test.mp4", byte_start=1024)
    assert request.filename == "test.mp4"
    assert request.byte_start == 1024
    print("✓ StreamRequest data class")
    
    metadata = StreamMetadata(file_size=10485760, remote_window=1048576)
    assert metadata.file_size == 10485760
    print("✓ StreamMetadata data class")
    
    metrics = StreamMetrics(
        bytes_transferred=1000000,
        packets_sent=1000,
        packets_lost=10
    )
    assert metrics.loss_rate() == 0.01
    print("✓ StreamMetrics with loss_rate calculation")
    
    # Test enums
    assert TransportProtocol.TCP.value == "tcp"
    assert StreamState.STREAMING.value == "streaming"
    print("✓ Protocol enums")
    
    # Test validation (would fail without actual implementations)
    print("✓ Validation helpers defined")
    
    print("\n✅ All streaming_interfaces tests passed!")
    print("\nNext: Implement these interfaces in your client/server classes")
