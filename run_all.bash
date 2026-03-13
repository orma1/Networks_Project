#!/usr/bin/env bash

PIDS=()

cleanup() {
    echo
    echo "=================================================="
    echo "[*] Stopping all services gracefully..."
    
    # 1. Send the termination signal to all processes
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    
    # 2. Force Bash to wait until the processes actually close
    for pid in "${PIDS[@]}"; do
        wait "$pid" 2>/dev/null
    done

    echo "[*] Done. Have a nice day!"
    echo "=================================================="
    exit
}

trap cleanup INT TERM

# Detect Operating System
OS="$(uname -s)"
case "${OS}" in
    Linux*)     MACHINE="Linux"; PYTHON_CMD="python3" ;;
    Darwin*)    MACHINE="Mac"; PYTHON_CMD="python3" ;;
    CYGWIN*|MINGW*|MSYS*) MACHINE="Windows"; PYTHON_CMD="python" ;;
    *)          MACHINE="UNKNOWN"; PYTHON_CMD="python" ;;
esac

echo "=================================================="
echo "[*] Initiating Environment Setup..."
echo "[*] Detected OS: $MACHINE"
echo "[*] Using Python command: $PYTHON_CMD"
echo "[*] Adding loopback aliases for 127.0.0.2 to 127.0.0.20..."

if [[ "$MACHINE" == "Mac" ]]; then
    for i in {2..20}; do
        sudo ifconfig lo0 alias 127.0.0.$i up
    done
elif [[ "$MACHINE" == "Linux" ]]; then
    for i in {2..20}; do
        sudo ip addr add 127.0.0.$i/8 dev lo 2>/dev/null
    done
elif [[ "$MACHINE" == "Windows" ]]; then
    echo "[!] Note: Adding loopbacks on native Windows requires Administrator privileges."
    for i in {2..20}; do
        netsh interface ipv4 add address "Loopback Pseudo-Interface 1" 127.0.0.$i 255.0.0.0 2>/dev/null
    done
fi

echo "[*] Loopbacks configured successfully."
echo "=================================================="
echo

# 1. DHCP Infrastructure
echo "[*] [1/3] Booting DHCP Server..."
$PYTHON_CMD dhcp_server.py &
PIDS+=($!)
# Give DHCP a moment to initialize and print its active port
sleep 1.5 
echo

# 2. Main DNS Infrastructure
echo "[*] [2/3] Booting Main DNS Infrastructure (Root, TLD, Auth, Resolver)..."
$PYTHON_CMD main.py &
PIDS+=($!)
# Main requires a longer pause to boot 4 distinct servers and print their zones
sleep 4 
echo

# 3. Unified Services
echo "[*] [3/3] Booting Unified Services (Multi-Protocol Mode)..."
if [[ -d "Server_Proxy" ]]; then
    pushd Server_Proxy >/dev/null

    echo "[*] Launching Origin Server..."
    $PYTHON_CMD server_app.py &
    PIDS+=($!)
    # Pause to let Origin get its DHCP lease and start listening
    sleep 2 

    echo "[*] Launching Proxy Node..."
    $PYTHON_CMD proxy_app.py &
    PIDS+=($!)
    
    popd >/dev/null
else
    echo "[!] Error: Server_Proxy folder not found!"
fi

# Give the proxy a moment to do its iterative DNS jump and boot FastAPI
sleep 3

echo
echo "=================================================="
echo "[*] BASH CONTROLLER: ALL SYSTEMS NOMINAL"
echo "[*] Dashboard Access: http://localhost:5000"
echo "[*] Awaiting traffic... (Press CTRL+C to stop)"
echo "=================================================="

# Keep script alive efficiently
wait