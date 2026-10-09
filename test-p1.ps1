# Citty P1 acceptance test - four checks, one command.
#   .\test-p1.ps1                 # all checks (live check needs a working audio device)
#   .\test-p1.ps1 -SkipLive       # only the first three (no audio hardware needed)
#   .\test-p1.ps1 -Device "Speakers (Realtek)"   # force a specific output device
#
# Chinese explanation of every check: docs\14-*.md
#
# NOTE: this file is deliberately ASCII-only. Windows PowerShell 5.1 mis-decodes
# non-ASCII .ps1 files that lack a BOM, so all messages stay in English here.
[CmdletBinding()]
param(
    [string]$Config      = "D:\Citty\config.local.yaml",
    [string]$Wav         = "D:\Citty\out\samples\official_en.wav",
    [string]$Device      = "",
    [int]$FileSeconds    = 22,
    [int]$LiveSeconds    = 46,
    [switch]$SkipLive
)

$ErrorActionPreference = "Continue"
$script:Failed = 0
$Py    = "D:\Citty\.venv\Scripts\python.exe"
$Root  = "D:\Citty"
$Engine = Join-Path $Root "apps\engine"
$LogDir = Join-Path $Root "out"
$Srt   = Join-Path $LogDir "selftest.srt"

function Step($n, $title) { Write-Host ""; Write-Host ("=" * 66); Write-Host "[$n/4] $title"; Write-Host ("=" * 66) }
function Pass($msg) { Write-Host ("  PASS  " + $msg) -ForegroundColor Green }
function Fail($msg) { Write-Host ("  FAIL  " + $msg) -ForegroundColor Red; $script:Failed++ }
function Warn($msg) { Write-Host ("  WARN  " + $msg) -ForegroundColor Yellow }

# This box has duplicate case-variant proxy variables (http_proxy AND HTTP_PROXY).
# PowerShell's Start-Process / Get-ChildItem Env: throw "duplicate key" because of it.
# Dropping the lower-case copies keeps every child-process launch working.
foreach ($k in 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy') {
    if ([System.Environment]::GetEnvironmentVariable($k, 'Process')) {
        Remove-Item -Path "Env:\$k" -ErrorAction SilentlyContinue
    }
}

# Force UTF-8 for child python processes so Chinese in logs stays readable.
# (The engine no longer *crashes* on a GBK console - see citty/tame_console_encoding -
#  but the log files are nicer in UTF-8.)
$env:PYTHONIOENCODING = 'utf-8'

if (-not (Test-Path $Py))   { Fail "python missing: $Py"; exit 2 }
if (-not (Test-Path $Config)) { Fail "config missing: $Config"; exit 2 }
if (-not (Test-Path $Wav))  { Fail "test wav missing: $Wav"; exit 2 }

# --------------------------------------------------------------------------- #
# 1. doctor
# --------------------------------------------------------------------------- #
Step 1 "doctor - environment self-check"
# cli is a package under apps\engine, so every invocation has to run from there.
Push-Location $Engine
& $Py -m citty.cli -c $Config doctor 2>&1 | ForEach-Object { "    $_" }
if ($LASTEXITCODE -eq 0) { Pass "doctor exited 0" } else { Fail "doctor exited $LASTEXITCODE" }

# --------------------------------------------------------------------------- #
# 2. file mode - full chain with no audio hardware (ASR + translate + SRT)
# --------------------------------------------------------------------------- #
Step 2 "file mode - audio file through ASR + translation + SRT"
$out = & $Py -m citty.cli -c $Config file $Wav --seconds $FileSeconds 2>&1
Pop-Location
$out | Where-Object { $_ -match 'rev|segments|SRT|=>|:' } | Select-Object -Last 8 | ForEach-Object { "    $_" }

$stats = ($out | Select-String -Pattern '"translated":\s*(\d+)' | Select-Object -Last 1)
if ($stats) {
    $n = [int]$stats.Matches[0].Groups[1].Value
    if ($n -gt 0) { Pass "translated $n segment(s)" } else { Fail "translated 0 segments" }
    $err = [int](($out | Select-String -Pattern '"errors":\s*(\d+)' | Select-Object -Last 1).Matches[0].Groups[1].Value)
    if ($err -eq 0) { Pass "0 errors" } else { Fail "$err error(s)" }
} else {
    Fail "no stats line found in engine output"
}
if (Test-Path $Srt) {
    $age = ((Get-Date) - (Get-Item $Srt).LastWriteTime).TotalSeconds
    if ($age -lt 300) { Pass ("SRT written just now ({0:N0} bytes)" -f (Get-Item $Srt).Length) }
    else { Warn "SRT exists but is old ($([int]$age)s) - this run may not have written it" }
} else { Fail "SRT not written: $Srt" }

