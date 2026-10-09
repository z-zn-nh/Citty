# Citty launcher (Windows PowerShell)
#   .\run.ps1 doctor            environment self-check
#   .\run.ps1 selftest 30       headless end-to-end run for 30s
#   .\run.ps1 run               start engine (engine + browser overlay URL)
#   .\run.ps1 overlay           start desktop always-on-top subtitle window
#   .\run.ps1 file a.wav 60     use an audio file instead of live capture
#   .\run.ps1 translate "hi"    test the MT backend only
#   .\run.ps1 record 40         record 40s of system audio -> out\sample.wav
#   .\run.ps1 ab out\sample.wav [ref.txt]   compare cloud ASR candidates
#   .\run.ps1 say "text" [-v voice] [-o out\x.mp3] [-r +10%]
#   .\run.ps1 voices [prefix]   list TTS voices
param(
  [Parameter(Position=0)][string]$Cmd = "doctor",
  [Parameter(Position=1)][string]$Arg1 = "",
  [Parameter(Position=2)][string]$Arg2 = "",
  [string]$Config = "D:\Citty\config.yaml",
  [string]$Voice = "",
  [string]$Out = "",
  [string]$Rate = "",
  [string]$Prefix = ""
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
# Python/uv print progress to stderr; do not let PowerShell treat that as fatal.
$PSNativeCommandUseErrorActionPreference = $false
$ErrorActionPreference = "Continue"

$Engine = "D:\Citty\apps\engine"
$Py     = "D:\Citty\.venv\Scripts\python.exe"
$Caller = (Get-Location).Path

if (-not (Test-Path $Py)) { throw "venv missing. Run: powershell -File D:\Citty\setup.ps1" }

# Resolve user-supplied relative paths against the directory they ran the script from
# (we Push-Location into the engine dir below).
function Resolve-Arg([string]$p) {
  if (-not $p) { return $p }
  if ([IO.Path]::IsPathRooted($p)) { return $p }
  return (Join-Path $Caller $p)
}

Push-Location $Engine
try {
  switch ($Cmd) {
    "doctor"    { & $Py -m citty.cli -c $Config doctor }
    "selftest"  { $s = if ($Arg1) { $Arg1 } else { "30" }; & $Py -m citty.cli -c $Config selftest --seconds $s }
    "run"       { & $Py -m citty.cli -c $Config run }
    "overlay"   { & $Py -m citty.cli -c $Config overlay }
    "file"      { $s = if ($Arg2) { $Arg2 } else { "60" }; & $Py -m citty.cli -c $Config file (Resolve-Arg $Arg1) --seconds $s }
    "translate" { & $Py -m citty.cli -c $Config translate $Arg1 }
    "record"    { $s = if ($Arg1) { $Arg1 } else { "30" }; & $Py -m citty.cli -c $Config record --seconds $s }
    "ab"        {
      $a = @("--wav", (Resolve-Arg $Arg1))
      if ($Arg2) { $a += @("--ref", (Resolve-Arg $Arg2)) }
      & $Py -m citty.cli -c $Config ab @a
    }
    "say"       {
      $a = @()
      if ($Voice)  { $a += @("-v", $Voice) }
      if ($Rate)   { $a += @("-r", $Rate) }
      if ($Out)    { $a += @("-o", (Resolve-Arg $Out)) }
      & $Py -m citty.cli -c $Config say $Arg1 @a
    }
    "voices"    {
      # .\run.ps1 voices zh-CN   (positional)  or  -Prefix zh-CN
      $p = if ($Prefix) { $Prefix } else { $Arg1 }
      if ($p) { & $Py -m citty.cli -c $Config say --list-voices ("--voice-prefix=" + $p) }
      else { & $Py -m citty.cli -c $Config say --list-voices }
    }
    default     { & $Py -m citty.cli --help }
  }
} finally {
  Pop-Location
}
