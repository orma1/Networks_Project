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

function Wait-For-DnsApi {
    param(
        [int]$MaxRetries = 20,
        [int]$RetryDelay = 2
    )
    
    Write-Host "[*] Waiting for DNS API to be ready..." -ForegroundColor Yellow
    
    for ($i = 1; $i -le $MaxRetries; $i++) {
        try {
            # Simple TCP port check - is port 8000 listening?
            $tcpClient = New-Object System.Net.Sockets.TcpClient
            $tcpClient.Connect("127.0.0.1", 8000)
            $tcpClient.Close()
            
            Write-Host "[*] DNS API is ready! (Port 8000 is listening)" -ForegroundColor Green
            return $true
        }
        catch {
            if ($i -lt $MaxRetries) {
                Write-Host "    [Attempt $i/$MaxRetries] Port 8000 not open, retrying in ${RetryDelay}s..." -ForegroundColor DarkGray
                Start-Sleep -Seconds $RetryDelay
            }
        }
    }
    
    Write-Host "[!] DNS API did not become ready after $MaxRetries attempts" -ForegroundColor Red
    return $false
}

# 1. DHCP & Main Infrastructure
Write-Host "[1/3] Starting DHCP and Main..." -ForegroundColor Cyan
$processes += Start-Process python -ArgumentList "dhcp/dhcp_server.py" -PassThru -NoNewWindow
$processes += Start-Process python -ArgumentList "main.py" -PassThru -NoNewWindow

# Wait for DNS API to be fully ready
Start-Sleep -Seconds 3
if (-not (Wait-For-DnsApi -MaxRetries 20 -RetryDelay 2)) {
    Write-Host "[!] Warning: DNS API not ready, proxy may fail to register" -ForegroundColor Yellow
}

# 2. Unified Services (Server & Proxy)
Write-Host "[2/3] Starting Unified Services (Multi-Protocol Mode)..." -ForegroundColor Cyan
if (Test-Path "Server_Proxy") {
    Push-Location "Server_Proxy"
    
    # Start Origin Server first
    $processes += Start-Process python -ArgumentList "server_app.py" -PassThru -NoNewWindow
    Start-Sleep -Seconds 2
    
    # Then start Proxy (now that DNS API is ready)
    $processes += Start-Process python -ArgumentList "proxy_app.py" -PassThru -NoNewWindow
    
    Pop-Location
} else {
    Write-Host "[!] Error: Server_Proxy folder not found!" -ForegroundColor Red
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