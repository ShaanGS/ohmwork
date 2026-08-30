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

## The bundled evaluator (PRD gap 1 — landed 2026-08-30, Windows)

The installer ships Logisim Evolution inside it, because an installed app
that cannot verify anything is the failure this project exists to prevent
(measured, not assumed: with no Logisim, `best_available_backend()` falls
back to an engine that raises on the first solve — every question fails and
the app looks installed).

How it is built, and what each piece proves:

- `logisim-bundle.json` is the **single copy of every pin**: the 4.1.0
  release JAR's sha256 (measured from the GitHub artifact), the Temurin
  JDK 21 used at build time, and the 12-module list read from the vendor's
  own jpackage image — their measured answer to "which modules does Logisim
  need", not our guess.
- `fetch-logisim.ps1` downloads both with hash verification, runs `jlink`,
  and refuses to report success until the bundle has **evaluated a real
  circuit** (`exp8_gates.circ`, a student's hand-drawn file). Existence is
  never the acceptance. Output lands in `desktop/vendor/logisim/`
  (gitignored, 97.7 MB measured, rebuilt reproducibly from the pins).
- `ensure-bundle.mjs` runs before every `electron-builder` invocation and
  fails the build loudly if any installer ingredient is missing — bundle,
  built page, or PyInstaller backend.
- `main.mjs` points `OHMWORK_LOGISIM` at the bundled JAR and `OHMWORK_JAVA`
  at the bundled runtime (the `.jar`-through-java path the backend already
  supports for the Linux container). A **packaged** app missing its bundle
  refuses to start, the way the server refuses to start without a password.
  Dev mode falls back to a system-installed Logisim.
- `tests/test_desktop_bundle.py` is the acceptance: hash identity with the
  pin, Java 21 in the runtime's own `release` file, 32 correct rows on the
  hand-drawn fixture through the app's own backend class, and row-for-row
  agreement with the installed jpackage launcher when both exist.

One finding worth keeping: the release JAR and the jar inside the winget msi
are the **same version but not the same bytes** (200 bytes apart — separate
CI rebuilds). Byte identity with the installed launcher is therefore
unavailable, and behavioural agreement on a real file is the check that
replaces it.

macOS is not covered yet: its bundle must be built on a Mac (as must the
PyInstaller backend), with the Temurin mac-aarch64 JDK added to the spec.

## Release work still required

### 2. A platform-native backend executable — DONE for Windows 2026-08-30

`build-backend.ps1` produces `desktop-backend/ohmwork-server.exe` (onefile
PyInstaller, ~80 MB), and it is PROVEN, not just built: it refuses to start
without `OHMWORK_PASSWORD`, serves the API with one, and has run real
solves end to end in a browser — digital (priority encoder, and the 7447
against its probed behaviour) and analog (spicelib and the LTspice spawn
work inside the frozen bundle, which nothing else had tested). The script
checks that the executable exists rather than announcing success: it once
printed its success line over a PyInstaller run that had died and left
nothing.

Two facts a rebuild needs: stop any running `ohmwork-server.exe` first (a
running image cannot be replaced and even locks electron-builder's rmdir of
an old `dist/`), and OneDrive can hold a transient lock on the fresh exe —
retry the `dist` removal rather than diagnosing a phantom process.

macOS still requires a Mac: a Windows executable cannot make a Mac app.

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
