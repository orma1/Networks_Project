"""
FLOW CONTROLLER
═════════════════════════════════════════════════════════════

Manages flow control using receiver window (RWnd).

Responsibilities:
- Track receiver's buffer capacity
- Prevent sender from overwhelming receiver
- Calculate safe sending limit

Does NOT handle:
- Congestion control (that's CongestionController's job)
- Window management (that's SlidingWindow's job)
- Network congestion (that's separate from receiver capacity)

Flow Control vs Congestion Control:
- Flow Control: Prevents overwhelming the RECEIVER
- Congestion Control: Prevents overwhelming the NETWORK

Both must be satisfied - send at min(cwnd, rwnd).
"""

from dataclasses import dataclass
from typing import Optional


# ══════════════════════════════════════════════════════════════════════════════
# FLOW CONTROL METRICS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class FlowMetrics:
    """Metrics for flow control analysis."""
    current_rwnd: int
    min_rwnd_seen: int
    max_rwnd_seen: int
    rwnd_updates: int
    zero_window_events: int
    last_update_time: float
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "current_rwnd": self.current_rwnd,
            "min_rwnd_seen": self.min_rwnd_seen,
            "max_rwnd_seen": self.max_rwnd_seen,
            "rwnd_updates": self.rwnd_updates,
            "zero_window_events": self.zero_window_events,
            "last_update_time": self.last_update_time,
        }


# ══════════════════════════════════════════════════════════════════════════════
# FLOW CONTROLLER
# ══════════════════════════════════════════════════════════════════════════════

