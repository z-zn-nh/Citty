# Citty setup: create venv (reuse system site-packages) and install deps.
# No model weights are downloaded - capture/overlay deps only.
# NOTE: no ErrorActionPreference=Stop - uv writes progress to stderr, which PS would treat as a fatal error.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$Engine = "D:\Citty"
$Venv   = "D:\Citty\.venv"

uv venv --python E:\Python\python.exe --system-site-packages $Venv
uv pip install --python (Join-Path $Venv "Scripts\python.exe") -r (Join-Path $Engine "requirements.txt")

Write-Host ""
Write-Host "Done. Next:" -ForegroundColor Green
Write-Host "  .\run.ps1 doctor"
Write-Host "  .\run.ps1 selftest 30"
