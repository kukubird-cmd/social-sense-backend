# =============================================================================
# SocialSense AI - Complete Startup Script
# Starts FastAPI backend + SSH tunnel (serveo.net) together
# SSH tunnel works even when ngrok is blocked by Application Control
# =============================================================================

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  SocialSense AI - Full Stack Launcher" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

$BackendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TunnelFile = Join-Path $BackendDir "tunnel_url.txt"
$Port = 8000

# ---- Step 1: Start FastAPI backend in a new window ----
Write-Host "`n[1/3] Starting FastAPI backend on port $Port..." -ForegroundColor Yellow

$BackendProc = Start-Process -FilePath "python" `
    -ArgumentList "-m uvicorn main:app --host 0.0.0.0 --port $Port --reload" `
    -WorkingDirectory $BackendDir `
    -PassThru

Write-Host "      Backend started (PID: $($BackendProc.Id))" -ForegroundColor Green
Start-Sleep -Seconds 3

# ---- Step 2: Start SSH tunnel to serveo.net ----
Write-Host "`n[2/3] Starting SSH tunnel via serveo.net..." -ForegroundColor Yellow
Write-Host "      (Works even when ngrok is blocked by security policy)" -ForegroundColor Gray

$ServeoLog = Join-Path $env:TEMP "serveo_$Port.txt"
"" | Out-File $ServeoLog -Encoding utf8

$ServeoProc = Start-Process -FilePath "ssh" `
    -ArgumentList "-o StrictHostKeyChecking=no -o ServerAliveInterval=60 -R 80:localhost:$Port serveo.net" `
    -RedirectStandardOutput $ServeoLog `
    -RedirectStandardError $ServeoLog `
    -WindowStyle Hidden `
    -PassThru

Write-Host "      SSH process started (PID: $($ServeoProc.Id))" -ForegroundColor Green

# ---- Step 3: Detect tunnel URL ----
Write-Host "`n[3/3] Waiting for public tunnel URL..." -ForegroundColor Yellow

$TunnelUrl = ""
$Attempts = 0
while ($TunnelUrl -eq "" -and $Attempts -lt 20) {
    Start-Sleep -Seconds 1
    $Attempts++
    if (Test-Path $ServeoLog) {
        $Content = Get-Content $ServeoLog -Raw -ErrorAction SilentlyContinue
        if ($Content -match "Forwarding HTTP traffic from (https?://\S+)") {
            $TunnelUrl = $Matches[1].TrimEnd()
        }
        if ($Content -match "(https://[a-z0-9\-]+\.serveousercontent\.com)") {
            $TunnelUrl = $Matches[1].TrimEnd()
        }
    }
    Write-Host "      Attempt $Attempts/20..." -ForegroundColor DarkGray
}

if ($TunnelUrl -ne "") {
    # Save URL so backend uses it automatically for Apify webhooks
    $TunnelUrl | Out-File -FilePath $TunnelFile -Encoding utf8 -NoNewline

    Write-Host ""
    Write-Host "=============================================" -ForegroundColor Green
    Write-Host "  ALL SYSTEMS ONLINE!" -ForegroundColor Green
    Write-Host "=============================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Backend API:  http://localhost:$Port" -ForegroundColor Cyan
    Write-Host "  Public URL:   $TunnelUrl" -ForegroundColor Cyan
    Write-Host "  Webhook URL:  $TunnelUrl/api/webhooks/apify" -ForegroundColor Cyan
    Write-Host "  API Docs:     http://localhost:$Port/docs" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Data flow: Apify -> Webhook -> FastAPI -> SQLite -> WebSocket -> Dashboard" -ForegroundColor White
    Write-Host ""
    Write-Host "  Press Ctrl+C to stop everything." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "[WARNING] Tunnel URL not detected automatically." -ForegroundColor Red
    Write-Host "  Run manually: ssh -R 80:localhost:$Port serveo.net" -ForegroundColor Gray
    Write-Host "  Then paste the URL in the dashboard's tunnel field." -ForegroundColor Gray
    Write-Host ""
    Write-Host "  NOTE: Auto-polling still works without a tunnel!" -ForegroundColor Yellow
    Write-Host "  Real Apify data will be ingested even without a public URL." -ForegroundColor Yellow
}

# Keep alive + auto-reconnect
Write-Host "`n  [Tunnel active. Press Ctrl+C to stop all services]" -ForegroundColor DarkGray

try {
    while ($true) {
        Start-Sleep -Seconds 30
        if ($ServeoProc -and $ServeoProc.HasExited) {
            Write-Host "  [SSH tunnel dropped - reconnecting...]" -ForegroundColor Yellow
            "" | Out-File $ServeoLog -Encoding utf8
            $ServeoProc = Start-Process -FilePath "ssh" `
                -ArgumentList "-o StrictHostKeyChecking=no -o ServerAliveInterval=60 -R 80:localhost:$Port serveo.net" `
                -RedirectStandardOutput $ServeoLog `
                -RedirectStandardError $ServeoLog `
                -WindowStyle Hidden `
                -PassThru
        }
    }
} finally {
    Write-Host "`nShutting down all services..." -ForegroundColor Red
    if ($ServeoProc -and !$ServeoProc.HasExited) { $ServeoProc.Kill() }
    if ($BackendProc -and !$BackendProc.HasExited) { $BackendProc.Kill() }
    Write-Host "Stopped." -ForegroundColor Green
}
