"""
CONGESTION CONTROLLER
═════════════════════════════════════════════════════════════

Implements TCP Reno-style congestion control for RUDP.

Responsibilities:
- Manage congestion window (cwnd)
- Implement slow-start and congestion avoidance
- Handle loss events (timeout, duplicate ACKs)
- Calculate sending rate

Does NOT handle:
- Window management (that's SlidingWindow's job)
- Flow control (that's FlowController's job)
- Actual packet sending (that's RUDPSession's job)

Algorithm:
- Slow Start: cwnd grows exponentially (doubles per RTT)
- Congestion Avoidance: cwnd grows linearly (1 packet per RTT)
- Fast Recovery: halve cwnd on duplicate ACKs
- Timeout: reset cwnd to initial value
"""

from enum import Enum
from dataclasses import dataclass


# ══════════════════════════════════════════════════════════════════════════════
# CONGESTION CONTROL STATE
# ══════════════════════════════════════════════════════════════════════════════

class CongestionState(Enum):
    """Congestion control states (TCP Reno)."""
    SLOW_START = "slow_start"
    CONGESTION_AVOIDANCE = "congestion_avoidance"
    FAST_RECOVERY = "fast_recovery"


@dataclass
class CongestionMetrics:
    """Metrics for congestion control analysis."""
    current_cwnd: float
    ssthresh: float
    state: CongestionState
    packets_since_increase: int
    total_increases: int
    total_decreases: int
    min_cwnd: float
    max_cwnd: float
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "current_cwnd": self.current_cwnd,
            "ssthresh": self.ssthresh,
            "state": self.state.value,
            "packets_since_increase": self.packets_since_increase,
            "total_increases": self.total_increases,
            "total_decreases": self.total_decreases,
            "min_cwnd": self.min_cwnd,
            "max_cwnd": self.max_cwnd,
        }


# ══════════════════════════════════════════════════════════════════════════════
# CONGESTION CONTROLLER
# ══════════════════════════════════════════════════════════════════════════════

