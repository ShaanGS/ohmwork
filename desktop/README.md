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
the top-right corner and paste keys for every provider you want to use on the
same screen. Blank fields are ignored. Press **save keys** once; the app
restarts and the provider pool can move to another configured provider when
one is rate limited. Existing keys are never displayed: leave a field marked
`saved` blank to keep it, or enter a replacement. Keys are stored encrypted by
Windows/macOS and are not placed in `.env`.

The development shell starts `python -m ohmwork.server`. Set `OHMWORK_PYTHON`
if `python` is not the interpreter that has Ohmwork's dependencies.

## Release work still required

### 1. The evaluator is not in the installer, and without it nothing verifies

**This is the release blocker, and it is bigger than the packaging one.** The
installer ships the Python backend and the page. It does not ship **Logisim
Evolution or a JRE**, and Logisim is the entire reason an answer here is worth
anything: it is the outside tool that checks the emitted file.

What happens on a machine that does not already have it, measured rather than
assumed: `locate_logisim()` finds nothing, `best_available_backend()` falls
back to `InternalLogicBackend`, and its `truth_table` raises
`NotImplementedError` on the first solve. Every question fails. The app looks
installed and works for nothing.

Three honest ways out, none of them chosen here:

- **Bundle it.** The jar is ~50 MB and needs Java 21; a `jlink` runtime with
  only the modules Logisim uses is ~40 MB. Biggest installer, zero
  instructions, and the version stays PINNED — which matters, because every
  published number in this project was measured against 4.1.0.
- **Require it, and check at startup.** Refuse to start with a message naming
  the download, the way the server refuses to start without a password. Small
  installer, one instruction, fails closed and loudly.
- **Ship it broken with a warning.** Not acceptable: this project's whole
  claim is that an outside tool checked the answer, and an app that silently
  cannot check anything is the failure it exists to prevent.

Until one of those lands, the desktop app is a **development-mode tool for a
machine that already has Logisim installed**, which is exactly what the
"Running it from this clone" section above describes.

### 2. A platform-native backend executable

An installer must package the Python backend as a platform-native executable.
That is intentionally not faked here: a Windows executable cannot make a Mac
app, and macOS must be built and tested on macOS. The required next step is a
PyInstaller build on each target platform, copied into `desktop-backend/` for
Electron Builder's `extraResources` section.

### 3. Signing

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
3. Add all desired model keys once through the key icon, then ask a question.
