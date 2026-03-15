"""
SLIDING WINDOW MANAGER
═════════════════════════════════════════════════════════════

Manages the sliding window for reliable data transfer.

Responsibilities:
- Track packets in flight
- Handle ACKs and slide window forward
- Detect duplicate ACKs
- Identify packets needing retransmission

Does NOT handle:
- Congestion control (that's CongestionController's job)
- Flow control (that's FlowController's job)
- Actual packet sending (that's RUDPSession's job)
"""

import time
from typing import Dict, Optional, Set, Tuple
from dataclasses import dataclass, field
from Application.shared.protocol_utils import seq_less_than, seq_less_equal


# ══════════════════════════════════════════════════════════════════════════════
# WINDOW ENTRY
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class WindowEntry:
    """
    One packet in the sliding window.
    
    Tracks:
    - Packet data (for retransmission)
    - Send timestamp (for timeout detection)
    - ACK status
    """
    data: bytes
    timestamp: float
    acked: bool = False
    retransmit_count: int = 0
    
    def age(self) -> float:
        """Get packet age in seconds."""
        return time.monotonic() - self.timestamp
    
    def mark_retransmitted(self) -> None:
        """Mark packet as retransmitted and update timestamp."""
        self.retransmit_count += 1
        self.timestamp = time.monotonic()


# ══════════════════════════════════════════════════════════════════════════════
# SLIDING WINDOW
# ══════════════════════════════════════════════════════════════════════════════

