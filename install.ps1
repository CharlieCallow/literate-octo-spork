# Forte Research — one-time install script for Windows.
# Installs Python deps + Playwright browser + npm deps.
# Run once after cloning: `.\install.ps1`

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot

Write-Host "[install] Python dependencies" -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -e "$repo"

Write-Host "[install] Playwright Chromium (~150 MB, one-time)" -ForegroundColor Cyan
python -m playwright install chromium

Write-Host "[install] Frontend dependencies" -ForegroundColor Cyan
Push-Location "$repo\web"
npm install --legacy-peer-deps
Pop-Location

Write-Host ""
Write-Host "Install complete. Next:" -ForegroundColor Green
Write-Host "  1. Copy .env.example -> .env and fill in keys" -ForegroundColor Gray
Write-Host "  2. Run .\start.ps1" -ForegroundColor Gray
