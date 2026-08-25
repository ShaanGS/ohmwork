"""The JSON input gate (build step 5).

This is the last checkpoint where a human is still in the loop; the
vision/LLM layer will write this format, and everything downstream is
machine-generated intent. Hence a gate, not a loader:

  - strict schema: unknown keys anywhere are errors with a path-shaped
    message, never silently ignored
  - semiconductors arrive as specs and leave as resolved parts with
    their DeviceChoice recorded (policy paths a/b/c, always reported)
  - the full non-simulation validation chain runs at load time: device
    policy, emit + geometric parse round trip, plan validation
  - semantic heuristics WARN (never fail): they exist to be surfaced
    to the human at the confirmation step
  - to_dict() rebuilds the input from parsed state so a round-trip
    test catches schema drift the moment the LLM layer starts
    producing these files
"""

import copy
import re
import textwrap
from dataclasses import dataclass, field

from ohmwork import analysis, prose
from ohmwork.emitter import CircuitError, emit
from ohmwork.parser import ParseError, parse_asc
from ohmwork.parts import DeviceChoice, PartsLibrary, UnknownPartError
from ohmwork.symbols import PART_TYPES, VALUE_TYPES
from ohmwork.targets import (TargetError, UnknownTargetError,
                             check_component_types, get_target)

_TOP_KEYS = {"circuit", "analysis", "asks", "source", "question",
             "design_notes", "target", "constraints"}
_CONSTRAINT_KEYS = {"primitives_only"}

# Instruction verbs and glue that never carry ask content; excluded
# from word-level coverage so "Calculate" does not show as unclaimed.
_INSTRUCTION_WORDS = {
    "calculate", "obtain", "observe", "find", "determine", "design",
    "simulate", "measure", "compute", "both", "also", "then", "with",
    "from", "into", "that", "this", "using", "given",
}
#: kind "prose" marks an ask no measurement can ever answer.
_ASK_KEYS = {"text", "answered_by", "kind", "prose"}
_ASK_KINDS = {"measurement", "prose"}
#: The GROUNDING CONTRACT for a prose ask -- which notes, which rows. Not the
#: prose itself: extraction happens before simulation and cannot cite results
#: that do not exist yet. `answer` is the hand-written escape hatch, and
#: carries its authorship for the same reason a rationale does.
#: `answer_evidence` fingerprints the ROWS an answer was written over, so a
#: stored caption that has outlived its evidence is caught rather than sitting
#: on top of different rows still looking grounded.
_PROSE_KEYS = {"tier", "notes", "evidence", "answer", "answer_origin",
               "answer_evidence"}
_EVIDENCE_KEYS = {"label", "measurement", "select"}
_SOURCE_KEYS = {"file", "resolution", "question_chars", "extractor",
                "attempts", "confidence", "annotations_unused"}
_CIRCUIT_KEYS = {"components", "nets", "directives"}
# origin: where a value came from. "stated" (question text or image),
# "designed" (the tool chose it — requires a rationale, because the
# student must be able to defend it), "default" (library/policy
# fallback). A designed value indistinguishable from a stated one would
# submit our engineering judgement as the student's own.
_COMPONENT_KEYS = {"ref", "type", "value", "part", "device", "ac",
                   "origin", "rationale", "rationale_origin"}
# Structured AC source: the RMS -> peak conversion happens in code and
# is displayed in the dry run. An opaque "SINE(0 16.97 50)" string
# hides exactly the conversion a misread would corrupt (41% error).
_AC_KEYS = {"kind", "rms", "amplitude", "freq", "offset"}
_ORIGINS = {"stated", "designed", "default"}
_DESIGN_NOTE_KEYS = {"item", "choice", "rationale", "rationale_origin"}
# Who wrote a rationale. Trust does NOT come from human authorship —
# once the LLM layer lands most rationales are model-written (this
# repo's own examples/q3.json RS=220 rationale already is) — it comes
# from a human REVIEWING them at this gate. Absent authorship is never
# assumed human: that assumption is the unfounded trust being removed.
_RATIONALE_ORIGINS = {"human", "generated"}
_DEVICE_KEYS = {
    "zener": {"vz", "exact"},
    "npn": {"params"},
    "pnp": {"params"},
    "diode": set(),  # "a rectifier diode": no spec, path (c) default
}
_RUN_KEYS = {
    "op": {"id", "type", "label"},
    "dc": {"id", "type", "label", "sweep"},
    "param_sweep": {"id", "type", "label", "component", "values"},
    "tran": {"id", "type", "label", "stop", "settle", "max_step"},
    # Digital. No stop time and no sweep: the evaluator enumerates every
    # combination of `inputs` itself, so the run declares WHICH pins are
    # inputs and in what column order, and nothing else.
    "truth_table": {"id", "type", "label", "inputs"},
}
_SWEEP_KEYS = {"source", "start", "stop", "step"}
_MEASUREMENT_KEYS = {
    # "guard" declares deliberate extra work not tied to any ask (e.g.
    # a cross-simulator tripwire) and exempts it from coverage warnings.
    "simulated": {"name", "run", "expr", "at", "guard"},
    "waveform_stats": {"name", "kind", "run", "expr", "guard"},
    "derived": {"name", "kind", "formula", "definition", "guard"},
    "regime": {"kind", "run", "assert", "device", "vz",
               "min_reverse_current", "vce_sat"},
    # The whole table is ONE measurement: 2**n rows of every output. It is a
    # pinnable baseline, but see "The evaluator asymmetry" for who may
    # compute the pinned copy.
    "table": {"name", "kind", "run", "outputs", "guard"},
}


