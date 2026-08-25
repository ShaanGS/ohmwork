"""The design loop: this file is its spec.

WHAT IT DOES. A digital question in plain English goes in; a circuit file and
a truth table come out, and the loop does not return until an OUTSIDE tool has
confirmed that the circuit computes what the question asked for.

    1. SPEC     the model writes one boolean expression per output, from the
                question's words alone. No gates. Its signal names become
                authoritative for everything downstream.
    2. PLAN     built in Python, not asked for. A truth-table question's plan
                is fully determined by the spec, and every value this project
                can derive deterministically, it derives deterministically.
    3. DESIGN   the model writes ONLY components and nets, using exactly the
                spec's signal names.
    4. GATE     load_question. A rejection is fed back verbatim; its errors
                are path-shaped and are the most useful thing a model can be
                told.
    5. VERIFY   emit the .circ, hand THAT FILE to Logisim, compare its table
                against the spec's. A mismatch is fed back as the differing
                rows and the design is retried.

The model is never shown its own spec's truth table, only the rows where the
circuit disagreed with it. It is a design loop, not a curve fit.

WHAT THE LOOP GUARANTEES, precisely. That the file handed over computes the
function in the spec. NOT that the spec is the right reading of the question.
If the model decides I3 is lowest priority when the question meant highest,
spec and circuit agree and Logisim confirms them both. That failure is
invisible here by construction, which is why `Solution.spec.render()` is part
of the OUTPUT rather than an internal detail -- a human reading four lines of
algebra can catch what no amount of simulation can.

No test in this file touches the network. Every provider is a fake, as with
captions.py: the loop's logic is what is under test, not a model's mood.
"""

import json

import pytest

from ohmwork.design import (DesignError, Solution, build_plan, parse_spec_reply,
                            solve)
from ohmwork.llm import Reply
from ohmwork.spec import Spec, evaluate_spec

# The encoder from examples/q2.json, whose correct answer is pinned in
# tests/baselines.py -- so a fake that "designs" it can be graded.
QUESTION = ("Design and simulate a 4-to-2 priority encoder using Logisim. "
            "Your circuit should include an enable input and a 'valid "
            "output' signal.")

SPEC_JSON = json.dumps({
    "inputs": ["EN", "I3", "I2", "I1", "I0"],
    "outputs": ["Y1", "Y0", "V"],
    "expressions": {
        "Y1": "EN & (I3 | I2)",
        "Y0": "EN & (I3 | (I1 & ~I2 & ~I3))",
        "V": "EN & (I3 | I2 | I1 | I0)",
    },
    "notes": ["priority I3 > I2 > I1 > I0; all outputs 0 when EN is low"],
})


