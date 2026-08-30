# Building the macOS app (digital-only) — a brief for the session doing it

This file is the handoff for building Ohmwork's macOS `.dmg` on a real
Mac. It exists because the Windows machine that built everything else
cannot do this, and because the decisions below were already taken and
must not be re-litigated on the Mac.

## The goal, exactly

A `.dmg` from `npm run package:mac` in `desktop/`, containing the same
app as Windows with ONE deliberate difference: **analog is refused,
never answered.** When a user types an analog question, they must see
the refusal naming LTspice — which already happens when LTspice is not
found, and on macOS it is never found. Do not "fix" that.

## Decisions already taken — do not reopen

- **Digital-only on macOS.** LTspice exists for Mac, but not one
  baseline in this project has ever been measured on macOS, and this
  project does not claim numbers it has not measured. Enabling analog
  on Mac is a measurement project, not a build flag.
- **Logisim Evolution stays pinned at 4.1.0.** Every published number
  was measured against it. The release JAR is cross-platform and its
  sha256 pin in `logisim-bundle.json` is already correct for Mac.
- **Hashes are measured, never recalled.** When you add a macOS JDK to
  the bundle spec, download it first, hash what you downloaded, and pin
  that. A hash written from memory or from a web page you did not
  verify is how this project defines failure.

## The actual work (expect most of it in three files)

1. **`desktop/logisim-bundle.json`** — add a macOS (arm64) Temurin 21
   JDK entry beside the Windows one. Use Adoptium's API to find the
   asset, download, hash, pin. Keep the jlink module list AS IS: it is
   the vendor's own answer (read from their installed image), not a
   guess, and it is platform-independent.
2. **`desktop/fetch-logisim.ps1`** — either run it under `pwsh`
   (PowerShell for Mac) with platform branches, or write a faithful
   `fetch-logisim.sh`. Either way it must keep the script's one
   non-negotiable property: **it does not report success until the
   bundle EVALUATES a real circuit** — header plus 32 rows from
   `tests/fixtures/logisim/exp8_gates.circ` via `--tty table`. A bundle
   that exists but cannot evaluate is a broken installer.
3. **`desktop/ensure-bundle.mjs` and `desktop/main.mjs`** — both know
   the runtime's Java as `runtime/bin/java` vs `java.exe`; audit every
   path for platform assumptions. `ensure-bundle.mjs` must keep
   refusing to package when the bundle, backend, or page is missing —
   that guard caught a real would-be-broken release on its first CI
   run.

Then: build the backend with `desktop/build-backend.ps1` under `pwsh`
(or a faithful translation — PyInstaller must run ON the Mac), build
`web/` (`npm ci && npm run build`), and `npm run package:mac`.

## Acceptance — all of it, on the Mac

1. `python -m pytest` from the repo root: green, with simulator-needing
   tests skipping loudly (that is CI's normal state too).
2. The packaged app launches from the `.dmg`, its backend answers
   health, and it **solves a real digital question end to end** ("Design
   a 2-to-4 decoder with an active-high enable") with a real model key —
   the packaged Logisim, not a system one, doing the verification.
   `/api/status` in the app must report the digital evaluator as
   external.
3. An analog question ("design a series voltage regulator in LTspice…")
   is refused with the LTspice message — not answered, not crashed.
4. `desktop/dist/*.dmg` exists and is unsigned (signing is a separate,
   owner-gated step).

## When it works

Commit the spec/script/path changes (never `desktop/vendor/` — it is
gitignored on every platform for a reason). Note in the commit what was
measured: the JDK asset name and its hash's provenance, and the
evaluate-check output. The `.dmg` can then be attached to the existing
GitHub release alongside the Windows installer.
