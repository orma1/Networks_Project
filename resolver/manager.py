import time
import signal
import sys
import os

from config_loader import ConfigLoader
from resolver import Resolver

class ResolverManager:
    """
    Orchestrates the Resolver application lifecycle.
    Prevents multiple instances and manages all subsystem threads (DNS, API, etc.)
    """
    def __init__(self, config_filename="resolver_config.yaml"):
        print("[*] Booting Resolver Manager...")
        
        # 1. Load Config Once
        self.config = ConfigLoader(config_filename)
        
        # 2. Inject Config into exactly ONE Resolver instance
        self.resolver = Resolver(self.config)
        
        # 3. Future Placeholder: self.api_server = APIServer(self.resolver)
        
        self._shutdown_requested = False

    def start_all(self):
        """ Starts all child services and holds the main thread open. """
        try:
            print(f"\n{'='*40}")
            print(f"[*] DNS Resolver Online")
            print(f"[*] Cache Path: {self.config.cache_file_path}")
            print(f"{'='*40}\n")
            
            # Start subsystems
            self.resolver.start()
            # self.api_server.start()
            
            # The Main Thread Keep-Alive Loop
            while not self._shutdown_requested:
                time.sleep(0.5)
                
        except Exception as e:
            print(f"[FATAL] ResolverManager crashed: {e}")
            self.stop_all()

    def stop_all(self):
        """ Gracefully shuts down all child services. """
        if self._shutdown_requested:
            return # Prevent double-shutdown
            
        print("\n[*] ResolverManager initiating shutdown sequence...")
        self._shutdown_requested = True
        
        self.resolver.stop()
        # self.api_server.stop()
        
        print("[*] All subsystems offline. Goodbye.")
        sys.exit(0)

# --- GLOBAL SIGNAL HANDLING ---
app_manager = None

def handle_sigint(sig, frame):
    if app_manager:
        app_manager.stop_all()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, handle_sigint)
    
    app_manager = ResolverManager()
    app_manager.start_all()