class FakeProvider:
    """Returns canned replies in order, and records the prompts it was given.

    The prompts matter as much as the replies: several tests below assert
    that a failure was actually FED BACK, which is the entire value of a
    retry loop.
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


# ------------------------------------------------------- parsing a spec

def test_a_spec_reply_is_parsed():
    spec = parse_spec_reply(SPEC_JSON)
    assert spec.inputs == ("EN", "I3", "I2", "I1", "I0")
    assert spec.outputs == ("Y1", "Y0", "V")
    assert spec.notes


def test_prose_around_the_json_is_tolerated():
    """Models wrap JSON in fences and apologies. Failing on that spends a
    retry on formatting rather than on logic."""
    wrapped = f"Here you go:\n```json\n{SPEC_JSON}\n```\nHope that helps!"
    assert parse_spec_reply(wrapped).outputs == ("Y1", "Y0", "V")


def test_a_reply_that_is_not_json_is_rejected_with_what_came_back():
    with pytest.raises(DesignError) as excinfo:
        parse_spec_reply("I cannot help with that.")
    assert "cannot help" in str(excinfo.value)


def test_signal_names_must_survive_logisim_unrewritten():
    """Logisim rewrites a label like 'E IN' to 'E_IN_ef467da7' -- a hash we
    cannot reproduce. A name we emit that triggers the rewrite becomes
    unmatchable in our own results, so it is rejected at the spec, before it
    can reach a file."""
    bad = json.dumps({"inputs": ["E IN"], "outputs": ["Y"],
                      "expressions": {"Y": "1"}})
    with pytest.raises(DesignError) as excinfo:
        parse_spec_reply(bad)
    assert "E IN" in str(excinfo.value)


def test_an_unevaluable_spec_is_rejected_at_parse_time():
    """Better to fail here, where the message can be fed back to the model
    that wrote it, than three steps later inside the verifier."""
    bad = json.dumps({"inputs": ["A"], "outputs": ["Y"],
                      "expressions": {"Y": "A & Q9"}})
    with pytest.raises(DesignError) as excinfo:
        parse_spec_reply(bad)
    assert "Q9" in str(excinfo.value)


# --------------------------------------------------------------- the plan

def test_the_plan_is_built_not_asked_for():
    """Deterministic Python wherever possible. A truth-table question's plan
    follows from the spec with nothing left to choose, and a model asked for
    it can only introduce error."""
    spec = parse_spec_reply(SPEC_JSON)
    plan = build_plan(spec)

    assert [run["type"] for run in plan["runs"]] == ["truth_table"]
    assert plan["runs"][0]["inputs"] == list(spec.inputs)
    table = next(m for m in plan["measurements"] if m["kind"] == "table")
    assert table["outputs"] == list(spec.outputs)


def test_the_plan_asserts_the_digital_regimes():
    """convergence is not correctness, and its digital equivalent is that a
    table can be produced by a circuit with a floating input."""
    plan = build_plan(parse_spec_reply(SPEC_JSON))
    asserted = {m["assert"] for m in plan["measurements"]
                if m["kind"] == "regime"}
    assert {"no_floating_inputs", "all_outputs_driven",
            "no_combinational_loops"} <= asserted


# --------------------------------------------------------------- the loop

def encoder_circuit():
    """A correct gate-level 4-to-2 priority encoder, in the schema the gate
    accepts. Stands in for what the model is asked to produce."""
    return {
        "components": [
            {"ref": "EN", "type": "input_pin"},
            {"ref": "I3", "type": "input_pin"},
            {"ref": "I2", "type": "input_pin"},
            {"ref": "I1", "type": "input_pin"},
            {"ref": "I0", "type": "input_pin"},
            {"ref": "Y1", "type": "output_pin"},
            {"ref": "Y0", "type": "output_pin"},
            {"ref": "V", "type": "output_pin"},
            {"ref": "G1", "type": "not"},
            {"ref": "G2", "type": "and2"},
            {"ref": "G3", "type": "or2"},
            {"ref": "G4", "type": "or2"},
            {"ref": "G5", "type": "or4"},
            {"ref": "G6", "type": "and2"},
            {"ref": "G7", "type": "and2"},
            {"ref": "G8", "type": "and2"},
        ],
        "nets": {
            "i3": ["I3.pin", "G3.in0", "G4.in0", "G5.in0"],
            "i2": ["I2.pin", "G1.in0", "G4.in1", "G5.in1"],
            "i1": ["I1.pin", "G2.in0", "G5.in2"],
            "i0": ["I0.pin", "G5.in3"],
            "en": ["EN.pin", "G6.in1", "G7.in1", "G8.in1"],
            "n2": ["G1.out", "G2.in1"],
            "t0": ["G2.out", "G3.in1"],
            "y0pre": ["G3.out", "G7.in0"],
            "y1pre": ["G4.out", "G6.in0"],
            "vpre": ["G5.out", "G8.in0"],
            "y1": ["G6.out", "Y1.pin"],
            "y0": ["G7.out", "Y0.pin"],
            "v": ["G8.out", "V.pin"],
        },
    }


class FakeBackend:
    """Stands in for Logisim. Computes from a function the test supplies, so
    a test can make the 'circuit' disagree with the spec on demand."""

    name = "fake-logisim"
    verification = "external"

    def __init__(self, spec, wrong_rows=0):
        self.table = evaluate_spec(spec)
        self.wrong_rows = wrong_rows
        self.calls = 0

    def truth_table(self, circ_path, inputs, outputs, timeout=120):
        from ohmwork.logisim_backend import TruthTable
        self.calls += 1
        rows = [list(row) for row in self.table.rows]
        for index in range(self.wrong_rows):
            rows[index][-1] = 1 - rows[index][-1]
        return TruthTable(inputs=tuple(inputs), outputs=tuple(outputs),
                          rows=tuple(tuple(r) for r in rows),
                          backend=self.name, verification=self.verification)


def design_reply(circuit=None):
    return json.dumps(circuit or encoder_circuit())


def test_a_question_becomes_a_verified_solution(tmp_path):
    spec = parse_spec_reply(SPEC_JSON)
    provider = FakeProvider([SPEC_JSON, design_reply()])
    backend = FakeBackend(spec)

    solution = solve(QUESTION, provider=provider, backend=backend,
                     workdir=tmp_path)

    assert isinstance(solution, Solution)
    assert solution.comparison.agrees
    assert solution.attempts == 1
    assert solution.circ_path.is_file()
    assert backend.calls == 1


class PoolishProvider(FakeProvider):
    """A provider whose own name is not the name of whoever answered.

    Exactly the shape of llm.Pool: it dispatches to a member, and the member
    is what a manifest has to record.
    """

    name = "pool"
    model = "groq:a+cerebras:b"

    def complete(self, prompt, *, images=(), max_tokens=4000, temperature=0.2,
                 json_object=False):
        reply = super().complete(prompt, images=images, max_tokens=max_tokens,
                                 temperature=temperature)
        return Reply(text=reply.text, model="llama-3.3-70b",
                     provider="cerebras")


def test_provenance_names_the_model_that_answered_not_the_pool(tmp_path):
    """"pool" is not a model id and not an extractor anyone can re-run.

    Every result in this project names the tool that produced it, and the
    reply already carries the truth -- so the recorded provenance is read
    from the reply, never from the provider object.
    """
    provider = PoolishProvider([SPEC_JSON, design_reply()])
    solution = solve(QUESTION, provider=provider,
                     backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)

    assert (solution.provider, solution.model) == ("cerebras", "llama-3.3-70b")
    extractor = solution.question_data["source"]["extractor"]
    assert "cerebras/llama-3.3-70b" in extractor
    assert "pool" not in extractor


def test_the_solution_carries_the_spec_for_a_human_to_read(tmp_path):
    """The one failure the loop cannot catch is a misreading of the question.
    The only defence is a person seeing the reading, so it must be part of
    the result and not an internal detail."""
    provider = FakeProvider([SPEC_JSON, design_reply()])
    solution = solve(QUESTION, provider=provider,
                     backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)
    rendered = solution.spec.render()
    assert "Y1 = EN & (I3 | I2)" in rendered
    assert "priority I3 > I2 > I1 > I0" in rendered


def test_a_gate_rejection_is_fed_back_verbatim(tmp_path):
    """The gate's errors are path-shaped ('circuit.components[1]: unknown
    key(s)') and that specificity is the whole reason a retry can work."""
    broken = encoder_circuit()
    broken["components"][8]["resistance"] = "10k"       # not a Logisim key
    provider = FakeProvider([SPEC_JSON, design_reply(broken), design_reply()])

    solution = solve(QUESTION, provider=provider,
                     backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)

    assert solution.comparison.agrees
    assert solution.attempts == 2
    assert "resistance" in provider.prompts[-1]


