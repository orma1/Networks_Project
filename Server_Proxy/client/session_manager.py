"""
SESSION MANAGER
═════════════════════════════════════════════════════════════

Manages multiple active streaming sessions.

Responsibilities:
- Track active sessions by ID
- Create and destroy sessions
- Cleanup idle/dead sessions
- Provide aggregated metrics across all sessions

Does NOT handle:
- Actual data transfer (that's RUDPSession/TCPHandler's job)
- Protocol-specific logic (that's in the session implementations)
- File access (that's FileRepository's job)

Session Lifecycle:
1. create_session() → new session with unique ID
2. get_session() → retrieve active session
3. close_session() → explicitly close
4. cleanup_idle_sessions() → auto-cleanup timeouts
"""

import time
import threading
from typing import Dict, Optional, Callable
from dataclasses import dataclass, field
from Server_Proxy.shared.streaming_interfaces import (
    SessionManager,
    StreamingServer,
    StreamMetrics,
    TransportProtocol
)


# ══════════════════════════════════════════════════════════════════════════════
# SESSION INFO
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class SessionInfo:
    """Information about an active session."""
    session_id: str
    server: StreamingServer
    protocol: TransportProtocol
    client_addr: tuple
    created_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    
    def age(self) -> float:
        """Get session age in seconds."""
        return time.monotonic() - self.created_at
    
    def idle_time(self) -> float:
        """Get seconds since last activity."""
        return time.monotonic() - self.last_activity
    
    def update_activity(self) -> None:
        """Mark session as active now."""
        self.last_activity = time.monotonic()


# ══════════════════════════════════════════════════════════════════════════════
# SIMPLE SESSION MANAGER
# ══════════════════════════════════════════════════════════════════════════════

