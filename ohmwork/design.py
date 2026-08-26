"""Question in, verified circuit out: the digital design loop.

    1. SPEC     the model writes one boolean expression per output, from the
                question's words alone. No gates. Its signal names become
                authoritative for everything downstream.
    2. PLAN     built HERE, in Python. A truth-table question's plan follows
                from the spec with nothing left to choose, and a model asked
                for it can only introduce error.
    3. DESIGN   the model writes ONLY components and nets, using exactly the
                spec's signal names.
    4. GATE     load_question. A rejection is fed back verbatim -- its errors
                are path-shaped and are the most useful thing a model can be
                told.
    5. VERIFY   emit the .circ, hand THAT FILE to Logisim, and compare its
                table against the spec's. A mismatch is fed back as the
                differing rows, and the design is retried.

WHAT THIS GUARANTEES, precisely: that the file handed over computes the
function in the spec. It does NOT guarantee that the spec is the right
reading of the question. If the model decides I3 is the lowest-priority input
when the question meant highest, the spec and the circuit agree, Logisim
confirms them both, and every check here passes. That failure is invisible to
this loop by construction -- which is why `Solution.spec.render()` is part of
the output. Four lines of algebra are something a human can actually check.

The designer is never shown the spec's truth table, only the rows where its
circuit disagreed. It must design from the specification rather than fit a
curve to an answer, or a student gets a lookup table that satisfies the
verifier and teaches nothing.

`tests/test_design.py` is the spec for this module.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ohmwork.domain import (ANALOG_ADVICE, DomainError, check_digital,
                            check_spec_has_logic, named_parts)
from ohmwork.llm import LLMError, PoolExhausted
from ohmwork.logisim_backend import DigitalEvaluationError
from ohmwork.logisim_symbols import SAFE_LABEL
from ohmwork.spec import Spec, SpecError, compare_tables, evaluate_spec

#: Bounded for the same reason extract.py bounds its retries: a model that
#: cannot satisfy the verifier in a few attempts will not on the tenth, and
#: each attempt costs money and rate limit.
DEFAULT_ATTEMPTS = 4

#: The spec call's budget. MEASURED, not guessed: at 1500 a reasoning model
#: given the four-sentence 7447 question spent the entire budget thinking and
#: returned an empty string. The budget counts toward a free tier's
#: per-minute limit, so it is not set higher than it needs to be -- but a
#: number chosen to save tokens that produces no answer saves nothing.
SPEC_MAX_TOKENS = 6000

#: The design call's budget, and it is a squeeze between two measured walls.
#: 4000 was not enough for the seven-segment question -- the reply came back
#: empty, the whole budget spent thinking. 8000 was REFUSED outright: a free
#: Groq account allows 8000 tokens per MINUTE counting prompt plus
#: max_tokens, so asking for 8000 plus a prompt is a request that can never
#: fit, and the answer is HTTP 413 rather than a slow one.
#:
#: So this is not a "bigger is safer" number: it is a STARTING budget, small
#: enough that the smallest free tier in the pool can still take the job. If
#: a reply comes back cut off, the loop asks again with double the room --
#: and a member whose per-minute cap cannot fit the larger request refuses
#: with 413, which the pool reads as "not this one" and moves on. The budget
#: is therefore set by the job, and which provider can do the job sorts
#: itself out.
DESIGN_MAX_TOKENS = 5000

#: Where doubling stops. Past this the reply is not big, it is looping.
DESIGN_MAX_TOKENS_CEILING = 20000


class TruncatedReply(Exception):
    """The model was still writing when its token budget ran out.

    Its own class because it is the one failure with a mechanical remedy: ask
    again with more room. Feeding "your JSON is malformed" back to a model
    whose JSON was fine until it was cut off spends a retry teaching it
    nothing.
    """


class DesignError(Exception):
    """No verified circuit was produced.

    Raised rather than returning an unverified one. Handing back a circuit
    whose truth table disagrees with the question is precisely the failure
    this project exists to prevent.
    """


# ------------------------------------------------------------- the prompts

SPEC_PROMPT = """\
You are turning a digital-logic lab question into a SPECIFICATION of what the
circuit must DO. Not how. No gates, no netlist, no components, no wiring.

Return ONE JSON object and nothing else:

{{"inputs": ["..."], "outputs": ["..."], "expressions": {{"OUT": "..."}},
  "notes": ["..."]}}