def test_a_circuit_that_disagrees_with_the_spec_is_retried_with_the_rows(
        tmp_path):
    """The heart of it. The circuit is valid, emits, and Logisim evaluates it
    happily -- it simply computes the wrong function, which is the failure
    every other check in this project is blind to."""
    spec = parse_spec_reply(SPEC_JSON)
    provider = FakeProvider([SPEC_JSON, design_reply(), design_reply()])
    backend = WrongThenRight(spec)

    solution = solve(QUESTION, provider=provider, backend=backend,
                     workdir=tmp_path)

    assert solution.comparison.agrees
    assert solution.attempts == 2
    fed_back = provider.prompts[-1]
    assert "expected" in fed_back.lower()
    assert "EN=" in fed_back            # whole rows, not just a count


class WrongThenRight(FakeBackend):
    def __init__(self, spec):
        super().__init__(spec)
        self.wrong_rows = 2

    def truth_table(self, *args, **kwargs):
        table = super().truth_table(*args, **kwargs)
        self.wrong_rows = 0              # the "fix" lands on the next attempt
        return table


class AlwaysWrongDifferently(FakeBackend):
    """Wrong every time, but wrong in a NEW way each time.

    That distinction matters: an identical failure stops the loop early (see
    below), so exhausting the attempt budget needs failures that genuinely
    differ -- which is also what a real model produces as it flails.
    """

    def truth_table(self, *args, **kwargs):
        self.wrong_rows = self.calls + 1
        return super().truth_table(*args, **kwargs)


def test_it_never_returns_a_solution_that_did_not_verify(tmp_path):
    """The only failure mode that would matter. Handing back a circuit whose
    table disagrees with the question is precisely what this project exists
    to prevent, so it raises instead."""
    spec = parse_spec_reply(SPEC_JSON)
    provider = FakeProvider([SPEC_JSON] + [design_reply()] * 3)
    backend = AlwaysWrongDifferently(spec)

    with pytest.raises(DesignError) as excinfo:
        solve(QUESTION, provider=provider, backend=backend, workdir=tmp_path,
              attempts=3)
    assert "disagree" in str(excinfo.value).lower()
    assert backend.calls == 3


def test_an_identical_verification_failure_also_stops_early(tmp_path):
    """The early-stop rule is not only about gate rejections. A circuit that
    fails the verifier the same way twice is not converging either, and the
    message must still carry WHAT disagreed rather than only that it did."""
    spec = parse_spec_reply(SPEC_JSON)
    provider = FakeProvider([SPEC_JSON] + [design_reply()] * 4)
    backend = FakeBackend(spec, wrong_rows=4)

    with pytest.raises(DesignError) as excinfo:
        solve(QUESTION, provider=provider, backend=backend, workdir=tmp_path,
              attempts=4)
    message = str(excinfo.value)
    assert "same" in message.lower()
    assert "disagree" in message.lower()
    assert backend.calls == 2


