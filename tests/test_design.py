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
invisible here by construction, which is why the reading is part of the
OUTPUT rather than an internal detail -- a human reading four lines of algebra
can catch what no amount of simulation can.

STEP 5 HAS TWO BASES, and which one ran is a fact about the answer. A
gate-level question is checked against the SPEC, above. A question that names
a part this build has measured is checked against THE PART: a bare one is
evaluated first, and the design must reproduce that measured behaviour
through its own wiring. The spec cannot serve there, because for a named chip
it is the model's memory of a datasheet -- see the section at the foot of
this file, and ohmwork/partcheck.py for the incident. `Solution.basis` says
which ran, what it proves, and what it does not.

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


class FlubbingProvider(FakeProvider):
    """A queued Exception is RAISED instead of returned as a reply."""

    def complete(self, prompt, **kwargs):
        if self.replies and isinstance(self.replies[0], Exception):
            self.prompts.append(prompt)
            raise self.replies.pop(0)
        return super().complete(prompt, **kwargs)


def test_a_malformed_reply_spends_an_attempt_not_the_run(tmp_path):
    """MEASURED 2026-08-30: attempt 4 of a Q3 run died as "the model could
    not be reached" because Groq refused the model's own invalid JSON
    (`json_validate_failed`), throwing away three attempts of progress.
    The model answered and the answer was garbage -- a spent attempt, and
    the next attempt is told to emit one valid JSON object."""
    from ohmwork.llm import MalformedReply

    provider = FlubbingProvider(
        [SPEC_JSON, MalformedReply("fake produced a reply that failed the "
                                   "provider's JSON validation"),
         design_reply()])
    solution = solve(QUESTION, provider=provider,
                     backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)

    assert solution.comparison.agrees
    assert solution.attempts == 2
    assert solution.failed_attempts
    assert "one valid JSON object" in provider.prompts[-1]


def test_a_network_timeout_spends_an_attempt_not_the_run(tmp_path):
    """MEASURED killing runs twice (mistral 2026-08-26, gemini 2026-08-31):
    one 120 s read timeout on a design call ended the whole run as "the
    model could not be reached". The wire failing once is a spent attempt
    -- and unlike a malformed reply, nothing new is fed back, because the
    model never saw the prompt."""
    from ohmwork.llm import TransientNetworkError

    provider = FlubbingProvider(
        [SPEC_JSON, TransientNetworkError("https://x: TimeoutError"),
         design_reply()])
    solution = solve(QUESTION, provider=provider,
                     backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)

    assert solution.comparison.agrees
    assert solution.attempts == 2
    assert solution.failed_attempts
    assert "one valid JSON object" not in provider.prompts[-1]


def test_a_rejected_spec_is_fed_back_once_and_the_fix_accepted(tmp_path):
    """Measured on the live repro of issue #1: the model named inputs A..D
    and outputs a..d -- refused case-insensitively, correctly -- and the
    whole run died on a naming choice one line of feedback would fix. A
    spec validation failure now goes back to the model ONCE, with the
    error; a second bad spec still ends the run."""
    import json as _json
    bad = _json.loads(SPEC_JSON)
    bad["outputs"] = [o.lower() for o in bad["inputs"]]
    bad["logic"] = {o: bad["inputs"][0] for o in bad["outputs"]}

    provider = FakeProvider([_json.dumps(bad), SPEC_JSON, design_reply()])
    solution = solve(QUESTION, provider=provider,
                     backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)
    assert solution.comparison.agrees
    retry_prompt = provider.prompts[1]
    assert "REJECTED" in retry_prompt
    assert "case-insensitively" in retry_prompt

    always_bad = FakeProvider([_json.dumps(bad)] * 3)
    with pytest.raises(DesignError, match="case-insensitively"):
        solve(QUESTION, provider=always_bad,
              backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
              workdir=tmp_path)


def test_a_malformed_SPEC_reply_is_retried_then_given_up_on(tmp_path):
    """The spec call flubbing once costs nothing; flubbing endlessly must
    end as an error naming what kept failing, not spin."""
    from ohmwork.llm import MalformedReply

    once = FlubbingProvider([MalformedReply("flub"), SPEC_JSON,
                             design_reply()])
    solution = solve(QUESTION, provider=once,
                     backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)
    assert solution.comparison.agrees

    always = FlubbingProvider([MalformedReply("flub")] * 5)
    with pytest.raises(DesignError, match="invalid JSON"):
        solve(QUESTION, provider=always,
              backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
              workdir=tmp_path)


