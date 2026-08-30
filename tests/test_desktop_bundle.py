"""The bundled evaluator: the desktop installer's Logisim + jlink runtime.

The release blocker (desktop/README.md, PRD gap 1) is that an installed app
with no Logisim cannot verify anything -- every question fails and the app
looks installed. The fix is to ship the evaluator INSIDE the installer:
the 4.1.0 all-in-one JAR plus a jlink'd Java 21 runtime, produced by
desktop/fetch-logisim.ps1 into desktop/vendor/logisim/.

These tests are the acceptance for that bundle, and they run against the
REAL vendored files, not a mock. They skip when the vendor directory is
absent (CI, or a clone that has not run the fetch script) -- and per the
unrun-check rule the skip reason names the script that would make them run.

What is pinned here and why:

- The JAR's sha256. "Bundled Logisim" must mean THE evaluator every
  published number was measured against, 4.1.0 -- not whatever jar is lying
  around. The hash lives in desktop/logisim-bundle.json, the single copy
  both the fetch script and this test read; a second copy here would be the
  two-texts-drift failure this project documents elsewhere.
- The runtime's Java version. Logisim Evolution 4.1.0 needs Java 21, and a
  jlink image records its own version in its `release` file.
- The evaluation itself. A jar that exists and hashes right but cannot
  evaluate a circuit is still a broken installer, so the bundled runtime +
  jar are run on exp8_gates.circ -- a student's hand-drawn file -- through
  the same LogisimBackend the app uses, and the 32 rows are checked against
  the geometrically recovered logic, exactly as test_logisim_backend does
  for the installed launcher.
"""

import json
import hashlib
from pathlib import Path

import pytest

from ohmwork.logisim_backend import LogisimBackend, locate_logisim

REPO = Path(__file__).parent.parent
SPEC_PATH = REPO / "desktop" / "logisim-bundle.json"
VENDOR = REPO / "desktop" / "vendor" / "logisim"
FIXTURE = Path(__file__).parent / "fixtures" / "logisim" / "exp8_gates.circ"

INPUTS = ("E IN", "D3", "D2", "D1", "D0")
OUTPUTS = ("OUT 1", "OUT 2", "V")


def _spec():
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def _bundle_present():
    if not SPEC_PATH.is_file():
        return False
    spec = _spec()
    jar = VENDOR / spec["jar_filename"]
    java = VENDOR / "runtime" / "bin" / "java.exe"
    java_posix = VENDOR / "runtime" / "bin" / "java"
    return jar.is_file() and (java.is_file() or java_posix.is_file())


needs_bundle = pytest.mark.skipif(
    not _bundle_present(),
    reason="no vendored Logisim bundle; run desktop/fetch-logisim.ps1 to "
           "build it (these tests are the installer bundle's acceptance)",
)


def _bundled_java():
    exe = VENDOR / "runtime" / "bin" / "java.exe"
    return exe if exe.is_file() else VENDOR / "runtime" / "bin" / "java"


def recovered_logic(en, d3, d2, d1, d0):
    """The logic read out of exp8_gates.circ's geometry -- see
    test_logisim_backend, which is the reference for this comparison."""
    return (en & (d3 | d2),
            en & (d3 | (d1 & (1 - d2))),
            en & (d3 | d2 | d1 | d0))


# ------------------------------------------------------------- the spec file
#
# These run everywhere, bundle or not: the spec is committed, and a broken
# spec breaks the fetch script on the machine that needs it most.


def test_the_spec_pins_the_evaluator_this_project_measured_against():
    spec = _spec()
    assert spec["jar_filename"] == "logisim-evolution-4.1.0-all.jar"
    assert "4.1.0" in spec["jar_url"]
    # sha256 is 64 hex chars; a truncated pin verifies nothing.
    assert len(spec["jar_sha256"]) == 64
    assert len(spec["jdk_sha256"]) == 64
    assert spec["java_version"].startswith("21")


def test_the_module_list_is_the_vendors_own():
    """The 12 modules were read from the installed 4.1.0 jpackage image's
    runtime/release file on 2026-08-30 -- the vendor's measured answer to
    'which modules does Logisim need', not our guess. java.desktop is the
    one that would be tempting to trim and must never be: Swing classes
    load even in --tty mode."""
    modules = _spec()["modules"]
    assert "java.base" in modules
    assert "java.desktop" in modules
    assert len(modules) == 12


# ------------------------------------------------------------- the bundle


@needs_bundle
def test_the_vendored_jar_is_byte_identical_to_the_pinned_release():
    spec = _spec()
    digest = hashlib.sha256((VENDOR / spec["jar_filename"]).read_bytes())
    assert digest.hexdigest().lower() == spec["jar_sha256"].lower()


@needs_bundle
def test_the_vendored_runtime_is_java_21():
    release = (VENDOR / "runtime" / "release").read_text(encoding="utf-8")
    assert 'JAVA_VERSION="21' in release


@needs_bundle
def test_the_bundle_evaluates_a_real_file_correctly(monkeypatch):
    """The whole point: the installer's own evaluator, on a student's own
    file, through the app's own backend class -- 32 rows against the
    geometrically recovered logic."""
    monkeypatch.setenv("OHMWORK_JAVA", str(_bundled_java()))
    spec = _spec()
    table = LogisimBackend(exe=VENDOR / spec["jar_filename"]).truth_table(
        FIXTURE, INPUTS, OUTPUTS)

    assert len(table.rows) == 32
    assert table.verification == "external"
    for row in table.as_dicts():
        expected = recovered_logic(row["E IN"], row["D3"], row["D2"],
                                   row["D1"], row["D0"])
        assert (row["OUT 1"], row["OUT 2"], row["V"]) == expected, row


@needs_bundle
def test_the_bundle_agrees_with_the_installed_launcher_when_both_exist(
        monkeypatch):
    """Two launchers, one pinned version: the jar-through-our-runtime and
    the jpackage exe must produce identical rows. Skips (and says so) on a
    machine with no installed Logisim -- there the previous test is the
    evidence."""
    try:
        installed = locate_logisim()
    except FileNotFoundError:
        pytest.skip("no installed Logisim to compare the bundle against")
    if installed.suffix.lower() == ".jar":
        pytest.skip("the installed Logisim IS a jar; nothing independent "
                    "to compare against")

    exe_table = LogisimBackend(exe=installed).truth_table(
        FIXTURE, INPUTS, OUTPUTS)
    monkeypatch.setenv("OHMWORK_JAVA", str(_bundled_java()))
    spec = _spec()
    jar_table = LogisimBackend(exe=VENDOR / spec["jar_filename"]).truth_table(
        FIXTURE, INPUTS, OUTPUTS)
    assert jar_table.rows == exe_table.rows
