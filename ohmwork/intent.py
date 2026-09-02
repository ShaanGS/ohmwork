"""The analog design intent: what the question demands of the finished circuit.

WHY THIS IS NOT `spec.py`. A digital question has an oracle. Write one boolean
expression per output, enumerate 2**n rows, and an outside tool either
reproduces them or does not. Analog has nothing of the kind: a circuit
converges, produces numbers, and there is no exhaustive table to check them
against.

So analog verification is three weaker things, and the weakness is stated
wherever a result ships rather than left to be inferred:

    1. IT SIMULATES        LTspice reads the emitted file and converges. Not
                           nothing -- a floating node or a source loop dies
                           here -- but far short of correctness.
    2. THE REGIMES HOLD    convergence is not correctness. A load sweep into
                           dropout converges perfectly and reports a
                           confident, meaningless regulation figure. These are
                           DERIVED from the circuit and never asked for: every
                           zener gets zener_in_breakdown, every BJT gets
                           bjt_active, on every measured run, so a design
                           cannot quietly omit the one that would have failed.
    3. THE NUMBERS MATCH   each target the question states -- "9 V output",
       THE INTENT          "line regulation better than 1%" -- measured by
                           LTspice and checked against the question's own
                           number, within a tolerance that is CAPPED. A
                           tolerance wide enough to admit any plausible
                           circuit is not a check.

THE HOLE IT SHARES WITH THE DIGITAL LOOP: the intent is the model's READING of
the question. Read "9 V" as "5 V" and the intent, the circuit and LTspice all
agree. `Intent.render()` is therefore output, not a debugging aid.

THE HOLE IT DOES NOT SHARE, and this one is bigger: **meeting a target is not
being a good design.** A regulator that hits 9.00 V with 2 V of ripple, or
with its pass transistor dissipating six watts, passes everything here. A
truth table has no equivalent gap -- 32 correct rows really are the whole
answer. Nothing in this module should ever be described as proving a design
is right; it proves the design does the things the question asked to be
measured.

A target with no number is an OBSERVATION. "Observe the output waveform" is a
real ask with nothing to check, so it is measured, reported, and counted
SEPARATELY -- a run where nothing could have failed must not look like a run
where nothing did.

`tests/test_intent.py` is the spec for this module.
"""

import json
import re
from dataclasses import dataclass, field

from ohmwork.basis import Basis

#: A tolerance is a claim about how closely the circuit must hit the number.
#: Past this it stops being a claim: any plausible circuit satisfies it, the
#: check cannot fail, and "verified" means nothing. Lab design questions live
#: at 1-10%, so 20 is generous rather than tight.
MAX_TOLERANCE_PCT = 20.0

#: Target kinds, and the fields each one needs. A closed vocabulary, for the
#: same reason `prose.py` closed its row filters: the guarantee comes from
#: there being one small set of things this can mean, all of which map onto a
#: run and a measurement the analysis layer already knows how to execute.
TARGET_KINDS = {
    "dc_voltage": ("net",),
    # A current names a "role" (supply, load, zener, ...) OR, when the
    # question itself names the part -- "the current through R3" -- a "ref".
    # `_parse_target` enforces exactly one of the two.
    "dc_current": (),
    "line_regulation": ("net", "low", "high"),
    "load_regulation": ("net", "light", "heavy"),
    "ripple_pp": ("net",),
    "ac_rms": ("net",),
    "ac_mean": ("net",),
    "waveform": ("net",),
    # A CURRENT over time, which `waveform` cannot express: it measures
    # V(net). MEASURED on the live Q3 run, whose "load current waveform" came
    # back as a `waveform` target on the load's node -- a voltage, reported
    # under a name that says current, with nothing to catch it because an
    # observation has no number to fail against.
    "current_waveform": (),
    # Added 2026-09-02 from the first two questions of the acceptance corpus.
    # `_waveform_stats` had computed min and max since Q3 and thrown them
    # away, so a clipper's "clipping level", a clamper's "DC level shift" and
    # a rectifier's "ripple factor" could only ever be reported as a MEAN --
    # the wrong statistic, under the right name. Each is one statistic of the
    # same settled window, so nothing new is simulated.
    "peak_max": ("net",),        # the most positive the waveform reaches
    "peak_min": ("net",),        # the most negative
    "dc_level": ("net",),        # (max + min) / 2: where a clamper puts it
    "ripple_factor": ("net",),   # rms of the AC part over the mean, unitless
}

#: Kinds that measure a current, and so name a role or a ref.
CURRENT_KINDS = {"dc_current", "current_waveform"}

#: Kinds that need a transient run, and therefore a source frequency.
TRANSIENT_KINDS = {"ripple_pp", "ac_rms", "ac_mean", "waveform",
                   "current_waveform", "peak_max", "peak_min", "dc_level",
                   "ripple_factor"}

#: The unit a kind is always in, used when the intent omits one. DERIVED
#: rather than required, for the same reason the plan is: a volt target is
#: measured in volts whatever anyone writes, so asking adds a way to be wrong
#: and no way to be right. MEASURED on the live Q3 run, where the reading
#: rendered "6.2  +/- 5%" with the unit missing -- cosmetic, and not worth
#: spending a retry on.
UNIT_OF = {"dc_voltage": "V", "dc_current": "A", "ripple_pp": "V",
           "ac_rms": "V", "ac_mean": "V", "waveform": "V",
           "line_regulation": "%", "load_regulation": "%",
           "current_waveform": "A", "peak_max": "V", "peak_min": "V",
           "dc_level": "V", "ripple_factor": ""}

