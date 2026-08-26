# Ohmwork Desktop

The desktop app is an Electron shell around the existing verified Python
backend. It is deliberately not a second implementation of the solver.

## Security model

- The backend binds only to `127.0.0.1` on a random port.
- A fresh random password is generated for each launch. It is held in the
  Electron main process and never reaches the React renderer.
- The renderer has `nodeIntegration: false`, `contextIsolation: true`,
  `sandbox: true`, no Electron IPC bridge, blocked popups, blocked navigation,
  and denied browser permissions.
- No port is exposed to a LAN or the public internet. Network traffic is only
  the HTTPS request to the model provider selected by the user.

This protects a user from hosting and tunnel risks. It does not protect an
already-compromised local OS, and it does not make model-provider requests
private from that provider.

## Running it from this clone

### First time only

```powershell
cd web
npm install
npm run build
cd ..\desktop
npm install
```

From the project root, also install the Python side once:

```powershell
python -m pip install -e ".[web,llm]"
```

### Every time, until a signed installer exists

From the project root:

```powershell
powershell -ExecutionPolicy Bypass -File desktop\run-desktop.ps1
```

Leave that PowerShell window open while using Ohmwork; closing it closes the
desktop app and its private backend. On first launch, click the key icon in
the top-right corner, select your model provider, paste its API key, and press
**save**. The app restarts automatically. That key is stored encrypted by
Windows/macOS and is not placed in `.env`.

The development shell starts `python -m ohmwork.server`. Set `OHMWORK_PYTHON`
if `python` is not the interpreter that has Ohmwork's dependencies.

## Release work still required

An installer must package the Python backend as a platform-native executable.
That is intentionally not faked here: a Windows executable cannot make a Mac
app, and macOS must be built and tested on macOS. The required next step is a
PyInstaller build on each target platform, copied into `desktop-backend/` for
Electron Builder's `extraResources` section.

Unsigned apps are usable for testing, but macOS Gatekeeper and Windows
SmartScreen warn. A trusted public Mac build needs an Apple Developer
certificate and notarization; a trusted Windows build needs code signing.

On this development machine, Windows Application Control blocks the unsigned
packaged executable outright. That is expected policy behaviour and is a
useful release gate: development mode was verified, but a distributable build
must be signed before asking classmates to trust it.

Once signed installers are released, everyday use becomes simply:

1. Install `Ohmwork Setup … .exe` on Windows, or drag `Ohmwork.app` from the
   notarized DMG to Applications on macOS.
2. Open **Ohmwork** from the Start menu, Applications, or Spotlight.
3. Add a model key once through the key icon, then ask a question.