def test_a_lone_provider_waits_out_a_short_rate_limit(tmp_path):
    """The pool moves members on RateLimited; a lone provider has nowhere
    to move. MEASURED 2026-08-31: gemini's HTTP 503 ("high demand ...
    usually temporary") is classified RateLimited with a 10 s cooldown FOR
    THE POOL'S BENEFIT, and a single-provider run died on it. Waiting a
    short cooldown costs seconds; dying costs the run. A wait spends no
    attempt -- nothing was designed and nothing failed."""
    from ohmwork.llm import RateLimited

    provider = FlubbingProvider(
        [SPEC_JSON, RateLimited("busy", retry_after=0.01), design_reply()])
    solution = solve(QUESTION, provider=provider,
                     backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)

    assert solution.comparison.agrees
    assert solution.attempts == 1
    assert not solution.failed_attempts


def test_a_rate_limit_too_long_to_wait_out_is_NOBODY_TO_ASK_not_a_failed_design(tmp_path):
    """A retry_after in the thousands is a quota story: sleeping through
    it silently would look like a hang. Tell the human instead -- and tell
    them the RIGHT thing. MEASURED 2026-09-02 on the owner's screen: a lone
    rate-limited provider surfaced as DesignError, which the page rendered
    as "No verified circuit" in red with "nothing is returned when the
    evaluator disagrees" beneath it. The evaluator was never asked. It is
    PoolExhausted -- the same fact the pool reports -- and names the member."""
    from ohmwork.llm import PoolExhausted, RateLimited

    provider = FlubbingProvider(
        [SPEC_JSON, RateLimited("come back later", retry_after=2400.0)])
    with pytest.raises(PoolExhausted) as caught:
        solve(QUESTION, provider=provider,
              backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
              workdir=tmp_path)
    assert dict(caught.value.members)  # names the member and the reason
    assert "come back later" in str(caught.value)


def test_a_pool_running_dry_MID_DESIGN_escapes_as_itself(tmp_path):
    """The design loop's own `except LLMError` used to catch PoolExhausted
    (a subclass) and re-wrap it as DesignError, so the server showed a red
    failed-design card for a provider outage that happened after the spec
    call. Pinned: the pool's verdict must reach the caller untouched."""
    from ohmwork.llm import PoolExhausted

    provider = FlubbingProvider(
        [SPEC_JSON, PoolExhausted("none of the 1 model provider(s) could "
                                  "answer right now.",
                                  members=[("groq", "busy")])])
    with pytest.raises(PoolExhausted) as caught:
        solve(QUESTION, provider=provider,
              backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
              workdir=tmp_path)
    assert dict(caught.value.members) == {"groq": "busy"}


def test_an_unreachable_network_on_the_SPEC_call_gives_up_bounded(tmp_path):
    from ohmwork.llm import TransientNetworkError

    once = FlubbingProvider([TransientNetworkError("t"), SPEC_JSON,
                             design_reply()])
    solution = solve(QUESTION, provider=once,
                     backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)
    assert solution.comparison.agrees

    always = FlubbingProvider([TransientNetworkError("t")] * 5)
    with pytest.raises(DesignError, match="network kept failing"):
        solve(QUESTION, provider=always,
              backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
              workdir=tmp_path)


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


def test_a_question_naming_a_supported_part_is_not_offered_a_refusal_channel(tmp_path):
    """Measured against a real model: asked to spec the 7447 question, it
    used the refusal channel, reasoning that an IC with active-low outputs is
    "not representable as pure combinational boolean logic". That is simply
    wrong -- a 7447 is combinational -- and a refusal that fires on questions
    this tool CAN answer is worse than none: it teaches the person to stop
    asking.

    So when the question names a part this tool has measured, the domain
    question is already settled and the channel is not offered at all.
    """
    provider = FakeProvider([SPEC_JSON, design_reply()])
    with pytest.raises(DesignError):
        # The design offered is an encoder, so it is rightly rejected for
        # containing no 7447. What is under test is the PROMPT, which was
        # written before any of that.
        solve("Design a BCD to seven-segment circuit using the 7447 decoder IC.",
              provider=provider,
              backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
              workdir=tmp_path, attempts=1)

    spec_prompt = provider.prompts[0]
    assert "Do not refuse it" in spec_prompt
    assert "unsupported" not in spec_prompt