#: Which statistic of a waveform each transient kind means. A waveform
#: measurement's `value` is its time-weighted MEAN, so reading a ripple target
#: off `value` would compare a peak-to-peak requirement against an average --
#: incident 5's number in a new place.
STATISTIC = {"ripple_pp": "ripple_pp", "ac_rms": "rms",
             "ac_mean": "mean", "waveform": "mean",
             "current_waveform": "mean",
             "peak_max": "max", "peak_min": "min",
             "dc_level": "dc_level", "ripple_factor": "ripple_factor"}

#: What a current target may name. Roles rather than refs, because a ref is a
#: DESIGN artefact and the intent is written before any circuit exists.
#:
#: Two of them resolve by reserved name and three by component type, and the
#: split is forced: a supply and a load are a `voltage` and a `res` like any
#: other, so nothing in the parts list distinguishes them. The design prompt
#: states the reserved names, and `build_analog_plan` says so when one is
#: missing.
ROLES = {
    "supply": ("ref", "V1"),
    "load": ("ref", "RL"),
    "zener": ("type", ("zener",)),
    "transistor": ("type", ("npn", "pnp")),
    "diode": ("type", ("diode",)),
}

#: Becomes a measurement name, and measurement names are referenced by derived
#: formulas and by `answered_by`.
NAME = re.compile(r"^[a-z][a-z0-9_]*$")

#: Becomes a FLAG label in the emitted `.asc` and a trace name in the results.
NET = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

#: Ten periods simulated, the first five discarded, two hundred steps per
#: period. MEASURED rather than chosen: for the 50 Hz supply in
#: `examples/q3.json` this reproduces the stop, settle and max_step a person
#: wrote by hand for that circuit, to the digit.
PERIODS_SIMULATED = 10
PERIODS_DISCARDED = 5
STEPS_PER_PERIOD = 200

#: Run ids, fixed so a report, a manifest and a deliverable all name the same
#: run the same way. They match the ids the hand-written examples use.
OP_RUN = "nominal"
LINE_RUN = "linesweep"
LOAD_RUN = "loadsweep"
TRAN_RUN = "waveforms"


class IntentError(Exception):
    """The intent is not something this module will build a plan from.

    Every message names the target, the field or the part, because these are
    fed straight back to the model that wrote them and a message that does not
    say what to fix wastes an entire retry.
    """


# ------------------------------------------------------------ the intent

@dataclass(frozen=True)
class Target:
    """One thing the finished circuit must measurably do."""

    name: str                    # becomes the measurement's name
    kind: str                    # from TARGET_KINDS
    quantity: str                # the question's own words for it
    unit: str
    #: At most ONE of these three. `value` is a centre with a tolerance;
    #: `maximum` and `minimum` are bounds ("better than 1%"). None of them
    #: means the question asked for it to be reported, not to be a number.
    value: float | None = None
    tolerance_pct: float | None = None
    maximum: float | None = None
    minimum: float | None = None
    #: kind-specific
    net: str | None = None
    net2: str | None = None      # for a difference, e.g. a floating source
    role: str | None = None
    #: The question's OWN name for a component whose current it asks for
    #: ("the current through R3"). Unlike a role it is the question's word,
    #: not a design artefact, so the design is required to use it. Added
    #: 2026-09-02: Thevenin, superposition and KCL labs ask for branch
    #: currents and could only report node voltages.
    ref: str | None = None
    low: float | None = None
    high: float | None = None
    light: object = None
    heavy: object = None

    @property
    def is_observation(self) -> bool:
        return (self.value is None and self.maximum is None
                and self.minimum is None)

    @property
    def expr(self) -> str:
        """The SPICE expression this target measures."""
        if self.net2:
            return f"V({self.net})-V({self.net2})"
        return f"V({self.net})"

    def where(self) -> str:
        """WHAT this target is measured on, for the reading.

        MEASURED on the live Q3 run: a "load current waveform" target came
        back as a voltage on the load's node, reported under a name that says
        current, with nothing to catch it -- an observation has no number to
        fail against. A reading that shows the expression makes the mistake
        visible to the one reader who can act on it.
        """
        if self.role:
            what = "current" if self.kind in ("dc_current",
                                              "current_waveform") else "value"
            return f"the {self.role}'s {what}"
        if self.ref:
            return f"the current through {self.ref}"
        where = (f"V({self.net}) - V({self.net2})" if self.net2
                 else f"V({self.net})")
        if self.kind == "line_regulation":
            return (f"{where}, over a {_g(self.low)} V to {_g(self.high)} V "
                    f"input")
        if self.kind == "load_regulation":
            return f"{where}, from a {self.light} to a {self.heavy} load"
        return where

    def wanted(self) -> str:
        """What was asked for, in words, for a message a person reads."""
        if self.value is not None:
            return f"{_g(self.value)} {self.unit} +/- {_g(self.tolerance_pct)}%"
        if self.maximum is not None:
            return f"at most {_g(self.maximum)} {self.unit}"
        if self.minimum is not None:
            return f"at least {_g(self.minimum)} {self.unit}"
        return "not checked (reported only)"


