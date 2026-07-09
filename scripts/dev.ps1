#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Vibe-Trading local dev environment launcher (PowerShell)
.DESCRIPTION
    Start backend API server (FastAPI) and frontend dev server (Vite).
    Usage: .\scripts\dev.ps1 <command>
    Commands: up | stop | restart | status | logs | urls | open
.EXAMPLE
    .\scripts\dev.ps1 up
    .\scripts\dev.ps1 stop
    .\scripts\dev.ps1 status
    .\scripts\dev.ps1 logs
#>

param(
    [Parameter(Position = 0)]
    [ValidateSet("up", "stop", "restart", "status", "logs", "urls", "open")]
    [string]$Command = "up",

    [Parameter()]
    [string]$Service = ""
)

$ErrorActionPreference = "Stop"

# ── Config ──────────────────────────────────────────────
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path "$ScriptRoot\..").Path
$StateDir = if ($env:VIBE_DEV_STATE_DIR) { $env:VIBE_DEV_STATE_DIR } else { Join-Path $Root ".vibe-dev" }
$LogDir = Join-Path $StateDir "logs"
$PidDir = Join-Path $StateDir "pids"

$BackendHost = if ($env:VIBE_BACKEND_HOST) { $env:VIBE_BACKEND_HOST } else { "127.0.0.1" }
$BackendPort = if ($env:VIBE_BACKEND_PORT) { $env:VIBE_BACKEND_PORT } else { "8899" }
$FrontendHost = if ($env:VIBE_FRONTEND_HOST) { $env:VIBE_FRONTEND_HOST } else { "127.0.0.1" }
$FrontendPort = if ($env:VIBE_FRONTEND_PORT) { $env:VIBE_FRONTEND_PORT } else { "5899" }

