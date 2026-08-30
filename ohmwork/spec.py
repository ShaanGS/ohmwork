"""The digital spec oracle: what the question asks for, independent of gates.

THE GAP THIS FILLS. Every other check in this project verifies that the FILE
matches the ANSWER — emit a circuit, hand that exact file to Logisim, read the
result out of what Logisim produced. That catches an emitter bug, a routing
bug, a wrong pin offset. It cannot catch the circuit implementing the wrong
FUNCTION: Logisim evaluates a wrong encoder exactly as happily as a right one,
and reports its truth table with perfect fidelity.

Three questions solved by hand covered that gap with a human writing the
expected table from the question's wording. That does not scale to a question
nobody has seen, which is what a live site faces on every request.

So: from the question's WORDS alone, one boolean expression per output. No
gates, no netlist, no coordinates. Evaluated exhaustively here, and compared
against what Logisim computes from the emitted circuit.

    the spec says WHAT the question asks for
    the circuit says HOW
    Logisim says what the HOW actually does

Two artefacts from different reasoning, judged by a tool we did not write.

WHAT IT CANNOT CATCH, and this must never be overclaimed: a MISREADING of the
question. If the model decides I3 is the lowest-priority input when the
question meant highest, the spec and the circuit agree, both are wrong, and
Logisim confirms them. Same class as reading 1.8k as 1.8M, same answer —
show the reading to a human. That is why the resolved spec is rendered in the
output rather than kept as an internal detail.

`tests/test_spec.py` is the spec for this module.
"""

import re
from dataclasses import dataclass, field
from itertools import product

#: 2**n rows, so n is bounded. 20 inputs is already a million rows; a model
#: that writes more has misunderstood the question, and the useful response is
#: an error rather than a machine that stops responding.
MAX_INPUTS = 20

#: How many differing rows go into the message fed back to the model. All of
#: them are kept on the result; this only bounds the prose.
DEFAULT_MAX_DIFFERENCES = 8


class SpecError(Exception):
    """A spec is not something this module is willing to evaluate.

    Every message names the offending token, signal or count, because these
    errors are fed straight back to the model that wrote the spec and a
    message that does not say what to fix wastes an entire retry.
    """


# --------------------------------------------------------- the expression
#
# A closed vocabulary, parsed by hand. No eval(), no compile(), no ast
# module walking a tree of arbitrary Python. prose.py made the same choice
# for its row filters and the reasoning carries: the guarantee comes from
# there being exactly one evaluator and it being small enough to read.

_TOKEN = re.compile(r"""
    \s*(?:
        (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<postfix_not>')
      | (?P<op>&&|\|\||[&|^+*.~!])
      | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<const>[01])
      | (?P<bad>\S)
    )""", re.VERBOSE)

#: Word spellings a model actually writes. Accepting all of them costs
#: nothing; rejecting them burns a retry on notation rather than on logic.
_WORD_OPS = {"AND": "&", "OR": "|", "XOR": "^", "NOT": "~"}
_AND = {"&", "&&", ".", "*"}
_OR = {"|", "||", "+"}
_XOR = {"^"}
_NOT = {"~", "!"}


@dataclass(frozen=True)
class Expression:
    """A parsed expression, evaluable against a {name: 0|1} mapping."""

    #: ("const", 0|1) | ("var", name) | ("not", e) | ("and"|"or"|"xor", a, b)
    node: tuple

    def evaluate(self, values: dict) -> int:
        return _evaluate(self.node, values)


def _evaluate(node, values) -> int:
    kind = node[0]
    if kind == "const":
        return node[1]
    if kind == "var":
        return 1 if values[node[1]] else 0
    if kind == "not":
        return 1 - _evaluate(node[1], values)
    left = _evaluate(node[1], values)
    right = _evaluate(node[2], values)
    if kind == "and":
        return left & right
    if kind == "or":
        return left | right
    return left ^ right                                  # xor