class FlowController:
    """
    Flow control using receiver window (RWnd).
    
    The receiver advertises how much buffer space it has available.
    The sender must not exceed this limit to avoid overwhelming the receiver.
    
    Protocol:
    - Receiver sends RWnd in every ACK: "ACK|<seq>|<rwnd>"
    - Sender tracks latest RWnd value
    - Sender limits sending to min(cwnd, rwnd)
    
    Zero Window:
    - If receiver's buffer is full (rwnd=0), sender must pause
    - Sender periodically probes with small packets to detect window opening
    
    Window Scaling:
    - Convert byte-based RWnd to packet-based limit
    - Accounts for packet size variations
    """
    
    def __init__(
        self,
        initial_rwnd: int = 1048576,  # 1MB default
        packet_size: int = 1400,
        min_rwnd: int = 0,
    ):
        """
        Initialize flow controller.
        
        Args:
            initial_rwnd: Initial receiver window (bytes)
            packet_size: Average packet size (bytes) for conversion
            min_rwnd: Minimum allowed rwnd (bytes)
            
        Example:
            >>> controller = FlowController(
            ...     initial_rwnd=1048576,  # 1MB
            ...     packet_size=1400
            ... )
        """
        self._initial_rwnd = initial_rwnd
        self._packet_size = packet_size
        self._min_rwnd = min_rwnd
        
        # Current state
        self._rwnd_bytes = initial_rwnd
        self._last_update_time = 0.0
        
        # Statistics
        self._min_rwnd_seen = initial_rwnd
        self._max_rwnd_seen = initial_rwnd
        self._rwnd_updates = 0
        self._zero_window_events = 0
    
    # ── Properties ───────────────────────────────────────────────────────
    
    @property
    def rwnd_bytes(self) -> int:
        """Current receiver window in bytes."""
        return self._rwnd_bytes
    
    @property
    def rwnd_packets(self) -> int:
        """Current receiver window in packets."""
        if self._rwnd_bytes == 0:
            return 0
        return max(1, self._rwnd_bytes // self._packet_size)
    
    def is_window_closed(self) -> bool:
        """Check if receiver window is closed (zero)."""
        return self._rwnd_bytes == 0
    
    def is_window_small(self, threshold_bytes: int = 65536) -> bool:
        """
        Check if receiver window is getting small.
        
        Args:
            threshold_bytes: Threshold for "small" window (default 64KB)
            
        Returns:
            True if window is below threshold
            
        Example:
            >>> if controller.is_window_small():
            ...     print("Warning: Receiver buffer running low")
        """
        return 0 < self._rwnd_bytes < threshold_bytes
    
    # ── RWnd Updates ─────────────────────────────────────────────────────
    
    def update_rwnd(self, new_rwnd_bytes: int, timestamp: float = 0.0) -> bool:
        """
        Update receiver window from ACK.
        
        Args:
            new_rwnd_bytes: New receiver window size (bytes)
            timestamp: Timestamp of update (optional)
            
        Returns:
            True if window changed (False if same as before)
            
        Example:
            >>> # Received ACK with RWnd=524288 (512KB)
            >>> changed = controller.update_rwnd(524288)
            >>> if changed:
            ...     print(f"Window updated: {controller.rwnd_bytes} bytes")
        """
        import time
        
        # Enforce minimum
        new_rwnd_bytes = max(self._min_rwnd, new_rwnd_bytes)
        
        # Check if changed
        if new_rwnd_bytes == self._rwnd_bytes:
            return False
        
        # Detect zero window
        if new_rwnd_bytes == 0 and self._rwnd_bytes > 0:
            self._zero_window_events += 1
        
        # Update
        old_rwnd = self._rwnd_bytes
        self._rwnd_bytes = new_rwnd_bytes
        self._last_update_time = timestamp or time.monotonic()
        self._rwnd_updates += 1
        
        # Update statistics
        self._min_rwnd_seen = min(self._min_rwnd_seen, new_rwnd_bytes)
        self._max_rwnd_seen = max(self._max_rwnd_seen, new_rwnd_bytes)
        
        return True
    
    def decrease_rwnd(self, bytes_sent: int) -> None:
        """
        Decrease RWnd estimate based on bytes sent.
        
        This is an optimistic estimate - actual RWnd comes from ACKs.
        Used to avoid sending too much before getting ACK feedback.
        
        Args:
            bytes_sent: Number of bytes sent (decreases available window)
            
        Example:
            >>> controller.decrease_rwnd(1400)  # Sent one packet
        """
        self._rwnd_bytes = max(0, self._rwnd_bytes - bytes_sent)
    
    def reset_rwnd(self) -> None:
        """
        Reset RWnd to initial value.
        
        Used when connection resets or probing after zero window.
        
        Example:
            >>> controller.reset_rwnd()
        """
        self._rwnd_bytes = self._initial_rwnd
    
    # ── Sending Limit Calculation ────────────────────────────────────────
    
    def get_sending_limit(self) -> int:
        """
        Get maximum packets that can be sent (based on RWnd).
        
        Returns:
            Number of packets allowed by flow control
            
        Example:
            >>> limit = controller.get_sending_limit()
            >>> print(f"Can send {limit} packets (flow control)")
        """
        return self.rwnd_packets
    
    def get_bytes_available(self) -> int:
        """
        Get bytes available in receiver window.
        
        Returns:
            Bytes available for sending
            
        Example:
            >>> available = controller.get_bytes_available()
            >>> if available > 0:
            ...     send_data(min(chunk_size, available))
        """
        return self._rwnd_bytes
    
    def can_send_packet(self, packet_size: int) -> bool:
        """
        Check if a packet can be sent without exceeding RWnd.
        
        Args:
            packet_size: Size of packet to send (bytes)
            
        Returns:
            True if packet fits in receiver window
            
        Example:
            >>> if controller.can_send_packet(1400):
            ...     send_packet(data)
            ...     controller.decrease_rwnd(1400)
        """
        return packet_size <= self._rwnd_bytes
    
    # ── Zero Window Handling ─────────────────────────────────────────────
    
    def should_probe_window(self, last_probe_time: float, probe_interval: float = 1.0) -> bool:
        """
        Check if should send zero-window probe.
        
        When receiver window is zero, sender periodically sends small
        probe packets to detect when window opens.
        
        Args:
            last_probe_time: Timestamp of last probe sent
            probe_interval: Seconds between probes
            
        Returns:
            True if should send probe now
            
        Example:
            >>> import time
            >>> last_probe = time.monotonic()
            >>> if controller.is_window_closed():
            ...     if controller.should_probe_window(last_probe, 1.0):
            ...         send_probe_packet()
            ...         last_probe = time.monotonic()
        """
        import time
        
        if not self.is_window_closed():
            return False
        
        now = time.monotonic()
        return (now - last_probe_time) >= probe_interval
    
    # ── Metrics & Monitoring ─────────────────────────────────────────────
    
    def get_metrics(self) -> FlowMetrics:
        """
        Get flow control metrics.
        
        Returns:
            FlowMetrics object
            
        Example:
            >>> metrics = controller.get_metrics()
            >>> print(f"RWnd: {metrics.current_rwnd} bytes")
            >>> print(f"Updates: {metrics.rwnd_updates}")
        """
        return FlowMetrics(
            current_rwnd=self._rwnd_bytes,
            min_rwnd_seen=self._min_rwnd_seen,
            max_rwnd_seen=self._max_rwnd_seen,
            rwnd_updates=self._rwnd_updates,
            zero_window_events=self._zero_window_events,
            last_update_time=self._last_update_time,
        )
    
    def get_utilization(self) -> float:
        """
        Get receiver buffer utilization (0.0 to 1.0).
        
        Returns:
            Fraction of buffer available (1.0 = fully available, 0.0 = full)
            
        Example:
            >>> utilization = controller.get_utilization()
            >>> print(f"Receiver buffer: {utilization*100:.1f}% available")
        """
        if self._max_rwnd_seen == 0:
            return 1.0
        return self._rwnd_bytes / self._max_rwnd_seen
    
    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"FlowController(rwnd={self._rwnd_bytes} bytes, "
            f"{self.rwnd_packets} packets, "
            f"utilization={self.get_utilization()*100:.1f}%)"
        )