def test_a_repeated_identical_failure_stops_early(tmp_path):
    """Found in extract.py while probing: it burned four attempts on the
    same rejection, unchanged, because nothing noticed. A model that returns
    an identical failure twice will not fix it on the fifth try, and each
    attempt costs money and a minute of rate limit."""
    broken = encoder_circuit()
    broken["components"][8]["resistance"] = "10k"
    provider = FakeProvider([SPEC_JSON] + [design_reply(broken)] * 5)

    with pytest.raises(DesignError) as excinfo:
        solve(QUESTION, provider=provider,
              backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
              workdir=tmp_path, attempts=5)

    assert "same" in str(excinfo.value).lower()
    # spec + two designs. Not five.
    assert len(provider.prompts) == 3


def test_the_design_prompt_pins_the_spec_signal_names(tmp_path):
    """The probe that motivated this: a correct spec called the valid flag
    VALID while the reference called it V, and the comparison rightly refused
    to guess they were the same. Fixing it at the source -- the spec names
    are handed to the designer -- removes the whole class."""
    provider = FakeProvider([SPEC_JSON, design_reply()])
    solve(QUESTION, provider=provider,
          backend=FakeBackend(parse_spec_reply(SPEC_JSON)), workdir=tmp_path)

    design_prompt = provider.prompts[1]
    for name in ("EN", "I3", "I2", "I1", "I0", "Y1", "Y0", "V"):
        assert name in design_prompt


def test_the_designer_is_not_shown_the_expected_truth_table(tmp_path):
    """It must design from the specification, not transcribe an answer. A
    designer handed the table could produce a lookup that satisfies the
    verifier while teaching a student nothing about gates."""
    provider = FakeProvider([SPEC_JSON, design_reply()])
    solve(QUESTION, provider=provider,
          backend=FakeBackend(parse_spec_reply(SPEC_JSON)), workdir=tmp_path)

    design_prompt = provider.prompts[1]
    rows = evaluate_spec(parse_spec_reply(SPEC_JSON)).rows
    rendered = " ".join(str(bit) for bit in rows[17])
    assert rendered not in design_prompt


def test_the_emitted_file_is_the_file_that_was_verified(tmp_path):
    """The core design principle, one layer up. The .circ handed back must be
    byte-identical to what the backend was given, or the verification was of
    something else."""
    import hashlib

    seen = {}

    class Recording(FakeBackend):
        def truth_table(self, circ_path, inputs, outputs, timeout=120):
            seen["digest"] = hashlib.sha256(
                open(circ_path, "rb").read()).hexdigest()
            return super().truth_table(circ_path, inputs, outputs, timeout)

    provider = FakeProvider([SPEC_JSON, design_reply()])
    solution = solve(QUESTION, provider=provider,
                     backend=Recording(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)

    delivered = hashlib.sha256(solution.circ_path.read_bytes()).hexdigest()
    assert delivered == seen["digest"]


def test_the_question_json_it_produces_passes_the_gate(tmp_path):
    """The solution must be publishable by the machinery that already
    exists -- same schema, same gate, same manifest -- rather than a second
    parallel format that drifts."""
    from ohmwork.question import load_question

    provider = FakeProvider([SPEC_JSON, design_reply()])
    solution = solve(QUESTION, provider=provider,
                     backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)

    question = load_question(solution.question_data)
    assert question.target_name.startswith("logisim")
    assert solution.question_data["question"] == QUESTION


def test_a_solution_reports_the_attempts_that_FAILED(tmp_path):
    """A run that took three tries and reports only "verified" hides the two
    designs that were wrong. How they were wrong is the most interesting
    thing about the run: it is the evidence that the loop does something
    rather than dressing up a first draft."""
    spec = parse_spec_reply(SPEC_JSON)
    broken = encoder_circuit()
    broken["components"][8]["resistance"] = "10k"
    provider = FakeProvider([SPEC_JSON, design_reply(broken), design_reply()])

    solution = solve(QUESTION, provider=provider, backend=FakeBackend(spec),
                     workdir=tmp_path)

    assert solution.attempts == 2
    assert len(solution.failed_attempts) == 1
    index, failure = solution.failed_attempts[0]
    assert index == 1
    assert "resistance" in failure


def test_a_first_time_success_reports_no_failures(tmp_path):
    provider = FakeProvider([SPEC_JSON, design_reply()])
    solution = solve(QUESTION, provider=provider,
                     backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)
    assert solution.failed_attempts == ()
