"""The analog design loop: this file is its spec.

An analog question in plain English goes in; an `.asc` the student opens in
LTspice comes out, together with the numbers LTspice measured from that exact
file. The shape mirrors the digital loop deliberately -- the same gate, the
same feed-the-rejection-back retry, the same "the reading is OUTPUT" rule --
and it differs in exactly one place, which is the whole difficulty:

    digital     the reference is an exhaustive truth table. An outside tool
                either reproduces every row or does not.
    analog      THERE IS NO SUCH TABLE. What can be checked is that it
                simulates, that its operating regimes held, and that the
                numbers the question asked for came out where the question
                said they should.

That is weaker, and every layer says so rather than leaving it to be
inferred. It is also weaker in a way the digital side has no counterpart for:
meeting a target is not being a good design. A regulator that hits 9.00 V
with two volts of ripple passes everything here.

    1. ROUTE    domain.classify sends the question to this loop or the
                digital one, and check_analog refuses what this one cannot
                honestly answer -- before a token is spent.
    2. INTENT   the model writes the design TARGETS from the question's
                words. No components, no netlist. Its net names become
                authoritative downstream.
    3. PLAN     built in Python. Which runs a set of targets needs, and which
                measurement answers each, follows from the intent with
                nothing left to choose. The regime assertions are derived
                from the PARTS LIST, so a design cannot omit the one that
                would have failed.
    4. DESIGN   the model writes ONLY components and nets.
    5. GATE     load_question: schema, device policy, and the emit/parse
                geometric round trip. A rejection is fed back verbatim.
    6. VERIFY   LTspice runs the emitted file; the measured numbers are
                checked against the intent, and every regime must hold.

No test here touches the network or a simulator. The provider is a fake and
the executor is a seam, as with `captions.py`: what is under test is the
loop, not a model's mood or an install.
"""

import json

import pytest

from ohmwork.analog import AnalogSolution, solve_analog
from ohmwork.analysis import Experiment, Measurement, RegimeResult
from ohmwork.design import DesignError
from ohmwork.domain import DomainError
from ohmwork.llm import Reply

QUESTION = ("Design a series voltage regulator in LTspice that delivers 9 V "
            "to a 1 k load from a 15 V unregulated supply, and report the "
            "output voltage and the zener current.")

INTENT_JSON = json.dumps({
    "topology": "series voltage regulator",
    "targets": [
        {"name": "vout_nominal", "kind": "dc_voltage", "net": "vout",
         "quantity": "output voltage", "unit": "V",
         "value": 9.0, "tolerance_pct": 5},
        {"name": "iz_nominal", "kind": "dc_current", "role": "zener",
         "quantity": "zener current", "unit": "A"},
    ],
    "stated_values": [
        {"what": "unregulated supply", "value": "15", "unit": "V"},
        {"what": "load resistance", "value": "1k", "unit": "ohm"},
    ],
    "notes": ["the zener is chosen one Vbe above the wanted output"],
})

CIRCUIT = {
    "components": [
        {"ref": "V1", "type": "voltage", "value": "15", "origin": "stated"},
        {"ref": "R1", "type": "res", "value": "1.8k", "origin": "designed",
         "rationale": "sets about 3.5 mA of zener current at 15 V in"},
        {"ref": "D1", "type": "zener", "device": {"vz": 9.7, "exact": True},
         "origin": "designed",
         "rationale": "9 V wanted plus one Vbe drop across the pass transistor"},
        {"ref": "Q1", "type": "npn", "device": {"params": {"BF": 100}},
         "origin": "designed", "rationale": "a beta of 100 is a lab default"},
        {"ref": "RL", "type": "res", "value": "1k", "origin": "stated"},
    ],
    "nets": {
        "vin": ["V1.+", "R1.a", "Q1.C"],
        "vb": ["R1.b", "D1.cathode", "Q1.B"],
        "vout": ["Q1.E", "RL.a"],
        "0": ["V1.-", "D1.anode", "RL.b"],
    },
}


class FakeProvider:
    """Canned replies in order, and the prompts it was handed.

    The prompts matter as much as the replies: several tests below assert a
    failure was actually FED BACK, which is the entire value of a retry loop.
    """

    name = "fake"
    model = "fake-model"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, prompt, *, images=(), max_tokens=4000, temperature=0.2,
                 json_object=False):
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("the loop asked for more replies than the "
                                 "test supplied")
        return Reply(text=self.replies.pop(0), model=self.model,
                     provider=self.name)


class FakeBackend:
    name = "fake-ltspice"
    verification = "external"


