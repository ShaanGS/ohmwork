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

from ohmwork.logisim_symbols import SAFE_LABEL
from ohmwork.spec import Spec, SpecError, compare_tables, evaluate_spec

#: Bounded for the same reason extract.py bounds its retries: a model that
#: cannot satisfy the verifier in a few attempts will not on the tenth, and
#: each attempt costs money and rate limit.
DEFAULT_ATTEMPTS = 4


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
   the names the question uses wherever it gives any.
2. "expressions" has one boolean expression per output, over the INPUT names
   only. Allowed: & (AND), | (OR), ^ (XOR), ~ (NOT), parentheses, 0 and 1.
   An output may not appear in any expression, including its own.
3. Where the question leaves something open -- priority order, active level,
   what the outputs do when disabled -- CHOOSE, and record each choice in
   "notes" as one sentence. A choice left unstated is a choice a reader
   cannot check.
4. Do not restate the question. Do not explain. JSON only.

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
3. Each component has a unique "ref" and nothing else. Gates take no value,
   no part, and no label.
4. "nets" maps a net name to the list of ports on it. Every port of every
   component must appear on exactly one net, and every net must have exactly
   one driver (a pin's port on an input_pin, or a gate's "out").
5. Use gate primitives only. Do not use a library encoder, decoder,
   multiplexer or adder: implementing it from gates is the point of the
   exercise.
6. No JSON outside the object. No markdown fences, no explanation.
{retry}"""

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
    if start == -1 or end <= start:
        raise DesignError(
            f"{what}: the reply contains no JSON object. It said: "
            f"{text.strip()[:300]!r}")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise DesignError(
            f"{what}: the reply is not valid JSON ({exc}). It said: "
            f"{text.strip()[:300]!r}") from exc
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
    return {
        "question": question,
        "target": "logisim",
        "constraints": {"primitives_only": True},
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
        raise DesignError(f"the circuit could not be emitted: {exc}") from exc

    # THE FILE just written is the one handed over -- never a netlist built
    # alongside it. That is the project's core design principle, and this is
    # the line that keeps it true one layer up.
    table = backend.truth_table(circ_path, list(spec.inputs),
                                list(spec.outputs))
    comparison = compare_tables(evaluate_spec(spec), table)
    return data, circ_path, table, comparison


def solve(question: str, *, provider=None, backend=None, workdir,
          attempts: int = DEFAULT_ATTEMPTS) -> Solution:
    """Design a digital circuit for `question` and verify it against its spec."""
    if provider is None:
        from ohmwork.llm import get_provider
        provider = get_provider()
    if backend is None:
        from ohmwork.logisim_backend import best_available_backend
        backend = best_available_backend()

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    spec = parse_spec_reply(
        provider.complete(SPEC_PROMPT.format(question=question),
                          max_tokens=1500).text)

    types = type_vocabulary()

    last_error = None
    history: list = []
    for index in range(1, attempts + 1):
        retry = "" if last_error is None else RETRY_BLOCK.format(error=last_error)
        prompt = DESIGN_PROMPT.format(
            spec=spec.render(), types=types,
            inputs=", ".join(spec.inputs), outputs=", ".join(spec.outputs),
            retry=retry)
        reply = provider.complete(prompt, max_tokens=4000)

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
        if failure == last_error:
            raise DesignError(
                f"the same failure came back twice, so retrying is not "
                f"making progress. Stopped after {index} design attempt(s). "
                f"The failure was:\n{failure}")
        last_error = failure

    raise DesignError(
        f"no circuit matching the specification after {attempts} attempt(s). "
        f"The last failure was:\n{last_error}")
