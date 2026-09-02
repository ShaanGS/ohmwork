"""Digital evaluation backends. Logisim when present; our own engine offline.

THIS MODULE RESOLVES THE EVALUATOR ASYMMETRY, so read what it does and does
not claim.

The problem (CLAUDE.md, "The evaluator asymmetry"): for LTspice an outside
tool computes the answer, so a bug in our emitter or parser shows up as
disagreement. For Logisim we believed there was no batch simulator, which
would mean OUR engine computed both the result and the expected value used to
check it -- a defect in it would break both sides identically and they would
agree forever.

That belief was wrong. Logisim Evolution has a working command-line mode:

    logisim-evolution --tty table <file.circ>

It loads the circuit, enumerates EVERY input combination itself, and prints
the resulting truth table. Nothing about our parser, our netlist, or our
expectations is involved. Verified 2026-08-24 against
tests/fixtures/logisim/exp8_gates.circ -- a file we did not create -- where
all 32 rows x 3 outputs matched the logic recovered geometrically.

So LogisimBackend declares verification = "external", the same standing as
LTspice. The internal engine stays as the offline fallback and declares
"internal", and any report says which one ran.

Empirical facts about the tool, all measured on 4.1.0, none from docs:

- The jpackage launcher DOES accept CLI arguments and DOES write to stdout,
  unlike LTspice.exe. But its bundled runtime is a jlink image with NO
  java.exe, so `java -jar` is not available -- drive the .exe.
- `--tty table` needs no circuit name and no test-vector file. It is a better
  fit than `--test-vector`, which hung for 90s and was abandoned.
- `--tty stats` prints a component census. Cheap independent cross-check of
  our own parse, and it is used as one.
- The process exit code came back empty. DO NOT trust it. A run counts as
  successful only if a table parses with the expected columns and 2**n rows
  -- the same doctrine as "a raw file existing proves nothing, traces in it
  do".
- Opening an original-Logisim (2.7.1) file prints a compatibility WARNING on
  stderr and still evaluates correctly. That warning is expected, not a
  failure.
- **Labels are rewritten to VHDL-safe names.** A pin labelled "E IN" comes
  back as "E_IN_ef467da7": spaces to underscores, plus a hash suffix. The
  hash is not reproducible by us, so the emitter must use labels that need no
  rewriting (see SAFE_LABEL); column matching falls back to prefix matching
  only so that hand-drawn fixtures still work.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ohmwork import logisim_symbols

#: Re-exported from logisim_symbols, where the format rules live.
SAFE_LABEL = logisim_symbols.SAFE_LABEL

CANDIDATE_PATHS = [
    r"%ProgramFiles%\logisim-evolution\logisim-evolution.exe",
    r"%ProgramFiles(x86)%\logisim-evolution\logisim-evolution.exe",
    r"%LOCALAPPDATA%\Programs\logisim-evolution\logisim-evolution.exe",
]


class DigitalEvaluationError(Exception):
    """Logisim ran but produced nothing we are willing to believe."""


@dataclass(frozen=True)
class TruthTable:
    """One exhaustive evaluation of a combinational circuit."""

    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    rows: tuple[tuple[int, ...], ...]      # inputs then outputs, in order
    backend: str
    verification: str
    notes: tuple[str, ...] = ()

    def as_dicts(self):
        names = self.inputs + self.outputs
        return [dict(zip(names, row)) for row in self.rows]


def locate_logisim() -> Path:
    """Find the Logisim Evolution launcher, explicit override first."""
    override = os.environ.get("OHMWORK_LOGISIM")
    if override:
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError(
                f"OHMWORK_LOGISIM is set to {override!r} but no file exists there"
            )
        return path

    searched = []
    for template in CANDIDATE_PATHS:
        path = Path(os.path.expandvars(template))
        searched.append(str(path))
        if path.is_file():
            return path
    if found := shutil.which("logisim-evolution"):
        return Path(found)

    raise FileNotFoundError(
        "Logisim Evolution not found. Install it (winget install --id "
        "logisim-evolution.logisim-evolution) or set OHMWORK_LOGISIM to the "
        "launcher. Looked in: " + "; ".join(searched)
    )


def logisim_command(exe, args) -> list[str]:
    """The command line for one Logisim run.

    Two shapes, because the two platforms ship it differently. Windows has
    the jpackage launcher, which bundles its own Java 21 and has NO java.exe
    inside it -- so it is driven directly. Linux hosting has the all-in-one
    JAR from the release page, which needs a JVM naming it.

    Deriving this from the FILE EXTENSION rather than from the platform is
    deliberate: it is the file in hand that decides, and a Windows machine
    with a jar should work the same way.
    """
    exe = Path(exe)
    if exe.suffix.lower() == ".jar":
        java = os.environ.get("OHMWORK_JAVA") or shutil.which("java")
        if not java:
            raise FileNotFoundError(
                f"{exe.name} is a JAR and no java was found to run it. "
                f"Install a JRE (21 or newer) or set OHMWORK_JAVA.")
        return [java, "-jar", str(exe), *args]
    return [str(exe), *args]


def _match_column(wanted: str, columns: list[str]) -> str:
    """Find `wanted` among Logisim's column names, allowing for VHDL mangling.

    Exact match wins. Otherwise fall back to the rewrite Logisim performs on
    an unsafe label: spaces become underscores and a hash suffix is appended,
    so "OUT 1" arrives as "OUT_1_140ad176". Prefix matching is deliberately
    the LAST resort and must be unambiguous.
    """
    if wanted in columns:
        return wanted
    stem = wanted.replace(" ", "_")
    candidates = [c for c in columns if c == stem or c.startswith(stem + "_")]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise DigitalEvaluationError(
            f"pin {wanted!r} does not appear in Logisim's output columns "
            f"{columns}. If the label contains characters Logisim rewrites, "
            f"give it a name matching {SAFE_LABEL.pattern}."
        )
    raise DigitalEvaluationError(
        f"pin {wanted!r} matches more than one Logisim column: {candidates}"
    )


def parse_tty_table(text: str, inputs, outputs, *, backend, verification,
                    notes=()) -> TruthTable:
    """Turn `--tty table` output into a TruthTable, in OUR column order.

    Logisim chooses its own column order; the caller's order is what the rest
    of the system uses, so the columns are remapped rather than assumed.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise DigitalEvaluationError("Logisim produced no table output at all")

    columns = lines[0].split()
    index = {name: columns.index(_match_column(name, columns))
             for name in list(inputs) + list(outputs)}

    rows = []
    for lineno, line in enumerate(lines[1:], start=2):
        cells = line.split()
        if len(cells) != len(columns):
            raise DigitalEvaluationError(
                f"line {lineno} has {len(cells)} values for {len(columns)} "
                f"columns: {line!r}"
            )
        try:
            rows.append(tuple(int(cells[index[name]])
                              for name in list(inputs) + list(outputs)))
        except ValueError:
            raise DigitalEvaluationError(
                f"line {lineno} holds a non-binary value, which usually means "
                f"a floating or conflicting node: {line!r}"
            ) from None

    expected = 2 ** len(inputs)
    if len(rows) != expected:
        raise DigitalEvaluationError(
            f"expected {expected} rows for {len(inputs)} inputs, got {len(rows)}. "
            f"--tty table enumerates combinational circuits exhaustively, so a "
            f"short table usually means the circuit is not purely combinational."
        )
    seen = {row[:len(inputs)] for row in rows}
    if len(seen) != expected:
        raise DigitalEvaluationError(
            f"input combinations are not distinct: {len(seen)} unique of {expected}"
        )

    # Sort by the input tuple in OUR column order. Logisim enumerates in the
    # order of ITS OWN columns, which differs between two files computing the
    # same function -- our emitted Q2 counts up I3-first, the student's file
    # counts up D0-first. Comparing them as sequences would report a
    # difference that is not one.
    #
    # This is the .step lesson again (CLAUDE.md incident 3): never trust the
    # order a tool emits rows in; index by the values. A truth table is a
    # FUNCTION, and two files implement the same function when they agree
    # per input combination, whatever order each chose to walk them in.
    rows.sort()
    return TruthTable(tuple(inputs), tuple(outputs), tuple(rows),
                      backend=backend, verification=verification, notes=tuple(notes))