def test_a_question_naming_nothing_still_gets_the_refusal_channel():
    provider = FakeProvider([SPEC_JSON, design_reply()])
    solve(QUESTION, provider=provider,
          backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
          workdir=__import__("tempfile").mkdtemp())

    spec_prompt = provider.prompts[0]
    assert "unsupported" in spec_prompt
    # ...and it names what a refusal is FOR, so it does not fire on an IC.
    assert "not two-valued" in spec_prompt


# ----------------------------------------- a question that names a part
#
# THE INCIDENT. Q4 -- "design a BCD-to-seven-segment display circuit using
# the 7447-decoder IC" -- reached the comparison and failed there, and the
# failure was not a bug. The reference was the model's SPEC, which for a
# named chip is its memory of a datasheet. Its memory said BCD 0000 lights
# nothing; a real 7447 shows a nought. The chip is right.
#
# So a part-named question is checked against the PART: a bare one is
# evaluated, and the design must reproduce that through its own wiring. The
# tests below all turn on that difference, and the first one is the
# regression: a spec that is WRONG must not stop a correct circuit verifying.

PART_QUESTION = ("Design a BCD to seven-segment display circuit using the "
                 "7447 decoder IC and a seven-segment display.")

#: Deliberately a MISREMEMBERED decoder: these expressions are not what any
#: 7447 does. If the loop still verifies the circuit, the spec is provably
#: not what it was checked against.
WRONG_RECOLLECTION = json.dumps({
    "inputs": ["D", "C", "B", "A", "EN"],
    "outputs": ["Qa", "Qb", "Qc", "Qd", "Qe", "Qf", "Qg"],
    "expressions": {
        "Qa": "EN & (D | C)", "Qb": "EN & (C ^ B)", "Qc": "EN & (B | A)",
        "Qd": "EN & (D ^ A)", "Qe": "EN & (C & A)", "Qf": "EN & (D | B)",
        "Qg": "EN & (C | ~A)",
    },
    "notes": ["EN ties the lamp-test, blanking and ripple-blanking pins"],
})


def q4_design():
    from pathlib import Path
    return json.loads((Path(__file__).parent.parent / "examples" / "q4.json")
                      .read_text(encoding="utf-8"))["circuit"]


def stand_in_decoder(a, b, c, d, lt, bi, rbi):
    """A chip in the SHAPE a 7447 has: active low, blankable, lamp-testable.

    NOT the real 7447. What these tests exercise is the loop's plumbing, and
    a hand-copied datasheet here would be a second, unmeasured copy of the
    one thing this project refuses to keep in two places. The real chip's
    behaviour is measured in tests/test_logisim_ttl.py, against Evolution.
    """
    if bi == 0:
        return (1,) * 7
    if lt == 0:
        return (0,) * 7
    value = d * 8 + c * 4 + b * 2 + a
    return tuple(0 if (value >> index) % 2 else 1 for index in range(7))


class FakeChipBackend:
    """Stands in for Logisim on BOTH files: the probe and the design.

    It models the intended circuit by hand -- each input straight to the pin
    of the same name, the three control pins tied to EN, segments straight
    out. That model is written here, independently of
    `partcheck.derive_wiring`, so agreement between the two means something.
    """

    name = "fake-logisim"
    verification = "external"

    def __init__(self, corrupt_design=False):
        self.corrupt_design = corrupt_design
        self.files = []

    def truth_table(self, circ_path, inputs, outputs, timeout=120):
        from itertools import product

        from ohmwork.logisim_backend import TruthTable
        self.files.append(str(circ_path))
        is_design = "EN" in inputs
        width = len(inputs)
        rows = []
        for combination in product((0, 1), repeat=width):
            values = dict(zip(inputs, combination))
            if is_design:
                control = (values["EN"],) * 3
            else:
                control = (values["LT"], values["BI"], values["RBI"])
            segments = dict(zip(
                ("QA", "QB", "QC", "QD", "QE", "QF", "QG"),
                stand_in_decoder(values["A"], values["B"], values["C"],
                                 values["D"], *control)))
            rows.append(tuple(combination)
                        + tuple(segments[name.upper()] for name in outputs))
        if is_design and self.corrupt_design:
            # One wrong bit in one row. The check has to be able to fail.
            first = list(rows[0])
            first[width] = 1 - first[width]
            rows[0] = tuple(first)
        return TruthTable(inputs=tuple(inputs), outputs=tuple(outputs),
                          rows=tuple(rows), backend=self.name,
                          verification=self.verification)


