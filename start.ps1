# Start CR Automation Agentic server
Set-Location $PSScriptRoot

# Activate venv
if (Test-Path ".venv\Scripts\Activate.ps1") {
    & .venv\Scripts\Activate.ps1
}

# Ensure data directories exist
@("data\uploads", "data\runs", "data\logs", "data\knowledge") | ForEach-Object {
    New-Item -ItemType Directory -Path $_ -Force | Out-Null
}

# Kill existing instance if running
$existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $procId = $existing.OwningProcess | Select-Object -First 1
    Write-Host "Stopping existing server (PID: $procId) ..."
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

$logFile = "data\logs\server_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

Write-Host "============================================"
Write-Host "  CR Automation Agentic Pipeline Server"
Write-Host "============================================"
Write-Host "  URL:  http://localhost:8000"
Write-Host "  Log:  $logFile"
Write-Host "  Stop: .\stop.ps1"
Write-Host "============================================"
Write-Host ""

# Suppress all Python warnings
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
$env:HF_HUB_DISABLE_IMPLICIT_TOKEN = "1"
$env:TOKENIZERS_PARALLELISM = "false"
$env:PYTHONWARNINGS = "ignore"

# Run with logs to both console and file (suppress stderr warnings in PowerShell)
$ErrorActionPreference = "SilentlyContinue"
python -u -W ignore src/ui/app.py 2>&1 | Tee-Object -FilePath $logFile