class QuestionError(Exception):
    """The question JSON is not acceptable input."""


@dataclass(frozen=True)
class SkippedCheck:
    """A check that did not run, and why.

    THE RULE (CLAUDE.md, "An unrun check must announce itself"): a check that
    can be skipped must SAY it was skipped, in the output, not merely decline
    to add a warning. Silence is indistinguishable from a pass, and the reader
    has no way to tell a clean screen from an unexamined one.
    """

    name: str
    reason: str


@dataclass
class Question:
    circuit: dict                      # resolved, emit-ready
    plan: dict | None
    devices: dict[str, DeviceChoice]   # ref -> how its model was chosen
    warnings: list[str]
    asks: list | None = None           # verbatim question phrases + mapping
    source: dict | None = None         # extraction provenance, if any
    question: str | None = None        # THE verbatim question text
    design_notes: list | None = None   # non-component design choices
    #: Checks that did not run. Rendered in the dry run and published in the
    #: manifest — never only held internally.
    skipped: list = field(default_factory=list)
    target_name: str = "ltspice"
    #: Constraints WE impose that the question does not (Q2's
    #: primitives_only). Kept because to_dict() must rebuild them: a
    #: question republished without its target reloads as an LTspice one.
    constraints: dict | None = None
    # parsed input state, kept for to_dict() drift detection
    _input_circuit: dict = field(repr=False, default=None)
    _input_plan: dict | None = field(repr=False, default=None)
    #: the target string AS WRITTEN, not the resolved target's name. "logisim"
    #: and "logisim-2.7.1" both resolve to LogisimTarget, and rewriting the
    #: input to the canonical name would make to_dict() lossy in a way the
    #: round-trip test is meant to forbid.
    _input_target: str | None = field(repr=False, default=None)

    def to_dict(self) -> dict:
        """Rebuild the input from parsed state (NOT a stored copy of the
        raw dict): a field the loader parsed but forgot to keep makes
        the round-trip test fail, which is the point."""
        out = {}
        if self._input_target is not None:
            out["target"] = self._input_target
        if self.constraints is not None:
            out["constraints"] = copy.deepcopy(self.constraints)
        out["circuit"] = copy.deepcopy(self._input_circuit)
        if self._input_plan is not None:
            out["analysis"] = copy.deepcopy(self._input_plan)
        if self.asks is not None:
            out["asks"] = copy.deepcopy(self.asks)
        if self.source is not None:
            out["source"] = copy.deepcopy(self.source)
        if self.question is not None:
            out["question"] = self.question
        if self.design_notes is not None:
            out["design_notes"] = copy.deepcopy(self.design_notes)
        return out


# -------------------------------------------------------------- strictness


def _require_keys(obj: dict, allowed: set, where: str, required: set = ()):
    if not isinstance(obj, dict):
        raise QuestionError(f"{where}: expected an object, got "
                            f"{type(obj).__name__}")
    unknown = set(obj) - allowed
    if unknown:
        raise QuestionError(
            f"{where}: unknown key(s) {sorted(unknown)}; "
            f"allowed: {sorted(allowed)}"
        )
    missing = set(required) - set(obj)
    if missing:
        raise QuestionError(f"{where}: missing required {sorted(missing)}")


def carries_a_value(comp: dict) -> bool:
    """Does an origin even mean anything for this component?

    A gate has no value and no part, so every one of its properties is our
    choice by definition. Letting gates carry origins inflates the
    "rationales require your review" count with entries that cannot be
    wrong, diluting the signal in the cases where it can. Logic and topology
    choices belong in design_notes, which already carries authorship.
    """
    return comp.get("type") in VALUE_TYPES or comp.get("type") in PART_TYPES


def _check_component_keys(comp: dict, where: str, target=None) -> None:
    allowed = _COMPONENT_KEYS | (
        target.extra_component_keys if target is not None else frozenset())
    _require_keys(comp, allowed, where, required={"ref", "type"})
    origin = comp.get("origin")
    if origin is not None and not carries_a_value(comp):
        # A gate has neither value nor part, so "where did this value come
        # from" has no referent. Accepting it silently inflated the review
        # count with entries that cannot be wrong.
        raise QuestionError(
            f"{where}: {comp['type']!r} has no value or part, so 'origin' "
            f"does not apply to it. Put logic and topology choices in "
            f"design_notes, which already records rationale authorship."
        )
    if origin is not None and origin not in _ORIGINS:
        raise QuestionError(
            f"{where}: origin {origin!r} is not one of {sorted(_ORIGINS)}"
        )
    if origin == "designed" and not comp.get("rationale"):
        raise QuestionError(
            f"{where}: a designed value needs a rationale — the student "
            "has to be able to defend the choice"
        )
    _check_rationale_origin(comp, where)
    ctype = comp["type"]
    if ctype in VALUE_TYPES:
        if "part" in comp or "device" in comp:
            raise QuestionError(
                f"{where}: {ctype} takes 'value', not part/device"
            )
        if "ac" in comp and ctype != "voltage":
            raise QuestionError(f"{where}: only voltage sources take 'ac'")
        if ctype == "voltage":
            if ("value" in comp) == ("ac" in comp):
                raise QuestionError(
                    f"{where}: voltage needs exactly one of 'value' (DC) "
                    "or 'ac' (structured AC spec)"
                )
            if "ac" in comp:
                _check_ac_spec(comp["ac"], f"{where}.ac")
        elif "value" not in comp:
            raise QuestionError(f"{where}: {ctype} needs a 'value'")
    elif ctype in PART_TYPES:
        if "value" in comp:
            raise QuestionError(
                f"{where}: {ctype} takes 'part' or 'device', not 'value'"
            )
        if ("part" in comp) == ("device" in comp):
            raise QuestionError(
                f"{where}: {comp['ref']} ({ctype}) needs exactly one of "
                "'part' (question names the device) or 'device' "
                "(a spec to resolve)"
            )
        if "device" in comp:
            allowed = _DEVICE_KEYS.get(ctype)
            if allowed is None:
                raise QuestionError(
                    f"{where}: {ctype} devices must be named by 'part'"
                )
            _require_keys(comp["device"], allowed, f"{where}.device")