def test_a_part_question_is_checked_against_the_PART_not_a_recollection(tmp_path):
    """The Q4 regression, in one test.

    The spec here is wrong about the chip on purpose. Under the old loop that
    alone sank the answer. Now the spec supplies only names and the choices
    the question left open, and the reference is the chip itself.
    """
    from ohmwork.spec import compare_tables

    provider = FakeProvider([WRONG_RECOLLECTION, json.dumps(q4_design())])
    solution = solve(PART_QUESTION, provider=provider,
                     backend=FakeChipBackend(), workdir=tmp_path)

    assert solution.comparison.agrees
    assert solution.attempts == 1
    assert solution.basis.kind == "part"
    assert "7447" in solution.basis.headline

    # ...and prove the spec really was NOT the reference: it disagrees.
    assert not compare_tables(evaluate_spec(solution.spec),
                              solution.table).agrees


def test_the_reference_is_measured_from_a_bare_part_in_the_same_evaluator(tmp_path):
    """Two files reach the evaluator: a probe of the chip alone, and the
    design. If the probe stopped being run the reference would have to come
    from somewhere else, and the only other place is a recollection."""
    backend = FakeChipBackend()
    solve(PART_QUESTION,
          provider=FakeProvider([WRONG_RECOLLECTION, json.dumps(q4_design())]),
          backend=backend, workdir=tmp_path)

    assert len(backend.files) == 2
    assert any("probe" in name for name in backend.files)


def test_the_part_basis_still_fails_when_the_file_does_not_match(tmp_path):
    """A check that cannot fail is worth nothing. One flipped bit in one row
    of the emitted file's table, and the design is rejected."""
    provider = FakeProvider([WRONG_RECOLLECTION] + [json.dumps(q4_design())] * 2)

    with pytest.raises(DesignError) as excinfo:
        solve(PART_QUESTION, provider=provider,
              backend=FakeChipBackend(corrupt_design=True), workdir=tmp_path)
    # The rejection says what the circuit was measured against, in words that
    # do not read as "your algebra was wrong".
    assert "7447" in str(excinfo.value)


def test_a_swapped_signal_is_refused_by_NAME_before_it_reaches_the_file(tmp_path):
    """The one misreading this basis catches by itself.

    Prediction and evaluation both read the same nets, so a swap agrees with
    itself. The names do not: the question's signal A on the part's D pin is
    refused, and the message says why.
    """
    swapped = q4_design()
    swapped["nets"]["n_d"] = ["D.pin", "U1.A"]
    swapped["nets"]["n_a"] = ["A.pin", "U1.D"]
    provider = FakeProvider([WRONG_RECOLLECTION] + [json.dumps(swapped)] * 2)

    with pytest.raises(DesignError) as excinfo:
        solve(PART_QUESTION, provider=provider, backend=FakeChipBackend(),
              workdir=tmp_path)
    assert "must be on that pin" in str(excinfo.value)


def test_the_reading_shown_before_the_answer_hides_no_recollection(tmp_path):
    """What a person is asked to check, at the moment they are asked.

    For a gate-level question that is the algebra. For a part-named question
    it must NOT be: printing the model's expressions beside a verified answer
    presents a recollection as the thing that was checked.
    """
    seen = []
    solve(PART_QUESTION,
          provider=FakeProvider([WRONG_RECOLLECTION, json.dumps(q4_design())]),
          backend=FakeChipBackend(), workdir=tmp_path,
          progress=lambda name, data: seen.append((name, data)))

    reading = next(data for name, data in seen if name == "reading")["spec"]
    assert "Qa = EN & (D | C)" not in reading
    assert "7447" in reading


def test_a_gate_level_question_still_uses_the_specification(tmp_path):
    """The two bases must not collapse into one. A question naming no part
    has no chip to be its own reference, and the spec is the whole story."""
    solution = solve(QUESTION,
                     provider=FakeProvider([SPEC_JSON, design_reply()]),
                     backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                     workdir=tmp_path)
    assert solution.basis.kind == "spec"
    assert solution.basis.reading == solution.spec.render()


