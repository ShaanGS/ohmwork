"""Question in, verified analog circuit out: the LTspice design loop.

    1. ROUTE    `domain.check_analog` refuses what this loop cannot honestly
                answer -- a digital question, or one built on a device whose
                symbol geometry has never been measured -- before a token is
                spent on it.
    2. INTENT   the model writes the design TARGETS from the question's
                words. No components, no netlist. Its net names become
                authoritative for everything downstream.
    3. PLAN     built HERE, in Python. Which runs a set of targets needs and
                which measurement answers each follows from the intent with
                nothing left to choose, and the regime assertions follow from
                the PARTS LIST -- so a design cannot quietly omit the check
                that would have failed.
    4. DESIGN   the model writes ONLY components and nets.
    5. GATE     `load_question`: schema, device policy, and the emit/parse
                geometric round trip. A rejection is fed back verbatim -- its
                errors are path-shaped and are the most useful thing a model
                can be told.
    6. VERIFY   LTspice runs the emitted file, and the numbers it measured
                are checked against the intent. Every regime must hold.

WHAT THIS GUARANTEES, and it is WEAKER than the digital loop's, which is the
single most important thing to keep saying about it. A digital answer is
checked against an exhaustive truth table: 32 rows, every one reproduced by
an outside tool, and there is nothing left over. Analog has no such table.
What is established here is that the circuit converged, that its devices
stayed in the operating regimes the results depend on, and that the numbers
the question named came out where the question said they should.

Two things that leaves open:

* the intent is the model's READING of the question -- read "9 V" as "5 V"
  and the intent, the circuit and LTspice all agree. Same hole the digital
  loop has, same answer: `Intent.render()` is OUTPUT.
* **meeting a target is not being a good design.** A regulator that hits
  9.00 V while dissipating six watts in the pass transistor, or with two
  volts of ripple nobody asked about, passes every check here. The digital
  loop has no counterpart to this: correct rows really are the whole answer.
  `Basis.limit` says it, in those words, wherever a result ships.

`tests/test_analog.py` is the spec for this module.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ohmwork.basis import Basis
from ohmwork.design import (DEFAULT_ATTEMPTS, RETRY_BLOCK, DesignError,
                            TruncatedReply, _ask_until_it_fits, _json_object)
from ohmwork.domain import check_analog
from ohmwork.intent import (IntentError, build_analog_plan, compare_targets,
                            intent_basis, parse_intent_reply)
from ohmwork.llm import (LLMError, MalformedReply, PoolExhausted,
                         TransientNetworkError)

#: The intent call's budget. Smaller than the digital spec's: an intent is a
#: handful of targets, not seven sum-of-products expressions.
INTENT_MAX_TOKENS = 4000

#: The design call's starting budget. Same squeeze as the digital loop's --
#: big enough for a bridge rectifier's eleven components, small enough that
#: the smallest free tier can still take the job. A reply that comes back cut
#: off is retried with double the room.
DESIGN_MAX_TOKENS = 5000


INTENT_PROMPT = """\
You are turning an ANALOG electronics lab question into a statement of what
the finished circuit must measurably DO. Not how. No components, no netlist,
no part numbers, no topology design.

Return ONE JSON object and nothing else:

{{"topology": "...", "frequency": null, "targets": [...],
  "stated_values": [...], "notes": ["..."]}}

RULES:

