<#!
.SYNOPSIS
Build the platform-native private backend bundled into an Ohmwork installer.

.DESCRIPTION
Run this ON the target operating system. PyInstaller builds native binaries:
a Windows `.exe` cannot become a macOS executable by changing an extension.
The release workflow therefore runs this script on Windows and macOS runners.

The backend deliberately has no API keys baked into it. Electron supplies
runtime configuration and a per-launch random local password instead.
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root "desktop-backend"
$entry = Join-Path $PSScriptRoot "backend_entry.py"

Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $out | Out-Null

python -m PyInstaller --noconfirm --clean --onefile `
  --name ohmwork-server `
  --distpath $out `
  --workpath (Join-Path $root ".desktop-pyinstaller-build") `
  --specpath (Join-Path $root ".desktop-pyinstaller-spec") `
  --paths $root `
  --collect-all spicelib `
  $entry

# $ErrorActionPreference does not fail a script on a native command's exit
# code, and this script once printed the success line below after PyInstaller
# had died on a locked work directory and produced NO executable at all. The
# executable existing is the claim, so it is checked, not announced.
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
$exe = Join-Path $out ($(if ($IsWindows -or $env:OS -eq "Windows_NT") { "ohmwork-server.exe" } else { "ohmwork-server" }))
if (-not (Test-Path $exe)) { throw "PyInstaller reported success but $exe does not exist" }

Write-Host "Desktop backend written to $out"