def _tokenize(text: str, known: list[str]) -> list[tuple[str, str]]:
    tokens, position = [], 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None or match.end() == position:
            break
        position = match.end()
        if match.group("bad"):
            raise SpecError(
                f"{text!r}: {match.group('bad')!r} is not part of the boolean "
                f"vocabulary. Allowed: the signal names {known}, the operators "
                f"AND & . *, OR | +, XOR ^, NOT ~ ! (or a trailing '), "
                f"parentheses, and the constants 0 and 1."
            )
        for name in ("lparen", "rparen", "postfix_not", "op", "const"):
            if match.group(name):
                tokens.append((name, match.group(name)))
                break
        else:
            word = match.group("word")
            if word.upper() in _WORD_OPS:
                tokens.append(("op", _WORD_OPS[word.upper()]))
            else:
                tokens.append(("word", word))
    if position < len(text) and text[position:].strip():
        raise SpecError(f"{text!r}: cannot read from {text[position:]!r}")
    return tokens


class _Parser:
    """Recursive descent. Precedence, loosest first: OR, XOR, AND, NOT."""

    def __init__(self, tokens, known, text):
        self.tokens, self.known, self.text, self.at = tokens, known, text, 0

    def peek(self):
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self):
        token = self.peek()
        self.at += 1
        return token

    def parse(self):
        if not self.tokens:
            raise SpecError(
                f"{self.text!r}: empty expression. An output with no "
                f"expression is not the constant 0 -- say which it is."
            )
        node = self.parse_or()
        if self.at != len(self.tokens):
            leftover = self.tokens[self.at][1]
            raise SpecError(f"{self.text!r}: unexpected {leftover!r}")
        return node

    def _binary(self, operators, below, kind_of):
        node = below()
        while True:
            token = self.peek()
            if token is None or token[0] != "op" or token[1] not in operators:
                return node
            self.take()
            node = (kind_of[self.tokens[self.at - 1][1]], node, below())

    def parse_or(self):
        return self._binary(_OR, self.parse_xor, dict.fromkeys(_OR, "or"))

    def parse_xor(self):
        return self._binary(_XOR, self.parse_and, dict.fromkeys(_XOR, "xor"))

    def parse_and(self):
        return self._binary(_AND, self.parse_not, dict.fromkeys(_AND, "and"))

    def parse_not(self):
        token = self.peek()
        if token and token[0] == "op" and token[1] in _NOT:
            self.take()
            return ("not", self.parse_not())
        return self.parse_atom()

    def parse_atom(self):
        token = self.take()
        if token is None:
            raise SpecError(f"{self.text!r}: expression ends early")
        kind, value = token
        if kind == "lparen":
            node = self.parse_or()
            closing = self.take()
            if closing is None or closing[0] != "rparen":
                raise SpecError(f"{self.text!r}: unbalanced parentheses")
        elif kind == "const":
            node = ("const", int(value))
        elif kind == "word":
            if value not in self.known:
                raise SpecError(
                    f"{self.text!r}: {value!r} is not a declared signal. "
                    f"The inputs are {self.known}. An expression referring to "
                    f"a signal that does not exist is the most likely spec "
                    f"error there is."
                )
            node = ("var", value)
        else:
            raise SpecError(f"{self.text!r}: unexpected {value!r}")

        # A trailing apostrophe is complement: the notation every lab manual
        # uses and the one a model reaches for unprompted.
        while True:
            token = self.peek()
            if token and token[0] == "postfix_not":
                self.take()
                node = ("not", node)
            else:
                return node


def parse_expression(text: str, known_signals) -> Expression:
    """Parse one boolean expression over `known_signals`."""
    if not isinstance(text, str) or not text.strip():
        raise SpecError(
            "empty expression. An output with no expression is not the "
            "constant 0 -- an omission and a deliberate 0 must be different "
            "things, or a spec can be silently incomplete."
        )
    known = list(known_signals)
    tokens = _tokenize(text, known)
    return Expression(_Parser(tokens, known, text).parse())


# --------------------------------------------------------------- the spec