# --------------------------------------------------------------------------- #
# 3. loopback probe - can this machine capture system audio at all?
# --------------------------------------------------------------------------- #
Step 3 "loopback probe - which output device can actually be recorded"
$probe = & $Py (Join-Path $Root "tools\probe_loopback.py") 2>&1
$probe | Select-Object -Last 6 | ForEach-Object { "    $_" }
$okDefault = ($LASTEXITCODE -eq 0)

if (-not $okDefault) {
    Warn "default output device captures silence - sweeping all devices"
    $sweep = & $Py (Join-Path $Root "tools\probe_loopback.py") --sweep 2>&1
    $sweep | Select-Object -Last 8 | ForEach-Object { "    $_" }
    $good = ($sweep | Select-String -Pattern '^..\s+(.+?)\s+peak|^\S+\s+(.+?)\s{2,}').Count
    $line = $sweep | Select-String -Pattern '\[.*\]' | Select-Object -Last 1
    if ($LASTEXITCODE -eq 0) {
        Pass "a device did work - set audio.device to the one marked OK above"
        if (-not $Device) {
            $m = [regex]::Match(($sweep -join "`n"), "\['(.+?)'\]")
            if ($m.Success) { $Device = $m.Groups[1].Value; Write-Host "    -> using detected device: $Device" }
        }
    } else {
        Fail "no output device is capturable - this machine has no working audio path"
    }
} else {
    Pass "default output device loopback works"
}

# --------------------------------------------------------------------------- #
# 4. live - real capture -> ASR -> translation -> overlay
# --------------------------------------------------------------------------- #
Step 4 "live loopback - real-time capture through the whole chain"
if ($SkipLive) {
    Warn "skipped (-SkipLive)"
} else {
    $liveLog = Join-Path $LogDir "_p1_live.log"
    Remove-Item $liveLog -ErrorAction SilentlyContinue
    $devArg = @()
    if ($Device) { $devArg = @('-o', "audio.device=$Device") }

    $job = Start-Job -ScriptBlock {
        param($py, $wd, $cfg, $dev, $secs, $log)
        Set-Location $wd
        $env:PYTHONIOENCODING = 'utf-8'
        # Redirect through cmd, not with PowerShell's '>': cmd copies bytes, so the
        # log stays real UTF-8. PowerShell redirection would decode the child's UTF-8
        # with the console codepage and re-encode it, turning Chinese into mojibake.
        $a = "-m citty.cli -c `"$cfg`" -o audio.source=loopback"
        if ($dev) { $a += " -o `"audio.device=$dev`"" }
        $a += " selftest --seconds $secs"
        cmd /c "`"$py`" $a > `"$log`" 2>&1"
    } -ArgumentList $Py, $Engine, $Config, $Device, $LiveSeconds, $liveLog

    Start-Sleep -Seconds 5
    Write-Host "    feeding audio into the output device ..."
    $pa = @((Join-Path $Root "tools\play_wav.py"), $Wav, '--repeat', '2', '--gap', '0.4')
    if ($Device) { $pa += @('--device', $Device) }
    & $Py @pa 2>&1 | ForEach-Object { "    $_" }

    $null = Receive-Job $job -Wait
    Remove-Job $job -Force

    if (Test-Path $liveLog) {
        $live = Get-Content $liveLog -Encoding UTF8
        $live | Where-Object { $_ -match 'rev|segments|SRT|EN:|ZH:' } | Select-Object -Last 12 | ForEach-Object { "    $_" }
        $ls = ($live | Select-String -Pattern '"translated":\s*(\d+)' | Select-Object -Last 1)
        if ($ls -and [int]$ls.Matches[0].Groups[1].Value -gt 0) {
            Pass "live chain translated $($ls.Matches[0].Groups[1].Value) segment(s) from real audio"
        } else {
            Fail "live chain captured nothing - check audio.device (see step 3)"
        }
    } else { Fail "no live log produced" }
}

# --------------------------------------------------------------------------- #
Write-Host ""
Write-Host ("=" * 66)
if ($script:Failed -eq 0) {
    Write-Host "P1 ACCEPTANCE: PASS" -ForegroundColor Green
} else {
    Write-Host "P1 ACCEPTANCE: $script:Failed CHECK(S) FAILED" -ForegroundColor Red
}
Write-Host ("=" * 66)
exit $script:Failed