1. "topology" is what kind of circuit this is, in the question's own words
   ("series voltage regulator", "full-wave bridge rectifier with a C-L-C
   filter").

2. "targets" is the heart of it: one entry per quantity the question asks to
   be measured. Each has:
     "name"      a measurement name matching [a-z][a-z0-9_]*  (vout_nominal)
     "kind"      one of:
{kinds}
     "quantity"  the question's OWN WORDS for it ("output voltage")
     "unit"      "V", "A", "%", ...
   plus, for the number itself, AT MOST ONE of:
     "value" WITH "tolerance_pct"   the question states a figure to hit
     "max"                          "better than 1%", "less than 50 mV"
     "min"
   and NOTHING at all if the question only asks for it to be reported or
   observed. A target with no number is honest and is expected; inventing a
   figure the question never gave is not.
   BUT: when the question STATES a number for a quantity it also asks to be
   delivered or reported -- "delivers 9 V ... report the output voltage",
   "a regulated 6.2 V supply" -- that target MUST carry the stated value
   (choose the tolerance, and record the choice in "notes"). A stated figure
   that no target checks is dropped work: the one number the question gave
   would go unverified.
   "tolerance_pct" must be greater than 0 and at most {max_tolerance:g}.
   Wider than that and any plausible circuit satisfies it, so the check
   cannot fail.
   In an AC-FED circuit (a rectifier, anything with a source frequency) a
   stated DC OUTPUT figure ("a regulated 6.2 V supply") is the MEAN of the
   settled waveform: use kind "ac_mean", NEVER "dc_voltage" -- at the DC
   operating point an AC source is zero, so a dc_voltage target there reads
   0 V from a correct circuit. This rule is about the OUTPUT level; a
   clamper's "DC level shift" is "dc_level", see rule 3.
   A STATED INPUT AMPLITUDE IS A CHECKED TARGET. "10 Vpp input", "12 V RMS
   secondary": put a target on the input node with that value -- kind
   "ripple_pp" for a peak-to-peak figure, "ac_rms" for an RMS one -- with a
   tolerance of a few percent, recorded in "notes". MEASURED 2026-09-02 on
   a clamper question stating 10 Vpp: the designed source delivered 3.1 Vpp
   and every check passed, because the one number that would have failed
   was never a target. The source is the model's to get right, and this is
   the check that proves it did.

3. WHERE it is measured. A VOLTAGE target names the node(s) in "net" (and
   "net2" for a voltage measured BETWEEN two nodes, such as a floating
   transformer secondary). Choose short names matching
   [A-Za-z][A-Za-z0-9_]* -- vin, vb, vout, vrect, vfilt. THESE ARE
   AUTHORITATIVE: the circuit will be required to use exactly them.
   An AC source DRIVING A BRIDGE RECTIFIER floats -- neither terminal is
   ground -- so a target on the SOURCE's own voltage (its RMS, its
   waveform) MUST be measured between its two nodes, "net" AND "net2".
   Measured one node to ground it reads a different, smaller number, and a
   CORRECT circuit fails the check. A source with one end on GROUND (a
   clipper's, a clamper's, an amplifier's input) is measured with "net"
   alone -- do not invent a second node for it; the design will put that
   end on net "0".
   A CURRENT target names a "role" instead, never a net. "The load current
   waveform" is {{"kind": "current_waveform", "role": "load"}} -- NOT a
   "waveform" target on the load's node, which would measure a voltage and
   report it under a name that says current. When the question names the
   component ITSELF -- "the current through R3", "I in R2" -- give "ref"
   (exactly the question's name, e.g. "R3") instead of "role"; the design
   will be required to contain a component with that name.
   A question in PARTS -- "(i) ... (ii) ...", an unbiased and a biased
   clamper -- describes SEPARATE circuits. Give each part its own nodes
   (vin_a/vout_a and vin_b/vout_b) so each is designed and measured; two
   targets on one node are one measurement, and are refused.
   Pick the kind by what the words MEAN: "DC level shift" or "DC level" of
   a clamped waveform is "dc_level" ((max+min)/2); a "clipping level" or
   "peak" is "peak_max"/"peak_min"; "ripple factor" is "ripple_factor";
   "ripple" in volts is "ripple_pp"; an average is "ac_mean".
   THE WORD STATES THE SIGN. A POSITIVE clamper shifts the output UP: its
   dc_level target carries "min": 0. A NEGATIVE clamper carries "max": 0. A
   positive clipper's output carries "max" at the clipping level. A clamper
   of the wrong polarity passes every other check, so this one is required.

4. "frequency" is the source frequency in Hz, and is REQUIRED if any target
   is one of {transient}. The simulation window is derived from it. Use null
   when the question is a DC one.

5. "stated_values" lists every number the question FIXES: supply voltage,
   load resistance, capacitor values, RMS input. Each is
   {{"what": "...", "value": "...", "unit": "..."}}. These are printed back
   for a person to check against the original, because a value misread from
   a question simulates perfectly well and answers a different question.

6. Where the question leaves something open -- a regulation range, a
   tolerance, which load counts as "full" -- CHOOSE, and record each choice
   in "notes" as one sentence.

7. Do not restate the question. Do not explain. JSON only.

QUESTION:
{question}
"""

DESIGN_PROMPT = """\
Design an analog circuit that meets this design intent, for LTspice.

DESIGN INTENT:
{intent}

Return ONE JSON object and nothing else:

{{"components": [...], "nets": {{"netname": ["REF.pin", ...]}}}}

RULES:

1. Use ONLY these component types, with EXACTLY these pin names:
{types}

2. RESERVED NAMES, and they are not optional. The source the question calls
   the supply or the input MUST be named V1. The load MUST be named RL. A
   resistor looks like every other resistor, so nothing downstream can tell
   which one is the load unless it is named. Any component the intent
   measures BY NAME ("the current through R3") must exist with EXACTLY that
   ref.
   A QUESTION IN PARTS is SEPARATE circuits in one file, sharing only net
   "0". When the intent's node names carry part suffixes (vin_i / vin_ii,
   vout_a / vout_b), give EACH part its own source and load with the same
   suffix -- V1_i and RL_i, V1_ii and RL_ii -- and never connect one source
   to two parts' nodes. One pin on one net; each part's nets are its own.

3. NETS. A net is a NODE: every pin that touches one junction, listed once
   under one name, written "REF.pin". Never write two nets for one junction
   and never one net per pin. There MUST be a net named exactly "0": that is
   ground, the only ground, and every grounded pin goes in it directly --
   never in a net named "0_something". Use EXACTLY these node names where
   the intent names them: {nets}

4. VALUES. res, cap, ind and a DC voltage source carry "value" as a SPICE
   string -- "1.8k", "470u", "1m", "15". Never a bare number with a unit
   letter ("470uF" is wrong; "470u" is right).

5. DEVICES. A semiconductor carries "device" instead of "value":
     zener:    {{"device": {{"vz": 9.7, "exact": true}}}}   Vz in volts
     npn/pnp:  {{"device": {{"params": {{"BF": 100}}}}}}
     diode:    {{"device": {{}}}}                      a plain rectifier
   Give a zener the voltage the design needs, not the one the OUTPUT needs:
   a series regulator's output sits about 0.7 V below its zener.

6. DAMPING. An L-C filter with nothing resistive in series RINGS: the
   solver takes tinier and tinier steps, the result file grows to hundreds
   of megabytes, and the run is refused. If the design has an inductor in a
   filter, put a series resistance in the path.

7. AN AC SOURCE carries "ac" instead of "value":
     {{"ref": "V1", "type": "voltage",
       "ac": {{"kind": "sine", "rms": 12, "freq": 50}}}}
   Give EXACTLY one of "rms" or "amplitude" (the PEAK). Convert the
   question's figure first and do the arithmetic once:
     "10 Vpp"  -> "amplitude": 5      (peak-to-peak / 2; NOT rms 5,
                                       NOT amplitude 10)
     "12 V RMS" -> "rms": 12
     "8 V peak" -> "amplitude": 8
   MEASURED 2026-09-02: one run wrote rms 5 for 10 Vpp (14.1 Vpp
   delivered), the next amplitude 10 (20 Vpp). The intent checks the
   input amplitude, so a wrong conversion fails the design.
   A CLAMPER is a series capacitor from the source to the output node,
   a diode from the output node to ground (or to the bias source), and
   the load across the output. The diode must conduct on one peak and
   block for the rest of the cycle -- a regime check enforces both.

8. ORIGINS. Every component carries "origin": "stated" if the question gives
   that value, "designed" if you chose it. A "designed" value MUST carry a
   "rationale" of one sentence saying why that number. A chosen value that
   looks like a given one submits your judgement as the student's own.

9. A BRIDGE RECTIFIER is four diodes and a FLOATING AC source: V1 sits
   between two nodes and NEITHER is ground. Grounding an AC terminal shorts
   half the bridge. The working pattern (rename the nets to the intent's
   node names where it names them -- each of these is still ONE net):
     "ac_a":  ["V1.+", "D1.anode", "D3.cathode"]
     "ac_b":  ["V1.-", "D2.anode", "D4.cathode"]
     "vrect": ["D1.cathode", "D2.cathode", ...]
     "0":     ["D3.anode", "D4.anode", ...]

10. A SHUNT ZENER regulates only while current flows through it, so SIZE
    the series resistor feeding it: with Iload = Vz / RL,
      Rs = (Vsupply_dc - Vz) / (Iload + 0.005)
    Example: 15 V feeding a 6.8 V zener into a 1k load -> Iload is 6.8 mA,
    Rs = (15 - 6.8) / 0.0118, about 680 ohms. An Rs of several kilohms
    STARVES the zener: it never enters breakdown and regulates nothing.

11. No JSON outside the object. No markdown fences, no explanation.

SHAPE, exactly:

{{"components": [{{"ref": "V1", "type": "voltage", "value": "15",
                  "origin": "stated"}},
                {{"ref": "R1", "type": "res", "value": "1.8k",
                  "origin": "designed", "rationale": "sets the zener current"}},
                {{"ref": "RL", "type": "res", "value": "1k",
                  "origin": "stated"}}],
 "nets": {{"vin": ["V1.+", "R1.a"],
          "vout": ["R1.b", "RL.a"],
          "0": ["V1.-", "RL.b"]}}}}
{retry}"""


class AnalogSimulationError(DesignError):
    """LTspice would not produce results for this design.

    A subclass because it IS a design failure -- a floating node, a source
    loop, a circuit that will not converge -- and is fed back like any other.
    It is named apart so a caller can tell "the circuit is wrong" from "the
    simulator is missing", which are different problems with different fixes.
    """


@dataclass
class AnalogSolution:
    """A circuit LTspice ran, whose numbers meet the question's intent."""

    question: str
    intent: object                      # intent.Intent
    question_data: dict
    asc_path: Path
    experiment: object                  # analysis.Experiment
    comparison: object                  # intent.IntentComparison
    #: WHAT it was checked against, what that proves, and what it does not.
    #: An analog result makes a weaker claim than a digital one, and a reader
    #: who cannot tell them apart has been shown the stronger.
    basis: Basis
    attempts: int
    provider: str
    model: str
    #: The loaded Question. Carried because the report a person reads needs
    #: the device choices and the plan, and rebuilding either from the raw
    #: JSON would be a second account of what the gate already decided.
    question_object: object = None
    #: What went wrong on each attempt before the one that verified. A run
    #: reporting only "verified" hides the designs that were not.
    failed_attempts: tuple = ()
    warnings: list = field(default_factory=list)


# ------------------------------------------------------------ vocabulary

def target_vocabulary() -> str:
    """The target kinds and their fields, READ OUT OF `intent.py`.

    Typing them into the prompt would create two sources of truth that drift
    apart in silence, and the drift presents as a model being "wrong" about a
    vocabulary that moved underneath it.
    """
    from ohmwork.intent import ROLES, TARGET_KINDS

    lines = []
    for kind, required in sorted(TARGET_KINDS.items()):
        fields = ", ".join(f'"{name}"' for name in required)
        lines.append(f"                   {kind}: needs {fields}")
    lines.append(f'                   a "role" is one of: '
                 f'{", ".join(sorted(ROLES))}')
    return "\n".join(lines)


def component_vocabulary() -> str:
    """The LTspice component types and their pins, read out of the target."""
    from ohmwork import symbols
    from ohmwork.targets import get_target

    target = get_target("ltspice")
    lines = []
    for name in sorted(target.known_types()):
        pins = ", ".join(target.pin_names(name))
        carries = "value" if name in symbols.VALUE_TYPES else "device"
        lines.append(f"     {name}: pins {pins}   (carries {carries})")
    return "\n".join(lines)


# -------------------------------------------------------- the question JSON

def build_question_data(question, intent, circuit, plan,
                        provider_name, model, basis) -> dict:
    """Assemble the question JSON the existing gate and manifest already take.

    Deliberately the SAME format the hand-written questions use. A second
    parallel format for generated questions would drift from the one the
    library publishes, and the library is the product.
    """
    components = []
    for component in circuit.get("components") or []:
        component = dict(component)
        if component.get("rationale") and not component.get("rationale_origin"):
            # Stamped from what we KNOW, not asked for. Every rationale here
            # was written by a model, and an absent authorship renders as
            # "review this" rather than being assumed human -- so recording
            # it truthfully is strictly better than leaving it blank.
            component["rationale_origin"] = "generated"
        components.append(component)

    return {
        "question": question,
        "target": "ltspice",
        "circuit": {**circuit, "components": components},
        "analysis": plan,
        # Derived from the intent's own quantities, which are supposed to be
        # the question's words. That makes the gate's ask-coverage warning a
        # real, if weak, check on the intent: a quantity the model invented
        # does not appear in the question text, and the dry run says so.
        "asks": [{"text": target.quantity, "answered_by": target.name}
                 for target in intent.targets],
        "design_notes": [
            {"item": "verification basis",
             "choice": basis.headline,
             "rationale": ("the circuit was checked against this and nothing "
                           f"else. What that does NOT establish: {basis.limit}"),
             "rationale_origin": "generated"},
            {"item": "design intent",
             "choice": basis.summary,
             "rationale": (
                 "read from the question's wording before any circuit was "
                 "designed, and the thing LTspice's numbers were required to "
                 "meet. It is printed in full in the output because nothing "
                 "downstream of it can tell that it misreads the question."),
             "rationale_origin": "generated"},
            {"item": "topology",
             "choice": intent.topology,
             "rationale": "read from the question rather than chosen here",
             "rationale_origin": "generated"},
        ] + [
            {"item": "choice left open by the question",
             "choice": note,
             "rationale": ("the question does not specify this; it was chosen "
                           "here, and the measured numbers only mean what "
                           "they say once the choice is stated"),
             "rationale_origin": "generated"}
            for note in intent.notes
        ],
        "source": {
            "file": "typed by the person asking",
            "extractor": f"{provider_name}/{model}, analog design loop",
        },
    }


# --------------------------------------------------------------- the loop

def _attempt(circuit, question, intent, provider_name, model, backend,
             workdir, index, executor):
    """One design attempt: derive the plan, gate, emit, simulate, compare.

    Returns (question_data, asc_path, experiment, comparison, basis,
    question_object). Raises DesignError with a message written to be fed
    back to the model.
    """
    from ohmwork.analysis import AnalysisError, deliverable_circuit
    from ohmwork.emitter import CircuitError, write_asc
    from ohmwork.question import QuestionError, load_question
    from ohmwork.simulate import SimulationError

    try:
        plan = build_analog_plan(intent, circuit)
    except IntentError as exc:
        raise DesignError(str(exc)) from exc

    basis = intent_basis(intent, backend, plan)
    data = build_question_data(question, intent, circuit, plan,
                               provider_name, model, basis)
    try:
        question_object = load_question(data)
    except (QuestionError, CircuitError, AnalysisError) as exc:
        raise DesignError(str(exc)) from exc
    except Exception as exc:                                    # noqa: BLE001
        # Deliberately broad, exactly as the digital loop is: model output is
        # untrusted input and a malformed object can raise anything at all.
        raise DesignError(f"{type(exc).__name__}: {exc}") from exc

    # The deliverable: ONE .asc carrying the whole experiment, first run
    # active and the rest commented. It is written before the run so that a
    # failure leaves the evidence behind rather than only an error message.
    run_dir = Path(workdir) / f"attempt{index}"
    run_dir.mkdir(parents=True, exist_ok=True)
    asc_path = Path(workdir) / f"design{index}.asc"
    write_asc(deliverable_circuit(question_object.circuit,
                                  question_object.plan), asc_path)

    try:
        experiment = executor(question_object.circuit, question_object.plan,
                              backend, run_dir)
    except SimulationError as exc:
        raise AnalogSimulationError(
            f"LTspice could not produce results for this circuit: {exc}"
        ) from exc
    except AnalysisError as exc:
        raise DesignError(str(exc)) from exc

    try:
        comparison = compare_targets(intent, experiment)
    except IntentError as exc:
        raise DesignError(str(exc)) from exc
    return data, asc_path, experiment, comparison, basis, question_object


#: More room than the digital loop's DEFAULT_ATTEMPTS, from measurement:
#: three Q3 runs on 2026-08-30 (two vendors) each spent all 4 attempts on
#: DISTINCT real rejections -- the repeated-failure stop never fired, so the
#: loop was still converging when the budget ran out. An analog design has
#: more independent ways to be wrong than a gate network (wiring, values,
#: operating point), and each attempt is bounded by the simulator timeout,
#: so the extra room costs minutes, not runaway.
ANALOG_ATTEMPTS = 6


def solve_analog(question: str, *, provider=None, backend=None, workdir,
                 executor=None, attempts: int = ANALOG_ATTEMPTS,
                 progress=None) -> AnalogSolution:
    """Design an analog circuit for `question` and check it against its intent.

    `progress(name, data)` is called as the loop runs: "reading" once the
    intent is parsed, and "attempt" as each design is tried and rejected. A
    solve makes several model calls and several simulator runs, and a caller
    that can only see the final answer cannot show the reading BEFORE the
    answer -- which is the one thing a human is here to check.

    `executor` is the seam that runs the experiment, defaulting to
    `analysis.execute`. It exists so the loop is testable without LTspice
    installed anywhere; nothing else about it varies.
    """
    emit = progress or (lambda name, data: None)

    # Before a single token is spent. See ohmwork/domain.py: a question this
    # loop cannot honestly answer must be refused rather than answered
    # badly, and a refusal is not a failure.
    check_analog(question)

    if provider is None:
        from ohmwork.llm import get_provider
        provider = get_provider()
    if backend is None:
        from ohmwork.simulate import LTspiceBackend
        backend = LTspiceBackend()
    if executor is None:
        from ohmwork.analysis import execute as executor

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    intent = _ask_until_it_fits(
        provider,
        INTENT_PROMPT.format(
            question=question, kinds=target_vocabulary(),
            max_tolerance=_max_tolerance(),
            transient=", ".join(sorted(_transient_kinds()))),
        INTENT_MAX_TOKENS, _parse_intent, "the design intent")

    emit("reading", {"intent": intent.render(),
                     "checkable": intent.checkable,
                     "observations": len(intent.targets) - intent.checkable,
                     **intent.reading_data()})

    types = component_vocabulary()
    nets = ", ".join(sorted({target.net for target in intent.targets
                             if target.net}
                            | {target.net2 for target in intent.targets
                               if target.net2}))

    last_error = None
    history: list = []
    budget = DESIGN_MAX_TOKENS
    for index in range(1, attempts + 1):
        retry = "" if last_error is None else RETRY_BLOCK.format(error=last_error)
        prompt = DESIGN_PROMPT.format(intent=intent.render(), types=types,
                                      nets=nets, retry=retry)
        emit("attempt", {"index": index, "status": "designing"})
        try:
            from ohmwork.design import _complete_patiently
            reply = _complete_patiently(provider, prompt, max_tokens=budget)
        except PoolExhausted:
            raise
        except TransientNetworkError as exc:
            # The wire failed, not the design. Spend the attempt and ask
            # again -- and leave last_error alone: the model never saw this
            # prompt, so there is nothing new to feed back.
            failure = str(exc)
            history.append((index, failure))
            emit("attempt", {"index": index, "status": "rejected",
                             "failure": failure})
            continue
        except MalformedReply as exc:
            # The model answered and the answer was garbage. A spent attempt,
            # never a dead run: the run this rule comes from lost three
            # attempts of real progress to one stochastic JSON flub.
            failure = str(exc)
            history.append((index, failure))
            emit("attempt", {"index": index, "status": "rejected",
                             "failure": failure})
            last_error = ("your previous reply was discarded by the provider "
                          "because it was not valid JSON. Reply with exactly "
                          "one valid JSON object and nothing else.")
            continue
        except PoolExhausted:
            # Nobody left to ask: its own outcome, never a failed design.
            raise
        except LLMError as exc:
            # A provider failure is not a design failure, and must not be fed
            # back to the model as though its circuit were wrong.
            raise DesignError(f"the model could not be reached: {exc}") from exc

        try:
            # Provenance comes from the REPLY, never from the provider object:
            # a pool's name is "pool" and its model is a description of its
            # membership, and recording either as the model that designed this
            # circuit would be a lie in the field that exists to prevent lies.
            circuit = _json_object(reply.text, "design")
            (data, asc_path, experiment, comparison, basis,
             loaded) = _attempt(
                circuit, question, intent, reply.provider, reply.model,
                backend, workdir, index, executor)
        except TruncatedReply as exc:
            budget, failure, stop = _grow(budget, exc)
            history.append((index, failure))
            emit("attempt", {"index": index, "status": "rejected",
                             "failure": failure})
            if stop:
                raise DesignError(failure) from exc
            continue
        except DesignError as exc:
            failure = str(exc)
        else:
            if comparison.agrees:
                return AnalogSolution(
                    question=question, intent=intent, question_data=data,
                    asc_path=asc_path, experiment=experiment,
                    comparison=comparison, basis=basis, attempts=index,
                    provider=reply.provider, model=reply.model,
                    question_object=loaded,
                    failed_attempts=tuple(history))
            failure = comparison.summary

        history.append((index, failure))
        emit("attempt", {"index": index, "status": "rejected",
                         "failure": failure})
        # A model that returns the IDENTICAL failure twice will not fix it on
        # the fifth try. Found by probing extract.py, which burned four
        # attempts on one unchanging rejection because nothing was watching.
        if failure == last_error:
            raise DesignError(
                f"the same failure came back twice, so retrying is not making "
                f"progress. Stopped after {index} design attempt(s). The "
                f"failure was:\n{failure}")
        last_error = failure

    raise DesignError(
        f"no circuit meeting the design intent after {attempts} attempt(s). "
        f"The last failure was:\n{last_error}")


def _grow(budget, exc):
    """A cut-off reply is not a mistake: it was still writing. Ask for more."""
    from ohmwork.design import DESIGN_MAX_TOKENS_CEILING

    grown = min(budget * 2, DESIGN_MAX_TOKENS_CEILING)
    if grown == budget:
        return budget, (f"the design does not fit in {budget} tokens, which "
                        f"is the ceiling. {exc}"), True
    return grown, (f"the reply ran out of room at {budget} tokens; retrying "
                   f"with {grown}"), False


def _parse_intent(text):
    """Adapt `parse_intent_reply` to the shared ask-until-it-fits helper.

    That helper distinguishes a TruncatedReply from a real error, so the JSON
    is located here with the same code the digital loop uses rather than with
    a second, looser reader.
    """
    data = _json_object(text, "intent")
    try:
        return parse_intent_reply(data)
    except IntentError as exc:
        raise DesignError(f"intent: {exc}") from exc


def _max_tolerance():
    from ohmwork.intent import MAX_TOLERANCE_PCT
    return MAX_TOLERANCE_PCT


def _transient_kinds():
    from ohmwork.intent import TRANSIENT_KINDS
    return TRANSIENT_KINDS