def test_every_basis_states_what_it_cannot_prove(tmp_path):
    """A claim with no stated edge reads as a claim with none, and these two
    claims have very different edges."""
    part = solve(PART_QUESTION,
                 provider=FakeProvider([WRONG_RECOLLECTION,
                                        json.dumps(q4_design())]),
                 backend=FakeChipBackend(), workdir=tmp_path)
    gates = solve(QUESTION, provider=FakeProvider([SPEC_JSON, design_reply()]),
                  backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                  workdir=tmp_path / "gates")

    for solution in (part, gates):
        assert "reading of the question" in solution.basis.limit
    assert part.basis.limit != gates.basis.limit
    # The published question carries the basis too: a manifest that did not
    # say which claim it was making would make the stronger one by default.
    notes = {note["item"]: note for note in part.question_data["design_notes"]}
    assert "verification basis" in notes
    assert "wiring, as checked" in notes


# ------------------------------------------------ "using NAND gates only"
#
# Found by the 2026-09-02 gap review: a full adder "using NAND gates only"
# came back VERIFIED as AND/OR/NOT. Every row matched, Logisim agreed, and
# the answer did not use the gates the question named -- verified-and-wrong,
# the exact species this project exists to prevent. Nothing in the loop ever
# looked at WHICH gates a design used.

def test_the_gate_family_is_read_from_the_questions_words():
    from ohmwork.design import gate_family_of
    for text in ["Realise a full adder using NAND gates only.",
                 "Implement the function using only NAND gates",
                 "Design a half subtractor with NOR gates alone",
                 "Build an XOR gate from NAND gates only in Logisim",
                 "NAND-only implementation of a 2:1 mux"]:
        assert gate_family_of(text) in ("nand", "nor"), text
    assert gate_family_of("Build an XOR gate from NAND gates only") == "nand"
    assert gate_family_of("Design a half subtractor with NOR gates alone") == "nor"


def test_a_question_that_merely_mentions_a_gate_is_not_restricted():
    from ohmwork.design import gate_family_of
    assert gate_family_of("Design a 2-to-4 decoder with an active-high enable") is None
    assert gate_family_of("Compare a NAND gate and a NOR gate") is None
    # Two families named as restrictions cancel: the check disarms rather
    # than guessing which one the question meant.
    assert gate_family_of("using NAND gates only and NOR gates only") is None


def test_a_design_outside_the_family_is_refused_BEFORE_the_evaluator():
    from ohmwork.design import check_gate_family
    circuit = {"components": [{"ref": "A", "type": "input_pin"},
                              {"ref": "G1", "type": "and2"},
                              {"ref": "G2", "type": "nand2"},
                              {"ref": "K", "type": "low"},
                              {"ref": "Y", "type": "output_pin"}]}
    with pytest.raises(DesignError) as caught:
        check_gate_family(circuit, "nand")
    message = str(caught.value)
    assert "G1 (and2)" in message and "G2" not in message
    assert "NAND gates ONLY" in message
    assert "nand2, nand3, nand4, nand8" in message      # names what IS allowed
    check_gate_family(circuit, None)                     # no constraint, no check
    check_gate_family({"components": [{"ref": "G2", "type": "nand4"},
                                      {"ref": "Y", "type": "output_pin"}]}, "nand")


def test_the_nand_only_rule_reaches_the_prompt_and_the_gate(tmp_path):
    """End to end through solve(): the model is TOLD the rule, and a design
    that ignores it is a rejected attempt with the offending gate named."""
    from ohmwork import design as design_module
    from ohmwork.design import solve

    prompts = []
    real_format = design_module.DESIGN_PROMPT.format

    class Recording(str):
        def format(self, **kw):
            prompts.append(kw.get("gate_rule", ""))
            return real_format(**kw)

    original = design_module.DESIGN_PROMPT
    design_module.DESIGN_PROMPT = Recording(original)
    try:
        # SPEC_JSON is a decoder; the design reply uses AND gates, which the
        # constraint must refuse without ever calling the backend.
        provider = FlubbingProvider([SPEC_JSON, design_reply(), design_reply()])
        with pytest.raises(DesignError):
            solve("Design a 2-to-4 decoder with an active-high enable using "
                  "NAND gates only", provider=provider,
                  backend=FakeBackend(parse_spec_reply(SPEC_JSON)),
                  workdir=tmp_path, attempts=2)
    finally:
        design_module.DESIGN_PROMPT = original
    assert prompts and all("NAND GATES ONLY" in p for p in prompts)