def fake_executor(values, regimes_hold=True, reliable=True):
    """Stands in for LTspice: returns whatever numbers the test wants.

    A seam rather than a mock of the raw-file reader. What is under test is
    the loop's judgement about numbers, and manufacturing a plausible SPICE
    raw file to reach it would test the raw parser instead.
    """
    def execute(circuit, plan, backend, workdir):
        results = {}
        for entry in plan["measurements"]:
            name = entry.get("name")
            if name is None or name not in values:
                continue
            # A waveform measurement carries the full statistics, and its
            # `value` is the time-weighted MEAN. Reproduced here rather than
            # left as None, because reading a target off the wrong statistic
            # is exactly the failure `STATISTIC` exists to prevent.
            stats = None
            if entry.get("kind") == "waveform_stats":
                stats = {"mean": values[name], "rms": values[name],
                         "min": values[name], "max": values[name],
                         "ripple_pp": 0.0}
            results[name] = Measurement(
                name=name, value=values[name], run=entry.get("run"),
                backend=backend.name, source="simulation", reliable=reliable,
                stats=stats,
                warnings=() if reliable else ("Q1 left the active region",))
        regimes = [
            RegimeResult(assertion=entry["assert"], run=entry["run"],
                         device=entry.get("device"), held=regimes_hold,
                         examined="1 operating point",
                         reasons=() if regimes_hold else ("out of breakdown",))
            for entry in plan["measurements"] if entry.get("kind") == "regime"]
        return Experiment(results, regimes)
    return execute


GOOD = {"vout_nominal": 9.05, "iz_nominal": 0.0035}


def solve(provider, executor=None, **kwargs):
    return solve_analog(
        QUESTION, provider=provider, backend=FakeBackend(),
        executor=executor or fake_executor(GOOD), **kwargs)


# ------------------------------------------------------------ the loop

def test_a_question_becomes_a_verified_solution(tmp_path):
    provider = FakeProvider([INTENT_JSON, json.dumps(CIRCUIT)])
    solution = solve(provider, workdir=tmp_path)

    assert isinstance(solution, AnalogSolution)
    assert solution.comparison.agrees
    assert solution.attempts == 1
    assert solution.asc_path.is_file()
    assert solution.asc_path.read_text(encoding="ascii").startswith("Version 4.1")


def test_the_deliverable_is_the_file_the_student_opens(tmp_path):
    """One `.asc` carrying the whole experiment, not the per-run scratch
    files the runner made. What LTspice actually ran were those; the
    deliverable's own claim is the geometric round trip, and the manifest
    format makes you write which claim you are making."""
    solution = solve(FakeProvider([INTENT_JSON, json.dumps(CIRCUIT)]),
                     workdir=tmp_path)
    text = solution.asc_path.read_text(encoding="ascii")
    assert ".op" in text
    assert ".model" in text, "the device cards must travel with the file"


def test_the_plan_is_DERIVED_and_never_asked_for(tmp_path):
    """The design prompt must not mention runs or measurements. A model asked
    for a plan that follows from the intent can only introduce error."""
    provider = FakeProvider([INTENT_JSON, json.dumps(CIRCUIT)])
    solve(provider, workdir=tmp_path)

    design_prompt = provider.prompts[1]
    assert "components" in design_prompt and "nets" in design_prompt
    for word in ("measurements", '"runs"', "param_sweep"):
        assert word not in design_prompt


def test_the_intent_prompt_says_a_bridge_source_is_measured_across_itself():
    """Measured on the first paid Q3 run (claude-opus-5): the intent put a
    12 V RMS check on V(vin), one node to ground -- and even RECORDED the
    choice in its notes -- while every design correctly floated the bridge
    source. Three sound circuits failed at 9.7 V RMS against a measurement
    no correct bridge circuit can satisfy. The design loop cannot revise an
    intent, so the rule has to reach the intent writer."""
    from ohmwork.analog import INTENT_PROMPT
    assert "DRIVING A BRIDGE RECTIFIER floats" in INTENT_PROMPT
    assert '"net" AND "net2"' in INTENT_PROMPT
    assert "CORRECT circuit fails the check" in INTENT_PROMPT