def _check_plan_keys(plan: dict) -> None:
    _require_keys(plan, {"runs", "measurements"}, "analysis",
                  required={"runs", "measurements"})
    for i, run in enumerate(plan["runs"]):
        where = f"analysis.runs[{i}]"
        _require_keys(run, {"id", "type"} | set().union(*_RUN_KEYS.values()),
                      where, required={"id", "type"})
        allowed = _RUN_KEYS.get(run["type"])
        if allowed:  # unknown types fall through to validate_plan's error
            _require_keys(run, allowed, where, required={"id", "type"})
            if run["type"] == "dc":
                _require_keys(run["sweep"], _SWEEP_KEYS, f"{where}.sweep",
                              required=_SWEEP_KEYS)
    for i, m in enumerate(plan["measurements"]):
        where = f"analysis.measurements[{i}]"
        kind = m.get("kind", "simulated")
        allowed = _MEASUREMENT_KEYS.get(kind)
        if allowed is None:
            raise QuestionError(f"{where}: unknown kind {kind!r}")
        _require_keys(m, allowed, where)


def _check_rationale_origin(obj: dict, where: str) -> None:
    value = obj.get("rationale_origin")
    if value is not None and value not in _RATIONALE_ORIGINS:
        raise QuestionError(
            f"{where}: rationale_origin {value!r} is not one of "
            f"{sorted(_RATIONALE_ORIGINS)}"
        )


def _authorship_tag(obj: dict) -> str:
    """Honest label for who wrote a rationale."""
    origin = obj.get("rationale_origin")
    if origin == "human":
        return "[human-written]"
    if origin == "generated":
        return "[generated, reviewed at input gate]"
    return "[authorship not recorded — review]"


def _needs_review(obj: dict) -> bool:
    return obj.get("rationale_origin") != "human"


def _check_ac_spec(ac: dict, where: str) -> None:
    _require_keys(ac, _AC_KEYS, where, required={"kind", "freq"})
    if ac["kind"] != "sine":
        raise QuestionError(f"{where}: only kind 'sine' is supported")
    if ("rms" in ac) == ("amplitude" in ac):
        raise QuestionError(
            f"{where}: exactly one of 'rms' or 'amplitude' — stating "
            "both (or neither) is how RMS/peak confusion starts"
        )


def _ac_peak(ac: dict) -> float:
    if "amplitude" in ac:
        return float(ac["amplitude"])
    return float(ac["rms"]) * 2 ** 0.5


def _ac_value(ac: dict) -> str:
    """The LTspice SINE() form, conversion done here, in code."""
    offset = ac.get("offset", 0)
    return f"SINE({offset} {_ac_peak(ac):.6g} {ac['freq']})"


# --------------------------------------------------------- device resolution


def _resolve_device(comp: dict, library: PartsLibrary) -> DeviceChoice:
    ctype = comp["type"]
    try:
        if "part" in comp:  # path (a): the question names the device
            if ctype == "zener":
                return library.choose_zener(part=comp["part"])
            if ctype == "diode":
                return library.choose_diode(part=comp["part"])
            return library.choose_bjt(ctype, part=comp["part"])
        spec = comp["device"]
        if ctype == "diode":
            return library.choose_diode()
        if ctype == "zener":
            if "vz" not in spec:
                raise QuestionError(
                    f"{comp['ref']}: zener device spec needs 'vz'"
                )
            return library.choose_zener(
                vz=spec["vz"], exact=spec.get("exact", True)
            )
        return library.choose_bjt(ctype, params=spec.get("params"))
    except UnknownPartError as e:
        raise QuestionError(f"{comp['ref']}: {e.args[0]}") from None


# ---------------------------------------------------------------- warnings

# Plausibility windows per component type: (low, high, unit note).
# Heuristics for the human confirmation step, never hard failures —
# the classic vision misread (1.8k as 1.8Meg) simulates fine and is
# unfalsifiable downstream, so this is the only place it can surface.
_PLAUSIBLE = {
    "res": (1.0, 10e6, "ohm"),
    "cap": (1e-12, 1.0, "farad"),
    "ind": (1e-9, 10.0, "henry"),
    "voltage": (0.001, 1000.0, "volt"),
}


