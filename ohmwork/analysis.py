"""The experiment plan: runs, measurements, regimes, and the report.

A lab question is an experiment, not a circuit: several simulation runs
of different types, scalar measurements picked out of them, quantities
derived across them, and regime assertions guarding that the numbers
mean what they claim to mean. This module owns that whole layer; the
schema and every contract here are pinned by tests/test_analysis.py.

Key decisions (empirically grounded, see CLAUDE.md):
  - The student receives ONE .asc: first run active, other runs as
    comment lines to uncomment. Verified: .step overrides an active
    .param (so the swept component keeps a default), but two active
    analysis directives hang LTspice batch mode (so comments say
    "comment out .op first", and generated files never carry two).
  - Scratch per-run files ("<run id>.asc") are an implementation
    detail of execute(), not a deliverable.
  - `at` selection: the plan names the point, the raw file's own axis
    trace locates it. Verified: LTspice runs .step LIST values in
    ascending numeric order regardless of written order, so plan-order
    indexing silently flips sweeps.
  - A violated regime marks results UNRELIABLE instead of discarding
    them: dropout behaviour is worth showing a student, flagged.
"""

import ast
import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ohmwork.emitter import write_asc

#: Run types partition by TARGET, and the partition is load-bearing: a
#: truth_table run needs a .circ and Logisim, everything else needs a .asc and
#: LTspice. One plan cannot contain both, because there would be no single
#: file to write and no single evaluator to hand it to. validate_plan says so
#: rather than letting execute() discover it halfway through.
ANALOG_RUN_TYPES = {"op", "dc", "param_sweep", "tran"}
DIGITAL_RUN_TYPES = {"truth_table"}
RUN_TYPES = ANALOG_RUN_TYPES | DIGITAL_RUN_TYPES
#: Analog regimes guard convergence-without-correctness; digital ones guard
#: the equivalent failure, a circuit that evaluates cleanly while being
#: structurally wrong. zener_in_breakdown/bjt_active are meaningless for a
#: gate network, and these three are meaningless for a SPICE circuit, so each
#: family names the devices or the run it applies to.
ANALOG_REGIMES = {"zener_in_breakdown", "bjt_active"}
DIGITAL_REGIMES = {"no_floating_inputs", "all_outputs_driven",
                   "no_combinational_loops"}
REGIME_ASSERTS = ANALOG_REGIMES | DIGITAL_REGIMES
# Directive keywords that make a schematic simulate something. At most
# one may ever be active in a generated file (.step is a modifier of an
# analysis, not an analysis itself).
ANALYSIS_KEYWORDS = {".op", ".dc", ".tran", ".ac"}


class AnalysisError(Exception):
    """The plan is malformed or its execution cannot be trusted."""


@dataclass
class Measurement:
    name: str
    #: None for a table, which has no single number. Nothing downstream may
    #: quietly substitute a zero: a manifest with a null value and a table
    #: beside it is honest, a manifest reading `"value": 0` is a lie.
    value: float | None
    run: str | None            # None for derived
    backend: str
    source: str                # "simulation" | "derived"
    formula: str | None = None
    at: dict | None = None
    definition: str | None = None
    reliable: bool = True
    warnings: tuple[str, ...] = ()
    # waveform_stats measurements: full statistics over the post-settle
    # window; `value` holds the mean so derived formulas can reference
    # the measurement by name.
    stats: dict | None = None
    # table measurements: {"columns": [...], "rows": [[...]], "notes": [...]}.
    # The whole exhaustive evaluation is ONE measurement, because that is what
    # the question asks for ("show the truth table") and what can be pinned.
    table: dict | None = None
    # "external": an outside simulator computed this, so a bug in our
    # emitter/parser shows up as disagreement. "internal": WE computed
    # it and also compute anything it would be checked against — a
    # strictly weaker guarantee (CLAUDE.md, "The evaluator asymmetry").
    verification: str = "external"


@dataclass(frozen=True)
class RegimeResult:
    """One regime assertion, EVALUATED — including when it held.

    A regime that passes silently is indistinguishable from one nobody ran,
    which is the failure "An unrun check must announce itself" exists to stop.
    So `examined` records what was actually looked at, in words, and it is
    rendered and published whether or not the assertion held.
    """

    assertion: str
    run: str
    device: str | None
    held: bool
    examined: str
    reasons: tuple[str, ...] = ()


class Experiment(Mapping):
    """The results of one experiment: measurements by name, plus the regimes.

    A Mapping so every existing caller keeps working (`results["vout"]`,
    `results.values()`), with `.regimes` alongside — the regime outcomes used
    to exist only as warnings attached to whatever they invalidated, so a
    regime that HELD left no trace anywhere.
    """

    def __init__(self, results: dict, regimes: list):
        self._results = results
        self.regimes = list(regimes)

    def __getitem__(self, key):
        return self._results[key]

    def __iter__(self):
        return iter(self._results)

    def __len__(self):
        return len(self._results)

    def __repr__(self):
        return (f"Experiment({len(self._results)} measurements, "
                f"{len(self.regimes)} regime checks)")


# ------------------------------------------------------------- validation


