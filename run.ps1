# Looper OSS launcher for Windows.
#
#   .\run.ps1              start everything, open the UI yourself
#   .\run.ps1 -Browser     start everything and open an app window
#   .\run.ps1 -Kiosk       same, fullscreen (for the small touchscreen)
#   .\run.ps1 -Setup       create .venv + regenerate engine config, then exit
#
# Requires: SuperCollider (sclang on PATH or in Program Files), Python 3.

param(
    [switch]$Setup,
    [switch]$Browser,
    [switch]$Kiosk
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$Config = "ui/config.json"
$Venv = ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$LogDir = "logs"

function Find-Sclang {
    $cmd = Get-Command sclang.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $found = Get-ChildItem "C:\Program Files" -Filter "sclang.exe" -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}

function Setup-Env {
    Write-Host "== python venv"
    if (-not (Test-Path $Py)) { python -m venv $Venv }
    & (Join-Path $Venv "Scripts\pip.exe") install --quiet --upgrade pip
    & (Join-Path $Venv "Scripts\pip.exe") install --quiet -r requirements.txt
}

function Write-EngineConfig {
    Write-Host "== engine config from $Config"
    python bridge/write_config.py $Config engine/_config.scd
}

if ($Setup) {
    Setup-Env
    Write-EngineConfig
    Write-Host "ready."
    exit 0
}

if (-not (Test-Path $Py)) { Setup-Env }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Write-EngineConfig

$sclang = Find-Sclang
if (-not $sclang) {
    Write-Host "missing: sclang.exe (install SuperCollider from supercollider.github.io)"
    exit 1
}

$uiPort = (& $Py -c "import json;print(json.load(open('$Config'))['ui']['port'])").Trim()

$http = Start-Process -FilePath $Py `
    -ArgumentList "-m", "http.server", $uiPort, "--bind", "127.0.0.1", "--directory", "ui" `
    -PassThru -WindowStyle Hidden
$bridge = Start-Process -FilePath $Py `
    -ArgumentList "bridge/bridge.py" `
    -PassThru -RedirectStandardOutput "$LogDir\bridge.log" -RedirectStandardError "$LogDir\bridge.log.err" `
    -WindowStyle Hidden

$url = "http://127.0.0.1:$uiPort/"

try {
    Start-Sleep -Milliseconds 800
    if ($Browser -or $Kiosk) {
        $flags = if ($Kiosk) { "--kiosk --app=$url" } else { "--app=$url" }
        foreach ($b in @("chrome", "msedge", "brave", "chromium")) {
            $exe = Get-Command $b -ErrorAction SilentlyContinue
            if ($exe) {
                Start-Process -FilePath $exe.Source -ArgumentList $flags
                break
            }
        }
    }

    Write-Host ""
    Write-Host "  UI   $url"
    Write-Host "  OSC  engine :9000   state -> bridge :9001 -> ws :8182"
    Write-Host "  logs $LogDir\"
    Write-Host "  ctrl-c to stop"
    Write-Host ""

    # sclang runs in the foreground; it quits on stdin EOF, so keep stdin open
    $engine = Start-Process -FilePath $sclang `
        -ArgumentList "engine/looper.scd" -NoNewWindow -PassThru `
        -RedirectStandardOutput "$LogDir\engine.log" -RedirectStandardError "$LogDir\engine.log.err"
    $engine.WaitForExit()
    $code = $engine.ExitCode
    if ($code -eq 42) {
        Write-Host "== engine restart requested; re-reading $Config"
        Write-EngineConfig
    }
}
finally {
    foreach ($p in @($http, $bridge)) {
        if ($p -and -not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    }
    Get-Process scsynth -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