# ══════════════════════════════════════════════════════════════════════════════
# COMBINED FLOW + CONGESTION LIMIT
# ══════════════════════════════════════════════════════════════════════════════

def calculate_combined_limit(
    congestion_limit: int,
    flow_limit: int
) -> int:
    """
    Calculate combined sending limit from both controllers.
    
    Must satisfy BOTH congestion control and flow control:
    - Congestion limit (cwnd): Don't overwhelm network
    - Flow limit (rwnd): Don't overwhelm receiver
    
    Args:
        congestion_limit: Max packets from CongestionController
        flow_limit: Max packets from FlowController
        
    Returns:
        Minimum of both limits (most restrictive)
        
    Example:
        >>> from congestion_controller import CongestionController
        >>> 
        >>> cc = CongestionController()
        >>> fc = FlowController()
        >>> 
        >>> limit = calculate_combined_limit(
        ...     cc.get_sending_limit(),
        ...     fc.get_sending_limit()
        ... )
        >>> print(f"Can send {limit} packets")
    """
    return min(congestion_limit, flow_limit)


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Running FlowController self-tests...\n")
    
    # Test 1: Initialization
    controller = FlowController(
        initial_rwnd=1048576,  # 1MB
        packet_size=1400
    )
    
    assert controller.rwnd_bytes == 1048576
    assert controller.rwnd_packets == 1048576 // 1400
    print("✓ Initialization")
    
    # Test 2: RWnd updates
    changed = controller.update_rwnd(524288)  # 512KB
    assert changed == True
    assert controller.rwnd_bytes == 524288
    
    changed = controller.update_rwnd(524288)  # Same value
    assert changed == False
    print("✓ RWnd updates")
    
    # Test 3: Zero window detection
    controller2 = FlowController(initial_rwnd=65536)
    
    assert controller2.is_window_closed() == False
    
    controller2.update_rwnd(0)
    assert controller2.is_window_closed() == True
    assert controller2.get_metrics().zero_window_events == 1
    print("✓ Zero window detection")
    
    # Test 4: Small window detection
    controller3 = FlowController(initial_rwnd=1048576)
    
    controller3.update_rwnd(32768)  # 32KB
    assert controller3.is_window_small(threshold_bytes=65536) == True
    print("✓ Small window detection")
    
    # Test 5: Sending limit
    controller4 = FlowController(
        initial_rwnd=14000,  # 10 packets worth
        packet_size=1400
    )
    
    limit = controller4.get_sending_limit()
    assert limit == 10
    print("✓ Sending limit calculation")
    
    # Test 6: Decrease RWnd
    controller5 = FlowController(initial_rwnd=10000)
    
    controller5.decrease_rwnd(1400)
    assert controller5.rwnd_bytes == 8600
    
    controller5.decrease_rwnd(20000)  # More than available
    assert controller5.rwnd_bytes == 0  # Can't go negative
    print("✓ Decrease RWnd")
    
    # Test 7: Can send packet
    controller6 = FlowController(initial_rwnd=5000)
    
    assert controller6.can_send_packet(1400) == True
    assert controller6.can_send_packet(10000) == False
    print("✓ Can send packet check")
    
    # Test 8: Utilization
    controller7 = FlowController(initial_rwnd=10000)
    
    util1 = controller7.get_utilization()
    assert util1 == 1.0  # Fully available
    
    controller7.update_rwnd(5000)
    util2 = controller7.get_utilization()
    assert util2 == 0.5  # Half available
    print("✓ Buffer utilization")
    
    # Test 9: Metrics
    metrics = controller.get_metrics()
    assert metrics.current_rwnd == controller.rwnd_bytes
    assert metrics.rwnd_updates > 0
    print("✓ Metrics collection")
    
    # Test 10: Combined limit
    from congestion_controller import CongestionController
    
    cc = CongestionController(initial_cwnd=10.0)
    fc = FlowController(initial_rwnd=7000, packet_size=1400)  # 5 packets
    
    combined = calculate_combined_limit(
        cc.get_sending_limit(),  # 10 packets
        fc.get_sending_limit()   # 5 packets
    )
    
    assert combined == 5  # Limited by flow control
    print("✓ Combined limit calculation")
    
    # Test 11: Reset
    controller8 = FlowController(initial_rwnd=10000)
    controller8.update_rwnd(1000)
    controller8.reset_rwnd()
    assert controller8.rwnd_bytes == 10000
    print("✓ Reset RWnd")
    
    print("\n✅ All FlowController tests passed!")
    print("\nExample usage:")
    print("  controller = FlowController(initial_rwnd=1048576)")
    print("  controller.update_rwnd(524288)  # From ACK")
    print("  limit = controller.get_sending_limit()")
    print("  if controller.can_send_packet(1400):")
    print("      send_packet(data)")
    print("      controller.decrease_rwnd(1400)")
