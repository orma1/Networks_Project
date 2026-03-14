"""
STREAM ORCHESTRATOR
═════════════════════════════════════════════════════════════

Orchestrates the complete streaming pipeline.

Responsibilities:
- Coordinate HTTP, streaming, and quality selection
- Manage client lifecycle (connect, stream, close)
- Switch protocols (TCP vs RUDP)
- Integrate quality adaptation
- Provide unified interface

Does NOT handle:
- HTTP protocol details (that's HTTPHandler's job)
- Low-level streaming (that's StreamingClient's job)
- Quality algorithms (that's QualitySelector's job)

This is the "maestro" that makes all components work together.
"""

from typing import Generator, Tuple, Optional
from dataclasses import dataclass

from Server_Proxy.shared.streaming_interfaces import (
    StreamingClient,
    QualitySelector,
    StreamRequest,
    StreamMetadata,
    StreamMetrics,
    TransportProtocol,
)
from Server_Proxy.client.http_handler import HTTPHandler, RangeRequest
from Server_Proxy.client.rudp_client import RUDPClient


# ══════════════════════════════════════════════════════════════════════════════
# STREAM ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

class StreamOrchestrator:
    """
    Orchestrates streaming pipeline: HTTP → Streaming → Quality.
    
    Coordinates:
    - HTTPHandler (HTTP protocol)
    - StreamingClient (data fetching)
    - QualitySelector (adaptation)
    
    Provides clean, unified interface for endpoints.
    
    Example:
        >>> orchestrator = StreamOrchestrator(
        ...     http_handler=HTTPHandler(),
        ...     default_protocol=TransportProtocol.RUDP,
        ...     quality_selector=quality_selector
        ... )
        >>> 
        >>> # Fetch stream
        >>> generator, metrics = orchestrator.fetch_stream(
        ...     filename="video.mp4",
        ...     byte_start=1024,
        ...     server_addr=("127.0.0.1", 9000)
        ... )
        >>> 
        >>> # Use in FastAPI
        >>> return StreamingResponse(generator, ...)
    """
    
    def __init__(
        self,
        http_handler: HTTPHandler,
        default_protocol: TransportProtocol = TransportProtocol.RUDP,
        quality_selector: Optional[QualitySelector] = None,
    ):
        """
        Initialize stream orchestrator.
        
        Args:
            http_handler: HTTPHandler instance
            default_protocol: Default transport (TCP or RUDP)
            quality_selector: Optional quality selector for adaptation
            
        Example:
            >>> orchestrator = StreamOrchestrator(
            ...     http_handler=HTTPHandler(),
            ...     default_protocol=TransportProtocol.RUDP
            ... )
        """
        self.http_handler = http_handler
        self.default_protocol = default_protocol
        self.quality_selector = quality_selector
        
        # Active clients (for cleanup)
        self._active_clients = []
    
    # ── Main Orchestration Method ────────────────────────────────────────
    
    def fetch_stream(
        self,
        filename: str,
        byte_start: int,
        server_addr: Tuple[str, int],
        protocol: Optional[TransportProtocol] = None,
        quality: str = "auto",
        enable_quality_adaptation: bool = True,
    ) -> Tuple[Generator[bytes, None, None], StreamMetrics]:
        """
        Fetch stream from origin server.
        
        This is the main entry point that coordinates everything:
        1. Creates StreamingClient (RUDP or TCP)
        2. Connects to server
        3. Streams data (with optional quality adaptation)
        4. Returns generator + metrics
        
        Args:
            filename: File to stream
            byte_start: Starting byte offset
            server_addr: Server address (host, port)
            protocol: Transport protocol (default: use self.default_protocol)
            quality: Quality level or "auto"
            enable_quality_adaptation: Enable adaptive quality
            
        Returns:
            Tuple of (data_generator, metrics)
            
        Example:
            >>> generator, metrics = orchestrator.fetch_stream(
            ...     filename="video.mp4",
            ...     byte_start=1024,
            ...     server_addr=("127.0.0.1", 9000)
            ... )
            >>> 
            >>> for chunk in generator:
            ...     send_to_client(chunk)
        """
        # Determine protocol
        if protocol is None:
            protocol = self.default_protocol
        
        # Create streaming client
        if protocol == TransportProtocol.RUDP:
            client = RUDPClient()
        else:
            # TCP client (would need to implement TCPClient)
            raise NotImplementedError("TCP client not yet implemented")
        
        # Track for cleanup
        self._active_clients.append(client)
        
        # Create request
        request = StreamRequest(
            filename=filename,
            byte_start=byte_start,
            protocol=protocol,
            quality=quality
        )
        
        # Connect to server
        try:
            metadata = client.connect(server_addr, request)
        except Exception as e:
            self._cleanup_client(client)
            raise ConnectionError(f"Failed to connect: {e}")
        
        # Stream data
        if enable_quality_adaptation and self.quality_selector and quality == "auto":
            # With quality adaptation
            generator = self._stream_with_adaptation(client, metadata)
        else:
            # Without quality adaptation
            generator = self._stream_simple(client)
        
        # Get metrics
        metrics = client.get_metrics()
        
        return generator, metrics
    
    # ── Streaming Implementations ────────────────────────────────────────
    
    def _stream_simple(
        self,
        client: StreamingClient
    ) -> Generator[bytes, None, None]:
        """
        Simple streaming without quality adaptation.
        
        Args:
            client: Connected streaming client
            
        Yields:
            Data chunks
        """
        try:
            for chunk in client.stream():
                yield chunk
        finally:
            self._cleanup_client(client)
    
    def _stream_with_adaptation(
        self,
        client: StreamingClient,
        metadata: StreamMetadata
    ) -> Generator[bytes, None, None]:
        """
        Stream with quality adaptation.
        
        Args:
            client: Connected streaming client
            metadata: Stream metadata from server
            
        Yields:
            Data chunks
            
        Note:
            This is a placeholder. Full quality adaptation would require:
            - Monitoring throughput
            - Requesting quality changes from server
            - Handling quality switches mid-stream
        """
        try:
            bytes_received = 0
            
            for chunk in client.stream():
                yield chunk
                bytes_received += len(chunk)
                
                # Update quality selector
                if self.quality_selector:
                    self.quality_selector.update_metrics(
                        bytes_received=bytes_received,
                        elapsed_seconds=1.0  # Would need real timing
                    )
                    
                    # Get quality recommendation
                    current_quality = self.quality_selector.get_current_quality()
                    
                    # Note: Actually switching quality mid-stream would require
                    # disconnecting and reconnecting with new quality
                    # For now, this just tracks adaptation
        
        finally:
            self._cleanup_client(client)
    
    # ── Client Management ────────────────────────────────────────────────
    
    def _cleanup_client(self, client: StreamingClient) -> None:
        """
        Cleanup streaming client.
        
        Args:
            client: Client to cleanup
        """
        try:
            client.close()
        except Exception as e:
            print(f"[!] Error closing client: {e}")
        finally:
            if client in self._active_clients:
                self._active_clients.remove(client)
    
    def close_all(self) -> None:
        """
        Close all active clients.
        
        Example:
            >>> orchestrator.close_all()
        """
        for client in list(self._active_clients):
            self._cleanup_client(client)
    
    # ── Protocol Switching ───────────────────────────────────────────────
    
    def switch_protocol(self, new_protocol: TransportProtocol) -> None:
        """
        Switch default protocol.
        
        Args:
            new_protocol: New default protocol
            
        Example:
            >>> orchestrator.switch_protocol(TransportProtocol.TCP)
        """
        self.default_protocol = new_protocol
    
    def get_protocol(self) -> TransportProtocol:
        """
        Get current default protocol.
        
        Returns:
            Current protocol
            
        Example:
            >>> print(orchestrator.get_protocol())
            TransportProtocol.RUDP
        """
        return self.default_protocol
    
    # ── Helper Methods ───────────────────────────────────────────────────
    
    def get_active_client_count(self) -> int:
        """
        Get number of active streaming clients.
        
        Returns:
            Active client count
            
        Example:
            >>> count = orchestrator.get_active_client_count()
            >>> print(f"{count} active streams")
        """
        return len(self._active_clients)
    
    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"StreamOrchestrator("
            f"protocol={self.default_protocol.value}, "
            f"active_clients={len(self._active_clients)})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from unittest.mock import Mock
    
    print("Running StreamOrchestrator self-tests...\n")
    
    # Test 1: Initialization
    http_handler = HTTPHandler()
    orchestrator = StreamOrchestrator(
        http_handler=http_handler,
        default_protocol=TransportProtocol.RUDP
    )
    
    assert orchestrator.get_protocol() == TransportProtocol.RUDP
    assert orchestrator.get_active_client_count() == 0
    print("✓ Initialization")
    
    # Test 2: Protocol switching
    orchestrator.switch_protocol(TransportProtocol.TCP)
    assert orchestrator.get_protocol() == TransportProtocol.TCP
    
    orchestrator.switch_protocol(TransportProtocol.RUDP)
    assert orchestrator.get_protocol() == TransportProtocol.RUDP
    print("✓ Protocol switching")
    
    # Test 3: Client tracking
    mock_client = Mock(spec=StreamingClient)
    orchestrator._active_clients.append(mock_client)
    
    assert orchestrator.get_active_client_count() == 1
    print("✓ Client tracking")
    
    # Test 4: Cleanup client
    orchestrator._cleanup_client(mock_client)
    assert orchestrator.get_active_client_count() == 0
    print("✓ Cleanup client")
    
    # Test 5: Close all
    mock_client1 = Mock(spec=StreamingClient)
    mock_client2 = Mock(spec=StreamingClient)
    orchestrator._active_clients.extend([mock_client1, mock_client2])
    
    orchestrator.close_all()
    assert orchestrator.get_active_client_count() == 0
    assert mock_client1.close.called
    assert mock_client2.close.called
    print("✓ Close all clients")
    
    print("\n✅ All StreamOrchestrator tests passed!")
    print("\nExample usage:")
    print("  orchestrator = StreamOrchestrator(")
    print("      http_handler=HTTPHandler(),")
    print("      default_protocol=TransportProtocol.RUDP")
    print("  )")
    print("  ")
    print("  generator, metrics = orchestrator.fetch_stream(")
    print("      filename='video.mp4',")
    print("      byte_start=1024,")
    print("      server_addr=('127.0.0.1', 9000)")
    print("  )")
    print("  ")
    print("  for chunk in generator:")
    print("      yield chunk")