@dataclass(frozen=True)
class Intent:
    """What the question demands, read from its words before any design."""

    topology: str
    targets: tuple
    stated_values: tuple = ()
    frequency: float | None = None
    notes: tuple = ()

    @property
    def checkable(self) -> int:
        """How many targets could actually have failed.

        Published rather than computed at each call site: a run in which
        nothing could fail must not be reported the same way as one in which
        nothing did.
        """
        return sum(0 if t.is_observation else 1 for t in self.targets)

    def reading_data(self) -> dict:
        """The reading as DATA: the same facts `render` prints, for a page
        that lays them out instead of printing a monospace block.

        Each target carries what it is measured ON (`where`) beside what was
        asked FOR (`wanted`), for the reason `Target.where` records: a
        current reported as a voltage is only visible to a reader who can
        see the expression.
        """
        return {
            "topology": self.topology,
            "frequency": self.frequency,
            "targets": [{"name": t.name, "quantity": t.quantity,
                         "unit": t.unit, "where": t.where(), "ref": t.ref,
                         "wanted": t.wanted(),
                         "checked": not t.is_observation,
                         "figure_stated": figure_is_stated(t, self.stated_values)}
                        for t in self.targets],
            "stated": [{"what": s["what"], "value": s["value"],
                        "unit": s.get("unit", "")} for s in self.stated_values],
            "notes": list(self.notes),
        }

    def render(self) -> str:
        """The reading, shown to a human before the answer.

        The one failure nothing downstream can catch is a misreading of the
        question -- `1.8k` read as `1.8M` simulates perfectly well and answers
        a different question confidently. So every number that a misread would
        corrupt appears here, and this is output rather than a debugging aid.
        """
        width = max((len(t.name) for t in self.targets), default=0)
        quantity = max((len(t.quantity) for t in self.targets), default=0)
        lines = [f"topology: {self.topology}"]
        if self.frequency:
            lines.append(f"source frequency: {_g(self.frequency)} Hz")
        lines.append("targets:")
        where = max((len(t.where()) for t in self.targets), default=0)
        for target in self.targets:
            flag = ("  [figure NOT among the stated values -- check it "
                    "against the question]"
                    if figure_is_stated(target, self.stated_values) is False
                    else "")
            if self.frequency and target.kind in ("dc_voltage", "dc_current"):
                flag = "  (as the mean of the settled waveform)" + flag
            lines.append(f"  {target.name:<{width}}  "
                         f"{target.quantity:<{quantity}}  "
                         f"{target.where():<{where}}  {target.wanted()}{flag}")
        if self.stated_values:
            lines.append("stated in the question:")
            for stated in self.stated_values:
                lines.append(f"  {stated['what']} = {stated['value']} "
                             f"{stated.get('unit', '')}".rstrip())
        if self.notes:
            # Its own heading. MEASURED on the first live analog run: a note
            # about a tolerance CHOSEN here rendered directly underneath
            # "stated in the question:", at the same indent, and read as
            # though the question had stated it.
            lines.append("chosen here, because the question left it open:")
            lines += [f"  {note}" for note in self.notes]
        return "\n".join(lines)


def _g(value) -> str:
    """A number as a person would write it: 9, not 9.0."""
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


# ------------------------------------------------------------- parsing