def validate_plan(circuit: dict, plan: dict) -> None:
    runs = {run["id"]: run for run in _checked_runs(circuit, plan)}
    named = []  # measurement names defined so far, in order
    for m in plan.get("measurements", []):
        kind = m.get("kind", "simulated")
        if kind == "derived":
            _check_derived(m, named)
            named.append(m["name"])
        elif kind == "regime":
            _check_regime_entry(circuit, m, runs)
        elif kind == "table":
            _check_table(circuit, m, runs)
            named.append(m["name"])
        elif kind == "waveform_stats":
            run = runs.get(m.get("run"))
            if run is None:
                raise AnalysisError(
                    f"measurement {m.get('name')!r} references unknown "
                    f"run {m.get('run')!r}"
                )
            if run["type"] != "tran":
                raise AnalysisError(
                    f"waveform_stats measurement {m['name']!r} needs a "
                    f"tran run, not {run['type']!r}"
                )
            named.append(m["name"])
        else:
            _check_simulated(m, runs)
            named.append(m["name"])


def _checked_runs(circuit, plan):
    refs = {c["ref"]: c for c in circuit["components"]}
    runs = plan.get("runs", [])
    if not runs:
        raise AnalysisError("plan has no runs")
    seen = set()
    for run in runs:
        if run["id"] in seen:
            raise AnalysisError(f"duplicate run id {run['id']!r}")
        seen.add(run["id"])
        if run["type"] not in RUN_TYPES:
            raise AnalysisError(
                f"run {run['id']!r} has unknown type {run['type']!r}"
            )
        if run["type"] == "dc" and run["sweep"]["source"] not in refs:
            raise AnalysisError(
                f"run {run['id']!r} sweeps {run['sweep']['source']!r}, "
                "which is not a component"
            )
        if run["type"] == "param_sweep":
            if run["component"] not in refs:
                raise AnalysisError(
                    f"run {run['id']!r} steps {run['component']!r}, "
                    "which is not a component"
                )
            if not run["values"]:
                raise AnalysisError(f"run {run['id']!r} has no values")
        if run["type"] == "tran" and not run.get("stop"):
            raise AnalysisError(f"tran run {run['id']!r} needs a 'stop'")
        if run["type"] == "truth_table":
            inputs = run.get("inputs") or []
            if not inputs:
                raise AnalysisError(
                    f"truth_table run {run['id']!r} needs 'inputs': the pins "
                    f"to enumerate, in column order"
                )
            unknown = [ref for ref in inputs if ref not in refs]
            if unknown:
                raise AnalysisError(
                    f"truth_table run {run['id']!r} lists inputs that are not "
                    f"components: {unknown}"
                )
            if len(set(inputs)) != len(inputs):
                raise AnalysisError(
                    f"truth_table run {run['id']!r} repeats an input"
                )
    kinds = {"digital" if r["type"] in DIGITAL_RUN_TYPES else "analog"
             for r in runs}
    if len(kinds) > 1:
        raise AnalysisError(
            "this plan mixes analog and digital runs "
            f"({', '.join(sorted(r['id'] + ':' + r['type'] for r in runs))}). "
            "They belong to different targets: a digital run needs a .circ "
            "and Logisim, an analog one a .asc and LTspice, so there is no "
            "single file to write and no single evaluator to run it"
        )
    return runs


def is_digital(plan: dict) -> bool:
    """Which evaluator this plan needs. Safe only because validate_plan
    refuses a mixed plan; call it after validation."""
    return any(run["type"] in DIGITAL_RUN_TYPES for run in plan["runs"])


def _check_simulated(m, runs):
    if m.get("run") not in runs:
        raise AnalysisError(
            f"measurement {m.get('name')!r} references unknown run "
            f"{m.get('run')!r}"
        )
    run = runs[m["run"]]
    if run["type"] == "tran":
        raise AnalysisError(
            f"measurement {m['name']!r}: a tran run has no single "
            "operating point — use kind 'waveform_stats'"
        )
    axis_name, axis_len = _axis(run)
    if axis_len > 1:
        if "at" not in m:
            raise AnalysisError(
                f"measurement {m['name']!r} reads sweep run {run['id']!r} "
                "and needs an 'at' selector"
            )
        _at_index(run, m["at"])  # raises if the point is not on the axis
    elif "at" in m:
        raise AnalysisError(
            f"measurement {m['name']!r} has 'at' but run {run['id']!r} "
            "has a single point"
        )


def _check_derived(m, named):
    for name in _formula_names(m["formula"]):
        if name not in named:
            raise AnalysisError(
                f"derived measurement {m['name']!r} references "
                f"{name!r}, which is not defined before it"
            )


def _check_table(circuit, m, runs):
    """A table measurement: every output pin, over a truth_table run."""
    run = runs.get(m.get("run"))
    if run is None:
        raise AnalysisError(
            f"table measurement {m.get('name')!r} references unknown run "
            f"{m.get('run')!r}"
        )
    if run["type"] != "truth_table":
        raise AnalysisError(
            f"table measurement {m['name']!r} needs a truth_table run, not "
            f"{run['type']!r}"
        )
    outputs = m.get("outputs") or []
    if not outputs:
        raise AnalysisError(
            f"table measurement {m['name']!r} needs 'outputs'"
        )
    refs = {c["ref"] for c in circuit["components"]}
    unknown = [ref for ref in outputs if ref not in refs]
    if unknown:
        raise AnalysisError(
            f"table measurement {m['name']!r} lists outputs that are not "
            f"components: {unknown}"
        )
    overlap = sorted(set(outputs) & set(run.get("inputs", [])))
    if overlap:
        raise AnalysisError(
            f"table measurement {m['name']!r} lists {overlap} as outputs, but "
            f"run {run['id']!r} drives them as inputs"
        )