def test_the_design_prompt_teaches_the_two_measured_killers(tmp_path):
    """Seven Q3 runs across two vendors (gpt-oss and mistral, 2026-08-30)
    failed the same two ways: a bridge with a grounded AC terminal (vrect
    averaging 3 V instead of ~14) and a zener starved by an oversized
    series resistor -- the regime diagnosis said "too little drive" on
    attempt after attempt and the models never did the arithmetic. The
    design prompt now carries the bridge net pattern and the Rs sizing
    formula, and this pins that they actually reach the model."""
    provider = FakeProvider([INTENT_JSON, json.dumps(CIRCUIT)])
    solve(provider, workdir=tmp_path)

    design_prompt = provider.prompts[1]
    assert "NEITHER is ground" in design_prompt
    assert "STARVES the zener" in design_prompt
    assert "Rs = (Vsupply_dc - Vz) / (Iload + 0.005)" in design_prompt


def test_the_reading_is_emitted_BEFORE_any_answer(tmp_path):
    """Nothing downstream can prove the intent is the right reading of the
    question, so a caller must be able to show it first."""
    seen = []
    solve(FakeProvider([INTENT_JSON, json.dumps(CIRCUIT)]), workdir=tmp_path,
          progress=lambda name, data: seen.append((name, data)))

    names = [name for name, _ in seen]
    assert names.index("reading") < len(names)
    reading = next(data for name, data in seen if name == "reading")
    assert "series voltage regulator" in reading["intent"]
    assert "9" in reading["intent"]


# ------------------------------------------------------ when it is wrong

def test_a_missed_target_is_fed_back_and_the_design_retried(tmp_path):
    """The entire value of a retry loop is that the failure reaches the model
    that caused it, in words naming what was wanted and what came out."""
    provider = FakeProvider([INTENT_JSON, json.dumps(CIRCUIT),
                             json.dumps(CIRCUIT)])
    misses = {"vout_nominal": 6.2, "iz_nominal": 0.0035}
    calls = {"n": 0}

    def executor(circuit, plan, backend, workdir):
        calls["n"] += 1
        values = misses if calls["n"] == 1 else GOOD
        return fake_executor(values)(circuit, plan, backend, workdir)

    solution = solve(provider, executor=executor, workdir=tmp_path)

    assert solution.attempts == 2
    assert solution.failed_attempts
    retry_prompt = provider.prompts[2]
    assert "6.2" in retry_prompt and "vout_nominal" in retry_prompt


def test_a_malformed_reply_spends_an_attempt_not_the_run(tmp_path):
    """MEASURED 2026-08-30 on a live Q3 run: the fourth design call came
    back as Groq's `json_validate_failed` and the run died as "the model
    could not be reached", losing three attempts of real progress. The
    model answered and the answer was garbage: a spent attempt."""
    from ohmwork.llm import MalformedReply

    class Flubbing(FakeProvider):
        def complete(self, prompt, **kwargs):
            if self.replies and isinstance(self.replies[0], Exception):
                self.prompts.append(prompt)
                raise self.replies.pop(0)
            return super().complete(prompt, **kwargs)

    provider = Flubbing(
        [INTENT_JSON, MalformedReply("fake produced a reply that failed "
                                     "the provider's JSON validation"),
         json.dumps(CIRCUIT)])
    solution = solve(provider, workdir=tmp_path)

    assert solution.comparison.agrees
    assert solution.attempts == 2
    assert solution.failed_attempts
    assert "one valid JSON object" in provider.prompts[-1]


def test_a_network_timeout_spends_an_attempt_not_the_run(tmp_path):
    """The analog twin of the digital test: gemini's 2026-08-31 Q3 run died
    on one read timeout AFTER printing an excellent reading."""
    from ohmwork.llm import TransientNetworkError

    class Flubbing(FakeProvider):
        def complete(self, prompt, **kwargs):
            if self.replies and isinstance(self.replies[0], Exception):
                self.prompts.append(prompt)
                raise self.replies.pop(0)
            return super().complete(prompt, **kwargs)

    provider = Flubbing(
        [INTENT_JSON, TransientNetworkError("https://x: TimeoutError"),
         json.dumps(CIRCUIT)])
    solution = solve(provider, workdir=tmp_path)

    assert solution.comparison.agrees
    assert solution.attempts == 2
    assert solution.failed_attempts
    assert "one valid JSON object" not in provider.prompts[-1]


def test_it_never_returns_a_solution_that_did_not_verify(tmp_path):
    """The only failure that would really matter. A circuit whose numbers
    disagree with the question is precisely what this project exists to stop
    being handed over."""
    provider = FakeProvider([INTENT_JSON] + [json.dumps(CIRCUIT)] * 4)
    values = {"vout_nominal": 3.0, "iz_nominal": 0.0035}

    with pytest.raises(DesignError):
        solve(provider, executor=fake_executor(values), workdir=tmp_path,
              attempts=2)


