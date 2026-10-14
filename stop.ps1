# Stop CR Automation Agentic server
$existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue

if (-not $existing) {
    Write-Host "Server is not running on port 8000."
    exit 0
}

$procId = $existing.OwningProcess | Select-Object -First 1
Write-Host "Stopping server (PID: $procId) ..."
Stop-Process -Id $procId -Force
Write-Host "Server stopped."
