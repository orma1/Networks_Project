$processes = @()

function Cleanup {
    Write-Host "`n[*] Stopping all services..." -ForegroundColor Yellow
    foreach ($p in $processes) {
        # Using -Force ensures the background Python windows actually close
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[*] Done. Have a nice day!" -ForegroundColor Green
    exit
}

# Set PYTHONPATH to project root so 'Application.*' imports resolve
$env:PYTHONPATH = $PSScriptRoot

# 1. DHCP & Main Infrastructure
Write-Host "[1/3] Starting DHCP and Main..." -ForegroundColor Cyan
$processes += Start-Process python -ArgumentList "dhcp/dhcp_server.py" -PassThru -NoNewWindow
$processes += Start-Process python -ArgumentList "DNS/main.py" -PassThru -NoNewWindow
Start-Sleep -Seconds 2

# 2. Unified Services (Server & Proxy)
Write-Host "[2/3] Starting Unified Services (Multi-Protocol Mode)..." -ForegroundColor Cyan
if (Test-Path "Application") {
    # We no longer pass --protocol because the Python code handles it internally
    $processes += Start-Process python -ArgumentList "Application/server/server_app.py" -PassThru -NoNewWindow
    $processes += Start-Process python -ArgumentList "Application/client/proxy_app.py" -PassThru -NoNewWindow
} else {
    Write-Host "[!] Error: Application folder not found!" -ForegroundColor Red
}

Write-Host "------------------------------------------"
Write-Host "[*] SYSTEM RUNNING." -ForegroundColor Green
Write-Host "[*] Access the Library at http://localhost:5000" -ForegroundColor White
Write-Host "[*] Press CTRL+C to stop all services." -ForegroundColor Green
Write-Host "------------------------------------------"

# Keep script alive for CTRL+C to trigger the finally block
try {
    while($true) { Start-Sleep -Seconds 1 }
} 
catch {
    # This catches the CTRL+C interrupt
}
finally {
    Cleanup
}