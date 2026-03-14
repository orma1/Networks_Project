import time
import threading
import pickle
import os
from collections import OrderedDict
from typing import Optional, Tuple, Dict
from dnslib import DNSRecord


class DNSCache:
    def __init__(self, filename: str = "cache.db", capacity: int = 1000, save_interval: int = 5) -> None:
        """
        :param filename: Where to save the cache on disk.
        :param capacity: Max items in memory.
        :param save_interval: How often (in seconds) to check for autosave.
        """
        self.capacity: int = capacity
        self.filename: str = filename
        self.save_interval: int = save_interval
        
        self._store: OrderedDict[Tuple[str, int], Tuple[DNSRecord, float]] = OrderedDict()
        
        # --- THREADING LOCKS ---
        self._lock: threading.Lock = threading.Lock()     # Protects the in-memory dictionary
        self._io_lock: threading.Lock = threading.Lock()  # Protects the disk to prevent overlapping writes
        
        # Persistence flags
        self._dirty: bool = False  # Has the cache changed since last save
        self._running: bool = True
        
        # Load existing data immediately
        self._load_from_disk()

        # Start the Background Saver Thread
        self._saver_thread = threading.Thread(target=self._auto_save_loop, daemon=True)
        self._saver_thread.start()

    def _get_key(self, qname: str, qtype: int) -> Tuple[str, int]:
        return (str(qname), int(qtype))

    def get(self, qname: str, qtype: int) -> Optional[DNSRecord]:
        key = self._get_key(qname, qtype)
        
        with self._lock:
            if key not in self._store:
                return None
            
            record, expiration = self._store[key]
            
            if time.time() > expiration:
                del self._store[key]
                self._dirty = True  # Deletion changes state
                return None
            
            self._store.move_to_end(key)
            return record

    def put(self, qname: str, qtype: int, record: DNSRecord, ttl: float) -> None:
        key = self._get_key(qname, qtype)
        expiration = time.time() + ttl
        
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            
            self._store[key] = (record, expiration)
            
            if len(self._store) > self.capacity:
                self._store.popitem(last=False)
            
            # Mark as dirty so the background thread knows to save
            self._dirty = True

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    # --- PERSISTENCE LOGIC ---

    def _load_from_disk(self) -> None:
        """Called only on startup."""
        if not os.path.exists(self.filename):
            return
            
        try:
            with open(self.filename, 'rb') as f:
                data = pickle.load(f)
                
                # Cleanup: Filter out expired items immediately on load
                now = time.time()
                valid_data = OrderedDict()
                for key, (record, exp) in data.items():
                    if exp > now:
                        valid_data[key] = (record, exp)
                
                with self._lock:
                    self._store = valid_data
                    
            print(f"[*] Loaded {len(self._store)} records from {self.filename}")
        except Exception as e:
            print(f"[!] Failed to load cache: {e}")

    def _save_to_disk(self) -> None:
        with self._io_lock:
            with self._lock:
                if not self._dirty:
                    return 
                
                snapshot = self._store.copy()
                self._dirty = False # Reset flag 
            
            try:
                # 1. Write to a temporary file first (ATOMIC WRITE)
                temp_filename = f"{self.filename}.tmp"
                with open(temp_filename, 'wb') as f:
                    pickle.dump(snapshot, f)
                
                os.replace(temp_filename, self.filename)
                
            except Exception as e:
                # If the disk write fails, we MUST restore the dirty flag 
                # so the data isn't silently lost forever.
                with self._lock:
                    self._dirty = True
                print(f"[!] Auto-save failed: {e}")

    def _auto_save_loop(self) -> None:
        """Background worker function."""
        while self._running:
            time.sleep(self.save_interval)
            self._save_to_disk()

    def stop(self) -> None:
        """Call this on server shutdown to ensure final save."""
        self._running = False
        
        self._save_to_disk()