def _semantic_warnings(components: list, plan: dict | None) -> list[str]:
    warnings = []
    for comp in components:
        window = _PLAUSIBLE.get(comp.get("type"))
        if not window or "value" not in comp:
            continue
        try:
            value = abs(analysis.parse_spice_number(comp["value"]))
        except analysis.AnalysisError:
            continue  # parameterised or exotic values are not our call
        low, high, unit = window
        if value and not low <= value <= high:
            warnings.append(
                f"{comp['ref']}: {comp['value']} {unit} is outside the "
                f"plausible window [{low:g}, {high:g}] — check for a "
                "misread value (e.g. k vs Meg, u vs p)"
            )
    # Relative spread: each resistor may be individually plausible, but
    # a 1.8Meg next to a 2k (ratio 900) in one small circuit is the
    # signature of a k-vs-Meg misread. Threshold 500 is a heuristic.
    res = []
    for comp in components:
        if comp.get("type") == "res" and "value" in comp:
            try:
                res.append(
                    (comp["ref"], comp["value"],
                     abs(analysis.parse_spice_number(comp["value"])))
                )
            except analysis.AnalysisError:
                pass
    if len(res) >= 2:
        lo = min(res, key=lambda r: r[2])
        hi = max(res, key=lambda r: r[2])
        if lo[2] > 0 and hi[2] / lo[2] > 500:
            warnings.append(
                f"resistor values span a factor of {hi[2] / lo[2]:.0f} "
                f"({lo[0]}={lo[1]} vs {hi[0]}={hi[1]}) — check for a "
                "k vs Meg misread"
            )

    if plan:
        guarded = {m["run"] for m in plan["measurements"]
                   if m.get("kind") == "regime"}
        measured = {m["run"] for m in plan["measurements"]
                    if m.get("kind", "simulated") == "simulated"}
        for run_id in sorted(measured - guarded):
            warnings.append(
                f"run {run_id!r} is measured but has no regime "
                "assertions: convergence is not correctness"
            )
    return warnings


def _countable_words(text: str) -> list[str]:
    """Words that carry ask content: 4+ chars, not instruction glue."""
    return [
        w for w in re.findall(r"[A-Za-z0-9']+", text)
        if len(w) > 3 and w.lower() not in _INSTRUCTION_WORDS
    ]


def _text_coverage(question: str, asks: list):
    """Word-level coverage of the verbatim question text by the asks.

    Deliberately crude: a question word is claimed if any ask uses it.
    The point is not precision but visibility — text no ask claims must
    appear on screen instead of vanishing. This is the one check whose
    reference (the raw question string) was not written by the model
    being audited.
    """
    ask_words = {
        w.lower() for a in asks for w in _countable_words(a["text"])
    }
    words = _countable_words(question)
    claimed = [w for w in words if w.lower() in ask_words]
    # Group consecutive unclaimed words into readable fragments.
    fragments, current = [], []
    for w in words:
        if w.lower() in ask_words:
            if current:
                fragments.append(" ".join(current))
                current = []
        else:
            current.append(w)
    if current:
        fragments.append(" ".join(current))
    pct = round(100 * len(claimed) / len(words)) if words else 100
    return pct, fragments


def _ask_text_warnings(question: str | None, asks: list | None) -> list[str]:
    """An ask whose words are absent from the question text was
    paraphrased or invented by the extractor."""
    if question is None or asks is None:
        return []
    q_words = {w.lower() for w in _countable_words(question)}
    warnings = []
    for a in asks:
        missing = [w for w in _countable_words(a["text"])
                   if w.lower() not in q_words]
        if missing:
            warnings.append(
                f'ask "{a["text"]}" is not verbatim from the question '
                f"text ({', '.join(missing)} not found) — paraphrased "
                "or invented?"
            )
    return warnings


def _coverage_warnings(plan: dict | None, asks: list | None) -> list[str]:
    """Two-way coverage between the question's asks and the plan.

    An unmapped ask is the dominant vision failure: the question wanted
    something that never became a measurement, and every other screen
    looks perfect. A measurement covering no ask is the inverse:
    invented work. Intermediates feeding a covered derived measurement
    are covered transitively; 'guard' measurements are declared
    deliberate and exempt.
    """
    if asks is None:
        return []
    # A prose ask has no measurement BY NATURE ("Explain your design
    # choices"). Warning about it is a permanent false alarm on the one
    # screen designed to surface real drops, and a reader who learns to skip
    # that line stops seeing the true positives too. So it is counted
    # separately and never reported as possible dropped work.
    warnings = [
        f'ask "{a["text"]}" has no measurement answering it — '
        "the extractor may have dropped it"
        for a in asks
        if not a.get("answered_by") and a.get("kind") != "prose"
    ]
    if plan is None:
        return warnings

    measurements = [m for m in plan["measurements"]
                    if m.get("kind", "simulated") != "regime"]
    covered = {a["answered_by"] for a in asks if a.get("answered_by")}
    covered |= {m["name"] for m in measurements if "guard" in m}
    changed = True
    while changed:
        changed = False
        for m in measurements:
            if m.get("kind") == "derived" and m["name"] in covered:
                deps = set(analysis.formula_names(m["formula"]))
                if not deps <= covered:
                    covered |= deps
                    changed = True
    for m in measurements:
        if m["name"] not in covered:
            warnings.append(
                f"measurement {m['name']!r} answers no ask — invented "
                "work, or a missing ask mapping?"
            )
    return warnings