RULES:
1. "inputs" and "outputs" are signal names matching [A-Za-z][A-Za-z0-9_]* --
   letters, digits and underscores, starting with a letter. No spaces. Use
   the names the question uses wherever it gives any. No two names may differ
   only by CASE: Logisim treats labels case-insensitively and renames a clash
   to a letter of its own choosing. If the question uses A, B, C, D for
   inputs, do not use a, b, c, d for outputs -- use Qa, Qb, Sa, SEG_A or
   similar.
2. "expressions" has one boolean expression per output, over the INPUT names
   only. Allowed: & (AND), | (OR), ^ (XOR), ~ (NOT), parentheses, 0 and 1.
   An output may not appear in any expression, including its own.
3. Where the question leaves something open -- priority order, active level,
   what the outputs do when disabled -- CHOOSE, and record each choice in
   "notes" as one sentence. A choice left unstated is a choice a reader
   cannot check.
4. Do not restate the question. Do not explain. JSON only.

{refusal}

QUESTION:
{question}
"""

DESIGN_PROMPT = """\
Design a gate-level logic circuit that implements this specification, for
Logisim.

SPECIFICATION:
{spec}

Return ONE JSON object and nothing else:

{{"components": [...], "nets": {{"netname": ["REF.pin", ...]}}}}

RULES:
1. Use ONLY these component types, with EXACTLY these ports:
{types}
   The port names are zero-indexed. There is no 3-input AND and no NAND or
   NOR: build what you need from these.
2. Every input signal is an "input_pin" and every output signal an
   "output_pin", and its "ref" is EXACTLY the specification's signal name:
     inputs:  {inputs}
     outputs: {outputs}
   Create NO OTHER input_pin. The evaluator enumerates every input pin, so an
   extra one doubles the truth table and the result no longer describes the
   specification. To hold a wire at a fixed level -- a control pin, an unused
   enable -- use "high" or "low", which are constants and not inputs.
3. Each component has a unique "ref" and nothing else. Gates take no value,
   no part, and no label.
4. "nets" maps a net name to the list of ports on it. Every port of every
   component must appear on exactly one net, and every net must have exactly
   one driver (a pin's port on an input_pin, or a gate's "out").
5. {parts_rule}
6. No JSON outside the object. No markdown fences, no explanation.

SHAPE, exactly. A component carries "ref" and "type" and NOTHING else -- no
"ports", no "pins", no "connections", no "label". Connections live only in
"nets", as "REF.portname" strings:

{{"components": [{{"ref": "IN0", "type": "input_pin"}},
                {{"ref": "G1", "type": "and2"}},
                {{"ref": "OUT0", "type": "output_pin"}}],
 "nets": {{"n_in0": ["IN0.pin", "G1.in0"],
          "n_g1":  ["G1.out", "OUT0.pin"]}}}}
{retry}"""

#: The refusal channel, and it is deliberately NARROW. Its one job is to
#: catch a question whose signals are not two-valued -- and it was found
#: refusing a 7447 question on the grounds that an IC with active-low outputs
#: is "not pure boolean logic", which is simply wrong. A refusal that fires
#: on questions this tool CAN answer is worse than no refusal: it teaches the
#: person to stop asking.
REFUSAL_RULE = """REFUSING. This loop builds COMBINATIONAL DIGITAL LOGIC. Refuse ONLY if the
question's signals are not two-valued -- voltages, currents, waveforms,
resistances, capacitances, frequencies -- or if the circuit must REMEMBER
something (a counter, a flip-flop, a register, anything clocked). Return
exactly:

{{"unsupported": "one sentence saying what the question actually asks for"}}

Do NOT refuse for any other reason. In particular: a named logic IC (a 7447,
a decoder, an encoder, a multiplexer) IS combinational digital logic and is
in scope. Active-low signals are still boolean -- write the expression that
produces the 0 or the 1 directly."""

#: When the question names a part this tool has measured, the domain question
#: is already settled: the circuit is buildable and digital, and the model has
#: nothing to add by second-guessing that. Offering it a refusal channel here
#: only creates a way to be wrong.
NO_REFUSAL_RULE = """This question names {parts}, which this tool supports and can build. It is a
combinational digital-logic question. Do not refuse it."""

#: Rule 5, when the question named no particular part. Building it from gates
#: IS the exercise, and a library encoder that answers the question in one
#: component teaches nothing -- the trap the Plexers priority encoder sets.
PRIMITIVES_RULE = (
    "Use gate primitives only. Do not use a library encoder, decoder, "
    "multiplexer or adder: implementing it from gates is the point of the "
    "exercise.")

#: Rule 5, when the question named a part by number. Answering "use the 7447"
#: with a pile of gates is answering a neighbouring question -- correct, and
#: not the one asked.
NAMED_PARTS_RULE = (
    "The question asks for {parts} BY NAME, so USE {parts}. Wire the "
    "question's input signals to its inputs and the question's output "
    "signals to its outputs. Do not rebuild the part out of gates: the "
    "question named it, and a gate-level equivalent answers a different "
    "question. Use gates only for anything the named part does not cover.")

RETRY_BLOCK = """
YOUR PREVIOUS DESIGN WAS REJECTED:

