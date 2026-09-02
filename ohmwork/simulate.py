"""Run a generated .asc and extract results. Backend protocol + two backends.

Roles are asymmetric by design (CLAUDE.md, "Simulate layer decisions"):

  LTspiceBackend  - authoritative. Runs the actual deliverable file with
                    the actual devices from LTspice's libraries. The only
                    numbers ever shown to the user.
  NgspiceBackend  - keeps the pipeline testable on Linux/CI. Simulates a
                    netlist rebuilt by our geometric parser, with
                    synthesised model cards. Its numbers are its own
                    baseline and are never reconciled with LTspice's.

Raw-file plumbing is spicelib's job, not ours: LTspice runs with -ascii
and both backends' outputs are read with spicelib.RawRead.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from spicelib import RawRead

from ohmwork.parser import parse_asc_file
from ohmwork.symbols import pins_of

# Executable names vary by version: scad3.exe (IV), XVIIx64.exe (XVII),
# LTspice.exe (24.x+). Searched in order after the OHMWORK_LTSPICE
# override; extend with real observed locations, not guesses.
CANDIDATE_PATHS = [
    r"%ProgramFiles%\ADI\LTspice\LTspice.exe",
    r"%LOCALAPPDATA%\Programs\ADI\LTspice\LTspice.exe",
    r"%ProgramFiles%\LTC\LTspiceXVII\XVIIx64.exe",
    r"%ProgramFiles(x86)%\LTC\LTspiceIV\scad3.exe",
]


class SimulationError(Exception):
    """The simulator ran but produced no usable result."""


@dataclass
class Results:
    traces: dict[str, list[float]]
    raw_path: Path
    log_path: Path

    def value(self, name: str) -> float:
        """First point of a trace, e.g. the .op value of V(vout)."""
        wave = self.traces.get(name)
        if wave is None:  # trace names differ in case between simulators
            lowered = {k.lower(): v for k, v in self.traces.items()}
            wave = lowered.get(name.lower())
        if wave is None:
            raise KeyError(
                f"no trace {name!r}; available: {sorted(self.traces)}"
            )
        return wave[0]


def locate_ltspice() -> Path:
    """Find the LTspice executable, explicit override first.

    The override matters: real installs end up in places no search would
    guess (this project's dev machine has it on F:\\).
    """
    override = os.environ.get("OHMWORK_LTSPICE")
    if override:
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError(
                f"OHMWORK_LTSPICE is set to {override!r} but no file "
                "exists there"
            )
        return path

    searched = []
    for template in CANDIDATE_PATHS:
        path = Path(os.path.expandvars(template))
        searched.append(str(path))
        if path.is_file():
            return path
    if which := shutil.which("LTspice"):
        return Path(which)

    raise FileNotFoundError(
        "LTspice not found. Set OHMWORK_LTSPICE to the full path of the "
        "executable, or install to one of: " + "; ".join(searched)
    )


def _read_results(raw_path: Path, log_path: Path) -> Results:
    raw = RawRead(str(raw_path))
    traces = {
        name: [float(v) for v in raw.get_trace(name).get_wave(0)]
        for name in raw.get_trace_names()
    }
    if not traces:
        # Verified empirically: on a hard failure (e.g. a voltage-source
        # loop) LTspice still writes a raw file, just with no traces in
        # it. A raw file existing proves nothing; traces do.
        raise SimulationError(
            f"{raw_path.name} contains no traces: the simulation failed. "
            f"End of log:\n{_log_tail(log_path)}"
        )
    return Results(traces=traces, raw_path=raw_path, log_path=log_path)


def _log_tail(log_path: Path, lines: int = 15) -> str:
    if not log_path.exists():
        return "(no log file was written)"
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


#: How large an ASCII raw file this will read before giving up on it.
#:
#: MEASURED, on the first live analog solve of the real Q3: a generated
#: bridge-rectifier-plus-C-L-C design with no damping resistance produced a
#: **335 MB** raw file from a 100 ms saved window. The SIMULATION finished
#: well inside its timeout -- what did not finish was parsing a third of a
#: gigabyte of ASCII, so the subprocess timeout never fired and the run hung
#: with no error and no ceiling.
#:
#: A file that size is a FACT ABOUT THE CIRCUIT, not about the file: the
#: solver only takes steps that small when something is ringing. So it is
#: reported as a design failure, in words a design can act on, rather than
#: absorbed silently or waited on forever.
MAX_RAW_BYTES = 64_000_000


def check_raw_size(raw_path: Path, limit: int | None = None) -> None:
    """Refuse to parse a result file that is too large to be about a circuit.

    Its own function so it can be tested without a 335 MB fixture, and so
    both backends can reach it. The message is written to be fed back to a
    design loop, so it says what to change rather than what went wrong.
    """
    limit = MAX_RAW_BYTES if limit is None else limit
    size = raw_path.stat().st_size
    if size <= limit:
        return
    raise SimulationError(
        f"LTspice wrote a {size / 1e6:.0f} MB result for {raw_path.name}, "
        f"over the {limit / 1e6:.0f} MB limit, so it was not parsed. A "
        f"transient run this large means the solver took very small steps "
        f"for a long time, which almost always means the circuit is RINGING: "
        f"an undamped LC, or a filter with no series resistance to damp it. "
        f"Add damping, or shorten the run."
    )


class LTspiceBackend:
    name = "ltspice"
    #: An OUTSIDE simulator computed this, so a bug in our emitter or our
    #: parser shows up as disagreement rather than as agreement with itself.
    #: Declared rather than left to a getattr default: every other backend in
    #: this project states its standing, and a backend that says nothing is
    #: indistinguishable from one nobody checked.
    verification = "external"

    def __init__(self, exe: Path | None = None):
        self.exe = Path(exe) if exe else locate_ltspice()

    def run(self, asc_path: Path, timeout: float = 120) -> Results:
        asc_path = Path(asc_path)
        raw_path = asc_path.with_suffix(".raw")
        log_path = asc_path.with_suffix(".log")
        raw_path.unlink(missing_ok=True)  # never read a stale result

        try:
            subprocess.run(
                [str(self.exe), "-b", "-ascii", str(asc_path)],
                timeout=timeout,
                capture_output=True,
            )
        except subprocess.TimeoutExpired:
            # Verified empirically: two active analysis directives (e.g.
            # .op AND .dc) hang LTspice batch mode until killed.
            raise SimulationError(
                f"LTspice timed out after {timeout}s on {asc_path.name}. "
                "A common cause is more than one active analysis "
                "directive in the schematic."
            ) from None
        if not raw_path.exists():
            raise SimulationError(
                f"LTspice produced no raw file for {asc_path.name}. "
                f"End of log:\n{_log_tail(log_path)}"
            )
        check_raw_size(raw_path)
        return _read_results(raw_path, log_path)


class NgspiceBackend:
    name = "ngspice"
    #: An OUTSIDE simulator computed this, so a bug in our emitter or our
    #: parser shows up as disagreement rather than as agreement with itself.
    #: Declared rather than left to a getattr default: every other backend in
    #: this project states its standing, and a backend that says nothing is
    #: indistinguishable from one nobody checked.
    verification = "external"

    def __init__(self, exe: Path | None = None):
        exe = exe or os.environ.get("OHMWORK_NGSPICE") or shutil.which("ngspice")
        if not exe:
            raise FileNotFoundError(
                "ngspice not found on PATH (or set OHMWORK_NGSPICE)"
            )
        self.exe = Path(exe)

    def run(self, asc_path: Path, timeout: float = 120) -> Results:
        # Simulate what the file actually encodes: rebuild the netlist
        # through the geometric parser, never from the emitter's intent.
        asc_path = Path(asc_path)
        circuit = parse_asc_file(asc_path)
        cir_path = asc_path.with_suffix(".cir")
        raw_path = asc_path.with_suffix(".ngraw")
        log_path = asc_path.with_suffix(".nglog")
        cir_path.write_text(to_spice(circuit), encoding="ascii")
        raw_path.unlink(missing_ok=True)

        subprocess.run(
            [str(self.exe), "-b", "-r", str(raw_path), "-o", str(log_path),
             str(cir_path)],
            timeout=timeout,
            capture_output=True,
        )
        if not raw_path.exists():
            raise SimulationError(
                f"ngspice produced no raw file for {cir_path.name}. "
                f"End of log:\n{_log_tail(log_path)}"
            )
        return _read_results(raw_path, log_path)


def to_spice(circuit: dict) -> str:
    """Circuit description -> SPICE netlist text.

    Node order per element follows the verified pin table order, which
    matches SPICE conventions: D anode cathode / Q C B E / V + -.
    """
    net_of_pin = {
        pin: net
        for net, pins in circuit["nets"].items()
        for pin in pins
    }
    lines = ["* ohmwork netlist"]
    for comp in circuit["components"]:
        nodes = " ".join(
            net_of_pin[f"{comp['ref']}.{pin.name}"]
            for pin in pins_of(comp["type"])
        )
        token = comp.get("value") or comp.get("part")
        lines.append(f"{comp['ref']} {nodes} {token}")
    lines += circuit.get("directives", [])
    lines.append(".end")
    return "\n".join(lines) + "\n"