def test_a_violated_regime_fails_the_design_rather_than_footnoting_it(tmp_path):
    """Convergence is not correctness. A load sweep into dropout converges
    perfectly and reports a confident, meaningless regulation figure."""
    provider = FakeProvider([INTENT_JSON] + [json.dumps(CIRCUIT)] * 3)
    with pytest.raises(DesignError) as excinfo:
        solve(provider, executor=fake_executor(GOOD, regimes_hold=False),
              workdir=tmp_path, attempts=2)
    assert "zener_in_breakdown" in str(excinfo.value)


def test_a_gate_rejection_is_fed_back_verbatim(tmp_path):
    """The gate's errors are path-shaped and are the most useful thing a
    model can be told."""
    broken = json.loads(json.dumps(CIRCUIT))
    broken["components"][1]["type"] = "resistor"        # not a real type
    provider = FakeProvider([INTENT_JSON, json.dumps(broken),
                             json.dumps(CIRCUIT)])

    solution = solve(provider, workdir=tmp_path)
    assert solution.attempts == 2
    assert "resistor" in provider.prompts[2]


def test_the_same_failure_twice_stops_the_loop(tmp_path):
    """Found by probing extract.py, which burned four attempts on one
    unchanging rejection because nothing was watching."""
    provider = FakeProvider([INTENT_JSON] + [json.dumps(CIRCUIT)] * 4)
    with pytest.raises(DesignError) as excinfo:
        solve(provider, executor=fake_executor({"vout_nominal": 3.0,
                                                "iz_nominal": 0.0035}),
              workdir=tmp_path, attempts=4)
    assert "twice" in str(excinfo.value)


# ------------------------------------------------------------ refusals

def test_a_digital_question_is_refused_before_a_token_is_spent(tmp_path):
    provider = FakeProvider([])
    with pytest.raises(DomainError):
        solve_analog("Design a 4-to-2 priority encoder with an enable, and "
                     "give its truth table, in Logisim.",
                     provider=provider, backend=FakeBackend(),
                     executor=fake_executor(GOOD), workdir=tmp_path)
    assert provider.prompts == [], "a refusal must cost nothing"


def test_a_part_this_tool_has_never_measured_is_refused_by_name(tmp_path):
    provider = FakeProvider([])
    with pytest.raises(DomainError) as excinfo:
        solve_analog("Design an inverting amplifier with a gain of 10 using "
                     "a 741 op-amp in LTspice.",
                     provider=provider, backend=FakeBackend(),
                     executor=fake_executor(GOOD), workdir=tmp_path)
    assert "741" in str(excinfo.value) or "op-amp" in str(excinfo.value)
    assert provider.prompts == []


# -------------------------------------------------- what it says it did

def test_the_basis_states_BOTH_holes_and_not_only_the_familiar_one(tmp_path):
    """Analog has the misreading hole the digital loop has, and one it does
    not: meeting a target is not being a good design. A basis that mentioned
    only the first would overstate what was established."""
    solution = solve(FakeProvider([INTENT_JSON, json.dumps(CIRCUIT)]),
                     workdir=tmp_path)

    assert solution.basis.kind == "intent"
    assert "reading of the question" in solution.basis.limit
    assert "good design" in solution.basis.limit
    assert "LTspice" in solution.basis.headline or \
           "fake-ltspice" in solution.basis.headline


def test_the_quantities_that_were_NOT_checked_are_counted_separately(tmp_path):
    """The zener current here carries no number, so nothing about it could
    have failed. A run where nothing COULD fail must not read like one where
    nothing did."""
    solution = solve(FakeProvider([INTENT_JSON, json.dumps(CIRCUIT)]),
                     workdir=tmp_path)
    assert solution.comparison.observations == 1
    assert "without being checked" in solution.comparison.summary


def test_the_published_question_carries_the_asks_the_intent_named(tmp_path):
    """Derived from the intent's own quantities, which are supposed to be the
    question's words -- so the gate's ask-coverage warning becomes a real, if
    weak, check on the intent: an invented quantity does not appear in the
    question text and says so."""
    solution = solve(FakeProvider([INTENT_JSON, json.dumps(CIRCUIT)]),
                     workdir=tmp_path)
    asks = {ask["text"]: ask["answered_by"]
            for ask in solution.question_data["asks"]}
    assert asks["output voltage"] == "vout_nominal"
    assert asks["zener current"] == "iz_nominal"


