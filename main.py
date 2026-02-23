import subprocess
import time
import sys
import os
import signal
import argparse
# Define the paths to your individual server launchers
SERVICES = [
    {"name": "Root Server", "path": "root_ns/root_name_server.py"},
    {"name": "TLD Server", "path": "tld_ns/tld_name_server.py"},
    {"name": "Auth Server", "path": "auth_ns/auth_name_server.py"},
    {"name": "Resolver", "path": "resolver/manager.py"} 
]

# Keep track of running processes
active_processes = []
def start_all(args):
    print("=" * 50)
    print("[*] Initiating Custom DNS Infrastructure Boot Sequence...")
    
    mode_text = "DNSSEC SECURE MODE" if args.dnssec else "STANDARD INSECURE MODE"
    print(f"[*] Mode: {mode_text}")
    print("=" * 50)
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    for service in SERVICES:
        name = service["name"]
        script_path = os.path.join(base_dir, service["path"])
        
        # Safety check
        if not os.path.exists(script_path):
            print(f"[!] ERROR: Could not find {script_path}")
            continue
            
        print(f"\n[*] Launching {name}...")
        
        # Build the command dynamically
        cmd = [sys.executable, script_path]
        
        # Pass the flag down to the child servers!
        # Note: We exclude the Resolver here so it doesn't crash if it 
        # doesn't have an argparse block built for it yet.
        if args.dnssec:
            cmd.append('--dnssec')
            
        try:
            process = subprocess.Popen(cmd, start_new_session=True)
            active_processes.append((name, process))
        except Exception as e:
            print(f"[FATAL] Failed to start {name}: {e}")
        
        # Give each server 0.5 seconds to bind to its port before starting the next
        time.sleep(0.5)    

def stop_all():
    print("\n" + "=" * 50)
    print("[*] Master kill signal received. Shutting down infrastructure...")
    print("=" * 50)
    
    for name, process in active_processes:
        print(f"[*] Terminating {name}...")
        try:
            # Send the polite Ctrl+C signal instead of a harsh kill
            process.send_signal(signal.SIGINT)
            process.wait(timeout=3) # Give it 3 seconds to close its sockets
        except Exception as e:
            print(f"[!] Error stopping {name}: {e}")
            process.terminate() # Fallback to harsh kill if it's completely frozen
            
    print("\n[*] All DNS services are offline. Goodbye!")
    sys.exit(0)

def parse_args():
    parser = argparse.ArgumentParser(description="Custom DNS Infrastructure Master")
    parser.add_argument('--dnssec', action='store_true', help='Enable DNSSEC mode (loads .signed.zone files)')
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    try:
        user_args = parse_args()
        start_all(args=user_args)
        print("\n" + "=" * 50)
        print("[*] ALL SYSTEMS ONLINE AND LISTENING")
        print("[*] Press Ctrl+C at any time to gracefully stop all servers.")
        print("=" * 50 + "\n")
        
        # The Orchestrator's only job now is to wait for you to press Ctrl+C
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        stop_all()