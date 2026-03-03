$processes = @()

function Cleanup {
    Write-Host "`n[*] Stopping all services..." -ForegroundColor Yellow
    foreach ($p in $processes) {
        Stop-Process -Id $p.Id -ErrorAction SilentlyContinue
    }
    exit
}

# 1. DHCP & Main
Write-Host "[1/4] Starting DHCP and Main..." -ForegroundColor Cyan
$processes += Start-Process python -ArgumentList "dhcp_server.py" -PassThru -NoNewWindow
$processes += Start-Process python -ArgumentList "main.py" -PassThru -NoNewWindow
Start-Sleep -Seconds 2

# 2. Protocol
sleep 3
$protocol = Read-Host "[2/4] Enter protocol (rudp or tcp)"
$protocol = $protocol.ToLower().Trim()

# 3. Server & Proxy
Write-Host "[3/4] Starting Unified Services..." -ForegroundColor Cyan
Set-Location "Server_Proxy"
$processes += Start-Process python -ArgumentList "unified_server.py --protocol $protocol" -PassThru -NoNewWindow
$processes += Start-Process python -ArgumentList "unified_proxy.py --protocol $protocol" -PassThru -NoNewWindow
Set-Location ".."

Write-Host "------------------------------------------"
Write-Host "[*] SYSTEM RUNNING. Press CTRL+C to stop." -ForegroundColor Green

# Keep script alive for CTRL+C
try {
    while($true) { Start-Sleep -Seconds 1 }
} finally {
    Cleanup
}