class SlidingWindow:
    """
    Manages sliding window for reliable packet delivery.
    
    Uses selective repeat ARQ (Automatic Repeat reQuest):
    - Receiver can ACK packets out of order
    - Sender retransmits only lost packets
    - Window slides when consecutive ACKs received
    
    Window state:
    
        base                                next_seq
        ↓                                   ↓
        [ACKED][ACKED][IN-FLIGHT][IN-FLIGHT][READY]
        └─────────────────┬────────────────┘
                    Window Size
    
    - base: Oldest un-ACKed sequence number
    - next_seq: Next sequence to assign
    - Window contains [base, next_seq)
    """
    
    def __init__(self, initial_base: int = 0):
        """
        Initialize sliding window.
        
        Args:
            initial_base: Starting sequence number (default 0)
        """
        self._window: Dict[int, WindowEntry] = {}
        self._base: int = initial_base
        self._next_seq: int = initial_base
        
        # Duplicate ACK tracking (for fast retransmit)
        self._last_new_ack: int = -1
        self._dup_count: int = 0
        
        # Statistics
        self._total_acked: int = 0
        self._total_retransmitted: int = 0
        self._max_window_size: int = 0
    
    # ── Window State Properties ──────────────────────────────────────────
    
    @property
    def base(self) -> int:
        """Oldest un-ACKed sequence number."""
        return self._base
    
    @property
    def next_seq(self) -> int:
        """Next sequence number to assign."""
        return self._next_seq
    
    @property
    def size(self) -> int:
        """Current number of packets in window."""
        return len(self._window)
    
    @property
    def max_size_seen(self) -> int:
        """Maximum window size reached."""
        return self._max_window_size
    
    def packets_in_flight(self) -> int:
        """Count un-ACKed packets."""
        return sum(1 for entry in self._window.values() if not entry.acked)
    
    def is_empty(self) -> bool:
        """Check if window is empty."""
        return len(self._window) == 0
    
    def has_capacity(self, limit: int) -> bool:
        """
        Check if window has capacity for more packets.
        
        Args:
            limit: Maximum packets allowed in flight
            
        Returns:
            True if can add more packets
        """
        return self.packets_in_flight() < limit
    
    # ── Window Operations ────────────────────────────────────────────────
    
    def add_packet(self, packet_data: bytes) -> int:
        """
        Add packet to window and return assigned sequence number.
        
        Args:
            packet_data: Packet bytes to track
            
        Returns:
            Assigned sequence number
            
        Example:
            >>> window = SlidingWindow()
            >>> seq = window.add_packet(b"Hello")
            >>> print(seq)
            0
        """
        seq = self._next_seq
        
        self._window[seq] = WindowEntry(
            data=packet_data,
            timestamp=time.monotonic(),
            acked=False
        )
        
        # Advance next_seq with wraparound
        self._next_seq = (self._next_seq + 1) & 0xFFFFFFFF
        
        # Update statistics
        self._max_window_size = max(self._max_window_size, len(self._window))
        
        return seq
    
    def mark_acked(self, seq: int) -> bool:
        """
        Mark packet as ACKed.
        
        Args:
            seq: Sequence number to ACK
            
        Returns:
            True if this is a new ACK (was not already ACKed)
            False if duplicate ACK or seq not in window
            
        Example:
            >>> window.mark_acked(0)
            True
            >>> window.mark_acked(0)  # Duplicate
            False
        """
        if seq not in self._window:
            return False
        
        entry = self._window[seq]
        
        # Already ACKed - this is a duplicate
        if entry.acked:
            return False
        
        # New ACK
        entry.acked = True
        self._total_acked += 1
        
        return True
    
    def slide_window(self) -> int:
        """
        Slide window forward, removing consecutive ACKed packets from base.
        
        Returns:
            Number of packets removed (window slid by this amount)
            
        Example:
            >>> # ACK packets 0, 1, 2
            >>> window.mark_acked(0)
            >>> window.mark_acked(1)
            >>> window.mark_acked(2)
            >>> removed = window.slide_window()
            >>> print(removed)
            3
        """
        removed = 0
        
        while self._base in self._window and self._window[self._base].acked:
            del self._window[self._base]
            self._base = (self._base + 1) & 0xFFFFFFFF
            removed += 1
        
        return removed
    
    def get_packet(self, seq: int) -> Optional[bytes]:
        """
        Get packet data for retransmission.
        
        Args:
            seq: Sequence number
            
        Returns:
            Packet data or None if not in window
            
        Example:
            >>> packet = window.get_packet(5)
            >>> if packet:
            ...     send(packet)
        """
        if seq not in self._window:
            return None
        return self._window[seq].data
    
    # ── Duplicate ACK Detection ─────────────────────────────────────────
    
    def process_ack(self, seq: int) -> Tuple[bool, int]:
        """
        Process ACK and detect duplicates (for fast retransmit).
        
        Args:
            seq: ACKed sequence number
            
        Returns:
            Tuple of (is_new_ack, duplicate_count)
            
        Example:
            >>> is_new, dup_count = window.process_ack(5)
            >>> if dup_count >= 3:
            ...     # Fast retransmit
            ...     retransmit_packet(window.base)
        """
        is_new = self.mark_acked(seq)
        
        if is_new:
            # New ACK - reset duplicate counter
            self._last_new_ack = seq
            self._dup_count = 0
            return True, 0
        else:
            # Duplicate ACK
            if seq == self._last_new_ack:
                self._dup_count += 1
            return False, self._dup_count
    
    # ── Timeout Detection ───────────────────────────────────────────────
    
    def get_timed_out_packets(self, timeout_seconds: float) -> list:
        """
        Get packets that have timed out (need retransmission).
        
        Args:
            timeout_seconds: Timeout threshold (RTO)
            
        Returns:
            List of (seq, packet_data) tuples for timed-out packets
            
        Example:
            >>> timed_out = window.get_timed_out_packets(timeout_seconds=0.5)
            >>> for seq, packet in timed_out:
            ...     retransmit(packet)
            ...     window.mark_retransmitted(seq)
        """
        now = time.monotonic()
        timed_out = []
        
        for seq, entry in self._window.items():
            if not entry.acked and (now - entry.timestamp) > timeout_seconds:
                timed_out.append((seq, entry.data))
        
        return timed_out
    
    def mark_retransmitted(self, seq: int) -> None:
        """
        Mark packet as retransmitted (updates timestamp).
        
        Args:
            seq: Sequence number that was retransmitted
            
        Example:
            >>> window.mark_retransmitted(5)
        """
        if seq in self._window:
            self._window[seq].mark_retransmitted()
            self._total_retransmitted += 1
    
    # ── Window Queries ──────────────────────────────────────────────────
    
    def get_unacked_seqs(self) -> Set[int]:
        """
        Get set of un-ACKed sequence numbers.
        
        Returns:
            Set of sequence numbers waiting for ACK
            
        Example:
            >>> unacked = window.get_unacked_seqs()
            >>> print(f"{len(unacked)} packets waiting for ACK")
        """
        return {seq for seq, entry in self._window.items() if not entry.acked}
    
    def get_oldest_unacked_age(self) -> Optional[float]:
        """
        Get age of oldest un-ACKed packet.
        
        Returns:
            Age in seconds, or None if all ACKed
            
        Example:
            >>> age = window.get_oldest_unacked_age()
            >>> if age and age > 5.0:
            ...     print("Oldest packet waiting 5+ seconds!")
        """
        oldest_age = None
        
        for entry in self._window.values():
            if not entry.acked:
                age = entry.age()
                if oldest_age is None or age > oldest_age:
                    oldest_age = age
        
        return oldest_age
    
    # ── Statistics ──────────────────────────────────────────────────────
    
    def get_statistics(self) -> dict:
        """
        Get window statistics.
        
        Returns:
            Dictionary of statistics
            
        Example:
            >>> stats = window.get_statistics()
            >>> print(f"ACKed: {stats['total_acked']}")
            >>> print(f"Retransmitted: {stats['total_retransmitted']}")
        """
        return {
            "base": self._base,
            "next_seq": self._next_seq,
            "current_size": len(self._window),
            "max_size": self._max_window_size,
            "packets_in_flight": self.packets_in_flight(),
            "total_acked": self._total_acked,
            "total_retransmitted": self._total_retransmitted,
            "duplicate_ack_count": self._dup_count,
        }
    
    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"SlidingWindow(base={self._base}, next={self._next_seq}, "
            f"size={len(self._window)}, in_flight={self.packets_in_flight()})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Running SlidingWindow self-tests...\n")
    
    # Test 1: Basic operations
    window = SlidingWindow()
    
    # Add packets
    seq0 = window.add_packet(b"packet0")
    seq1 = window.add_packet(b"packet1")
    seq2 = window.add_packet(b"packet2")
    
    assert seq0 == 0
    assert seq1 == 1
    assert seq2 == 2
    assert window.size == 3
    print("✓ Add packets")
    
    # ACK packets
    assert window.mark_acked(0) == True
    assert window.mark_acked(0) == False  # Duplicate
    assert window.mark_acked(1) == True
    print("✓ Mark ACKed (detect duplicates)")
    
    # Slide window
    removed = window.slide_window()
    assert removed == 2  # Removed packets 0 and 1
    assert window.base == 2
    assert window.size == 1  # Only packet 2 remains
    print("✓ Slide window")
    
    # Test 2: Duplicate ACK detection
    window2 = SlidingWindow()
    window2.add_packet(b"p0")
    window2.add_packet(b"p1")
    window2.add_packet(b"p2")
    
    is_new, dup_count = window2.process_ack(0)
    assert is_new == True and dup_count == 0
    
    is_new, dup_count = window2.process_ack(0)
    assert is_new == False and dup_count == 1
    
    is_new, dup_count = window2.process_ack(0)
    assert is_new == False and dup_count == 2
    
    is_new, dup_count = window2.process_ack(0)
    assert is_new == False and dup_count == 3  # Trigger fast retransmit
    print("✓ Duplicate ACK detection (fast retransmit)")
    
    # Test 3: Timeout detection
    import time
    window3 = SlidingWindow()
    window3.add_packet(b"timeout_test")
    
    time.sleep(0.1)
    timed_out = window3.get_timed_out_packets(timeout_seconds=0.05)
    assert len(timed_out) == 1
    print("✓ Timeout detection")
    
    # Test 4: Capacity check
    window4 = SlidingWindow()
    for i in range(5):
        window4.add_packet(f"packet{i}".encode())
    
    assert window4.has_capacity(limit=10) == True
    assert window4.has_capacity(limit=3) == False
    print("✓ Capacity check")
    
    # Test 5: Statistics
    stats = window.get_statistics()
    assert "base" in stats
    assert "packets_in_flight" in stats
    print("✓ Statistics")
    
    # Test 6: Sequence number wraparound
    window5 = SlidingWindow(initial_base=0xFFFFFFFE)
    seq1 = window5.add_packet(b"before_wrap")
    seq2 = window5.add_packet(b"wrapped")
    assert seq1 == 0xFFFFFFFE
    assert seq2 == 0xFFFFFFFF
    seq3 = window5.add_packet(b"after_wrap")
    assert seq3 == 0  # Wrapped around
    print("✓ Sequence number wraparound")
    
    print("\n✅ All SlidingWindow tests passed!")
    print("\nExample usage:")
    print("  window = SlidingWindow()")
    print("  seq = window.add_packet(packet_data)")
    print("  window.mark_acked(seq)")
    print("  window.slide_window()")