@dataclass(frozen=True)
class Spec:
    """What the question asks the circuit to DO, in algebra rather than gates."""

    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    expressions: dict          # output name -> boolean expression text
    notes: tuple[str, ...] = ()

    def render(self) -> str:
        """The reading, shown to a human.

        The one failure this oracle cannot catch is a misreading of the
        question, and the only defence against that is a person seeing what
        was understood. So this is output, not a debugging aid.
        """
        lines = [f"inputs:  {', '.join(self.inputs)}",
                 f"outputs: {', '.join(self.outputs)}"]
        lines += [f"  {name} = {self.expressions.get(name, '(missing)')}"
                  for name in self.outputs]
        lines += [f"  note: {n}" for n in self.notes]
        return "\n".join(lines)


@dataclass(frozen=True)
class SpecTable:
    """The spec, evaluated over every input combination."""

    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    rows: tuple[tuple[int, ...], ...]

    @property
    def columns(self):
        return self.inputs + self.outputs


def evaluate_spec(spec: Spec) -> SpecTable:
    """Exhaustive evaluation. Rows ordered by the input tuple, ascending.

    Ordered rather than merely produced: incident 10 was two truth tables
    that agreed on every value and disagreed as sequences, because Logisim
    enumerates in its own per-file column order. This side of the comparison
    is ours, so it is defined here rather than observed later.
    """
    if not spec.inputs:
        raise SpecError("a spec needs at least one input")
    if len(spec.inputs) > MAX_INPUTS:
        raise SpecError(
            f"{len(spec.inputs)} inputs is {2 ** len(spec.inputs)} rows. "
            f"The limit is {MAX_INPUTS}; a spec this wide has misread the "
            f"question."
        )
    if not spec.outputs:
        raise SpecError("a spec needs at least one output")

    # Logisim treats labels case-insensitively. `A` and `a` are therefore
    # not separate columns even though Python strings distinguish them.
    output_by_folded_name = {name.casefold(): name for name in spec.outputs}
    overlap = sorted(name for name in spec.inputs
                     if name.casefold() in output_by_folded_name)
    if overlap:
        raise SpecError(
            f"{overlap} named as both input and output (case-insensitively). A signal cannot be "
            f"both, and letting it be one produces a table whose columns do "
            f"not mean what their names say."
        )
    for name in (spec.inputs, spec.outputs):
        duplicates = sorted({n for n in name if list(name).count(n) > 1})
        if duplicates:
            raise SpecError(f"duplicate signal name(s) {duplicates}")

    missing = [name for name in spec.outputs if name not in spec.expressions]
    if missing:
        raise SpecError(
            f"no expression for output(s) {missing}. Every output must say "
            f"what it is; an output left out is not the constant 0."
        )
    extra = sorted(set(spec.expressions) - set(spec.outputs))
    if extra:
        raise SpecError(
            f"expression(s) for {extra}, which are not declared outputs")

    parsed = {name: parse_expression(spec.expressions[name], spec.inputs)
              for name in spec.outputs}

    rows = []
    for combination in product((0, 1), repeat=len(spec.inputs)):
        values = dict(zip(spec.inputs, combination))
        rows.append(combination
                    + tuple(parsed[name].evaluate(values)
                            for name in spec.outputs))
    return SpecTable(inputs=tuple(spec.inputs), outputs=tuple(spec.outputs),
                     rows=tuple(rows))


# ------------------------------------------------- the priority gate
#
# Incident 24: a live solve produced Y0 = EN&(D3|D1) for a priority encoder
# and it VERIFIED -- externally, all 32 rows -- because the circuit
# faithfully implemented a spec that answers code 11 where priority says 10.
# A prompt nudge was tried first and FAILED, measurably: the same model
# re-wrote the same wrong algebra and added a note CLAIMING the masking it
# does not do. Prose does not fix this; arithmetic does.

_PRIORITY_QUESTION = re.compile(r"priority[\s-]+encoder", re.IGNORECASE)
_DATA_INPUT = re.compile(r"^[DI]\d+$", re.IGNORECASE)