def _check_regime_entry(circuit, m, runs):
    if m.get("run") not in runs:
        raise AnalysisError(
            f"regime assertion references unknown run {m.get('run')!r}"
        )
    if m.get("assert") not in REGIME_ASSERTS:
        raise AnalysisError(
            f"unknown regime assertion {m.get('assert')!r}"
        )
    # A digital regime is a property of the WHOLE circuit -- no floating
    # input anywhere, every output driven, no combinational loop -- so it
    # names no device, and naming one would be meaningless.
    if m["assert"] in DIGITAL_REGIMES:
        if m.get("device"):
            raise AnalysisError(
                f"{m['assert']} is a property of the whole circuit, not of "
                f"device {m['device']!r}"
            )
        run = runs[m["run"]]
        if run["type"] != "truth_table":
            raise AnalysisError(
                f"{m['assert']} applies to a truth_table run, not "
                f"{run['type']!r}"
            )
        return
    refs = {c["ref"]: c for c in circuit["components"]}
    device = refs.get(m.get("device"))
    if device is None:
        raise AnalysisError(
            f"regime assertion targets unknown device {m.get('device')!r}"
        )
    wanted = {
        "zener_in_breakdown": {"zener", "diode"},
        "bjt_active": {"npn", "pnp"},
    }[m["assert"]]
    if device["type"] not in wanted:
        raise AnalysisError(
            f"{m['assert']} does not apply to {m['device']} "
            f"({device['type']})"
        )
    run = runs[m["run"]]
    if run["type"] == "tran" and not run.get("settle"):
        raise AnalysisError(
            f"regime assertion on tran run {run['id']!r} requires a "
            "'settle' window: the device is out of its operating regime "
            "during startup by design, and asserting over startup would "
            "flag physics working correctly"
        )


# ---------------------------------------------------------- sweep geometry


def _axis(run) -> tuple[str | None, int | None]:
    """(axis name, point count) for a run, from the plan alone.
    A tran run's point count is the simulator's choice: (\"time\", None)."""
    if run["type"] == "op":
        return None, 1
    if run["type"] == "tran":
        return "time", None
    if run["type"] == "dc":
        s = run["sweep"]
        return s["source"], round((s["stop"] - s["start"]) / s["step"]) + 1
    return run["component"], len(run["values"])


def _at_index(run, at: dict) -> int:
    """Static (validation-time) check that 'at' names a point of the
    declared axis. The runtime index comes from _at_index_live."""
    axis_name, axis_len = _axis(run)
    if list(at) != [axis_name]:
        raise AnalysisError(
            f"run {run['id']!r} is swept over {axis_name!r}, "
            f"but 'at' selects {list(at)}"
        )
    value = at[axis_name]
    if run["type"] == "dc":
        s = run["sweep"]
        idx = (value - s["start"]) / s["step"]
        if idx != int(idx) or not 0 <= idx < axis_len:
            raise AnalysisError(
                f"{axis_name}={value} is not a point of run {run['id']!r} "
                f"({s['start']} to {s['stop']} step {s['step']})"
            )
        return int(idx)
    try:
        return run["values"].index(value)
    except ValueError:
        raise AnalysisError(
            f"{axis_name}={value!r} is not one of run {run['id']!r}'s "
            f"values {run['values']}"
        ) from None


def _axis_trace_name(run) -> str:
    """The trace under which the raw file records the sweep axis.
    Verified: the .dc source appears under its own name ("V1"); a
    stepped param appears as "<component>step" ("rlstep")."""
    if run["type"] == "dc":
        return run["sweep"]["source"]
    return f"{run['component']}step"


def _at_index_live(run, at: dict, results) -> int:
    """Locate the requested point in the axis the raw actually recorded.

    LTspice reorders .step LIST values (ascending numeric, verified
    empirically), so the plan's ordering means nothing at runtime; the
    axis trace is the file's own account of what ran where.
    """
    _at_index(run, at)  # shape/on-axis errors first, with plan context
    axis_name, n = _axis(run)
    target = parse_spice_number(at[axis_name])
    axis = _wave(results, _axis_trace_name(run), run["id"], n)
    matches = [
        i for i, v in enumerate(axis)
        if abs(v - target) <= 1e-9 + 1e-6 * max(abs(v), abs(target))
    ]
    if len(matches) != 1:
        raise AnalysisError(
            f"run {run['id']!r}: {axis_name}={at[axis_name]!r} matched "
            f"{len(matches)} points of the recorded axis {axis}"
        )
    return matches[0]


# -------------------------------------------------------- SPICE numbers

# Order matters: 'meg' must match before 'm' (milli). Trailing letters
# after a recognised suffix are ignored, as SPICE itself does ("1kOhm").
_NUMBER_RE = re.compile(
    r"^\s*([-+]?[0-9.]+(?:e[-+]?[0-9]+)?)(meg|[tgkmunpfµ])?",
    re.IGNORECASE,
)
_SUFFIX = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
           "u": 1e-6, "µ": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}