class CongestionController:
    """
    TCP Reno-style congestion control.
    
    Manages the congestion window (cwnd) which limits how many packets
    can be in flight at once. Adapts to network congestion.
    
    States:
    1. Slow Start: Exponential growth (cwnd doubles per RTT)
       - Used at connection start and after timeout
       - Fast ramp-up to find available bandwidth
    
    2. Congestion Avoidance: Linear growth (cwnd += 1 per RTT)
       - Used when cwnd >= ssthresh
       - Conservative increase to avoid congestion
    
    3. Fast Recovery: Temporary state after duplicate ACKs
       - Triggered by 3 duplicate ACKs
       - Halves cwnd but stays in congestion avoidance
    
    Loss Events:
    - Duplicate ACKs (3+): Halve ssthresh, enter fast recovery
    - Timeout: Reset cwnd to initial, restart slow start
    """
    
    def __init__(
        self,
        initial_cwnd: float = 2.0,
        initial_ssthresh: float = 64.0,
        min_cwnd: float = 1.0,
        max_cwnd: float = 1000.0,
    ):
        """
        Initialize congestion controller.
        
        Args:
            initial_cwnd: Initial congestion window (packets)
            initial_ssthresh: Initial slow-start threshold (packets)
            min_cwnd: Minimum cwnd (packets)
            max_cwnd: Maximum cwnd (packets)
            
        Example:
            >>> controller = CongestionController(
            ...     initial_cwnd=4.0,
            ...     initial_ssthresh=32.0
            ... )
        """
        self._initial_cwnd = float(initial_cwnd)
        self._initial_ssthresh = float(initial_ssthresh)
        self._min_cwnd = float(min_cwnd)
        self._max_cwnd = float(max_cwnd)
        
        # Current state
        self._cwnd = float(initial_cwnd)
        self._ssthresh = float(initial_ssthresh)
        self._state = CongestionState.SLOW_START
        
        # Statistics
        self._packets_since_increase = 0
        self._total_increases = 0
        self._total_decreases = 0
        self._min_cwnd_seen = self._cwnd
        self._max_cwnd_seen = self._cwnd
    
    # ── Properties ───────────────────────────────────────────────────────
    
    @property
    def cwnd(self) -> float:
        """Current congestion window size (packets)."""
        return self._cwnd
    
    @property
    def ssthresh(self) -> float:
        """Current slow-start threshold (packets)."""
        return self._ssthresh
    
    @property
    def state(self) -> CongestionState:
        """Current congestion control state."""
        return self._state
    
    def get_sending_limit(self) -> int:
        """
        Get maximum packets that can be sent.
        
        Returns:
            Integer number of packets allowed in flight
            
        Example:
            >>> limit = controller.get_sending_limit()
            >>> print(f"Can send up to {limit} packets")
        """
        return int(self._cwnd)
    
    # ── State Transitions ────────────────────────────────────────────────
    
    def _update_state(self) -> None:
        """Update state based on cwnd and ssthresh."""
        if self._cwnd < self._ssthresh:
            self._state = CongestionState.SLOW_START
        else:
            self._state = CongestionState.CONGESTION_AVOIDANCE
    
    # ── ACK Processing (Increase Window) ─────────────────────────────────
    
    def on_ack_received(self) -> None:
        """
        Process ACK - increase cwnd according to current state.
        
        Slow Start: cwnd += 1 (doubles per RTT)
        Congestion Avoidance: cwnd += 1/cwnd (increases by 1 per RTT)
        
        Example:
            >>> controller.on_ack_received()
            >>> print(f"New cwnd: {controller.cwnd}")
        """
        self._packets_since_increase += 1
        
        if self._state == CongestionState.SLOW_START:
            # Exponential growth: cwnd += 1 per ACK
            self._cwnd += 1.0
            self._total_increases += 1
        
        elif self._state == CongestionState.CONGESTION_AVOIDANCE:
            # Linear growth: cwnd += 1/cwnd per ACK (1 per RTT)
            self._cwnd += 1.0 / self._cwnd
            self._total_increases += 1
        
        # Enforce maximum
        self._cwnd = min(self._cwnd, self._max_cwnd)
        
        # Update statistics
        self._max_cwnd_seen = max(self._max_cwnd_seen, self._cwnd)
        
        # Update state
        self._update_state()
    
    # ── Loss Events (Decrease Window) ────────────────────────────────────
    
    def on_duplicate_ack(self) -> None:
        """
        Process duplicate ACK (fast retransmit trigger).
        
        TCP Reno behavior:
        - ssthresh = max(cwnd / 2, 2)
        - cwnd = ssthresh
        - Enter congestion avoidance (skip slow start)
        
        Example:
            >>> # After 3 duplicate ACKs
            >>> controller.on_duplicate_ack()
            >>> print(f"Reduced cwnd to {controller.cwnd}")
        """
        # Halve ssthresh (but not below 2)
        self._ssthresh = max(self._cwnd / 2.0, 2.0)
        
        # Set cwnd to ssthresh (multiplicative decrease)
        self._cwnd = self._ssthresh
        
        # Enter congestion avoidance (skip slow start)
        self._state = CongestionState.CONGESTION_AVOIDANCE
        
        # Update statistics
        self._total_decreases += 1
        self._min_cwnd_seen = min(self._min_cwnd_seen, self._cwnd)
    
    def on_timeout(self) -> None:
        """
        Process timeout (severe congestion signal).
        
        TCP Reno behavior:
        - ssthresh = max(cwnd / 2, 2)
        - cwnd = initial_cwnd (restart slow start)
        - Enter slow start
        
        More severe than duplicate ACKs because timeout indicates
        complete loss of ACKs (not just one dropped packet).
        
        Example:
            >>> # Packet timed out
            >>> controller.on_timeout()
            >>> print(f"Reset cwnd to {controller.cwnd}")
        """
        # Halve ssthresh
        self._ssthresh = max(self._cwnd / 2.0, 2.0)
        
        # Reset cwnd to initial (severe penalty)
        self._cwnd = self._initial_cwnd
        
        # Restart slow start
        self._state = CongestionState.SLOW_START
        
        # Update statistics
        self._total_decreases += 1
        self._min_cwnd_seen = min(self._min_cwnd_seen, self._cwnd)
    
    # ── Manual Control (Advanced) ────────────────────────────────────────
    
    def set_cwnd(self, new_cwnd: float) -> None:
        """
        Manually set cwnd (for testing or special cases).
        
        Args:
            new_cwnd: New congestion window size
            
        Example:
            >>> controller.set_cwnd(10.0)
        """
        self._cwnd = max(self._min_cwnd, min(new_cwnd, self._max_cwnd))
        self._update_state()
    
    def set_ssthresh(self, new_ssthresh: float) -> None:
        """
        Manually set ssthresh (for testing or special cases).
        
        Args:
            new_ssthresh: New slow-start threshold
            
        Example:
            >>> controller.set_ssthresh(16.0)
        """
        self._ssthresh = max(2.0, new_ssthresh)
        self._update_state()
    
    def reset(self) -> None:
        """
        Reset to initial state.
        
        Example:
            >>> controller.reset()
        """
        self._cwnd = self._initial_cwnd
        self._ssthresh = self._initial_ssthresh
        self._state = CongestionState.SLOW_START
        self._packets_since_increase = 0
    
    # ── Metrics & Monitoring ─────────────────────────────────────────────
    
    def get_metrics(self) -> CongestionMetrics:
        """
        Get congestion control metrics.
        
        Returns:
            CongestionMetrics object
            
        Example:
            >>> metrics = controller.get_metrics()
            >>> print(f"State: {metrics.state.value}")
            >>> print(f"cwnd: {metrics.current_cwnd:.2f}")
        """
        return CongestionMetrics(
            current_cwnd=self._cwnd,
            ssthresh=self._ssthresh,
            state=self._state,
            packets_since_increase=self._packets_since_increase,
            total_increases=self._total_increases,
            total_decreases=self._total_decreases,
            min_cwnd=self._min_cwnd_seen,
            max_cwnd=self._max_cwnd_seen,
        )
    
    def get_state_name(self) -> str:
        """
        Get human-readable state name.
        
        Returns:
            State name string
            
        Example:
            >>> print(controller.get_state_name())
            "Slow Start"
        """
        names = {
            CongestionState.SLOW_START: "Slow Start",
            CongestionState.CONGESTION_AVOIDANCE: "Congestion Avoidance",
            CongestionState.FAST_RECOVERY: "Fast Recovery",
        }
        return names[self._state]
    
    def is_in_slow_start(self) -> bool:
        """Check if in slow-start phase."""
        return self._state == CongestionState.SLOW_START
    
    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"CongestionController(cwnd={self._cwnd:.2f}, "
            f"ssthresh={self._ssthresh:.2f}, state={self._state.value})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Running CongestionController self-tests...\n")
    
    # Test 1: Slow start phase
    controller = CongestionController(initial_cwnd=2.0, initial_ssthresh=16.0)
    
    assert controller.cwnd == 2.0
    assert controller.state == CongestionState.SLOW_START
    
    # Simulate 3 ACKs (slow start: cwnd doubles per RTT)
    controller.on_ack_received()  # cwnd = 3
    controller.on_ack_received()  # cwnd = 4
    controller.on_ack_received()  # cwnd = 5
    
    assert controller.cwnd == 5.0
    print("✓ Slow start (exponential growth)")
    
    # Test 2: Transition to congestion avoidance
    controller2 = CongestionController(initial_cwnd=2.0, initial_ssthresh=4.0)
    
    # Grow to ssthresh
    for _ in range(10):
        controller2.on_ack_received()
    
    # Should be in congestion avoidance now
    assert controller2.state == CongestionState.CONGESTION_AVOIDANCE
    
    # Linear growth: cwnd += 1/cwnd per ACK
    cwnd_before = controller2.cwnd
    controller2.on_ack_received()
    cwnd_after = controller2.cwnd
    
    increase = cwnd_after - cwnd_before
    assert 0 < increase < 1.0  # Should be fractional increase
    print("✓ Congestion avoidance (linear growth)")
    
    # Test 3: Duplicate ACK (fast retransmit)
    controller3 = CongestionController(initial_cwnd=2.0, initial_ssthresh=16.0)
    
    # Grow cwnd to 10
    for _ in range(15):
        controller3.on_ack_received()
    
    cwnd_before_loss = controller3.cwnd
    controller3.on_duplicate_ack()
    cwnd_after_loss = controller3.cwnd
    
    # Should halve cwnd
    assert cwnd_after_loss < cwnd_before_loss
    assert cwnd_after_loss == controller3.ssthresh
    assert controller3.state == CongestionState.CONGESTION_AVOIDANCE
    print("✓ Duplicate ACK (multiplicative decrease)")
    
    # Test 4: Timeout (severe loss)
    controller4 = CongestionController(initial_cwnd=2.0, initial_ssthresh=16.0)
    
    # Grow cwnd
    for _ in range(20):
        controller4.on_ack_received()
    
    controller4.on_timeout()
    
    # Should reset to initial cwnd
    assert controller4.cwnd == 2.0
    assert controller4.state == CongestionState.SLOW_START
    print("✓ Timeout (reset to slow start)")
    
    # Test 5: Metrics
    metrics = controller.get_metrics()
    assert metrics.current_cwnd == controller.cwnd
    assert metrics.state == controller.state
    assert metrics.total_increases > 0
    print("✓ Metrics collection")
    
    # Test 6: Manual control
    controller5 = CongestionController()
    controller5.set_cwnd(10.0)
    assert controller5.cwnd == 10.0
    
    controller5.set_ssthresh(8.0)
    assert controller5.ssthresh == 8.0
    assert controller5.state == CongestionState.CONGESTION_AVOIDANCE
    print("✓ Manual control (set_cwnd, set_ssthresh)")
    
    # Test 7: Limits enforcement
    controller6 = CongestionController(
        initial_cwnd=2.0,
        min_cwnd=1.0,
        max_cwnd=50.0
    )
    
    # Try to grow beyond max
    for _ in range(100):
        controller6.on_ack_received()
    
    assert controller6.cwnd <= 50.0
    print("✓ Enforce maximum cwnd")
    
    # Test 8: Sending limit
    controller7 = CongestionController(initial_cwnd=5.5)
    limit = controller7.get_sending_limit()
    assert limit == 5  # Should be integer
    print("✓ Sending limit (integer conversion)")
    
    print("\n✅ All CongestionController tests passed!")
    print("\nExample usage:")
    print("  controller = CongestionController()")
    print("  controller.on_ack_received()  # Increase cwnd")
    print("  controller.on_duplicate_ack()  # Halve cwnd")
    print("  controller.on_timeout()  # Reset cwnd")
    print(f"  limit = controller.get_sending_limit()  # {controller7.get_sending_limit()} packets")