def check_priority_encoder(question: str, spec) -> str | None:
    """Does ANY priority order explain the spec's own table?

    The defining property, independent of which end has priority and of
    which code names which input: for every row where several data inputs
    are active, the outputs must equal the row where ONLY the winner is
    active. Both sides come from the spec itself, so this cannot smuggle in
    OUR reading of the question -- it only checks the spec's internal claim
    to BE a priority encoder, which the question's own words demand.

    Deliberately conservative about when it applies: the question must say
    "priority encoder", and the data inputs must be recognisable (D0..Dn or
    I0..In, the names every lab manual uses -- and rule 1 of the spec
    prompt makes the model reuse the question's names). When the shape
    cannot be identified the gate DISARMS rather than guessing, and the
    reading screen remains the defence, as it is for every question class
    this gate has never heard of.

    Returns None when the property holds or the gate does not apply;
    otherwise a message quoting the differing rows, written to be fed back
    to the model that wrote the spec.
    """
    from itertools import permutations

    if not _PRIORITY_QUESTION.search(question):
        return None
    data = [name for name in spec.inputs if _DATA_INPUT.match(name)]
    if len(data) < 2 or len(data) > 8:
        return None

    others = [name for name in spec.inputs if name not in data]
    table = evaluate_spec(spec)
    outputs_by_inputs = {row[:len(spec.inputs)]: row[len(spec.inputs):]
                         for row in table.rows}

    def outputs_for(assignment: dict) -> tuple:
        return outputs_by_inputs[tuple(assignment[name]
                                       for name in spec.inputs)]

    best_violations = None
    for order in permutations(data):
        violations = []
        for other_bits in product((0, 1), repeat=len(others)):
            base = dict(zip(others, other_bits))
            for data_bits in product((0, 1), repeat=len(data)):
                if sum(data_bits) < 2:
                    continue
                row = dict(base)
                row.update(zip(data, data_bits))
                winner = next(name for name in order if row[name])
                solo = dict(base, **{name: 0 for name in data})
                solo[winner] = 1
                if outputs_for(row) != outputs_for(solo):
                    violations.append((row, winner, outputs_for(row),
                                       outputs_for(solo)))
        if not violations:
            return None
        if best_violations is None or len(violations) < len(best_violations[0]):
            best_violations = (violations, order)

    violations, order = best_violations
    shown = []
    for row, winner, got, wanted in violations[:3]:
        active = ", ".join(f"{name}=1" for name in data if row[name])
        context = ", ".join(f"{name}={row[name]}" for name in others)
        outs = ", ".join(f"{name}={bit}"
                         for name, bit in zip(spec.outputs, got))
        want = ", ".join(f"{name}={bit}"
                         for name, bit in zip(spec.outputs, wanted))
        shown.append(
            f"  with {active}" + (f" ({context})" if context else "") +
            f" the spec answers {outs}, but a priority encoder must answer "
            f"exactly as if only {winner} were high: {want}")
    return (
        f"the expressions do not describe a priority encoder under ANY "
        f"priority order. The closest order ({' > '.join(order)}) still "
        f"fails {len(violations)} row(s):\n" + "\n".join(shown) + "\n"
        f"Mask every lower-priority input out of the code expressions with "
        f"the inputs above it.")


# --------------------------------------------------------- the comparison

@dataclass(frozen=True)
class Difference:
    inputs: tuple[int, ...]
    expected: tuple[int, ...]
    actual: tuple[int, ...]
    disagreeing_outputs: tuple[str, ...]


@dataclass(frozen=True)
class Comparison:
    agrees: bool
    summary: str
    differences: tuple = ()
    missing_outputs: tuple = ()
    missing_rows: tuple = ()