def parse_spice_number(text) -> float:
    if isinstance(text, (int, float)):
        return float(text)
    m = _NUMBER_RE.match(text)
    if not m or not m[1].strip("+-."):
        raise AnalysisError(f"cannot parse {text!r} as a SPICE number")
    value = float(m[1])
    if m[2]:
        value *= _SUFFIX[m[2].lower()]
    return value


# ------------------------------------------------------ safe evaluation


_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)


def evaluate(formula: str, names: dict[str, float]) -> float:
    """Arithmetic over named results. Deliberately tiny; never eval()."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise AnalysisError(f"cannot parse formula {formula!r}: {e}") from None
    try:
        return _eval_node(tree.body, names)
    except ZeroDivisionError:
        raise AnalysisError(
            f"division by zero evaluating {formula!r}"
        ) from None


def _eval_node(node, names) -> float:
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        left = _eval_node(node.left, names)
        right = _eval_node(node.right, names)
        op = type(node.op)
        if op is ast.Add:
            return left + right
        if op is ast.Sub:
            return left - right
        if op is ast.Mult:
            return left * right
        if op is ast.Div:
            return left / right
        return left ** right
    if isinstance(node, ast.UnaryOp) and isinstance(
        node.op, (ast.USub, ast.UAdd)
    ):
        value = _eval_node(node.operand, names)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise AnalysisError(f"unknown name {node.id!r} in formula")
        return names[node.id]
    raise AnalysisError(
        f"formulas allow only names, numbers and + - * / ** "
        f"(found {type(node).__name__})"
    )


def formula_names(formula: str) -> list[str]:
    """Measurement names a derived formula depends on. Public because
    the input gate's coverage check walks the same dependency graph."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as e:
        raise AnalysisError(f"cannot parse formula {formula!r}: {e}") from None
    return [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]


_formula_names = formula_names  # internal alias, same function


# --------------------------------------------------------- circuit variants


def _device_directives(circuit) -> list[str]:
    """Model cards etc. — everything except active analysis directives."""
    return [
        d for d in circuit.get("directives", [])
        if d.split()[0] not in ANALYSIS_KEYWORDS | {".step"}
    ]


def _analysis_directives(run) -> list[str]:
    if run["type"] == "op":
        return [".op"]
    if run["type"] == "tran":
        # .tran <Tprint> <Tstop> <Tstart> [<Tmaxstep>]: Tstart = the
        # settle window, so LTspice discards startup and the saved data
        # IS the post-settle waveform. Verified: bridge + 470u + 1m
        # converges in under a second with these settings.
        parts = ["0", str(run["stop"]), str(run.get("settle", "0"))]
        if run.get("max_step"):
            parts.append(str(run["max_step"]))
        return [".tran " + " ".join(parts)]
    if run["type"] == "dc":
        s = run["sweep"]
        return [f".dc {s['source']} {s['start']} {s['stop']} {s['step']}"]
    ref = run["component"]
    return [
        f".step param {ref}step LIST {' '.join(run['values'])}",
        ".op",
    ]


def _parametrize_component(circuit, ref) -> str:
    """Swap a component's value for {<ref>step}; return the original."""
    comp = next(c for c in circuit["components"] if c["ref"] == ref)
    original = comp["value"]
    comp["value"] = f"{{{ref}step}}"
    return original


def run_circuit(circuit: dict, run: dict) -> dict:
    """The scratch circuit for one run (internal, one analysis active)."""
    out = copy.deepcopy(circuit)
    out["directives"] = _device_directives(circuit)
    if run["type"] == "param_sweep":
        _parametrize_component(out, run["component"])
    out["directives"] += _analysis_directives(run)
    return out


def deliverable_circuit(circuit: dict, plan: dict) -> dict:
    """The ONE .asc the student receives: whole experiment in one file.

    First run active; every other run a comment line to uncomment. A
    swept component keeps an active .param default so the file works as
    handed over (verified: .step overrides .param when uncommented).
    """
    out = copy.deepcopy(circuit)
    directives = _device_directives(circuit)

    for run in plan["runs"]:
        if run["type"] == "param_sweep":
            original = _parametrize_component(out, run["component"])
            directives.append(f".param {run['component']}step {original}")

    active, *others = plan["runs"]
    active_analysis = _analysis_directives(active)[-1].split()[0]
    directives += _analysis_directives(active)

    for run in others:
        label = run.get("label", run["id"])
        for d in _analysis_directives(run):
            keyword = d.split()[0]
            if keyword in ANALYSIS_KEYWORDS and keyword == active_analysis:
                continue  # already active (e.g. .op for a .step run)
            note = label
            if keyword in ANALYSIS_KEYWORDS:
                note += f" -- comment out {active_analysis} first"
            directives.append(f";{d}   [{note}]")

    out["directives"] = directives
    return out


# ---------------------------------------------------------------- execution


def _wave(
    results, trace: str, run_id: str, expected_len: int | None
) -> list[float]:
    wave = results.traces.get(trace)
    if wave is None:
        lowered = {k.lower(): v for k, v in results.traces.items()}
        wave = lowered.get(trace.lower())
    if wave is None:
        raise AnalysisError(
            f"run {run_id!r} has no trace {trace!r}; available: "
            f"{sorted(results.traces)}"
        )
    if expected_len is not None and len(wave) != expected_len:
        raise AnalysisError(
            f"run {run_id!r}: trace {trace!r} has {len(wave)} points, "
            f"the plan's axis has {expected_len}"
        )
    return wave


