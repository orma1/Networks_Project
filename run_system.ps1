$processes = @()

function Cleanup {
    Write-Host "`n[*] Gracefully shutting down services (reverse order)..." -ForegroundColor Yellow
    
    # Order of shutdown: Proxy -> Server -> DNS -> DHCP
    
    Write-Host "`n[SHUTDOWN] Step 1/4: Stopping Proxy (releases IP)..." -ForegroundColor Cyan
    if ($processes.Count -ge 4) {
        Stop-Process -Id $processes[3].Id -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    
    Write-Host "[SHUTDOWN] Step 2/4: Stopping Origin Server (releases IP)..." -ForegroundColor Cyan
    if ($processes.Count -ge 3) {
        Stop-Process -Id $processes[2].Id -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    
    Write-Host "[SHUTDOWN] Step 3/4: Stopping DNS..." -ForegroundColor Cyan
    if ($processes.Count -ge 2) {
        Stop-Process -Id $processes[1].Id -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    
    Write-Host "[SHUTDOWN] Step 4/4: Stopping DHCP Server (last)..." -ForegroundColor Cyan
    if ($processes.Count -ge 1) {
        Stop-Process -Id $processes[0].Id -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    
    foreach ($p in $processes) {
        try {
            if ($null -ne (Get-Process -Id $p.Id -ErrorAction SilentlyContinue)) {
                Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            }
        } catch { }
    }
    
    Write-Host "[*] All services stopped gracefully" -ForegroundColor Green
    exit
}

# Use the absolute path for the script root
$PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$env:PYTHONPATH = $PSScriptRoot

# Clean string repetition
$line = "=" * 70
Write-Host "`n$line" -ForegroundColor Cyan
Write-Host "UNIFIED STREAMING SYSTEM - Startup with Graceful Shutdown" -ForegroundColor Cyan
Write-Host "$line" -ForegroundColor Cyan

# 1. DHCP Server
Write-Host "`n[STARTUP] Step 1/4: Starting DHCP Server..." -ForegroundColor Yellow
$dhcpProc = Start-Process python -ArgumentList "dhcp/dhcp_server.py" -PassThru -NoNewWindow
$processes += $dhcpProc
Write-Host "  v DHCP Server started (PID: $($dhcpProc.Id))" -ForegroundColor Green
Start-Sleep -Seconds 2

# 2. DNS Server
Write-Host "[STARTUP] Step 2/4: Starting DNS Server..." -ForegroundColor Yellow
$dnsProc = Start-Process python -ArgumentList "DNS/main.py" -PassThru -NoNewWindow
$processes += $dnsProc
Write-Host "  v DNS Server started (PID: $($dnsProc.Id))" -ForegroundColor Green
Start-Sleep -Seconds 1

# 3. Origin Server
Write-Host "[STARTUP] Step 3/4: Starting Origin Server..." -ForegroundColor Yellow
if (Test-Path "Application") {
    $serverProc = Start-Process python -ArgumentList "Application/server/server_app.py" -PassThru -NoNewWindow
    $processes += $serverProc
    Write-Host "  v Origin Server started (PID: $($serverProc.Id))" -ForegroundColor Green
    Start-Sleep -Seconds 1
} else {
    Write-Host "[!] Error: Application folder not found!" -ForegroundColor Red
    exit 1
}

# 4. Proxy
Write-Host "[STARTUP] Step 4/4: Starting Proxy..." -ForegroundColor Yellow
if (Test-Path "Application") {
    $proxyProc = Start-Process python -ArgumentList "Application/client/proxy_app.py" -PassThru -NoNewWindow
    $processes += $proxyProc
    Write-Host "  v Proxy started (PID: $($proxyProc.Id))" -ForegroundColor Green
    Start-Sleep -Seconds 1
} else {
    Write-Host "[!] Error: Application folder not found!" -ForegroundColor Red
    exit 1
}

Write-Host "`n$line" -ForegroundColor Green
Write-Host "[v] SYSTEM RUNNING - All 4 services started successfully" -ForegroundColor Green
Write-Host "$line" -ForegroundColor Green
Write-Host "`n  Component Summary:" -ForegroundColor White
Write-Host "    1. DHCP Server    -> Assigns IPs to clients" -ForegroundColor Gray
Write-Host "    2. DNS Server     -> Resolves domain names" -ForegroundColor Gray
Write-Host "    3. Origin Server  -> Streams video files" -ForegroundColor Gray
Write-Host "    4. Proxy          -> Edge caching '&' load balancing" -ForegroundColor Gray
Write-Host "`n  Access the system at: http://127.0.0.30:5000" -ForegroundColor Cyan
Write-Host "`n  Shutdown Instructions:" -ForegroundColor White
Write-Host "    Press CTRL+C to gracefully stop all services" -ForegroundColor Yellow

# Main Loop
try {
    while($true) { 
        Start-Sleep -Seconds 1 
    }
} 
catch {
    # Handled by finally block
}
finally {
    Cleanup
}