# Detect Python binary (resolve to absolute path — UseShellExecute=false requirement)
$PythonBin = if ($env:PYTHON) { $env:PYTHON }
elseif (Test-Path "$Root\.venv\Scripts\python.exe") { "$Root\.venv\Scripts\python.exe" }
elseif (Test-Path "$Root\agent\.venv\Scripts\python.exe") { "$Root\agent\.venv\Scripts\python.exe" }
else { (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $PythonBin) { throw "Python not found. Install Python 3.11+ or set `$env:PYTHON." }

# Ensure state directories exist
New-Item -ItemType Directory -Force -Path $LogDir, $PidDir | Out-Null

# ── Helpers ────────────────────────────────────────────
function Get-PidFile { param([string]$Svc) return Join-Path $PidDir "$Svc.pid" }
function Get-LogFile { param([string]$Svc) return Join-Path $LogDir "$Svc.log" }

function Test-Running {
    param([string]$Svc)
    $pidFile = Get-PidFile $Svc
    if (-not (Test-Path $pidFile)) { return $false }
    try {
        $svcPid = [int](Get-Content $pidFile -Raw).Trim()
        $proc = Get-Process -Id $svcPid -ErrorAction Stop
        return -not $proc.HasExited
    } catch { return $false }
}

function Get-ServiceUrl {
    param([string]$Svc)
    switch ($Svc) {
        "backend"  { return "http://127.0.0.1:$BackendPort/health" }
        "frontend" { return "http://127.0.0.1:$FrontendPort" }
        default    { throw "Unknown service: $Svc" }
    }
}

function Test-UrlOk {
    param([string]$Url)
    try {
        $code = curl.exe -s -o NUL -w "%{http_code}" --connect-timeout 3 $Url 2>&1
        if ($LASTEXITCODE -eq 0 -and $code) {
            $statusCode = [int]$code
            return ($statusCode -ge 200 -and $statusCode -lt 500)
        }
        return $false
    } catch { return $false }
}

function Wait-ForUrl {
    param([string]$Svc, [string]$Url, [int]$Attempts = 30)
    Write-Host -NoNewline "  waiting for $Svc at $Url"
    for ($i = 1; $i -le $Attempts; $i++) {
        if (Test-UrlOk $Url) {
            Write-Host " ready" -ForegroundColor Green
            return $true
        }
        if (-not (Test-Running $Svc)) {
            Write-Host ""
            Write-Host "  $Svc exited early; see $(Get-LogFile $Svc)" -ForegroundColor Red
            return $false
        }
        Write-Host -NoNewline "."
        Start-Sleep -Seconds 1
    }
    Write-Host ""
    Write-Host "  $Svc not ready after ${Attempts}s; see $(Get-LogFile $Svc)" -ForegroundColor Red
    return $false
}

# ── Service: Start / Stop ──────────────────────────────
function Start-Backend {
    if (Test-Running "backend") {
        Write-Host "  backend already running (pid $(Get-Content (Get-PidFile backend)))" -ForegroundColor Yellow
        return
    }
    if (Test-UrlOk (Get-ServiceUrl "backend")) {
        Remove-Item -Force (Get-PidFile "backend") -ErrorAction SilentlyContinue
        Write-Host "  backend already reachable at $(Get-ServiceUrl backend) (external)" -ForegroundColor Yellow
        return
    }

    Write-Host "  starting backend..." -ForegroundColor Cyan

    $agentDir = Join-Path $Root "agent"
    $logFile = Get-LogFile "backend"
    $pidFile = Get-PidFile "backend"

    # Build Python command to run serve_main
    $pyCode = "import sys; sys.path.insert(0, r'$agentDir'); from api_server import serve_main; raise SystemExit(serve_main(['--host', '$BackendHost', '--port', '$BackendPort']))"

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonBin
    $psi.Arguments = "-c `"$pyCode`""
    $psi.WorkingDirectory = $agentDir
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $psi.EnvironmentVariables["PYTHONPATH"] = $agentDir
    $psi.EnvironmentVariables["VIBE_TRADING_CHANNELS_AUTO_START"] = "true"

    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.Id | Out-File -FilePath $pidFile -NoNewline

    # Capture output to log via event subscriber
    $logVar = $logFile
    Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action {
        $line = $Event.SourceEventArgs.Data
        if ($line -ne $null) { Add-Content -Path $Event.MessageData -Value $line }
    } -MessageData $logVar | Out-Null
    $proc.BeginOutputReadLine()
    $proc.BeginErrorReadLine()

    Write-Host "  backend pid $($proc.Id), log $logFile" -ForegroundColor Green
}

function Start-Frontend {
    if (Test-Running "frontend") {
        Write-Host "  frontend already running (pid $(Get-Content (Get-PidFile frontend)))" -ForegroundColor Yellow
        return
    }
    if (Test-UrlOk (Get-ServiceUrl "frontend")) {
        Remove-Item -Force (Get-PidFile "frontend") -ErrorAction SilentlyContinue
        Write-Host "  frontend already reachable at $(Get-ServiceUrl frontend) (external)" -ForegroundColor Yellow
        return
    }

    Write-Host "  starting frontend..." -ForegroundColor Cyan

    $frontendDir = Join-Path $Root "frontend"
    $logFile = Get-LogFile "frontend"
    $pidFile = Get-PidFile "frontend"

    # Install dependencies if needed
    if (-not (Test-Path "$frontendDir\node_modules")) {
        Write-Host "  installing frontend dependencies..." -ForegroundColor Yellow
        Push-Location $frontendDir
        try {
            npm install 2>&1 | Out-File $logFile -Append
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  npm install failed; see $logFile" -ForegroundColor Red
                Pop-Location
                return
            }
        } finally { Pop-Location }
    }

    # Resolve full path to npx.cmd — UseShellExecute=false requires a real executable
    $npxPath = (Get-Command npx.cmd -ErrorAction SilentlyContinue).Source
    if (-not $npxPath) { $npxPath = (Get-Command npx -ErrorAction SilentlyContinue).Source }
    if (-not $npxPath -or $npxPath -like "*.ps1") {
        # Fall back: find npx.cmd in the Node.js install directory
        $nodeDir = Split-Path (Get-Command node -ErrorAction SilentlyContinue).Source -Parent
        if ($nodeDir -and (Test-Path "$nodeDir\npx.cmd")) { $npxPath = "$nodeDir\npx.cmd" }
    }
    if (-not $npxPath) {
        Write-Host "  npx.cmd not found in PATH; install Node.js first" -ForegroundColor Red
        return
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $npxPath
    $psi.Arguments = "vite --host $FrontendHost --port $FrontendPort"
    $psi.WorkingDirectory = $frontendDir
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $psi.EnvironmentVariables["VITE_API_URL"] = "http://127.0.0.1:$BackendPort"

    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.Id | Out-File -FilePath $pidFile -NoNewline

    $logVar = $logFile
    Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action {
        $line = $Event.SourceEventArgs.Data
        if ($line -ne $null) { Add-Content -Path $Event.MessageData -Value $line }
    } -MessageData $logVar | Out-Null
    $proc.BeginOutputReadLine()
    $proc.BeginErrorReadLine()

    Write-Host "  frontend pid $($proc.Id), log $logFile" -ForegroundColor Green
}

function Stop-Service {
    param([string]$Svc)
    $pidFile = Get-PidFile $Svc
    if (-not (Test-Path $pidFile)) {
        Write-Host "  $Svc not started by scripts/dev" -ForegroundColor Yellow
        return
    }
    $svcPid = [int](Get-Content $pidFile -Raw).Trim()
    try {
        $proc = Get-Process -Id $svcPid -ErrorAction Stop
        Write-Host "  stopping $Svc (pid $svcPid)..." -ForegroundColor Cyan
        $proc.Kill($true)
    } catch {
        Write-Host "  $Svc process not found (pid $svcPid)" -ForegroundColor Yellow
    }
    Remove-Item -Force $pidFile -ErrorAction SilentlyContinue
}

# ── Commands ───────────────────────────────────────────
function Invoke-Up {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  Vibe-Trading Dev Environment" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""

    Start-Backend
    Start-Frontend

    $backendOk = Wait-ForUrl "backend" (Get-ServiceUrl "backend") 30
    $frontendOk = Wait-ForUrl "frontend" (Get-ServiceUrl "frontend") 30

    if (-not $backendOk -or -not $frontendOk) {
        Invoke-Stop
        throw "One or more services failed to start"
    }

    Invoke-Urls
}

function Invoke-Stop {
    Write-Host ""
    Write-Host "  stopping all services..." -ForegroundColor Cyan
    Stop-Service "frontend"
    Stop-Service "backend"
    Get-Job | Remove-Job -Force -ErrorAction SilentlyContinue
    Get-EventSubscriber | Unregister-Event -Force -ErrorAction SilentlyContinue
    Write-Host "  all services stopped." -ForegroundColor Green
    Write-Host ""
}

function Invoke-Status {
    $backendRunning = [bool](Test-Running "backend") -or [bool](Test-UrlOk (Get-ServiceUrl "backend"))
    $frontendRunning = [bool](Test-Running "frontend") -or [bool](Test-UrlOk (Get-ServiceUrl "frontend"))

    Write-Host ""
    Write-Host "  Vibe-Trading Dev Status" -ForegroundColor Cyan
    Write-Host "  ----------------------------------------"
    Write-Host ("  Backend  : " + $(if ($backendRunning) { "running" } else { "stopped" }))
    Write-Host ("  Frontend : " + $(if ($frontendRunning) { "running" } else { "stopped" }))
    Write-Host "  ----------------------------------------"

    if ($backendRunning) {
        Write-Host "  Backend  : http://127.0.0.1:$BackendPort"
        Write-Host "  API Docs : http://127.0.0.1:$BackendPort/docs"
    }
    if ($frontendRunning) {
        Write-Host "  Frontend : http://127.0.0.1:$FrontendPort"
    }
    Write-Host ""
}

function Invoke-Logs {
    $target = if ($Service) { $Service } else { "all" }
    $files = @()
    if ($target -eq "all" -or $target -eq "backend") {
        $f = Get-LogFile "backend"
        if (Test-Path $f) { $files += $f }
    }
    if ($target -eq "all" -or $target -eq "frontend") {
        $f = Get-LogFile "frontend"
        if (Test-Path $f) { $files += $f }
    }
    if ($files.Count -eq 0) {
        Write-Host "No logs found." -ForegroundColor Yellow
        return
    }
    Get-Content -Path $files -Tail 50 -Wait
}

function Invoke-Urls {
    Write-Host ""
    Write-Host "  Vibe-Trading Dev URLs" -ForegroundColor Cyan
    Write-Host "  ----------------------------------------"
    Write-Host "  Frontend : http://127.0.0.1:$FrontendPort"
    Write-Host "  Backend  : http://127.0.0.1:$BackendPort"
    Write-Host "  API Docs : http://127.0.0.1:$BackendPort/docs"
    Write-Host "  Health   : http://127.0.0.1:$BackendPort/health"
    Write-Host "  ----------------------------------------"
    Write-Host ""
}

function Invoke-Open {
    $url = "http://127.0.0.1:$FrontendPort"
    Write-Host "Opening $url ..."
    Start-Process $url
}

function Invoke-Restart {
    if ($Service) {
        Stop-Service $Service
        Start-Sleep -Seconds 1
        switch ($Service) {
            "backend"  { Start-Backend;  $null = Wait-ForUrl "backend" (Get-ServiceUrl "backend") 30 }
            "frontend" { Start-Frontend; $null = Wait-ForUrl "frontend" (Get-ServiceUrl "frontend") 30 }
        }
    } else {
        Invoke-Stop
        Start-Sleep -Seconds 1
        Invoke-Up
    }
}

# ── Entry ──────────────────────────────────────────────
function Show-Usage {
    @"
Vibe-Trading Dev Script (PowerShell)

Usage: scripts\dev.ps1 <command> [--service <name>]

Commands:
  up                  Start backend + frontend dev servers
  stop                Stop all dev servers
  restart [--service] Restart backend, frontend, or all
  status              Show process status and URLs
  logs [--service]    Tail logs (backend, frontend, or all)
  urls                Print local dev URLs
  open                Open frontend in default browser

Environment:
  VIBE_BACKEND_PORT    Backend port (default: 8899)
  VIBE_FRONTEND_PORT   Frontend port (default: 5899)
  PYTHON               Python binary (auto-detect .venv, then python)
"@
}

switch ($Command) {
    "up"       { Invoke-Up }
    "stop"     { Invoke-Stop }
    "restart"  { Invoke-Restart }
    "status"   { Invoke-Status }
    "logs"     { Invoke-Logs }
    "urls"     { Invoke-Urls }
    "open"     { Invoke-Open }
    default    { Show-Usage }
}