def _run_length(run, results) -> int:
    """Point count of a run at execution time: the plan's axis, or for
    tran the length of the recorded time trace."""
    _, n = _axis(run)
    if n is None:
        n = len(_wave(results, "time", run["id"], None))
    return n


_DIFF_EXPR_RE = re.compile(r"^\s*(V\(\w+\))\s*-\s*(V\(\w+\))\s*$")


def _stats_wave(results, expr: str, run_id: str, n: int) -> list[float]:
    """A raw trace, or the pointwise difference 'V(a)-V(b)' — needed
    for differential signals like the input across a floating bridge
    source, which no single raw trace holds."""
    if m := _DIFF_EXPR_RE.match(expr):
        a = _wave(results, m[1], run_id, n)
        b = _wave(results, m[2], run_id, n)
        return [x - y for x, y in zip(a, b)]
    return _wave(results, expr, run_id, n)


def _waveform_stats(time: list[float], wave: list[float]) -> dict:
    """Time-weighted statistics (trapezoidal): variable-step simulators
    cluster points where the waveform is busy, so a naive point average
    is biased. Hand-checked in tests against a known trapezoid."""
    if len(time) != len(wave):
        raise AnalysisError(
            f"time axis has {len(time)} points but the trace has "
            f"{len(wave)}"
        )
    span = time[-1] - time[0]
    if span <= 0 or len(time) < 2:
        raise AnalysisError("waveform window has no duration")
    area = sq_area = 0.0
    for i in range(len(time) - 1):
        dt = time[i + 1] - time[i]
        area += dt * (wave[i] + wave[i + 1]) / 2
        sq_area += dt * (wave[i] ** 2 + wave[i + 1] ** 2) / 2
    return {
        "mean": area / span,
        "rms": (sq_area / span) ** 0.5,
        "min": min(wave),
        "max": max(wave),
        "ripple_pp": max(wave) - min(wave),
    }


def _net_wave(results, net: str, run_id: str, n: int) -> list[float]:
    if net == "0":
        return [0.0] * n  # ground has no trace; it is 0 by definition
    return _wave(results, f"V({net})", run_id, n)


def _pin_nets(circuit, ref) -> dict[str, str]:
    return {
        pin.split(".", 1)[1]: net
        for net, pins in circuit["nets"].items()
        for pin in pins
        if pin.startswith(f"{ref}.")
    }


def _examined_analog(entry, run, results) -> str:
    """What an analog regime actually looked at, whether or not it held.

    A regime that passes silently is indistinguishable from one nobody ran.
    The n here is the real point count of the run, so "1 operating point" and
    "9 sweep points" are visibly different amounts of evidence.
    """
    n = _run_length(run, results)
    point = "operating point" if n == 1 else "sweep points"
    if entry["assert"] == "zener_in_breakdown":
        what = (f"I({entry['device']}) against a "
                f"{entry.get('min_reverse_current', 1e-4):g} A minimum "
                f"reverse current")
        if "vz" in entry:
            what += f", and its terminal voltage against Vz={entry['vz']} V"
    else:
        what = (f"Vce of {entry['device']} against "
                f"{entry.get('vce_sat', 0.2)} V, and Ib for cut-off")
    return f"{what}, at {n} {point} of run {run['id']!r}"


def _check_regime(circuit, entry, run, results) -> list[str]:
    dev = entry["device"]
    n = _run_length(run, results)
    nets = _pin_nets(circuit, dev)
    reasons = []

    if entry["assert"] == "zener_in_breakdown":
        min_rev = entry.get("min_reverse_current", 1e-4)
        current = _wave(results, f"I({dev})", run["id"], n)
        # Netlist convention D anode cathode: breakdown current flows
        # cathode -> anode, so I(dev) is negative in breakdown.
        starved = sum(1 for i in current if -i < min_rev)
        if starved:
            reasons.append(
                f"{dev} out of breakdown at {starved} of {n} sweep points "
                f"(reverse current below {min_rev:g} A)"
            )
        if "vz" in entry:
            vz = entry["vz"]
            vk = _net_wave(results, nets["cathode"], run["id"], n)
            va = _net_wave(results, nets["anode"], run["id"], n)
            off = sum(
                1 for k, a in zip(vk, va) if abs((k - a) - vz) > 0.2 * vz
            )
            if off:
                reasons.append(
                    f"{dev} terminal voltage more than 20% from "
                    f"Vz={vz} V at {off} of {n} sweep points"
                )

    elif entry["assert"] == "bjt_active":
        vce_sat = entry.get("vce_sat", 0.2)
        vc = _net_wave(results, nets["C"], run["id"], n)
        ve = _net_wave(results, nets["E"], run["id"], n)
        ib = _wave(results, f"Ib({dev})", run["id"], n)
        saturated = sum(1 for c, e in zip(vc, ve) if c - e <= vce_sat)
        if saturated:
            reasons.append(
                f"{dev} saturated (Vce <= {vce_sat} V) at {saturated} "
                f"of {n} sweep points"
            )
        cut_off = sum(1 for i in ib if i <= 0)
        if cut_off:
            reasons.append(
                f"{dev} base current not positive at {cut_off} of {n} "
                "sweep points"
            )
    return reasons


