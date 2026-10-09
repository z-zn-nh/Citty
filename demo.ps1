# One-click run: engine + desktop subtitle window.
#   .\demo.ps1                      # local profile (config.local.yaml, real loopback audio)
#   .\demo.ps1 -Config D:\Citty\config.yaml -Source mock    # the old mock demo
# Stop with: close the two new PowerShell windows, or  Get-Process python | Stop-Process
#
# NOTE: this file is deliberately ASCII-only (Windows PowerShell 5.1 mis-decodes
# non-ASCII .ps1 files without a BOM).
[CmdletBinding()]
param(
    [string]$Config = "D:\Citty\config.local.yaml",
    [string]$Source = "",
    [string]$Device = ""
)

$Py = "D:\Citty\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "venv missing. Run: powershell -File D:\Citty\setup.ps1" }

# This box carries duplicate case-variant proxy variables (http_proxy AND HTTP_PROXY).
# Start-Process builds a case-insensitive environment dictionary and dies on them with
# "An item with the same key has already been added. Key: HTTP_PROXY".
# Dropping the lower-case copies is enough to make child launching work again.
foreach ($k in 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy') {
    if ([System.Environment]::GetEnvironmentVariable($k, 'Process')) {
        Remove-Item -Path "Env:\$k" -ErrorAction SilentlyContinue
    }
}

function Start-CittyWindow([string]$title, [string[]]$cliArgs) {
    $argLine = @('-NoExit', '-Command',
        "`$host.UI.RawUI.WindowTitle='$title'; & '$Py' -m citty.cli -c '$Config' " + ($cliArgs -join ' '))
    Start-Process powershell -ArgumentList $argLine -WorkingDirectory "D:\Citty\apps\engine"
}

$engineArgs = @()
if ($Source) { $engineArgs += @('-o', "audio.source=$Source") }
if ($Device) { $engineArgs += @('-o', "`"audio.device=$Device`"") }
$engineArgs += 'run'

Start-CittyWindow 'Citty Engine' $engineArgs
Start-Sleep -Seconds 4
Start-CittyWindow 'Citty Overlay' @('overlay')

Write-Host "Engine + overlay started." -ForegroundColor Green
Write-Host "  config:          $Config"
Write-Host "  Browser overlay: http://127.0.0.1:8756/overlay"
Write-Host "  Status:          http://127.0.0.1:8756/api/status"
Write-Host ""
Write-Host "If the overlay stays empty, check the audio device:" -ForegroundColor Yellow
Write-Host "  $Py D:\Citty\tools\probe_loopback.py --sweep"
