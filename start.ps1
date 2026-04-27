# Forte Research — one-click launcher for Windows.
# Opens three PowerShell windows: API, worker, web dashboard.
# Run this from the repo root: `.\start.ps1`

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot

if (-not (Test-Path "$repo\.env")) {
    Write-Host "[setup] No .env found. Copying from .env.example — fill it in before continuing." -ForegroundColor Yellow
    Copy-Item "$repo\.env.example" "$repo\.env"
}

Write-Host "[start] launching API on :8000" -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$repo'; python -m uvicorn api.main:app --reload --port 8000"
)

Write-Host "[start] launching worker" -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$repo'; python -m api.workflow.worker"
)

Write-Host "[start] launching web on :3000" -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$repo\web'; npm run dev"
)

Write-Host ""
Write-Host "Dashboard: http://localhost:3000" -ForegroundColor Green
Write-Host "API:       http://localhost:8000" -ForegroundColor Green
Write-Host ""
Write-Host "Close the three terminal windows to stop." -ForegroundColor Gray