# ------------------------------------------------------- digital regimes
#
# The analog regimes read simulation output. The digital ones read the CIRCUIT
# DESCRIPTION, because that is where the failures they name actually live: a
# gate input nobody wired, an output pin nothing drives, a cycle with no
# stable value. All three are decided before an evaluator is involved, and
# each one is a property of the whole circuit, which is why they name no
# device.
#
# Be precise about what this buys and what it does not. These are OUR checks
# on OUR description -- they are not externally verified the way the truth
# table is. What makes them worth having anyway is that Logisim cannot report
# them usefully: a floating input makes it print an error marker in a cell,
# which parse_tty_table refuses as non-binary, so the failure arrives as
# "Logisim printed something we would not believe" with no pin named. These
# checks name the pin.


class _Topology:
    """The circuit description read as ports, nets and drivers.

    Three small lookups the digital regimes all need. Built once and passed
    around rather than recomputed inline, because the inline version was
    three nested comprehensions doing a linear search for a component inside
    a loop over its own pins.
    """

    def __init__(self, circuit):
        from ohmwork.targets import LogisimTarget

        self.target = LogisimTarget()
        self.circuit = circuit
        self.by_ref = {c["ref"]: c for c in circuit["components"]}

        self.net_of = {}                 # "<ref>.<pin>" -> net name
        for net, members in circuit["nets"].items():
            for entry in members:
                self.net_of[entry] = net

        self.sinks, self.sources = [], []      # every port, by direction
        for comp in circuit["components"]:
            for pin in self.target.pin_names(comp["type"]):
                entry = f"{comp['ref']}.{pin}"
                (self.sources if self.drives(entry) else self.sinks).append(entry)

        #: nets with at least one port driving them
        self.driven_nets = {self.net_of[e] for e in self.sources
                            if e in self.net_of}

    def drives(self, entry: str) -> bool:
        ref, pin = entry.split(".", 1)
        return self.target.is_source(self.by_ref[ref]["type"], pin)

    def drivers(self) -> dict[str, list[str]]:
        """ref -> the refs driving its inputs."""
        sources_on = {}
        for entry in self.sources:
            net = self.net_of.get(entry)
            if net is not None:
                sources_on.setdefault(net, []).append(entry.split(".", 1)[0])
        out = {ref: [] for ref in self.by_ref}
        for entry in self.sinks:
            ref = entry.split(".", 1)[0]
            net = self.net_of.get(entry)
            out[ref] += [d for d in sources_on.get(net, []) if d != ref]
        return out


def _check_digital_regime(circuit, assertion, run_id) -> RegimeResult:
    topo = _Topology(circuit)
    reasons = []

    if assertion == "no_floating_inputs":
        for entry in topo.sinks:
            net = topo.net_of.get(entry)
            if net is None:
                reasons.append(
                    f"{entry} is on no net at all: nothing is wired to it, so "
                    f"Logisim would evaluate it as an error value"
                )
            elif net not in topo.driven_nets:
                reasons.append(
                    f"{entry} is on net {net!r}, which has no driving port"
                )
        examined = (f"{len(topo.sinks)} input port(s) across "
                    f"{len(circuit['components'])} components, each for "
                    f"membership of a net that has a driver")

    elif assertion == "all_outputs_driven":
        pins = [c["ref"] for c in circuit["components"]
                if c["type"] == "output_pin"]
        for ref in pins:
            net = topo.net_of.get(f"{ref}.pin")
            if net is None:
                reasons.append(f"output pin {ref} is on no net")
            elif net not in topo.driven_nets:
                reasons.append(
                    f"output pin {ref} is on net {net!r}, which nothing drives"
                )
        examined = (f"{len(pins)} output pin(s), each for a net carrying a "
                    f"driving port")

    elif assertion == "no_combinational_loops":
        drivers = topo.drivers()
        state, cycles = {}, []

        def walk(ref, path):
            if state.get(ref) == "done":
                return
            if state.get(ref) == "open":
                cycle = path[path.index(ref):] + [ref]
                cycles.append(" -> ".join(cycle))
                return
            state[ref] = "open"
            for driver in drivers.get(ref, []):
                walk(driver, path + [ref])
            state[ref] = "done"

        for comp in circuit["components"]:
            walk(comp["ref"], [])
        for cycle in dict.fromkeys(cycles):
            reasons.append(
                f"combinational loop: {cycle}. A cycle has no logic depth and "
                f"no stable value; Logisim shows it as an error state"
            )
        examined = (f"the driver graph over {len(drivers)} components, "
                    f"depth-first, for any cycle")

    else:                                   # pragma: no cover - guarded above
        raise AnalysisError(f"unknown digital regime {assertion!r}")

    return RegimeResult(assertion=assertion, run=run_id, device=None,
                        held=not reasons, examined=examined,
                        reasons=tuple(reasons))


