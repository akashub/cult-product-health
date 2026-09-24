# One-time install on Windows (PowerShell): uv, Python deps, headless browser.
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
uv sync
uv run playwright install chromium
New-Item -ItemType Directory -Force -Path data | Out-Null
if (-not (Test-Path config.private.yaml)) { Write-Host "NOTE: copy config.private.yaml from the handover bundle into this folder." }
uv run cultph doctor