class SimpleSessionManager:
    """
    Simple in-memory session manager.
    
    Implements SessionManager interface for tracking RUDP/TCP sessions.
    Thread-safe for concurrent access.
    
    Features:
    - Unique session ID generation
    - Active session tracking
    - Idle session cleanup
    - Aggregated metrics
    - Thread-safe operations
    
    Example:
        >>> manager = SimpleSessionManager()
        >>> session = manager.create_session(
        ...     "session_123",
        ...     TransportProtocol.RUDP,
        ...     ("127.0.0.1", 50000)
        ... )
    """
    
    def __init__(
        self,
        session_factory: Optional[Callable] = None,
        max_sessions: int = 100
    ):
        """
        Initialize session manager.
        
        Args:
            session_factory: Optional factory function to create sessions
            max_sessions: Maximum concurrent sessions allowed
            
        Example:
            >>> def create_rudp_session(client_addr, filepath):
            ...     return RUDPSession(client_addr, filepath)
            >>> 
            >>> manager = SimpleSessionManager(
            ...     session_factory=create_rudp_session,
            ...     max_sessions=50
            ... )
        """
        self._sessions: Dict[str, SessionInfo] = {}
        self._lock = threading.RLock()
        self._session_factory = session_factory
        self._max_sessions = max_sessions
        
        # Statistics
        self._total_created = 0
        self._total_closed = 0
        self._total_cleaned = 0
    
    # ── SessionManager Interface Implementation ──────────────────────────
    
    def create_session(
        self,
        session_id: str,
        protocol: TransportProtocol,
        client_addr: tuple,
        server: Optional[StreamingServer] = None
    ) -> StreamingServer:
        """
        Create new streaming session.
        
        Implements: SessionManager.create_session()
        
        Args:
            session_id: Unique session identifier
            protocol: Transport protocol (TCP or RUDP)
            client_addr: Client's address tuple
            server: Optional pre-created server instance
            
        Returns:
            New session handler
            
        Raises:
            ValueError: If session_id already exists or max sessions reached
            
        Example:
            >>> session = manager.create_session(
            ...     "session_123",
            ...     TransportProtocol.RUDP,
            ...     ("127.0.0.1", 50000)
            ... )
        """
        with self._lock:
            # Check if session already exists
            if session_id in self._sessions:
                raise ValueError(f"Session already exists: {session_id}")
            
            # Check max sessions limit
            if len(self._sessions) >= self._max_sessions:
                raise ValueError(
                    f"Max sessions reached ({self._max_sessions}). "
                    f"Close existing sessions first."
                )
            
            # Create or use provided server
            if server is None:
                if self._session_factory is None:
                    raise ValueError(
                        "No session_factory provided and no server instance given"
                    )
                server = self._session_factory(client_addr)
            
            # Create session info
            info = SessionInfo(
                session_id=session_id,
                server=server,
                protocol=protocol,
                client_addr=client_addr
            )
            
            # Store session
            self._sessions[session_id] = info
            self._total_created += 1
            
            return server
    
    def get_session(self, session_id: str) -> Optional[StreamingServer]:
        """
        Get existing session by ID.
        
        Implements: SessionManager.get_session()
        
        Args:
            session_id: Session identifier
            
        Returns:
            Session handler or None if not found
            
        Example:
            >>> session = manager.get_session("session_123")
            >>> if session:
            ...     metrics = session.get_metrics()
        """
        with self._lock:
            info = self._sessions.get(session_id)
            if info:
                info.update_activity()
                return info.server
            return None
    
    def close_session(self, session_id: str) -> None:
        """
        Close and remove session.
        
        Implements: SessionManager.close_session()
        
        Args:
            session_id: Session to close
            
        Example:
            >>> manager.close_session("session_123")
        """
        with self._lock:
            info = self._sessions.pop(session_id, None)
            if info:
                try:
                    info.server.close()
                except Exception as e:
                    print(f"[!] Error closing session {session_id}: {e}")
                finally:
                    self._total_closed += 1
    
    def get_active_sessions(self) -> Dict[str, StreamMetrics]:
        """
        Get metrics for all active sessions.
        
        Implements: SessionManager.get_active_sessions()
        
        Returns:
            Dictionary mapping session_id → metrics
            
        Example:
            >>> active = manager.get_active_sessions()
            >>> for sid, metrics in active.items():
            ...     print(f"{sid}: {metrics.bytes_transferred} bytes")
        """
        with self._lock:
            result = {}
            for session_id, info in self._sessions.items():
                try:
                    metrics = info.server.get_metrics()
                    result[session_id] = metrics
                except Exception as e:
                    print(f"[!] Error getting metrics for {session_id}: {e}")
            return result
    
    def cleanup_idle_sessions(self, timeout_seconds: float) -> int:
        """
        Close sessions idle longer than timeout.
        
        Implements: SessionManager.cleanup_idle_sessions()
        
        Args:
            timeout_seconds: Idle timeout threshold
            
        Returns:
            Number of sessions closed
            
        Example:
            >>> closed = manager.cleanup_idle_sessions(timeout_seconds=30.0)
            >>> print(f"Cleaned up {closed} idle sessions")
        """
        with self._lock:
            to_close = []
            
            for session_id, info in self._sessions.items():
                if info.idle_time() > timeout_seconds:
                    to_close.append(session_id)
            
            for session_id in to_close:
                self.close_session(session_id)
                self._total_cleaned += 1
            
            return len(to_close)
    
    # ── Additional Helper Methods ────────────────────────────────────────
    
    def get_session_count(self) -> int:
        """
        Get number of active sessions.
        
        Returns:
            Active session count
            
        Example:
            >>> count = manager.get_session_count()
            >>> print(f"Active sessions: {count}")
        """
        with self._lock:
            return len(self._sessions)
    
    def get_session_info(self, session_id: str) -> Optional[SessionInfo]:
        """
        Get detailed session information.
        
        Args:
            session_id: Session identifier
            
        Returns:
            SessionInfo or None if not found
            
        Example:
            >>> info = manager.get_session_info("session_123")
            >>> if info:
            ...     print(f"Age: {info.age():.1f}s")
            ...     print(f"Idle: {info.idle_time():.1f}s")
        """
        with self._lock:
            return self._sessions.get(session_id)
    
    def list_session_ids(self) -> list:
        """
        Get list of all active session IDs.
        
        Returns:
            List of session IDs
            
        Example:
            >>> session_ids = manager.list_session_ids()
            >>> for sid in session_ids:
            ...     print(sid)
        """
        with self._lock:
            return list(self._sessions.keys())
    
    def close_all_sessions(self) -> int:
        """
        Close all active sessions.
        
        Returns:
            Number of sessions closed
            
        Example:
            >>> closed = manager.close_all_sessions()
            >>> print(f"Closed {closed} sessions")
        """
        with self._lock:
            session_ids = list(self._sessions.keys())
            for session_id in session_ids:
                self.close_session(session_id)
            return len(session_ids)
    
    def get_statistics(self) -> dict:
        """
        Get manager statistics.
        
        Returns:
            Dictionary of statistics
            
        Example:
            >>> stats = manager.get_statistics()
            >>> print(f"Active: {stats['active_sessions']}")
            >>> print(f"Total created: {stats['total_created']}")
        """
        with self._lock:
            return {
                "active_sessions": len(self._sessions),
                "total_created": self._total_created,
                "total_closed": self._total_closed,
                "total_cleaned": self._total_cleaned,
                "max_sessions": self._max_sessions,
            }
    
    def get_aggregated_metrics(self) -> dict:
        """
        Get aggregated metrics across all sessions.
        
        Returns:
            Dictionary with totals and averages
            
        Example:
            >>> agg = manager.get_aggregated_metrics()
            >>> print(f"Total bytes: {agg['total_bytes']}")
            >>> print(f"Avg throughput: {agg['avg_throughput_mbps']:.2f} Mbps")
        """
        with self._lock:
            total_bytes = 0
            total_packets_sent = 0
            total_packets_lost = 0
            total_throughput = 0.0
            count = 0
            
            for info in self._sessions.values():
                try:
                    metrics = info.server.get_metrics()
                    total_bytes += metrics.bytes_transferred
                    total_packets_sent += metrics.packets_sent
                    total_packets_lost += metrics.packets_lost
                    total_throughput += metrics.current_throughput_mbps
                    count += 1
                except Exception:
                    continue
            
            return {
                "total_bytes": total_bytes,
                "total_packets_sent": total_packets_sent,
                "total_packets_lost": total_packets_lost,
                "avg_throughput_mbps": total_throughput / count if count > 0 else 0.0,
                "active_sessions": count,
            }
    
    def __repr__(self) -> str:
        """String representation for debugging."""
        with self._lock:
            return (
                f"SimpleSessionManager("
                f"active={len(self._sessions)}, "
                f"max={self._max_sessions})"
            )


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from unittest.mock import Mock
    
    print("Running SimpleSessionManager self-tests...\n")
    
    # Test 1: Initialization
    manager = SimpleSessionManager(max_sessions=10)
    assert manager.get_session_count() == 0
    print("✓ Initialization")
    
    # Test 2: Create session with mock server
    mock_server = Mock(spec=StreamingServer)
    mock_server.get_metrics.return_value = StreamMetrics(bytes_transferred=1000)
    
    session = manager.create_session(
        "test_session_1",
        TransportProtocol.RUDP,
        ("127.0.0.1", 50000),
        server=mock_server
    )
    
    assert session == mock_server
    assert manager.get_session_count() == 1
    print("✓ Create session")
    
    # Test 3: Get session
    retrieved = manager.get_session("test_session_1")
    assert retrieved == mock_server
    print("✓ Get session")
    
    # Test 4: Get non-existent session
    none_session = manager.get_session("nonexistent")
    assert none_session is None
    print("✓ Get non-existent session returns None")
    
    # Test 5: Duplicate session ID
    try:
        manager.create_session(
            "test_session_1",  # Duplicate
            TransportProtocol.RUDP,
            ("127.0.0.1", 50001),
            server=Mock()
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "already exists" in str(e)
    print("✓ Duplicate session ID raises ValueError")
    
    # Test 6: List session IDs
    mock_server2 = Mock(spec=StreamingServer)
    mock_server2.get_metrics.return_value = StreamMetrics(bytes_transferred=2000)
    
    manager.create_session(
        "test_session_2",
        TransportProtocol.TCP,
        ("127.0.0.1", 50001),
        server=mock_server2
    )
    
    session_ids = manager.list_session_ids()
    assert "test_session_1" in session_ids
    assert "test_session_2" in session_ids
    assert len(session_ids) == 2
    print("✓ List session IDs")
    
    # Test 7: Get active sessions metrics
    active = manager.get_active_sessions()
    assert len(active) == 2
    assert active["test_session_1"].bytes_transferred == 1000
    assert active["test_session_2"].bytes_transferred == 2000
    print("✓ Get active sessions metrics")
    
    # Test 8: Get session info
    info = manager.get_session_info("test_session_1")
    assert info is not None
    assert info.session_id == "test_session_1"
    assert info.protocol == TransportProtocol.RUDP
    assert info.age() >= 0
    assert info.idle_time() >= 0
    print("✓ Get session info")
    
    # Test 9: Close session
    manager.close_session("test_session_1")
    assert manager.get_session_count() == 1
    assert manager.get_session("test_session_1") is None
    print("✓ Close session")
    
    # Test 10: Cleanup idle sessions
    import time
    
    # Create session and let it idle
    mock_server3 = Mock(spec=StreamingServer)
    manager.create_session(
        "test_session_3",
        TransportProtocol.RUDP,
        ("127.0.0.1", 50002),
        server=mock_server3
    )
    
    # Manually set idle time (for testing)
    info = manager.get_session_info("test_session_3")
    info.last_activity = time.monotonic() - 35.0  # 35 seconds ago
    
    # Cleanup sessions idle > 30 seconds
    cleaned = manager.cleanup_idle_sessions(timeout_seconds=30.0)
    assert cleaned == 1
    assert manager.get_session("test_session_3") is None
    print("✓ Cleanup idle sessions")
    
    # Test 11: Statistics
    stats = manager.get_statistics()
    assert stats["active_sessions"] == 1  # Only test_session_2 left
    assert stats["total_created"] == 3
    assert stats["total_closed"] == 2
    assert stats["total_cleaned"] == 1
    print("✓ Statistics")
    
    # Test 12: Aggregated metrics
    agg = manager.get_aggregated_metrics()
    assert agg["total_bytes"] == 2000  # Only test_session_2
    assert agg["active_sessions"] == 1
    print("✓ Aggregated metrics")
    
    # Test 13: Close all sessions
    closed = manager.close_all_sessions()
    assert closed == 1
    assert manager.get_session_count() == 0
    print("✓ Close all sessions")
    
    # Test 14: Max sessions limit
    manager2 = SimpleSessionManager(max_sessions=2)
    
    for i in range(2):
        manager2.create_session(
            f"session_{i}",
            TransportProtocol.RUDP,
            ("127.0.0.1", 50000 + i),
            server=Mock(spec=StreamingServer)
        )
    
    try:
        manager2.create_session(
            "session_3",
            TransportProtocol.RUDP,
            ("127.0.0.1", 50003),
            server=Mock()
        )
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "Max sessions reached" in str(e)
    print("✓ Max sessions limit enforced")
    
    print("\n✅ All SimpleSessionManager tests passed!")
    print("\nExample usage:")
    print("  manager = SimpleSessionManager(max_sessions=50)")
    print("  session = manager.create_session(")
    print("      'session_123',")
    print("      TransportProtocol.RUDP,")
    print("      ('127.0.0.1', 50000),")
    print("      server=rudp_session")
    print("  )")
    print("  manager.cleanup_idle_sessions(timeout_seconds=30.0)")