def check_regimes(circuit: dict, plan: dict) -> list[RegimeResult]:
    """Evaluate every DIGITAL regime a plan declares, without an evaluator.

    Separate from execute() so the checks can be run — and tested — with no
    Logisim installed, and so a failure names the pin rather than arriving as
    an unparseable truth table.
    """
    out = []
    for entry in plan.get("measurements", []):
        if entry.get("kind") == "regime" and entry["assert"] in DIGITAL_REGIMES:
            out.append(_check_digital_regime(circuit, entry["assert"],
                                             entry["run"]))
    return out


# ----------------------------------------------------------- digital run


def _execute_digital(circuit, plan, backend, workdir) -> Experiment:
    """One .circ per truth_table run, evaluated by an OUTSIDE tool.

    Structurally the analog path with two substitutions: the emitter writes a
    .circ instead of a .asc, and the backend enumerates input combinations
    instead of solving an operating point.
    """
    from ohmwork.logisim_emitter import write_circ

    runs = {run["id"]: run for run in plan["runs"]}
    verification = getattr(backend, "verification", "external")

    # Which outputs each run must report: the run declares its inputs, the
    # table measurements over it declare the outputs.
    outputs_for = {rid: [] for rid in runs}
    for m in plan["measurements"]:
        if m.get("kind") == "table":
            for ref in m["outputs"]:
                if ref not in outputs_for[m["run"]]:
                    outputs_for[m["run"]].append(ref)

    regimes = check_regimes(circuit, plan)
    violations: dict[str, list[str]] = {}
    for regime in regimes:
        if not regime.held:
            violations.setdefault(regime.run, []).extend(regime.reasons)

    tables = {}
    for run in plan["runs"]:
        circ = Path(workdir) / f"{run['id']}.circ"
        write_circ(circuit, circ)
        labels = {c["ref"]: c.get("label", c["ref"])
                  for c in circuit["components"]}
        table = backend.truth_table(
            circ,
            [labels[ref] for ref in run["inputs"]],
            [labels[ref] for ref in outputs_for[run["id"]]],
        )
        tables[run["id"]] = table

    out: dict[str, Measurement] = {}
    for m in plan["measurements"]:
        if m.get("kind") != "table":
            continue
        table = tables[m["run"]]
        reasons = tuple(violations.get(m["run"], ()))
        out[m["name"]] = Measurement(
            name=m["name"], value=None, run=m["run"],
            backend=table.backend, verification=verification,
            source="simulation",
            table={
                "columns": list(table.inputs) + list(table.outputs),
                "rows": [list(row) for row in table.rows],
                "notes": list(table.notes),
            },
            reliable=not reasons, warnings=reasons,
        )
    return Experiment(out, regimes)


def execute(circuit: dict, plan: dict, backend, workdir) -> Experiment:
    """Run the whole experiment; return an Experiment (a Mapping of
    name -> Measurement, plus the regime outcomes)."""
    validate_plan(circuit, plan)
    if is_digital(plan):
        return _execute_digital(circuit, plan, backend, Path(workdir))
    workdir = Path(workdir)
    runs = {run["id"]: run for run in plan["runs"]}
    verification = getattr(backend, "verification", "external")

    results_by_run = {}
    for run in plan["runs"]:
        asc = workdir / f"{run['id']}.asc"
        write_asc(run_circuit(circuit, run), asc)
        results_by_run[run["id"]] = backend.run(asc)

    regimes, violations = [], {}
    for entry in plan["measurements"]:
        if entry.get("kind") == "regime":
            run = runs[entry["run"]]
            reasons = _check_regime(
                circuit, entry, run, results_by_run[run["id"]]
            )
            regimes.append(RegimeResult(
                assertion=entry["assert"], run=run["id"],
                device=entry.get("device"), held=not reasons,
                examined=_examined_analog(entry, run, results_by_run[run["id"]]),
                reasons=tuple(reasons),
            ))
            if reasons:
                violations.setdefault(run["id"], []).extend(reasons)

    out: dict[str, Measurement] = {}
    for m in plan["measurements"]:
        kind = m.get("kind", "simulated")
        if kind == "regime":
            continue
        if kind == "derived":
            refs = _formula_names(m["formula"])
            value = evaluate(
                m["formula"], {r: out[r].value for r in refs}
            )
            warnings = tuple(
                dict.fromkeys(w for r in refs for w in out[r].warnings)
            )
            out[m["name"]] = Measurement(
                name=m["name"], value=value, run=None,
                backend=backend.name, verification=verification, source="derived",
                formula=m["formula"], definition=m.get("definition"),
                reliable=all(out[r].reliable for r in refs),
                warnings=warnings,
            )
        elif kind == "waveform_stats":
            run = runs[m["run"]]
            results = results_by_run[run["id"]]
            n = _run_length(run, results)
            wave = _stats_wave(results, m["expr"], run["id"], n)
            time = _wave(results, "time", run["id"], n)
            stats = _waveform_stats(time, wave)
            reasons = tuple(violations.get(run["id"], ()))
            out[m["name"]] = Measurement(
                name=m["name"], value=stats["mean"], run=run["id"],
                backend=backend.name, verification=verification, source="simulation",
                stats=stats, reliable=not reasons, warnings=reasons,
            )
        else:
            run = runs[m["run"]]
            _, n = _axis(run)
            wave = _wave(results_by_run[run["id"]], m["expr"], run["id"], n)
            idx = (
                _at_index_live(run, m["at"], results_by_run[run["id"]])
                if "at" in m else 0
            )
            reasons = tuple(violations.get(run["id"], ()))
            out[m["name"]] = Measurement(
                name=m["name"], value=wave[idx], run=run["id"],
                backend=backend.name, verification=verification, source="simulation",
                at=m.get("at"), reliable=not reasons, warnings=reasons,
            )
    return Experiment(out, regimes)