def _number(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IntentError(f"{where}: expected a number, got {value!r}")
    return float(value)


def _parse_target(data, index) -> Target:
    where = f"targets[{index}]"
    if not isinstance(data, dict):
        raise IntentError(f"{where}: expected an object, got {data!r}")

    name = data.get("name")
    if not (isinstance(name, str) and NAME.match(name)):
        raise IntentError(
            f"{where}: name {name!r} is not usable as a measurement name. "
            f"It must match {NAME.pattern} -- it is referenced by derived "
            f"formulas and by the report.")

    kind = data.get("kind")
    if kind not in TARGET_KINDS:
        raise IntentError(
            f"{where}: unknown kind {kind!r}. Known kinds: "
            f"{', '.join(sorted(TARGET_KINDS))}")

    for required in TARGET_KINDS[kind]:
        if data.get(required) is None:
            raise IntentError(f"{where}: a {kind} target needs {required!r}")

    for key in ("net", "net2"):
        net = data.get(key)
        if net is not None and not (isinstance(net, str) and NET.match(net)):
            raise IntentError(
                f"{where}: net name {net!r} is not usable. It becomes a FLAG "
                f"label in the emitted file and a trace name in the results, "
                f"so it must match {NET.pattern}.")

    role = data.get("role")
    if role is not None and role not in ROLES:
        raise IntentError(
            f"{where}: unknown role {role!r}. Known roles: "
            f"{', '.join(sorted(ROLES))}")
    ref = data.get("ref")
    if ref is not None and not (isinstance(ref, str) and NET.match(ref)):
        raise IntentError(
            f"{where}: ref {ref!r} is not usable as a component name. It "
            f"must match {NET.pattern} and be the name the question uses.")
    if kind in CURRENT_KINDS:
        if (role is None) == (ref is None):
            raise IntentError(
                f"{where}: a {kind} target names WHERE the current flows: "
                f"exactly one of \"role\" (one of "
                f"{', '.join(sorted(ROLES))}) or \"ref\" (the question's own "
                f"name for the component, e.g. R3). It has "
                f"{'both' if role else 'neither'}.")

    bounds = [key for key in ("value", "maximum", "max", "minimum", "min")
              if data.get(key) is not None]
    normalised = {"max": "maximum", "min": "minimum"}
    bounds = sorted({normalised.get(key, key) for key in bounds})
    if len(bounds) > 1:
        raise IntentError(
            f"{where}: a target may carry only one of value, max or min; "
            f"it carries {bounds}. A centre with a tolerance and a bound are "
            f"different claims about the same number.")

    value = data.get("value")
    tolerance = data.get("tolerance_pct")
    if value is not None:
        value = _number(value, f"{where}.value")
        if tolerance is None:
            raise IntentError(
                f"{where}: a value needs a tolerance_pct. A simulated number "
                f"never equals a stated one exactly, so a target with no "
                f"tolerance fails every time and reads as a broken design "
                f"rather than as a malformed intent.")
        tolerance = _number(tolerance, f"{where}.tolerance_pct")
        if not 0 < tolerance <= MAX_TOLERANCE_PCT:
            raise IntentError(
                f"{where}: tolerance_pct {_g(tolerance)} is outside "
                f"(0, {_g(MAX_TOLERANCE_PCT)}]. Past that any plausible "
                f"circuit satisfies it, so the check cannot fail and "
                f"'verified' would mean nothing.")
    elif tolerance is not None:
        raise IntentError(
            f"{where}: tolerance_pct with no value has nothing to be a "
            f"tolerance of")

    quantity = data.get("quantity")
    if not isinstance(quantity, str) or not quantity.strip():
        raise IntentError(
            f"{where}: needs a 'quantity' -- the question's own words for "
            f"what this is. The reading is what a person checks, and a row "
            f"of measurement names is not something anyone can check.")

    maximum = data.get("maximum", data.get("max"))
    minimum = data.get("minimum", data.get("min"))
    return Target(
        ref=ref,
        name=name, kind=kind, quantity=quantity.strip(),
        unit=str(data.get("unit") or UNIT_OF[kind]),
        value=value, tolerance_pct=tolerance,
        maximum=None if maximum is None else _number(maximum, f"{where}.max"),
        minimum=None if minimum is None else _number(minimum, f"{where}.min"),
        net=data.get("net"), net2=data.get("net2"), role=role,
        low=None if data.get("low") is None else _number(data["low"], where),
        high=None if data.get("high") is None else _number(data["high"], where),
        light=data.get("light"), heavy=data.get("heavy"),
    )


def parse_intent_reply(text) -> Intent:
    """A model's intent reply -> an Intent that is known to build a plan.

    Validation happens HERE rather than later so a rejection can be fed back
    to the model while the retry is still about the thing it got wrong.
    """
    if isinstance(text, str):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise IntentError(f"the intent is not valid JSON: {exc}") from exc
    else:
        data = text
    if not isinstance(data, dict):
        raise IntentError(f"expected a JSON object, got {type(data).__name__}")

    topology = data.get("topology")
    if not isinstance(topology, str) or not topology.strip():
        raise IntentError(
            "the intent needs a 'topology': what kind of circuit this is, in "
            "the question's words")

    raw = data.get("targets")
    if not isinstance(raw, list) or not raw:
        # The analog shape of `check_spec_has_logic`. An intent that demands
        # nothing is satisfied by anything that converges, and would report
        # VERIFIED over a circuit nobody checked.
        raise IntentError(
            "the intent lists no targets. A circuit checked against nothing "
            "is not checked: every question asks for SOMETHING to be "
            "measured, even if only to be reported.")

    targets = tuple(_parse_target(item, index)
                    for index, item in enumerate(raw))
    seen = {}
    for target in targets:
        if target.name in seen:
            raise IntentError(f"duplicate target name {target.name!r}")
        seen[target.name] = target

    # Two targets that are the SAME measurement under two names. MEASURED
    # 2026-09-02 on the Exp 4.7 clamper, a two-part question: the intent
    # named part (i)'s output and part (ii)'s output both "vout", one circuit
    # was designed, and the report showed two quantities with identical
    # numbers -- half the question answered, nothing to catch it, because an
    # observation carries no figure to fail. The measurement is the same
    # expression on the same run, so identical numbers are not a coincidence
    # to notice afterwards; they are guaranteed, and refusable up front.
    where = {}
    for target in targets:
        key = (target.kind, target.net, target.net2, target.role, target.ref)
        other = where.setdefault(key, target)
        if other is not target:
            raise IntentError(
                f"{other.name} and {target.name} are the same measurement "
                f"({target.kind} on {target.where()}) under two names, so "
                f"they would always report the same number. If the question "
                f"has two parts -- (i) and (ii), an unbiased and a biased "
                f"circuit -- each part is its OWN circuit with its OWN nodes: "
                f"name them differently (vout_a and vout_b, vout1 and vout2) "
                f"so each is designed and measured separately.")

    for kind in ("line_regulation", "load_regulation"):
        many = [t.name for t in targets if t.kind == kind]
        if len(many) > 1:
            raise IntentError(
                f"{len(many)} {kind} targets ({', '.join(many)}). Each one "
                f"needs its own sweep, and this build derives one sweep per "
                f"kind. Ask for one.")

    frequency = data.get("frequency")
    if frequency is not None:
        frequency = _number(frequency, "frequency")
    if any(t.kind in TRANSIENT_KINDS for t in targets) and not frequency:
        raise IntentError(
            "a transient target needs the source 'frequency' in Hz. The "
            "simulation window is derived from it -- ten periods, the first "
            "five discarded -- and an invented window either misses the "
            "steady state or takes minutes to run.")

    # An AC-fed circuit has no meaningful DC operating point: the AC source
    # is ZERO there, so a dc_voltage/dc_current target measures a silent 0
    # that NO correct circuit can bring to the stated figure. MEASURED on a
    # live claude-opus-5 Q3 run: two sound rectifier designs failed a
    # dc_voltage 6.2 V check at 0 V, and the loop's feedback was advice
    # about a run that means nothing.
    # A dc_voltage/dc_current target in an AC-FED circuit used to be REFUSED
    # here (measured at the operating point, where an AC source is zero, no
    # correct rectifier passes a 6.2 V check -- the second paid Q3 run). The
    # refusal was right about the operating point and wrong about the fix:
    # MEASURED 2026-09-02, a clamper question died twice at this line because
    # the model insisted, correctly, that a 2 V bias source is a DC voltage.
    # So the plan now CONVERTS: with a frequency set, a DC target is measured
    # as the mean of the settled waveform, and the reading says so.

    stated = tuple(data.get("stated_values") or ())
    for index, item in enumerate(stated):
        if not isinstance(item, dict) or "what" not in item or "value" not in item:
            raise IntentError(
                f"stated_values[{index}]: needs 'what' and 'value'. These are "
                f"the numbers the question fixes, and they are the ones a "
                f"misread corrupts invisibly.")

    if frequency:
        _check_stated_amplitudes(targets, stated)
    _check_clamper_polarity(topology, targets)

    return Intent(topology=topology.strip(), targets=targets,
                  stated_values=stated, frequency=frequency,
                  notes=tuple(data.get("notes") or ()))


def _check_clamper_polarity(topology: str, targets) -> None:
    """A POSITIVE clamper shifts the waveform up; a negative one down. The
    word states the sign, so the sign is checkable -- and it was not being
    checked. MEASURED 2026-09-02: a "positive clamper" question MET THE
    INTENT in one attempt with an output swinging -7.6 V to +0.7 V, a
    negative clamper, because the only thing on the output was an
    observation. A dc_level target on a clamper output must carry the sign
    as a bound: "min": 0 for positive, "max": 0 for negative. Zero is not a
    figure the question has to state; it is what the word means.
    """
    words = (topology or "").lower()
    if "positive clamp" in words and "negative clamp" in words:
        return                      # both polarities named: nothing to infer
    if "positive clamp" in words:
        wanted, field, label = "minimum", "min", "positive"
    elif "negative clamp" in words:
        wanted, field, label = "maximum", "max", "negative"
    else:
        return
    for target in targets:
        if target.kind != "dc_level":
            continue
        if getattr(target, wanted) == 0 or target.value is not None:
            continue
        raise IntentError(
            f"{target.name} ({target.quantity}): the topology is a {label} "
            f"clamper, which shifts the output {'up' if label == 'positive' else 'down'}, "
            f"so its DC level has a SIGN the question states. Give this "
            f"dc_level target \"{field}\": 0 so a clamper of the wrong "
            f"polarity fails instead of being reported as an observation.")


def figure_is_stated(target, stated) -> bool | None:
    """Is the number this target is checked against one the question states?

    None for a target with no figure. MEASURED 2026-09-02 on the Exp 4.7
    clamper: the model made "DC level shift" a checked target of 5 V, a
    figure the question never gives (it asks the student to DETERMINE it). A
    real clamper shifts by about 4.3 V, so a correct circuit would have
    failed a check against the model's own arithmetic. Refusing outright was
    tried and broke every honest intent that checks "delivers 9 V" without
    repeating the 9 under stated_values -- so this is DISCLOSED in the
    reading, beside the figure, where the one reader who can judge it looks.
    """
    figures = [f for f in (target.value, target.maximum, target.minimum)
               if f is not None]
    if not figures:
        return None
    numbers = [n for n in (_stated_number(item) for item in stated)
               if n is not None]
    # Zero is a sign, not a figure: "positive clamper" states it.
    return all(f == 0 or any(abs(f - n) <= 0.02 * max(abs(n), 1e-12)
                             for n in numbers)
               for f in figures)


#: Words in a stated value that mean "this is an AC amplitude".
_PP_WORDS = ("vpp", "v pp", "p-p", "peak-to-peak", "peak to peak", "pk-pk")
_RMS_WORDS = ("rms",)


def _stated_number(item) -> float | None:
    try:
        return float(str(item.get("value")).strip().split()[0])
    except (ValueError, IndexError, AttributeError):
        return None


def _check_stated_amplitudes(targets, stated) -> None:
    """A stated input amplitude must be a checked target, mechanically.

    The prompt rule saying so was followed on one live run and ignored on the
    next (2026-09-02, Exp 4.7 clamper): the model listed "10 Vpp" under
    stated_values, made no target of it, and a design whose output swung
    3.6 V for a 10 Vpp input passed every check. A prompt nudge that a model
    follows sometimes is not a check -- the same lesson as
    `check_priority_encoder`. So the gate looks for every stated value whose
    words say peak-to-peak or RMS and requires a target of the matching kind
    carrying that number.
    """
    for item in stated:
        words = f"{item.get('what', '')} {item.get('unit', '')}".lower()
        number = _stated_number(item)
        if number is None:
            continue
        if any(w in words for w in _PP_WORDS):
            kind, label = "ripple_pp", "peak-to-peak"
        elif any(w in words for w in _RMS_WORDS):
            kind, label = "ac_rms", "RMS"
        else:
            continue
        # Only INPUT-ish amplitudes: a stated ripple limit on the output is a
        # bound, not an amplitude, and is handled by max/min already.
        if any(w in words for w in ("ripple", "output")):
            continue
        matched = any(t.kind == kind and t.value is not None
                      and abs(t.value - number) <= 0.02 * abs(number)
                      for t in targets)
        if not matched:
            raise IntentError(
                f"the question states an input amplitude -- "
                f"{item.get('what')} = {item.get('value')} "
                f"{item.get('unit', '')}".rstrip() + f" -- but no target "
                f"checks it. Add a target of kind \"{kind}\" on the input "
                f"node with value {_g(number)} and a tolerance of a few "
                f"percent (recorded in notes). A stated {label} figure that "
                f"nothing checks is the one number the design could get "
                f"wrong unnoticed: on a live run a 10 Vpp question was "
                f"answered by a 3 Vpp source and passed.")


# --------------------------------------------------------- SPICE numbers

_SCALES = ((1e12, "T"), (1e9, "G"), (1e6, "Meg"), (1e3, "k"), (1, ""),
           (1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p"))


def spice_number(value: float) -> str:
    """A float as SPICE writes it: 0.2 -> "200m", 1e-4 -> "100u".

    Read back by `analysis.parse_spice_number`, and a test round-trips the
    pair. Two independent conventions for one number is how a 470u becomes a
    470p.
    """
    for scale, suffix in _SCALES:
        if abs(value) >= scale:
            return f"{value / scale:.6g}{suffix}"
    return f"{value:.6g}"


# ---------------------------------------------------------- the plan

def _resolve_current(target, circuit: dict) -> str:
    """The ref whose current a target measures: by role, or by the question's
    own name for the part, which the design is then required to contain."""
    if target.ref:
        if any(c.get("ref") == target.ref for c in circuit.get("components") or []):
            return target.ref
        raise IntentError(
            f"the intent measures the current through {target.ref}, which is "
            f"the question's own name for that component, and this design "
            f"has no component named {target.ref}. Name it {target.ref}.")
    return _resolve_role(target.role, circuit)


def _resolve_role(role: str, circuit: dict) -> str:
    """A role -> the ref in this design that fills it."""
    how, what = ROLES[role]
    components = circuit.get("components") or []
    if how == "ref":
        if any(c.get("ref") == what for c in components):
            return what
        # A question in parts names each part's source and load with a
        # suffix (V1_i, V1_ii). One such component is unambiguous; several
        # means the intent has to say which part it measures.
        suffixed = sorted(c["ref"] for c in components
                          if str(c.get("ref", "")).startswith(what + "_"))
        if len(suffixed) == 1:
            return suffixed[0]
        if suffixed:
            raise IntentError(
                f"the intent measures the {role}, and this design has "
                f"{len(suffixed)} of them ({', '.join(suffixed)}), one per "
                f"part. A role cannot say which part it means: measure that "
                f"quantity by net or by ref instead.")
        raise IntentError(
            f"the intent measures the {role}, which by convention is the "
            f"component named {what!r} (or {what}_<part> in a question with "
            f"parts), and this design has no {what}. Name the {role} {what}.")
    matching = [c["ref"] for c in components if c.get("type") in what]
    if not matching:
        raise IntentError(
            f"the intent measures the {role}, and this design contains no "
            f"{' or '.join(what)} component.")
    if len(matching) > 1:
        raise IntentError(
            f"the intent measures the {role}, and this design has "
            f"{len(matching)} of them ({', '.join(sorted(matching))}). "
            f"Nothing here can tell which one the question meant.")
    return matching[0]


def _regime_entries(circuit: dict, runs) -> list:
    """Regimes, DERIVED from the parts list rather than requested.

    Convergence is not correctness: a load sweep into dropout converges
    perfectly and reports a confident, meaningless regulation figure. A model
    asked to declare these can omit exactly the one that would have failed, so
    it is not asked.
    """
    run_ids = [run["id"] if isinstance(run, dict) else run for run in runs]
    tran_ids = [run["id"] for run in runs
                if isinstance(run, dict) and run.get("type") == "tran"]
    entries = []
    for component in circuit.get("components") or []:
        kind = component.get("type")
        if kind == "diode":
            # Only over a settled transient window: on a DC operating point a
            # rectifier's or clamper's diode has one state, and demanding
            # both there would fail a correct circuit.
            entries += [{"kind": "regime", "run": run,
                         "assert": "diode_conducts",
                         "device": component["ref"]} for run in tran_ids]
        elif kind == "zener":
            for run in run_ids:
                entry = {"kind": "regime", "run": run,
                         "assert": "zener_in_breakdown",
                         "device": component["ref"]}
                # `vz` is omitted rather than guessed when the part is named
                # by number: the reverse-current half of the assertion still
                # runs, and a check that examined less must not claim more.
                vz = (component.get("device") or {}).get("vz")
                if vz is not None:
                    entry["vz"] = vz
                entries.append(entry)
        elif kind in ("npn", "pnp"):
            entries += [{"kind": "regime", "run": run, "assert": "bjt_active",
                         "device": component["ref"]} for run in run_ids]
    return entries


def build_analog_plan(intent: Intent, circuit: dict) -> dict:
    """The experiment plan, DERIVED from the intent and the parts list.

    Deterministic Python for the same reason `design.build_plan` is: which
    runs a set of targets needs, and which measurement answers each, follows
    with nothing left to choose. A model asked for it can only introduce
    error into something that has no judgement in it.

    The circuit is needed as well as the intent because a role has to be
    resolved to a ref and a regime has to name a device -- both of which are
    facts about the design, not about the question.
    """
    kinds = {target.kind for target in intent.targets}
    runs, measurements = [], []

    dc_kinds = {"dc_voltage", "dc_current"}
    # With a source frequency, a DC target is the MEAN of the settled window
    # (see parse_intent_reply); the operating point would read the AC source
    # as zero. Without one, the operating point is exactly the measurement.
    dc_on_tran = bool(intent.frequency) and bool(kinds & dc_kinds)
    if kinds & dc_kinds and not intent.frequency:
        runs.append({"id": OP_RUN, "type": "op",
                     "label": "the nominal operating point"})
    line = next((t for t in intent.targets
                 if t.kind == "line_regulation"), None)
    if line is not None:
        supply = _resolve_role("supply", circuit)
        runs.append({
            "id": LINE_RUN, "type": "dc", "label": "line regulation",
            "sweep": {"source": supply, "start": line.low, "stop": line.high,
                      "step": (line.high - line.low) / 8},
        })
    load = next((t for t in intent.targets
                 if t.kind == "load_regulation"), None)
    if load is not None:
        load_ref = _resolve_role("load", circuit)
        runs.append({"id": LOAD_RUN, "type": "param_sweep",
                     "label": "load regulation", "component": load_ref,
                     "values": [load.light, load.heavy]})
    if kinds & TRANSIENT_KINDS or dc_on_tran:
        period = 1.0 / intent.frequency
        runs.append({
            "id": TRAN_RUN, "type": "tran", "label": "steady-state waveforms",
            "stop": spice_number(period * PERIODS_SIMULATED),
            "settle": spice_number(period * PERIODS_DISCARDED),
            "max_step": spice_number(period / STEPS_PER_PERIOD),
        })

    for target in intent.targets:
        if target.kind == "dc_voltage":
            if dc_on_tran:
                measurements.append({"name": target.name, "kind": "waveform_stats",
                                     "run": TRAN_RUN, "expr": target.expr})
            else:
                measurements.append({"name": target.name, "run": OP_RUN,
                                     "expr": target.expr})
        elif target.kind == "dc_current":
            ref = _resolve_current(target, circuit)
            if dc_on_tran:
                measurements.append({"name": target.name, "kind": "waveform_stats",
                                     "run": TRAN_RUN, "expr": f"I({ref})"})
            else:
                measurements.append({"name": target.name, "run": OP_RUN,
                                     "expr": f"I({ref})"})
        elif target.kind == "line_regulation":
            supply = _resolve_role("supply", circuit)
            measurements += [
                {"name": f"{target.name}_low", "run": LINE_RUN,
                 "expr": target.expr, "at": {supply: target.low}},
                {"name": f"{target.name}_high", "run": LINE_RUN,
                 "expr": target.expr, "at": {supply: target.high}},
                {"name": target.name, "kind": "derived",
                 "formula": (f"100 * ({target.name}_high - {target.name}_low)"
                             f" / {target.name}_low"),
                 "definition": (f"{_g(target.low)} V to {_g(target.high)} V "
                                f"input, normalised to the {_g(target.low)} V "
                                f"output")},
            ]
        elif target.kind == "load_regulation":
            load_ref = _resolve_role("load", circuit)
            measurements += [
                {"name": f"{target.name}_light", "run": LOAD_RUN,
                 "expr": target.expr, "at": {load_ref: target.light}},
                {"name": f"{target.name}_heavy", "run": LOAD_RUN,
                 "expr": target.expr, "at": {load_ref: target.heavy}},
                {"name": target.name, "kind": "derived",
                 "formula": (f"100 * ({target.name}_light - "
                             f"{target.name}_heavy) / {target.name}_heavy"),
                 "definition": (f"{target.light} to {target.heavy} load, "
                                f"normalised to the heavier load")},
            ]
        elif target.kind == "current_waveform":
            ref = _resolve_current(target, circuit)
            measurements.append({"name": target.name, "kind": "waveform_stats",
                                 "run": TRAN_RUN, "expr": f"I({ref})"})
        else:
            measurements.append({"name": target.name, "kind": "waveform_stats",
                                 "run": TRAN_RUN, "expr": target.expr})

    measurements += _regime_entries(circuit, runs)
    return {"runs": runs, "measurements": measurements}


# ------------------------------------------------------- the comparison

@dataclass(frozen=True)
class TargetOutcome:
    name: str
    wanted: str
    measured: float | None
    ok: bool
    checked: bool
    reason: str = ""


@dataclass(frozen=True)
class IntentComparison:
    agrees: bool
    summary: str
    outcomes: tuple = ()
    #: How many targets carried no number and so could not have failed.
    #: Reported separately because a run in which nothing COULD fail must not
    #: look like one in which nothing did.
    observations: int = 0
    #: How many DID carry a number. Zero is a real and legal outcome -- an
    #: "observe the waveforms" question states no figure to hit -- and it is
    #: a different result from meeting five targets, so callers branch on it
    #: rather than printing one headline over both.
    checked: int = 0
    regimes_held: int = 0
    regimes_failed: tuple = ()
    warnings: tuple = field(default_factory=tuple)


def _measured_value(target: Target, measurement):
    """The number this target is about, from the measurement that holds it."""
    statistic = STATISTIC.get(target.kind)
    if statistic is None:
        return measurement.value
    stats = getattr(measurement, "stats", None) or {}
    if statistic not in stats:
        raise IntentError(
            f"{target.name}: a {target.kind} target needs the {statistic!r} "
            f"statistic, and the measurement carries "
            f"{sorted(stats) or 'none'}")
    return stats[statistic]


def _within(target: Target, value: float) -> bool:
    if target.value is not None:
        allowed = abs(target.value) * target.tolerance_pct / 100.0
        return abs(value - target.value) <= allowed
    if target.maximum is not None:
        return value <= target.maximum
    return value >= target.minimum


def compare_targets(intent: Intent, experiment) -> IntentComparison:
    """Did the circuit do what the question asked, and did it stay honest?

    Three failures, all of them real and all of them reported the same way:
    a number outside its target, a regime that did not hold, and a
    measurement whose run was invalidated -- the last one fails even when the
    number happens to land inside the tolerance, because accepting it means
    publishing a number nobody should read.

    The summary is written to be fed back to the model that produced the
    design, so it names the target, what was asked for and what came out.
    """
    outcomes, failures = [], []
    observations = 0

    for target in intent.targets:
        measurement = experiment.get(target.name) if hasattr(
            experiment, "get") else None
        if measurement is None:
            # The plan is DERIVED from the intent, so an absent measurement is
            # a real fault rather than a target that happens not to apply.
            outcomes.append(TargetOutcome(
                target.name, target.wanted(), None, False, not target.is_observation,
                "no measurement was produced for it"))
            failures.append(f"  {target.name} ({target.quantity}): no "
                            f"measurement was produced for it at all")
            continue

        try:
            value = _measured_value(target, measurement)
        except IntentError as exc:
            outcomes.append(TargetOutcome(target.name, target.wanted(), None,
                                          False, True, str(exc)))
            failures.append(f"  {exc}")
            continue

        if not measurement.reliable:
            reason = "; ".join(measurement.warnings) or "its run was invalidated"
            outcomes.append(TargetOutcome(target.name, target.wanted(), value,
                                          False, True, reason))
            failures.append(
                f"  {target.name} ({target.quantity}) measured "
                f"{_g(value)} {target.unit}, but the measurement is NOT "
                f"RELIABLE: {reason}")
            continue

        if target.is_observation:
            observations += 1
            outcomes.append(TargetOutcome(target.name, target.wanted(), value,
                                          True, False))
            continue

        ok = _within(target, value)
        outcomes.append(TargetOutcome(target.name, target.wanted(), value,
                                      ok, True))
        if not ok:
            failures.append(
                f"  {target.name} ({target.quantity}): the question asks for "
                f"{target.wanted()}; the circuit gives "
                f"{_g(value)} {target.unit}")

    regimes = list(getattr(experiment, "regimes", ()) or ())
    broken = tuple(r for r in regimes if not r.held)
    for regime in broken:
        failures.append(
            f"  the regime assertion {regime.assertion} on run "
            f"{regime.run!r} did NOT hold for {regime.device}: "
            f"{'; '.join(regime.reasons)}. Convergence is not correctness -- "
            f"a circuit outside its operating regime still produces numbers, "
            f"and they mean nothing.")

    if failures:
        # The FIRST line has to be self-contained. Both the CLI and the web UI
        # render a rejected attempt as one line, and MEASURED on the first
        # live analog run: a bare header reading "the circuit does not meet
        # the design intent:" with every fact underneath it, which reads as an
        # attempt that failed for no stated reason.
        head, *rest = [line.strip() for line in failures]
        if rest:
            head += f"   [and {len(rest)} more]"
        return IntentComparison(
            agrees=False,
            summary=(f"the circuit does not meet the design intent: {head}"
                     + ("\n" + "\n".join(f"  {line}" for line in rest)
                        if rest else "")),
            outcomes=tuple(outcomes), observations=observations,
            checked=intent.checkable,
            regimes_held=len(regimes) - len(broken), regimes_failed=broken)

    checked = intent.checkable
    if checked:
        summary = (f"{checked} of {len(intent.targets)} stated target(s) "
                   f"carry a number, and every one was met; "
                   f"{len(regimes)} regime assertion(s) held; "
                   f"{observations} quantity(s) were reported without being "
                   f"checked, because the question gave no number for them")
    else:
        # "met every one" of nothing is a sentence that reads like a pass.
        # MEASURED on the live Q3 run, whose intent made all five quantities
        # observations: the question asks to OBSERVE waveforms and states no
        # figure to hit, which is legal and must be said plainly rather than
        # dressed as a numeric result.
        summary = (f"NO target carried a number, so nothing numeric could "
                   f"fail or pass. What WAS checked: the circuit converged, "
                   f"and {len(regimes)} regime assertion(s) held. All "
                   f"{len(intent.targets)} quantities are reported unchecked.")
    return IntentComparison(
        agrees=True, summary=summary,
        outcomes=tuple(outcomes), observations=observations, checked=checked,
        regimes_held=len(regimes), regimes_failed=())


# ------------------------------------------------------------- the basis

INTENT_LIMIT = (
    "two things, and the second has no counterpart on the digital side. "
    "FIRST, that the intent above is the right reading of the question: "
    "the circuit was checked against those targets, not against the "
    "sentence they came from, and a misread number passes every check. "
    "SECOND, and larger, that what came back is a good design. Meeting a "
    "target is not being good: a regulator that hits its voltage while "
    "dissipating "
    "six watts in the pass transistor, or with ripple nobody asked about, "
    "satisfies everything here. A digital answer has no equivalent gap -- "
    "correct rows are the whole answer -- and an analog one must never be "
    "read as though it did.")


def intent_basis(intent: Intent, backend, plan) -> Basis:
    """The analog basis: it converged, its regimes held, its numbers matched.

    Names the counts rather than the outcome, because the basis is built
    BEFORE the run: what it promises is which checks will be applied, and
    the comparison reports how they went. A headline that said "verified"
    would be making the claim twice, in two places free to disagree.
    """
    regimes = sum(1 for m in plan.get("measurements", ())
                  if m.get("kind") == "regime")
    observations = len(intent.targets) - intent.checkable
    return Basis(
        kind="intent",
        headline=(
            f"the design intent read from the question's words: "
            f"{intent.checkable} target(s) carrying a number, measured by "
            f"{getattr(backend, 'name', 'the simulator')} from the emitted "
            f"file, plus {regimes} regime assertion(s) derived from the "
            f"parts list. {observations} further quantity(s) are reported "
            f"WITHOUT being checked, because the question gave no number "
            f"for them. Analog has no truth table: this is a weaker "
            f"guarantee than a digital answer's, deliberately."),
        reading=intent.render(),
        limit=INTENT_LIMIT,
        summary="; ".join(f"{t.name} ({t.quantity}) {t.wanted()}"
                          for t in intent.targets),
    )
