# Build the Windows application: dist\LooperOSS\LooperOSS.exe (+ installer if Inno is present)
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#
# Requires Python 3.9+ on the build machine. SuperCollider is NOT bundled; the
# app tells the user in its own window if it is missing.
$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))

$version = (Get-Content VERSION -Raw).Trim()
Write-Host "== Looper OSS $version (windows)"

Write-Host "== build dependencies"
python -m pip install --quiet --disable-pip-version-check --upgrade pyinstaller
python -m pip install --quiet --disable-pip-version-check -r requirements.txt

Write-Host "== icon"
if (-not (Test-Path packaging\windows\looper-oss.ico)) {
    if (Test-Path packaging\linux\looper-oss.svg) {
        # ImageMagick converts SVG -> ICO if it is installed; otherwise the build
        # simply ships without an icon rather than failing.
        try { magick -background none packaging\linux\looper-oss.svg -define icon:auto-resize=256,64,32,16 packaging\windows\looper-oss.ico }
        catch { Write-Host "   no magick; building without an icon" }
    }
}

Write-Host "== pyinstaller"
python -m PyInstaller --clean --noconfirm packaging\windows\looper_oss.spec

if (Test-Path dist\LooperOSS\LooperOSS.exe) {
    Write-Host "   dist\LooperOSS\LooperOSS.exe"
} else {
    throw "PyInstaller did not produce dist\LooperOSS\LooperOSS.exe"
}

# content-based build stamp: a rebuilt exe with the same VERSION must still
# refresh the payload the launcher stages into %LOCALAPPDATA%
$hash = (Get-FileHash dist\LooperOSS\LooperOSS.exe -Algorithm SHA256).Hash.Substring(0, 16).ToLower()
$stamp = "$version-$hash"
Set-Content -Path dist\LooperOSS\BUILD -Value $stamp -NoNewline -Encoding ascii
Write-Host "   build stamp $stamp"

$iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
if ($iscc) {
    Write-Host "== installer (Inno Setup)"
    & $iscc.Source "/DAppVersion=$version" packaging\windows\looper-oss.iss
    Write-Host "   dist\looper-oss-$version-setup.exe"
} else {
    Write-Host "== installer skipped (Inno Setup's iscc.exe not on PATH)"
    Write-Host "   dist\LooperOSS\ is a complete portable build: copy it anywhere and run LooperOSS.exe"
}