# ------------------------------------------------------------------- load


def load_question(data: dict, library: PartsLibrary | None = None) -> Question:
    """Validate, resolve devices, and run every non-simulation check."""
    _require_keys(data, _TOP_KEYS, "question", required={"circuit"})

    # The target is chosen BEFORE any checking, because the checks belong to
    # it. Probing Q2 proved the old gate was not merely LTspice-flavoured but
    # LTspice-semantic: it demanded a SPICE ground net and reported unknown
    # Logisim components against LTspice's pin table.
    try:
        target = get_target(data.get("target"))
    except UnknownTargetError as e:
        raise QuestionError(str(e)) from None
    constraints = data.get("constraints") or {}
    _require_keys(constraints, _CONSTRAINT_KEYS, "constraints", required=set())

    circuit_in = data["circuit"]
    _require_keys(circuit_in, _CIRCUIT_KEYS, "circuit",
                  required={"components", "nets"})
    plan = data.get("analysis")
    asks = data.get("asks")
    if asks is not None:
        for i, ask in enumerate(asks):
            _require_keys(ask, _ASK_KEYS, f"asks[{i}]", required={"text"})
            kind = ask.get("kind", "measurement")
            if kind not in _ASK_KINDS:
                raise QuestionError(
                    f"asks[{i}]: unknown kind {kind!r}; expected one of "
                    f"{sorted(_ASK_KINDS)}"
                )
            if kind == "prose" and ask.get("answered_by"):
                raise QuestionError(
                    f"asks[{i}]: a prose ask cannot be answered_by a "
                    f"measurement; that is what makes it prose"
                )
            if kind == "prose":
                spec = ask.get("prose") or {}
                _require_keys(spec, _PROSE_KEYS, f"asks[{i}].prose",
                              required={"tier"})
                for j, group in enumerate(spec.get("evidence") or []):
                    _require_keys(group, _EVIDENCE_KEYS,
                                  f"asks[{i}].prose.evidence[{j}]",
                                  required={"label", "measurement"})
            elif ask.get("prose"):
                raise QuestionError(
                    f"asks[{i}]: only a prose ask carries a 'prose' block"
                )
    source = data.get("source")
    if source is not None:
        _require_keys(source, _SOURCE_KEYS, "source", required={"file"})
    question_text = data.get("question")
    if question_text is not None and not isinstance(question_text, str):
        raise QuestionError("'question' must be the verbatim text string")
    design_notes = data.get("design_notes")
    if design_notes is not None:
        for i, note in enumerate(design_notes):
            _require_keys(note, _DESIGN_NOTE_KEYS, f"design_notes[{i}]",
                          required={"item", "choice", "rationale"})
            _check_rationale_origin(note, f"design_notes[{i}]")

    components_in = circuit_in["components"]
    for i, comp in enumerate(components_in):
        _check_component_keys(comp, f"circuit.components[{i}]", target=target)

    # up front, in the target's own words, rather than incidentally inside
    # whichever emitter happened to run
    if type_errors := check_component_types(target, components_in):
        raise QuestionError("; ".join(type_errors))
    if label_errors := target.check_labels(circuit_in):
        raise QuestionError("; ".join(label_errors))
    if constraint_errors := target.check_constraints(circuit_in, constraints):
        raise QuestionError("; ".join(constraint_errors))

    # The plan is validated LAST of the schema checks. An analysis is an
    # analysis OF a circuit, so a plan error is only meaningful once the
    # circuit's components and pins are known good -- and an unknown-run-type
    # error was otherwise masking the circuit errors underneath it.
    if plan is not None:
        _check_plan_keys(plan)

    devices: dict[str, DeviceChoice] = {}
    resolved_components = []
    directives = list(circuit_in.get("directives", []))
    for comp in components_in:
        if target.uses_device_policy and comp["type"] in PART_TYPES:
            if library is None:
                library = PartsLibrary.locate()
            choice = _resolve_device(comp, library)
            devices[comp["ref"]] = choice
            resolved_components.append(
                {"ref": comp["ref"], "type": comp["type"],
                 "part": choice.part}
            )
            if choice.directive:
                directives.append(choice.directive)
        elif "ac" in comp:
            resolved = {k: v for k, v in comp.items()
                        if k in ("ref", "type")}
            resolved["value"] = _ac_value(comp["ac"])
            resolved_components.append(resolved)
        else:
            resolved_components.append(
                {k: v for k, v in comp.items()
                 if k in ("ref", "type", "value")})

    circuit = {
        "components": resolved_components,
        "nets": copy.deepcopy(circuit_in["nets"]),
        "directives": directives,
    }

    # The full non-simulation chain, as the TARGET defines it. For LTspice
    # that is emit + geometric parse + netlist comparison. For Logisim the
    # emitter is not built, and the target says so out loud rather than
    # letting an unrun check look like a passed one.
    skipped: list[SkippedCheck] = []
    try:
        trip = target.round_trip(circuit)
    except TargetError as e:
        raise QuestionError(str(e)) from None
    if not trip.ran:
        skipped.append(SkippedCheck("geometric round trip", trip.reason))
    if not target.uses_device_policy:
        skipped.append(SkippedCheck(
            "device policy",
            f"the {target.name} target has no device models, so no part was "
            f"chosen, substituted, or synthesised"))
    if plan is None:
        skipped.append(SkippedCheck(
            "analysis plan validation",
            "the question carries no 'analysis' block, so nothing was checked "
            "about runs, measurements or regimes"))
    if asks is None:
        skipped.append(SkippedCheck(
            "ask coverage",
            "the question carries no 'asks' array, so NOTHING checked whether "
            "its demands became measurements. This is the defence against a "
            "dropped ask, and it did not run"))
    # Word coverage needs BOTH the asks and the verbatim text. Reporting only
    # the ask-coverage skip would have left this one silently marked as run.
    if asks is None or question_text is None:
        missing = "asks" if asks is None else "verbatim question text"
        skipped.append(SkippedCheck(
            "question word coverage",
            f"no {missing}, so unclaimed phrases of the question could not be "
            f"shown. The verbatim text is the one defence that sits outside "
            f"the system"))
    if plan is not None:
        try:
            analysis.validate_plan(circuit, plan)
        except analysis.AnalysisError as e:
            raise QuestionError(str(e)) from None

    # An ask mapped to a measurement that does not exist is a hard
    # error (broken mapping), unlike an unmapped ask (a warning).
    names = ({m["name"] for m in plan["measurements"]
              if m.get("kind", "simulated") != "regime"}
             if plan is not None else set())
    if asks:
        for ask in asks:
            # NOT named `target`: that shadowed the Target object and only
            # showed up when something later in the function used it.
            answered = ask.get("answered_by")
            if answered is not None and answered not in names:
                raise QuestionError(
                    f'ask "{ask["text"]}" maps to {answered!r}, which is '
                    "not a measurement in the plan"
                )
        # Every prose ask's grounding contract must resolve NOW, at the gate,
        # and with NO plan the set of measurements is empty rather than
        # unchecked: a results-tier ask citing a table in a question that has
        # no analysis is broken, and should fail here rather than print a
        # confident empty section after a run that never happened.
        try:
            prose.validate_prose_asks(asks, names, design_notes)
        except prose.ProseError as e:
            raise QuestionError(str(e)) from None

    return Question(
        circuit=circuit,
        plan=copy.deepcopy(plan),
        devices=devices,
        warnings=_semantic_warnings(components_in, plan)
        + _coverage_warnings(plan, asks)
        + _ask_text_warnings(question_text, asks),
        asks=copy.deepcopy(asks),
        source=copy.deepcopy(source),
        question=question_text,
        design_notes=copy.deepcopy(design_notes),
        skipped=skipped,
        target_name=target.name,
        constraints=copy.deepcopy(data["constraints"])
        if "constraints" in data else None,
        _input_circuit=copy.deepcopy(circuit_in),
        _input_plan=copy.deepcopy(plan),
        _input_target=data.get("target"),
    )


