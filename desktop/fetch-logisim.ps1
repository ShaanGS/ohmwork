# Build the bundled evaluator: desktop/vendor/logisim/
#
#   logisim-evolution-4.1.0-all.jar   the pinned release JAR
#   runtime/                          a jlink'd Temurin 21 image that runs it
#
# Every download is verified against the sha256 pinned in
# logisim-bundle.json, and the script does not report success until the
# bundle has EVALUATED a real circuit (tests/fixtures/logisim/exp8_gates.circ,
# a student's hand-drawn file). A bundle that exists but cannot evaluate is
# still a broken installer, so existence is never the acceptance.
#
# Windows only for now: macOS packaging must happen on a Mac anyway (the
# PyInstaller backend build), and the JDK pinned here is the win-x64 zip.
#
# Idempotent: re-running with a good bundle just re-verifies and exits.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $here
$spec = Get-Content (Join-Path $here "logisim-bundle.json") -Raw | ConvertFrom-Json
$vendor = Join-Path $here "vendor\logisim"
$jarPath = Join-Path $vendor $spec.jar_filename
$runtime = Join-Path $vendor "runtime"
$javaExe = Join-Path $runtime "bin\java.exe"
$fixture = Join-Path $repo "tests\fixtures\logisim\exp8_gates.circ"

function Assert-Sha256($path, $expected, $what) {
    $actual = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $expected.ToLower()) {
        throw "$what at $path has sha256 $actual, expected $expected. Refusing to bundle it."
    }
}

function Assert-BundleEvaluates {
    # The acceptance: the bundled runtime + jar must evaluate a real file.
    # 5 inputs -> a 32-row table plus a header. The exit code is deliberately
    # not trusted (it came back empty on success; see logisim_backend.py).
    # Start-Process with file redirection, NOT `2>$null` on the native call:
    # PowerShell 5.1 wraps redirected native stderr in ErrorRecords, and with
    # ErrorActionPreference=Stop the EXPECTED "Old file format" compatibility
    # warning became a terminating error -- the check died on the very line
    # that proved Logisim had loaded the file.
    $stdoutFile = Join-Path $env:TEMP "ohmwork-bundle-check-out.txt"
    $stderrFile = Join-Path $env:TEMP "ohmwork-bundle-check-err.txt"
    $proc = Start-Process -FilePath $javaExe -NoNewWindow -Wait -PassThru `
        -ArgumentList @("-jar", "`"$jarPath`"", "--no-splash", "--tty",
                        "table", "`"$fixture`"") `
        -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile
    $out = Get-Content $stdoutFile -ErrorAction SilentlyContinue
    Remove-Item $stdoutFile, $stderrFile -ErrorAction SilentlyContinue
    $rows = @($out | Where-Object { $_.Trim() -ne "" })
    if ($rows.Count -ne 33) {
        throw ("the bundle ran but produced $($rows.Count) non-empty lines " +
               "for exp8_gates.circ where a header + 32 rows were expected. " +
               "The bundle cannot be trusted; not reporting success.")
    }
    Write-Host "bundle evaluated exp8_gates.circ: header + 32 rows, as expected"
}

if ((Test-Path $jarPath) -and (Test-Path $javaExe)) {
    Assert-Sha256 $jarPath $spec.jar_sha256 "existing vendored JAR"
    Assert-BundleEvaluates
    Write-Host "bundle already present and verified: $vendor"
    exit 0
}

New-Item -ItemType Directory -Force $vendor | Out-Null

# --- the JAR, verified against the pin measured from the installed 4.1.0 ---
if (-not (Test-Path $jarPath)) {
    Write-Host "downloading $($spec.jar_filename) ..."
    Invoke-WebRequest $spec.jar_url -OutFile $jarPath
}
Assert-Sha256 $jarPath $spec.jar_sha256 "downloaded JAR"
Write-Host "JAR verified: $($spec.jar_filename)"

# --- a JDK to run jlink; build-time only, never shipped ---
$work = Join-Path $env:TEMP ("ohmwork-jlink-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory $work | Out-Null
try {
    $zip = Join-Path $work "jdk.zip"
    Write-Host "downloading Temurin $($spec.jdk_release) (build-time only) ..."
    Invoke-WebRequest $spec.jdk_url -OutFile $zip
    Assert-Sha256 $zip $spec.jdk_sha256 "downloaded JDK zip"
    Expand-Archive $zip -DestinationPath $work
    $jlink = Get-ChildItem $work -Recurse -Filter "jlink.exe" | Select-Object -First 1
    if (-not $jlink) { throw "no jlink.exe inside the extracted JDK" }

    if (Test-Path $runtime) { Remove-Item -Recurse -Force $runtime }
    Write-Host "jlink: building the runtime from the vendor's measured module list ..."
    & $jlink.FullName --add-modules ($spec.modules -join ",") `
        --strip-debug --no-header-files --no-man-pages --compress zip-6 `
        --output $runtime
    if ($LASTEXITCODE -ne 0) { throw "jlink failed with exit code $LASTEXITCODE" }
}
finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

Assert-BundleEvaluates
$mb = [math]::Round(((Get-ChildItem $vendor -Recurse -File |
    Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "bundle built and verified: $vendor ($mb MB)"