def test_a_generated_rationale_is_never_recorded_as_a_human_one(tmp_path):
    """Trust in a rationale comes from a human REVIEWING it at the gate, not
    from who typed it -- so authorship is stamped from what we know, and
    every rationale here was written by a model."""
    solution = solve(FakeProvider([INTENT_JSON, json.dumps(CIRCUIT)]),
                     workdir=tmp_path)
    for component in solution.question_data["circuit"]["components"]:
        if component.get("rationale"):
            assert component["rationale_origin"] == "generated"


def test_the_basis_travels_into_the_published_design_notes(tmp_path):
    """A manifest that did not say which claim it was making would make the
    strongest one by default."""
    solution = solve(FakeProvider([INTENT_JSON, json.dumps(CIRCUIT)]),
                     workdir=tmp_path)
    notes = {note["item"]: note
             for note in solution.question_data["design_notes"]}
    assert "verification basis" in notes
    assert "design intent" in notes
    assert notes["verification basis"]["rationale_origin"] == "generated"


OBSERVE_ONLY = json.dumps({
    "topology": "regulated DC power supply",
    "targets": [
        {"name": "vout_waveform", "kind": "waveform", "net": "vout",
         "quantity": "regulated DC output waveform"},
    ],
    "frequency": 50,
    "notes": [],
})


def test_a_run_where_nothing_COULD_fail_says_so_rather_than_passing(tmp_path):
    """MEASURED on the live Q3 run, whose intent made all five quantities
    observations -- legally, because the question asks to OBSERVE waveforms
    and states no figure to hit.

    "met every one" of nothing is a sentence that reads like a pass. The
    result is real and worth having: the circuit converged and its regimes
    held. It just is not a numeric result, and must not be printed as one.
    """
    provider = FakeProvider([OBSERVE_ONLY, json.dumps(CIRCUIT)])
    solution = solve_analog(
        "Observe the output waveform of a 50 Hz supply in LTspice.",
        provider=provider, backend=FakeBackend(),
        executor=fake_executor({"vout_waveform": 9.0}), workdir=tmp_path)

    assert solution.comparison.agrees
    assert solution.comparison.checked == 0
    assert "NO target carried a number" in solution.comparison.summary
    assert "met every one" not in solution.comparison.summary
    # ...and the basis says the same thing one layer up.
    assert "0 target(s) carrying a number" in solution.basis.headline


def test_the_intent_prompt_makes_a_stated_input_amplitude_a_checked_target():
    """MEASURED 2026-09-02 on the Exp 4.7 clamper: the question stated a
    10 Vpp input, the design delivered 3.1 Vpp, and every check passed
    because the input amplitude was never a target. The rule that closes
    that is read out of the prompt the loop actually sends."""
    from ohmwork.analog import INTENT_PROMPT
    assert "STATED INPUT AMPLITUDE IS A CHECKED TARGET" in INTENT_PROMPT
    assert '"ripple_pp" for a peak-to-peak figure' in INTENT_PROMPT
    assert '"ac_rms" for an RMS one' in INTENT_PROMPT
    # and the DC-output rule no longer swallows a clamper's level shift
    assert 'a\n   clamper\'s "DC level shift" is "dc_level"' in INTENT_PROMPT.replace("\r\n", "\n") \
        or '"DC level shift" is "dc_level"' in INTENT_PROMPT


def test_the_prompts_handle_a_question_in_parts():
    """MEASURED 2026-09-02 on the Exp 4.7 clamper (two circuits): the
    design prompt insisted the source be V1, so one source was wired to
    both parts' nodes six attempts running, and the intent prompt's
    floating-source rule made the model invent a second node for a grounded
    source. Both rules now say what a question in parts needs."""
    from ohmwork.analog import DESIGN_PROMPT, INTENT_PROMPT
    assert "A QUESTION IN PARTS is SEPARATE circuits" in DESIGN_PROMPT
    assert "V1_i and RL_i" in DESIGN_PROMPT
    assert "A source with one end on GROUND" in INTENT_PROMPT


def test_the_design_prompt_converts_a_peak_to_peak_figure_once():
    """MEASURED 2026-09-02: across six clamper attempts the source came out at
    14.1, 20 and 10 Vpp for a stated 10 Vpp -- the model wrote rms 5, then
    amplitude 10. The prompt now states the conversion with the wrong
    answers named, and the amplitude gate is what makes it matter."""
    from ohmwork.analog import DESIGN_PROMPT
    assert '"10 Vpp"  -> "amplitude": 5' in DESIGN_PROMPT
    assert "NOT rms 5" in DESIGN_PROMPT
    assert "A CLAMPER is a series capacitor" in DESIGN_PROMPT