# ---------------------------------------------------------------- dry run


def _run_line(run: dict) -> str:
    label = f"   [{run['label']}]" if "label" in run else ""
    if run["type"] == "op":
        return f"  {run['id']:<10} op{label}"
    if run["type"] == "dc":
        s = run["sweep"]
        return (f"  {run['id']:<10} dc     {s['source']} from {s['start']} "
                f"to {s['stop']} step {s['step']}{label}")
    if run["type"] == "tran":
        detail = f"to {run['stop']}"
        if run.get("settle"):
            detail += f", keep after {run['settle']}"
        if run.get("max_step"):
            detail += f", max step {run['max_step']}"
        return f"  {run['id']:<10} tran   {detail}{label}"
    if run["type"] == "truth_table":
        inputs = run["inputs"]
        # State the row count explicitly. "5 inputs" and "32 rows" are the
        # same fact, but only one of them is checkable at a glance against a
        # question that says "test all possible input combinations".
        return (f"  {run['id']:<10} table  {2 ** len(inputs)} rows over "
                f"{', '.join(inputs)}{label}")
    return (f"  {run['id']:<10} step   {run['component']} in "
            f"{' '.join(run['values'])}{label}")


def _spec_string(comp: dict) -> str:
    """The value column: what the source said about this component."""
    if "ac" in comp:
        ac = comp["ac"]
        stated = (f"{ac['rms']} Vrms" if "rms" in ac
                  else f"{ac['amplitude']} V peak")
        return (f"SINE {stated} {ac['freq']} Hz "
                f"-> {_ac_peak(ac):.4g} V peak")
    if "value" in comp:
        return comp["value"]
    if "part" in comp:
        return comp["part"]
    spec = comp.get("device", {})
    if "vz" in spec:
        return f"Vz={spec['vz']} V"
    if spec.get("params"):
        return " ".join(f"{k}={v}" for k, v in spec["params"].items())
    return "(default)"


def _device_tag(choice: DeviceChoice) -> str:
    """One-glance policy tag. The full rationale lives behind --explain:
    it matters once (when deciding policy); the values matter every run."""
    if choice.policy == "named":
        return "[named]"
    if choice.policy == "synthesized":
        anchor = re.search(r"IBV=(\S+)\)", choice.directive or "")
        return f"[synth, anchored {anchor[1]}]" if anchor else "[synth]"
    vz = re.search(r"Vz=([\d.]+)", choice.report)
    detail = f" Vz={vz[1]}" if vz else ""
    return f"[nearest: {choice.part}{detail}]"