# ------------------------------------------------------------------ report


def _fmt(value: float) -> str:
    return f"{value:.6g}"


def _substituted(formula: str, names: dict[str, float]) -> str:
    for name in sorted(names, key=len, reverse=True):
        formula = re.sub(
            rf"\b{re.escape(name)}\b", _fmt(names[name]), formula
        )
    return formula


def render_report(
    results: dict[str, Measurement], plan: dict, devices: dict | None = None
) -> str:
    """Human-readable results with full provenance and formulas.

    Derived values show their formula, the numbers substituted in, and
    the plan's 'definition' line — load regulation alone has several
    textbook definitions, and a bare percentage gives a student no way
    to reconcile a mismatch with their manual.

    `devices` maps component refs to the DeviceChoice that selected
    their model; it heads the report so every number below is traceable
    to the device (and policy path) that produced it. An experiment
    once ran end-to-end on an outlawed model card with nothing in the
    output showing it; this line is what would have made that visible.
    """
    lines = []
    internal = [m for m in results.values() if m.verification == "internal"]
    if internal:
        # CLAUDE.md, "The evaluator asymmetry": say WHY this is weaker,
        # not merely which engine produced it.
        backend_names = sorted({m.backend for m in internal})
        lines += [
            f"!! results below were computed by {', '.join(backend_names)} "
            "— ohmwork's own evaluator, not an external simulator.",
            "   No external simulator ever checks them, so an error in "
            "the evaluator is",
            "   undetectable by any test in this repo: it would produce "
            "both the result and",
            "   the expected value. Strictly weaker than the LTspice "
            "path. See CLAUDE.md,",
            "   \"The evaluator asymmetry\".",
            "",
        ]
    if devices:
        lines.append("devices:")
        for ref, choice in devices.items():
            lines.append(f"  {ref}: [{choice.policy}] {choice.report}")
        lines.append("")
    for m in plan["measurements"]:
        if m.get("kind") == "regime":
            continue
        r = results[m["name"]]
        if r.table is not None:
            lines += _render_table(r)
        elif r.stats is not None:
            s = r.stats
            lines.append(
                f"{r.name}: mean {_fmt(s['mean'])}, "
                f"ripple pk-pk {_fmt(s['ripple_pp'])}, "
                f"min {_fmt(s['min'])}, max {_fmt(s['max'])}, "
                f"rms {_fmt(s['rms'])}"
            )
            lines.append(
                f"  [simulation: run {r.run} (post-settle window), "
                f"backend {r.backend}]"
            )
        elif r.source == "simulation":
            at = ""
            if r.at:
                key, value = next(iter(r.at.items()))
                at = f" at {key}={value}"
            lines.append(
                f"{r.name} = {_fmt(r.value)}   "
                f"[simulation: run {r.run}{at}, backend {r.backend}]"
            )
        else:
            refs = _formula_names(r.formula)
            values = {name: results[name].value for name in refs}
            lines.append(f"{r.name} = {_fmt(r.value)}")
            lines.append(f"  = {r.formula}")
            lines.append(f"  = {_substituted(r.formula, values)}")
            if r.definition:
                lines.append(f"  definition: {r.definition}")
            lines.append(f"  [derived, backend {r.backend}]")
        for w in r.warnings:
            lines.append(f"  UNRELIABLE: {w}")
    lines += _render_regimes(getattr(results, "regimes", ()))
    return "\n".join(lines) + "\n"


def _render_table(r: Measurement) -> list[str]:
    """Every row, never a summary. "Show the truth table" means show it."""
    columns = r.table["columns"]
    width = max(3, max(len(c) for c in columns))
    lines = [
        f"{r.name}: {len(r.table['rows'])} rows x {len(columns)} columns",
        f"  [simulation: run {r.run}, backend {r.backend}, "
        f"{r.verification} verification]",
        "  " + "  ".join(c.rjust(width) for c in columns),
        "  " + "  ".join("-" * width for _ in columns),
    ]
    for row in r.table["rows"]:
        lines.append("  " + "  ".join(str(v).rjust(width) for v in row))
    for note in r.table.get("notes", ()):
        lines.append(f"  note: {note}")
    return lines


def _render_regimes(regimes) -> list[str]:
    """Print every regime that RAN, held or not.

    CLAUDE.md's unrun-check rule, applied to its mirror image: silence about
    a passing check is indistinguishable from silence about a check nobody
    performed, and the reader cannot tell which they are looking at. So each
    one says what it examined.
    """
    if not regimes:
        return []
    lines = ["", "regime checks:"]
    for r in regimes:
        verdict = "held" if r.held else "VIOLATED"
        target = f" [{r.device}]" if r.device else ""
        lines.append(f"  {r.assertion}{target}: {verdict}")
        lines.append(f"    examined {r.examined}")
        for reason in r.reasons:
            lines.append(f"    {reason}")
    return lines