def _index_by_inputs(columns, rows, inputs, outputs):
    """Rebuild each row as (input tuple) -> (output tuple) in SPEC order.

    Matching by NAME rather than by position is what makes the row comparison
    mean anything: Logisim reports columns in its own order, and lining two
    tables up by index compares Y0 against Y3 and calls it a disagreement.
    """
    where = {name: index for index, name in enumerate(columns)}
    indexed = {}
    for row in rows:
        key = tuple(row[where[name]] for name in inputs)
        indexed[key] = tuple(row[where[name]] for name in outputs)
    return indexed


def compare_tables(expected: SpecTable, actual,
                   max_differences: int = DEFAULT_MAX_DIFFERENCES,
                   subject: str = "the specification") -> Comparison:
    """Compare the spec's table against what an evaluator produced.

    `actual` is anything with `.inputs`, `.outputs` and `.rows` — in practice
    a `logisim_backend.TruthTable`.

    The summary is written to be fed back to a model, so it names signals and
    prints whole rows. A bare "mismatch" teaches nothing and wastes the retry
    it triggers.

    `subject` names what the table was compared AGAINST. The gate-level path
    compares against a specification; an IC question compares against the
    part's own measured behaviour, and a summary that called both "the spec"
    would make two different claims read identically.
    """
    missing_outputs = tuple(name for name in expected.outputs
                            if name not in actual.outputs)
    missing_inputs = tuple(name for name in expected.inputs
                           if name not in actual.inputs)
    if missing_inputs or missing_outputs:
        # Agreement over a subset is not agreement, so this is a failure in
        # its own right rather than a comparison over what happens to match.
        absent = list(missing_inputs) + list(missing_outputs)
        return Comparison(
            agrees=False,
            missing_outputs=missing_outputs,
            summary=(
                f"the circuit does not report signal(s) {absent}. The "
                f"question requires inputs {list(expected.inputs)} and "
                f"outputs {list(expected.outputs)}; the circuit produced "
                f"inputs {list(actual.inputs)} and outputs "
                f"{list(actual.outputs)}."
            ),
        )

    actual_rows = _index_by_inputs(
        tuple(actual.inputs) + tuple(actual.outputs), actual.rows,
        expected.inputs, expected.outputs)

    width = len(expected.inputs)
    differences, missing_rows = [], []
    for row in expected.rows:
        key, wanted = row[:width], row[width:]
        got = actual_rows.get(key)
        if got is None:
            missing_rows.append(key)
            continue
        if got != wanted:
            differences.append(Difference(
                inputs=key, expected=wanted, actual=got,
                disagreeing_outputs=tuple(
                    name for index, name in enumerate(expected.outputs)
                    if wanted[index] != got[index]),
            ))

    if not differences and not missing_rows:
        return Comparison(agrees=True, summary=(
            f"the circuit matches {subject} on all {len(expected.rows)} input "
            f"combinations, for outputs {list(expected.outputs)}"))

    lines = []
    if missing_rows:
        lines.append(
            f"{len(missing_rows)} input combination(s) produced no row at "
            f"all, including {dict(zip(expected.inputs, missing_rows[0]))}.")
    if differences:
        lines.append(
            f"{len(differences)} of {len(expected.rows)} rows disagree with "
            f"{subject}:")
        for difference in differences[:max_differences]:
            given = ", ".join(f"{name}={value}" for name, value
                              in zip(expected.inputs, difference.inputs))
            wanted = ", ".join(f"{name}={value}" for name, value
                               in zip(expected.outputs, difference.expected))
            got = ", ".join(f"{name}={value}" for name, value
                            in zip(expected.outputs, difference.actual))
            lines.append(f"  with {given}: expected {wanted}; the circuit "
                         f"gives {got}")
        if len(differences) > max_differences:
            # Say it truncated. A model told about 3 wrong rows out of 256
            # is being told a smaller problem than it has.
            lines.append(f"  ... and {len(differences) - max_differences} "
                         f"more differing rows, {len(differences)} in total")
    return Comparison(agrees=False, summary="\n".join(lines),
                      differences=tuple(differences),
                      missing_rows=tuple(missing_rows))