{error}

Fix exactly that and return the whole object again.
"""


# ------------------------------------------------------------ parsing

def _json_object(text: str, what: str) -> dict:
    """Pull one JSON object out of a reply that may be wrapped in prose.

    Models put JSON in fences and apologies. Failing on that spends a retry
    on formatting rather than on logic, so it is tolerated here -- but only
    the outermost braces are trusted, and anything that will not parse is an
    error carrying what actually came back.
    """
    if not isinstance(text, str):
        raise DesignError(f"{what}: no text came back")
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end <= start:
        # It began an object and never closed one. Saying "contains no JSON
        # object" here is true and useless: the reply plainly starts with a
        # JSON object, and the reader goes looking for a formatting problem
        # that is not there. MEASURED on the seven-segment question, whose
        # spec is seven long sum-of-products expressions.
        raise TruncatedReply(
            f"{what}: the reply was CUT OFF mid-object -- it started a JSON "
            f"object and never closed it, which almost always means the "
            f"token budget ran out before the answer finished. It got as far "
            f"as: {text.strip()[-200:]!r}")
    if start == -1:
        raise DesignError(
            f"{what}: the reply contains no JSON object. It said: "
            f"{text.strip()[:300]!r}")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        # Show the text AROUND the failure, not the first 300 characters of a
        # reply whose problem is at character 458. This message is fed back
        # to the model that wrote it, so it has to point at the mistake --
        # and a person reading it needs the same thing.
        body = text[start:end + 1]
        # An error at the very end of the text is a reply that stopped, not a
        # reply that is wrong. The outer object never closed; rfind found the
        # closing brace of some INNER object and everything after it is
        # missing.
        if exc.pos >= len(body) - 2:
            raise TruncatedReply(
                f"{what}: the reply stopped mid-object -- the token budget "
                f"ran out before it finished. It got as far as: "
                f"...{body[-140:]}") from exc
        here = max(0, exc.pos - 120)
        raise DesignError(
            f"{what}: the reply is not valid JSON ({exc}). The problem is "
            f"where >>><<< is:" + chr(10)
            + f"{body[here:exc.pos]}>>><<<{body[exc.pos:exc.pos + 120]}"
        ) from exc
    if not isinstance(data, dict):
        raise DesignError(f"{what}: expected a JSON object, got {type(data).__name__}")
    return data


def parse_spec_reply(text: str) -> Spec:
    """A model's spec reply -> a Spec that is known to evaluate.

    Validation happens HERE rather than later so that the error can be fed
    back to the model that wrote it, while the retry is still about the thing
    it got wrong.
    """
    data = _json_object(text, "spec")

    # LAYER 2: the model's own refusal channel, for a question outside this
    # loop's domain written in words the deterministic screen does not know.
    # A refusal is NOT a malformed reply and must not be retried: asking the
    # same model the same question four more times is how a "no" becomes a
    # confident "yes".
    if "unsupported" in data and "expressions" not in data:
        raise DomainError(
            f"The model read this as a question the digital loop cannot "
            f"answer: {data['unsupported']}\n\n{ANALOG_ADVICE}")

    for key in ("inputs", "outputs", "expressions"):
        if key not in data:
            raise DesignError(f"spec: missing {key!r}")

    names = list(data["inputs"]) + list(data["outputs"])
    unsafe = [n for n in names if not (isinstance(n, str) and SAFE_LABEL.match(n))]
    if unsafe:
        # Logisim rewrites an unsafe label to a VHDL-safe name plus a hash we
        # cannot reproduce ('E IN' -> 'E_IN_ef467da7'), which makes the signal
        # unmatchable in our own results. Caught before it can reach a file.
        raise DesignError(
            f"spec: signal name(s) {unsafe} cannot be used. Logisim rewrites "
            f"any label that is not {SAFE_LABEL.pattern} and appends a hash "
            f"we cannot reproduce, so the signal becomes unmatchable in the "
            f"results. Rename them.")

    spec = Spec(inputs=tuple(data["inputs"]), outputs=tuple(data["outputs"]),
                expressions=dict(data["expressions"]),
                notes=tuple(data.get("notes") or ()))
    try:
        evaluate_spec(spec)
    except SpecError as exc:
        raise DesignError(f"spec: {exc}") from exc
    return spec


# --------------------------------------------------------------- the plan

def type_vocabulary() -> str:
    """The component types and their ports, READ OUT OF THE TARGET.

    Typing this into the prompt would create two sources of truth that drift
    apart in silence, and the drift presents as a model being "wrong" about a
    vocabulary that moved underneath it. extract.py generates its schema the
    same way and for the same reason.
    """
    from ohmwork.targets import get_target

    target = get_target("logisim")
    lines = []
    for type_name in sorted(target.known_types()):
        ports = ", ".join(target.pin_names(type_name))
        lines.append(f"     {type_name}: ports {ports}")
    return chr(10).join(lines)


def build_plan(spec: Spec) -> dict:
    """The analysis plan, DERIVED rather than requested.

    For a truth-table question there is nothing here to choose: the run
    enumerates the spec's inputs, the measurement tabulates the spec's
    outputs, and the three digital regime assertions apply to any
    combinational circuit. Asking a model for it would add a way to be wrong
    and no way to be right.
    """
    return {
        "runs": [{
            "id": "exhaustive",
            "type": "truth_table",
            "inputs": list(spec.inputs),
            "label": f"all {2 ** len(spec.inputs)} input combinations",
        }],
        "measurements": [
            {"name": "truth_table", "kind": "table", "run": "exhaustive",
             "outputs": list(spec.outputs)},
            # Convergence is not correctness, and its digital equivalent is a
            # table produced by a circuit with a floating input.
            {"kind": "regime", "run": "exhaustive",
             "assert": "no_floating_inputs"},
            {"kind": "regime", "run": "exhaustive",
             "assert": "all_outputs_driven"},
            {"kind": "regime", "run": "exhaustive",
             "assert": "no_combinational_loops"},
        ],
    }


def build_question_data(question: str, spec: Spec, circuit: dict,
                        provider_name: str, model: str) -> dict:
    """Assemble the question JSON the existing gate and manifest already take.

    Deliberately the SAME format the hand-written questions use. A second
    parallel format for generated questions would drift from the one the
    library publishes, and the library is the product.
    """
    # primitives_only is OUR constraint, not the question's, and it must not
    # outlive its reason. It exists because "design a priority encoder" is
    # answered uselessly by dropping in the Plexers priority encoder. A
    # question that NAMES a part is the opposite case: refusing the part it
    # asked for is us overruling the question.
    #
    # Found the hard way -- the loop used the 7447 correctly and the gate
    # rejected it three times running, with a failure the model could not
    # possibly fix because it was ours.
    return {
        "question": question,
        "target": "logisim",
        "constraints": {"primitives_only": not named_parts(question)},
        "circuit": circuit,
        "analysis": build_plan(spec),
        "design_notes": [
            {"item": "specification",
             "choice": "; ".join(f"{name} = {spec.expressions[name]}"
                                 for name in spec.outputs),
             "rationale": (
                 "read from the question's wording before any circuit was "
                 "designed, and checked against the emitted file by Logisim. "
                 "It is what the circuit was verified AGAINST, so if it "
                 "misreads the question every check below still passes."),
             "rationale_origin": "generated"},
        ] + [
            {"item": "choice left open by the question",
             "choice": note,
             "rationale": ("the question does not specify this; it was chosen "
                           "here, and the truth table only means what it says "
                           "once the choice is stated"),
             "rationale_origin": "generated"}
            for note in spec.notes
        ],
        "source": {
            "file": "typed by the person asking",
            "extractor": f"{provider_name}/{model}, design loop",
        },
    }


# --------------------------------------------------------------- the loop

@dataclass
class Solution:
    """A circuit an outside tool has confirmed against the specification."""

    question: str
    spec: Spec
    question_data: dict
    circ_path: Path
    table: object                       # logisim_backend.TruthTable
    comparison: object                  # spec.Comparison
    attempts: int
    provider: str
    model: str
    #: What went wrong on each attempt before the one that verified. A
    #: solution that took three tries and reports only "verified" hides the
    #: two designs that were wrong, and how they were wrong is the most
    #: interesting thing about the run -- it is the evidence that the loop
    #: does something rather than dressing up a first draft.
    failed_attempts: tuple = ()
    warnings: list = field(default_factory=list)


def _attempt(circuit, question, spec, provider_name, model, backend, workdir,
             index):
    """One design attempt: gate, emit, evaluate, compare.

    Returns (question_data, circ_path, table, comparison). Raises DesignError
    with a message written to be fed back to the model.
    """
    from ohmwork.logisim_emitter import write_circ
    from ohmwork.question import QuestionError, load_question

    data = build_question_data(question, spec, circuit, provider_name, model)
    try:
        question_object = load_question(data)
    except QuestionError as exc:
        raise DesignError(str(exc)) from exc
    except Exception as exc:                                    # noqa: BLE001
        # Deliberately broad, exactly as the extractor is: model output is
        # untrusted input and a malformed object can raise anything at all.
        raise DesignError(f"{type(exc).__name__}: {exc}") from exc

    circ_path = Path(workdir) / f"design{index}.circ"
    try:
        write_circ(question_object.circuit, circ_path)
    except Exception as exc:                                    # noqa: BLE001
        # Keep the rejected design. A routing failure is a fact about a
        # LAYOUT, and reasoning about one from an error message alone means
        # reconstructing the circuit by guesswork -- which is how an hour
        # goes missing. The file costs nothing and is the evidence.
        (Path(workdir) / f"rejected{index}.json").write_text(
            json.dumps(circuit, indent=1), encoding="utf-8")
        raise DesignError(f"the circuit could not be emitted: {exc}") from exc

    # THE FILE just written is the one handed over -- never a netlist built
    # alongside it. That is the project's core design principle, and this is
    # the line that keeps it true one layer up.
    try:
        table = backend.truth_table(circ_path, list(spec.inputs),
                                    list(spec.outputs))
    except DigitalEvaluationError as exc:
        # The evaluator disagreeing with the circuit is a DESIGN failure, to
        # be fed back, not a crash. The commonest shape: the circuit carries
        # input pins the specification does not have, so Logisim enumerates
        # more combinations than the spec describes. Name them -- the model
        # cannot fix "expected 16 rows, got 128".
        extra = [c["ref"] for c in circuit.get("components", [])
                 if c.get("type") == "input_pin" and c["ref"] not in spec.inputs]
        hint = ""
        if extra:
            hint = (f" The circuit has input pin(s) {', '.join(extra)} that "
                    f"the specification does not list, so the evaluator "
                    f"enumerates them too. Every input pin must be one of: "
                    f"{', '.join(spec.inputs)}. To hold a control pin at a "
                    f"fixed level, use a 'high' or 'low' component instead of "
                    f"an input pin.")
        raise DesignError(f"{exc}{hint}") from exc
    comparison = compare_tables(evaluate_spec(spec), table)
    return data, circ_path, table, comparison


def _ask_until_it_fits(provider, prompt, budget, parse, what):
    """Ask, and if the answer was cut off, ask again with more room.

    The one failure with a mechanical remedy, in the one place both callers
    can share. Feeding "your JSON is malformed" back to a model whose JSON
    was fine until the budget ran out spends a retry teaching it nothing.
    """
    while True:
        try:
            reply = provider.complete(prompt, max_tokens=budget,
                                      json_object=True)
        except PoolExhausted:
            # Nobody could be asked. The question was never wrong and no
            # circuit was ever designed; wrapping this as a design failure
            # would tell someone to rewrite a question that was fine.
            raise
        except LLMError as exc:
            raise DesignError(f"the model could not be reached: {exc}") from exc
        try:
            return parse(reply.text)
        except TruncatedReply as exc:
            grown = min(budget * 2, DESIGN_MAX_TOKENS_CEILING)
            if grown == budget:
                raise DesignError(
                    f"{what} does not fit in {budget} tokens, which is the "
                    f"ceiling. {exc}") from exc
            budget = grown


def solve(question: str, *, provider=None, backend=None, workdir,
          attempts: int = DEFAULT_ATTEMPTS, progress=None) -> Solution:
    """Design a digital circuit for `question` and verify it against its spec.

    `progress(name, data)` is called as the loop runs: "reading" once the
    specification is parsed, and "attempt" as each design is tried and
    rejected. A solve makes several model calls and several evaluator runs,
    and a caller that can only see the final answer cannot show the reading
    BEFORE the answer -- which is the one thing a human is here to check.
    """
    emit = progress or (lambda name, data: None)

    # LAYER 1, before a single token is spent: a deterministic screen. See
    # ohmwork/domain.py for the incident that put it here.
    check_digital(question)

    if provider is None:
        from ohmwork.llm import get_provider
        provider = get_provider()
    if backend is None:
        from ohmwork.logisim_backend import best_available_backend
        backend = best_available_backend()

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    parts = named_parts(question)
    refusal = (NO_REFUSAL_RULE.format(parts=" and ".join(parts)) if parts
               else REFUSAL_RULE)
    spec = _ask_until_it_fits(
        provider,
        SPEC_PROMPT.format(question=question, refusal=refusal),
        SPEC_MAX_TOKENS, parse_spec_reply, "the specification")
    # LAYER 3: a spec with no logic in it verifies perfectly and means
    # nothing. Checked BEFORE the reading is emitted, so a refused question
    # never renders a reading that looks like the start of an answer.
    check_spec_has_logic(spec)

    emit("reading", {"spec": spec.render(),
                     "inputs": list(spec.inputs),
                     "outputs": list(spec.outputs),
                     "notes": list(getattr(spec, "notes", ()) or ())})

    types = type_vocabulary()

    # A question that says "using the 7447-decoder IC" is not asking for an
    # equivalent built from gates, and a loop that silently gives it one has
    # answered a neighbouring question.
    parts_rule = (NAMED_PARTS_RULE.format(parts=" and ".join(parts))
                  if parts else PRIMITIVES_RULE)

    last_error = None
    history: list = []
    budget = DESIGN_MAX_TOKENS
    for index in range(1, attempts + 1):
        retry = "" if last_error is None else RETRY_BLOCK.format(error=last_error)
        prompt = DESIGN_PROMPT.format(
            spec=spec.render(), types=types, parts_rule=parts_rule,
            inputs=", ".join(spec.inputs), outputs=", ".join(spec.outputs),
            retry=retry)
        emit("attempt", {"index": index, "status": "designing"})
        try:
            reply = provider.complete(prompt, max_tokens=budget,
                                      json_object=True)
        except LLMError as exc:
            # A provider failure is not a design failure. It must not be fed
            # back to the model as though its circuit were wrong.
            raise DesignError(f"the model could not be reached: {exc}") from exc

        # Provenance comes from the REPLY, never from the provider object. A
        # pool is a provider whose name is "pool" and whose model is a
        # description of its membership; recording either as the model that
        # designed this circuit would be a lie in the field that exists to
        # prevent lies. The Reply already carries which member answered.
        try:
            circuit = _json_object(reply.text, "design")
            data, circ_path, table, comparison = _attempt(
                circuit, question, spec, reply.provider, reply.model,
                backend, workdir, index)
        except TruncatedReply as exc:
            # Not the model's mistake: it was still writing. Ask again with
            # more room, and say so -- a retry that changes nothing about the
            # request is the thing `failure == last_error` exists to stop.
            grown = min(budget * 2, DESIGN_MAX_TOKENS_CEILING)
            failure = (f"{exc}" if grown == budget else
                       f"the reply ran out of room at {budget} tokens; "
                       f"retrying with {grown}")
            history.append((index, failure))
            emit("attempt", {"index": index, "status": "rejected",
                             "failure": failure})
            if grown == budget:
                raise DesignError(
                    f"the design does not fit in {budget} tokens, which is "
                    f"the ceiling. {exc}") from exc
            budget = grown
            continue
        except DesignError as exc:
            failure = str(exc)
        else:
            if comparison.agrees:
                return Solution(
                    question=question, spec=spec, question_data=data,
                    circ_path=circ_path, table=table, comparison=comparison,
                    attempts=index, provider=reply.provider,
                    model=reply.model,
                    failed_attempts=tuple(history))
            failure = comparison.summary

        # A model that returns the IDENTICAL failure twice will not fix it on
        # the fifth try. Found by probing extract.py, which burned four
        # attempts on one unchanging rejection because nothing was watching.
        history.append((index, failure))
        emit("attempt", {"index": index, "status": "rejected",
                         "failure": failure})
        if failure == last_error:
            raise DesignError(
                f"the same failure came back twice, so retrying is not "
                f"making progress. Stopped after {index} design attempt(s). "
                f"The failure was:\n{failure}")
        last_error = failure

    raise DesignError(
        f"no circuit matching the specification after {attempts} attempt(s). "
        f"The last failure was:\n{last_error}")
