<#!
.SYNOPSIS
Run Ohmwork Desktop from source on this machine.

.DESCRIPTION
This is the everyday launcher until a signed installer exists. It rebuilds the
small React frontend, then starts Electron. Leave the PowerShell window open:
closing it closes the app and its private loopback backend.

One-time setup is documented in README.md. This script never reads `.env`,
never writes API keys, and never opens a public network port.
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Push-Location (Join-Path $root "web")
try {
  npm run build
} finally {
  Pop-Location
}

Push-Location $PSScriptRoot
try {
  npm run dev
} finally {
  Pop-Location
}