def _parameters_found(components: list) -> str:
    """The line that catches a dropped annotation: every numeric the
    extractor claims to have read, compactly."""
    found = []
    for comp in components:
        spec = comp.get("device", {})
        if "vz" in spec:
            found.append(f"Vz={spec['vz']} ({comp['ref']})")
        for k, v in (spec.get("params") or {}).items():
            found.append(f"{k}={v} ({comp['ref']})")
        if comp.get("type") == "voltage":
            if "ac" in comp:
                ac = comp["ac"]
                stated = (f"{ac['rms']} Vrms" if "rms" in ac
                          else f"{ac['amplitude']} V peak")
                found.append(
                    f"{comp['ref']}={stated} @ {ac['freq']} Hz "
                    f"(peak {_ac_peak(ac):.4g})"
                )
            else:
                found.append(f"{comp['ref']}={comp['value']} V")
    return ", ".join(found) if found else "(none)"


def _effective_origin(comp: dict) -> str:
    if not carries_a_value(comp):
        return "stated"          # not applicable; never counted for review
    if "origin" in comp:
        return comp["origin"]
    # A semiconductor with an empty device spec was resolved by policy
    # fallback: nothing about it was stated.
    if comp.get("type") in PART_TYPES and comp.get("device") == {}:
        return "default"
    return "stated"


