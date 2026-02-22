import subprocess
import time
import sys
import os

# Define the paths to your individual server launchers
SERVICES = [
    {"name": "Root Server", "path": "root_ns/root_name_server.py"},
    {"name": "TLD Server", "path": "tld_ns/tld_name_server.py"},
    {"name": "Auth Server", "path": "auth_ns/auth_name_server.py"},
    {"name": "Resolver", "path": "resolver/manager.py"} 
]

# Keep track of running processes
active_processes = []

def start_all():
    print("=" * 50)
    print("[*] Initiating Custom DNS Infrastructure Boot Sequence...")
    print("=" * 50 + "\n")
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    for service in SERVICES:
        script_path = os.path.join(base_dir, service["path"])
        
        # Safety check
        if not os.path.exists(script_path):
            print(f"[!] ERROR: Could not find {script_path}")
            continue
            
        print(f"[*] Launching {service['name']}...")
        
        # Start the script as a separate background process
        # sys.executable ensures it uses the exact same Python environment
        process = subprocess.Popen([sys.executable, script_path])
        active_processes.append((service["name"], process))
        
        # Give each server 0.5 seconds to bind to its port before starting the next
        time.sleep(0.5) 

def stop_all():
    print("\n" + "=" * 50)
    print("[*] Master kill signal received. Shutting down infrastructure...")
    print("=" * 50)
    
    for name, process in active_processes:
        print(f"[*] Terminating {name}...")
        process.terminate() # Sends a polite shutdown signal (like Ctrl+C)
        process.wait()      # Waits for it to finish saving cache/closing sockets
        
    print("\n[*] All DNS services are offline. Goodbye!")
    sys.exit(0)

if __name__ == "__main__":
    try:
        start_all()
        
        print("\n" + "=" * 50)
        print("[*] ALL SYSTEMS ONLINE AND LISTENING")
        print("[*] Press Ctrl+C at any time to gracefully stop all servers.")
        print("=" * 50 + "\n")
        
        # The Orchestrator's only job now is to wait for you to press Ctrl+C
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        stop_all()