class LogisimBackend:
    """An OUTSIDE evaluator. Its disagreement is evidence about our code."""

    name = "logisim-evolution"
    verification = "external"

    def __init__(self, exe: Path | None = None):
        self.exe = Path(exe) if exe else locate_logisim()

    def truth_table(self, circ_path, inputs, outputs, timeout: float = 120):
        circ_path = Path(circ_path)
        # The table is 2**n rows and Logisim writes every one. MEASURED
        # 2026-09-02: 20 inputs = 1,048,576 rows took 129.6 s and 75 MB of
        # stdout on this machine, so a flat 120 s would kill a healthy run
        # of anything past about 19 inputs. Scale with the table, never
        # below the caller's figure.
        timeout = max(timeout, 0.0005 * (2 ** len(inputs)))
        try:
            proc = subprocess.run(
                logisim_command(self.exe, ["--no-splash", "--tty", "table",
                                           str(circ_path)]),
                timeout=timeout, capture_output=True, text=True,
            )
        except subprocess.TimeoutExpired:
            raise DigitalEvaluationError(
                f"Logisim timed out after {timeout}s on {circ_path.name}"
            ) from None

        notes = []
        stderr = proc.stderr or ""
        if "Old file format" in stderr:
            notes.append(
                "read in compatibility mode: the file was written by original "
                "Logisim (2.7.1), not Logisim Evolution"
            )
        # NOTE: proc.returncode is deliberately not checked. It came back
        # empty on a successful run; the table is the evidence.
        return parse_tty_table(proc.stdout or "", inputs, outputs,
                               backend=self.name, verification=self.verification,
                               notes=notes)

    def component_census(self, circ_path, timeout: float = 120) -> dict[str, int]:
        """`--tty stats`: how many of each component Logisim thinks are there.

        An independent count to check our geometric parse against. It catches
        a component we dropped or invented, which the truth table alone would
        not necessarily reveal.
        """
        proc = subprocess.run(
            logisim_command(self.exe, ["--no-splash", "--tty", "stats",
                                       str(circ_path)]),
            timeout=timeout, capture_output=True, text=True,
        )
        census = {}
        for line in (proc.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0].strip().isdigit():
                name = parts[2].strip()
                if name and not name.startswith("TOTAL"):
                    census[name] = int(parts[0].strip())
        if not census:
            raise DigitalEvaluationError("could not parse a component census")
        return census


class InternalLogicBackend:
    """The offline fallback: our own evaluator.

    Declares "internal" because it computes the result AND anything the
    result would be checked against. It must keep working with no Logisim
    installed -- but whenever Logisim IS available, prefer it, because only
    an outside evaluator can catch a bug in this one.
    """

    name = "ohmwork-logic"
    verification = "internal"

    def truth_table(self, circ_path, inputs, outputs, timeout: float = 120):
        raise NotImplementedError(
            "the internal logic engine is not built yet; install Logisim "
            "Evolution and use LogisimBackend, which is externally verified"
        )


def best_available_backend():
    """Logisim if installed, our own engine otherwise.

    Callers must surface which one they got: the two carry different
    verification standing and a report that hides the difference is the
    failure this whole distinction exists to prevent.
    """
    try:
        return LogisimBackend()
    except FileNotFoundError:
        return InternalLogicBackend()