def dry_run_report(question: Question, explain: bool = False) -> str:
    """Everything the tool is about to do, for human confirmation.
    Nothing has been simulated when this prints."""
    q = question
    input_comps = {c["ref"]: c for c in q._input_circuit["components"]}
    lines = ["ohmwork dry run — nothing will be simulated", ""]

    # The verbatim question text leads: it is the only thing on this
    # screen not derived from model output, which makes it the most
    # valuable thing on it. Everything below is interpretation.
    if q.question is not None:
        pct, fragments = _text_coverage(q.question, q.asks or [])
        lines.append(
            f"question ({len(q.question)} chars, {pct}% claimed by asks)"
        )
        wrapped = textwrap.wrap(q.question, width=68)
        lines.append(f'  "{wrapped[0]}')
        lines += [f"   {w}" for w in wrapped[1:-1]]
        if len(wrapped) > 1:
            lines.append(f'   {wrapped[-1]}"')
        else:
            lines[-1] += '"'
        lines.append(
            "  unclaimed: "
            + (", ".join(f'"{f}"' for f in fragments)
               if fragments else "(none)")
        )
        lines.append("")

    # The gate is the only thing making a generated rationale
    # trustworthy, so say up front how much reviewing is owed.
    pending = [c for c in input_comps.values()
               if c.get("rationale") and _needs_review(c)]
    pending += [n for n in (q.design_notes or []) if _needs_review(n)]
    if pending:
        noun = "rationale requires" if len(pending) == 1 else \
            "rationales require"
        lines += [f"!! {len(pending)} {noun} your review "
                  "(see designed values below)", ""]

    if q.source:
        s = q.source
        head = f"source: {s['file']}"
        if "resolution" in s:
            head += f" ({s['resolution']})"
        if "question_chars" in s:
            head += f", question text {s['question_chars']} chars"
        lines.append(head)
        if "extractor" in s:
            ext = f"extraction: {s['extractor']}"
            if "attempts" in s:
                ext += f", {s['attempts']} attempts"
            for level in ("low", "medium"):
                refs = [ref for ref, c in s.get("confidence", {}).items()
                        if c == level]
                if refs:
                    ext += f", {level} confidence on {', '.join(refs)}"
            lines.append(ext)
        lines.append("")

    lines.append("components")
    for comp in q.circuit["components"]:
        ref = comp["ref"]
        source_comp = input_comps[ref]
        spec = _spec_string(source_comp)
        tag = f"   {_device_tag(q.devices[ref])}" if ref in q.devices else ""
        origin = _effective_origin(source_comp)
        arrow = f"   <- {origin}" if origin != "stated" else ""
        lines.append(f"  {ref:<5}{comp['type']:<9}{spec:<12}{tag}{arrow}")

    if explain and q.devices:
        lines += ["", "device rationale"]
        for ref, choice in q.devices.items():
            lines.append(f"  {ref}: [{choice.policy}] {choice.report}")

    lines += ["", "nets"]
    for net, pins in q.circuit["nets"].items():
        lines.append(f"  {net:<6}{'  '.join(pins)}")

    if explain and q.circuit["directives"]:
        lines += ["", "device model cards"]
        lines += [f"  {d}" for d in q.circuit["directives"]]

    if q.plan:
        lines += ["", "runs"]
        lines += [_run_line(run) for run in q.plan["runs"]]

        lines += ["", "measurements"]
        regimes = []
        for m in q.plan["measurements"]:
            kind = m.get("kind", "simulated")
            if kind == "regime":
                regimes.append(m)
            elif kind == "derived":
                lines.append(f"  {m['name']:<15}= {m['formula']}")
                if "definition" in m:
                    lines.append(f"  {'':<15}  definition: {m['definition']}")
            elif kind == "table":
                lines.append(
                    f"  {m['name']:<15}= truth table of "
                    f"{', '.join(m['outputs'])}   @ run {m['run']}"
                )
            else:
                at = ""
                if "at" in m:
                    key, value = next(iter(m["at"].items()))
                    at = f", {key}={value}"
                lines.append(
                    f"  {m['name']:<15}= {m['expr']}   "
                    f"@ run {m['run']}{at}"
                )

        if regimes:
            lines += ["", "regime assertions"]
            by_run: dict[str, list[str]] = {}
            for r in regimes:
                if r.get("device"):
                    extra = f", Vz={r['vz']}" if "vz" in r else ""
                    text = f"{r['assert']}({r['device']}{extra})"
                else:
                    # digital regimes are properties of the whole circuit
                    text = f"{r['assert']}(circuit)"
                by_run.setdefault(r["run"], []).append(text)
            for run_id, checks in by_run.items():
                lines.append(f"  {run_id}: {', '.join(checks)}")

    # Coverage: the section that shows what is NOT there. The rest of
    # this report displays what was extracted; only this part can
    # surface a dropped ask or invented work.
    lines.append("")
    if q.asks is not None:
        lines.append(f"question asks ({len(q.asks)} found in text)")
        unmapped = []
        prose_lines = prose.preview(q.asks)
        for ask in q.asks:
            answered = ask.get("answered_by")
            if prose.is_prose(ask):
                pass                      # rendered by prose.preview above
            elif answered:
                lines.append(f'  "{ask["text"]}"'.ljust(28)
                             + f" -> {answered:<16}OK")
            else:
                unmapped.append(ask["text"])
        if prose_lines:
            # NOT "unmapped". These can never map to a measurement, and
            # listing them as possible dropped work trains the reader to skip
            # the line that catches real drops. The count of UNGROUNDED ones
            # is the number that matters: it is how much unverifiable text is
            # coming, shown while the human can still do something about it.
            lines += prose_lines.splitlines()
        lines.append("unmapped")
        if unmapped:
            lines += [f'  ! "{t}"' for t in unmapped]
        else:
            lines.append("  (none)")
        guards = [m for m in (q.plan or {}).get("measurements", [])
                  if "guard" in m]
        if guards:
            lines.append("guards (deliberate extra measurements)")
            lines += [f"  {m['name']} — {m['guard']}" for m in guards]
    else:
        lines.append("question asks: none recorded — coverage unchecked "
                     "(add an 'asks' array)")

    comps_in = list(input_comps.values())
    lines += [
        "",
        f"extracted from source: {len(comps_in)} components, "
        f"{len(q._input_circuit['nets'])} nets, "
        f"{len(q.asks or [])} asks",
        f"parameters found: {_parameters_found(comps_in)}",
    ]
    if q.source is not None:
        unused = q.source.get("annotations_unused") or []
        lines.append(
            "annotations seen but unused: "
            + ("; ".join(unused) if unused else "(none)")
        )

    # Designed values: the part the student has to understand and
    # defend. A design question answered without this section would
    # submit our engineering judgement as theirs, invisibly.
    designed = []
    for comp in input_comps.values():
        origin = _effective_origin(comp)
        if origin == "stated":
            continue
        ref = comp["ref"]
        rationale = comp.get("rationale")
        if rationale:
            tag = _authorship_tag(comp)
        elif ref in q.devices:
            # A DeviceChoice report is deterministic output derived from
            # the verified library, not model prose: it carries its
            # policy tag and needs no authorship review.
            rationale, tag = q.devices[ref].report, ""
        else:
            rationale, tag = "(no rationale recorded)", ""
        if ref in q.devices:
            choice = q.devices[ref].part  # what was actually picked
        else:
            choice = _spec_string(comp)
        designed.append((ref, choice, rationale, tag))
    for note in q.design_notes or []:
        designed.append((note["item"], note["choice"], note["rationale"],
                         _authorship_tag(note)))
    if designed:
        lines += ["",
                  f"designed values ({len(designed)}) — these are "
                  "choices, not given"]
        for name, choice, rationale, tag in designed:
            suffix = f"  {tag}" if tag else ""
            # an explicit separator: left-padding alone ran the item and the
            # choice together whenever the item was longer than the column
            lines.append(f"  {name:<10} {choice} — {rationale}{suffix}")
        lines.append(
            "  If any of these look wrong for your lab, change them "
            "before use.")

    lines += ["", "warnings"]
    if q.warnings:
        lines += [f"  ! {w}" for w in q.warnings]
    else:
        lines.append("  (none)")

    # What ran, and what did NOT. This used to print a flat
    # "structural validation: OK (emits, geometric parse round-trips, plan
    # validates)" for every question — which is simply false for a target
    # whose round trip does not exist yet. That is the unrun-check failure
    # in the worst possible place: the screen a human reads to decide
    # whether to trust everything above it.
    lines += ["", f"checks [target: {q.target_name}]"]
    skipped_names = {entry.name for entry in q.skipped}
    for name in ("schema and component types", "net and pin references",
                 "geometric round trip", "device policy",
                 "analysis plan validation", "ask coverage",
                 "question word coverage"):
        if name not in skipped_names:
            lines.append(f"  ran      {name}")
    for entry in q.skipped:
        lines.append(f"  SKIPPED  {entry.name}")
        lines.append(f"           {entry.reason}")
    if q.skipped:
        lines.append("  a skipped check is not a passed check — the screen "
                     "above is quieter than it would otherwise be")
    return "\n".join(lines) + "\n"
