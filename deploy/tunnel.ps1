# Citty SSH tunnel: expose the remote inference server on local 127.0.0.1.
#
# On the server, llama-server (MT) and the ASR service listen on 127.0.0.1 only,
# so nothing is reachable from the public internet. This tunnel is encrypted,
# needs no open inbound port, no ICP filing and no TLS certificate.
#
# NOTE: this file is deliberately ASCII-only. Windows PowerShell 5.1 decodes
# .ps1 files without a BOM as ANSI, which mangles non-ASCII text and breaks parsing.
#
# Usage
#   .\deploy\tunnel.ps1 -Server 1.2.3.4 -User root      # start tunnel (Ctrl+C to stop)
#   .\deploy\tunnel.ps1 -Server 1.2.3.4 -Check          # connectivity self-check only
#   .\deploy\tunnel.ps1 -Stop                           # kill the tunnel this script started
#
# Then:
#   .\run.ps1 -Config D:\Citty\config.remote.yaml file out\samples\official_en.wav 30

param(
  [string]$Server = "",
  [string]$User = "root",
  [int]$AsrPort = 8001,
  [int]$MtPort = 8002,
  [string]$KeyFile = "",
  [int]$SshPort = 22,
  [switch]$Check,
  [switch]$Stop
)
$ErrorActionPreference = "Stop"
$PidFile = Join-Path $env:TEMP "citty-tunnel.json"

function Test-Port([string]$h, [int]$p, [int]$timeoutMs = 2000) {
  $c = New-Object System.Net.Sockets.TcpClient
  try {
    $t = $c.ConnectAsync($h, $p)
    if ($t.Wait($timeoutMs)) { return $true } else { return $false }
  } catch { return $false } finally { $c.Close() }
}

function Show-Check {
  Write-Host ""
  Write-Host "---- tunnel self-check ----" -ForegroundColor Cyan
  $targets = @(
    @{ Name = "ASR"; Port = $AsrPort; EnvKey = "CITTY_ASR_KEY" },
    @{ Name = "MT";  Port = $MtPort;  EnvKey = "CITTY_MT_KEY" }
  )
  foreach ($t in $targets) {
    if (-not (Test-Port "127.0.0.1" $t.Port)) {
      Write-Host ("[{0}] local port {1} closed - tunnel not up?" -f $t.Name, $t.Port) -ForegroundColor Red
      continue
    }
    $headers = @{}
    $key = [Environment]::GetEnvironmentVariable($t.EnvKey)
    if ($key) { $headers["Authorization"] = "Bearer $key" }
    try {
      $uri = "http://127.0.0.1:{0}/v1/models" -f $t.Port
      $r = Invoke-WebRequest -Uri $uri -Headers $headers -TimeoutSec 10 -UseBasicParsing
      $ids = ((($r.Content | ConvertFrom-Json).data) | ForEach-Object { $_.id }) -join ", "
      Write-Host ("[{0}] port {1} OK  models = {2}" -f $t.Name, $t.Port, $ids) -ForegroundColor Green
      if (-not $key) {
        Write-Host ("     hint: env var {0} is unset; server with --api-key will answer 401" -f $t.EnvKey) -ForegroundColor Yellow
      }
    } catch {
      Write-Host ("[{0}] port {1} open but request failed: {2}" -f $t.Name, $t.Port, $_.Exception.Message) -ForegroundColor Yellow
    }
  }
}

if ($Stop) {
  if (Test-Path $PidFile) {
    $info = Get-Content $PidFile -Raw | ConvertFrom-Json
    Write-Host ("stopping ssh tunnel PID {0} ({1})" -f $info.pid, $info.server) -ForegroundColor Cyan
    Stop-Process -Id $info.pid -Force -ErrorAction SilentlyContinue
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
  } else {
    Write-Host "no tunnel recorded by this script." -ForegroundColor Yellow
  }
  # fallback: kill any ssh still forwarding our ports
  $pattern = "*-L {0}:127.0.0.1:{0}*" -f $AsrPort
  Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like $pattern } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  return
}

if ($Check) { Show-Check; return }

if (-not $Server) {
  Write-Host "need -Server (public IP or hostname). e.g. .\deploy\tunnel.ps1 -Server 1.2.3.4 -User root" -ForegroundColor Yellow
  exit 2
}

$fwd = @("-N", "-L", ("{0}:127.0.0.1:{0}" -f $AsrPort), "-L", ("{0}:127.0.0.1:{0}" -f $MtPort), "-p", "$SshPort")
if ($KeyFile) { $fwd += @("-i", $KeyFile) }
$fwd += @("-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes")
$fwd += ("{0}@{1}" -f $User, $Server)

Write-Host ("tunnel: {0} -> local ASR:{1} MT:{2}" -f $Server, $AsrPort, $MtPort) -ForegroundColor Cyan
Write-Host ("  ssh " + ($fwd -join " ")) -ForegroundColor DarkGray
$proc = Start-Process -FilePath "ssh" -ArgumentList $fwd -WindowStyle Hidden -PassThru
@{ pid = $proc.Id; server = $Server; started = (Get-Date).ToString("s") } | ConvertTo-Json |
  Set-Content -Path $PidFile -Encoding UTF8

Start-Sleep -Seconds 3
if ($proc.HasExited) {
  Write-Host "ssh exited immediately - check IP/user/key/port, and whether sshd allows port forwarding." -ForegroundColor Red
  exit 1
}
Show-Check
Write-Host ""
Write-Host "tunnel running in background (PID $($proc.Id)). stop with: .\deploy\tunnel.ps1 -Stop" -ForegroundColor Cyan
