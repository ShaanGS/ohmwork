"""Question text and a photographed page -> question JSON, checked by the gate.

THE MOST DANGEROUS COMPONENT IN THE PROJECT. Everywhere else a mistake shows
up: a bad netlist will not converge, a wrong pin fails the round trip, a wrong
gate produces a wrong truth table. Here a mistake produces a circuit that is
WRONG BUT SELF-CONSISTENT — read `1.8k` as `1.8M` and the file emits,
round-trips, converges, and reports a confident number answering a question
nobody asked.

No downstream check can catch that, because every downstream check verifies
internal agreement and this is what internal agreement gets measured against.
So the defences are of a different kind:

**The gate is the referee, not the model.** Output goes straight through
`load_question`; a rejection is fed back verbatim for a bounded retry. Its
errors are path-shaped ("circuit.components[1]: unknown key(s) ['resistance']")
which makes them the most useful thing the model could possibly be told.

**The prompt's schema is generated from the gate's own constants.** Typing the
allowed keys into a prompt creates two sources of truth that drift apart in
silence, and the drift presents as a model being "wrong" about a schema that
moved underneath it. `schema_description` reads `question._TOP_KEYS` and the
target's own type vocabulary, so the prompt cannot describe a schema the gate
does not enforce.

**Transcription is restored, not trusted.** When the caller supplies the
question text, the model's copy of it is overwritten with the original. That
removes a whole class of silent corruption — a smart quote, a dropped unit, a
tidied clause — and it is cheap because we already have the authoritative
text. CLAUDE.md incident 8: a paraphrase in one example made ask coverage read
74% against the paraphrase and 59% against the real wording. The screen
designed to reveal dropped work was grading itself.

**Extract twice and diff.** The dominant failure is a DROP — a missed beta, an
omitted component, an ask that never became a measurement — and it leaves
every extraction-driven screen looking perfect. Two independent passes
disagreeing is the only free signal that the source was ambiguous. Agreement
proves nothing; disagreement proves trouble.

WHAT THIS LAYER STILL CANNOT DO. It cannot tell a correct reading from a
confident misreading. That is what the dry-run confirmation screen is for, and
why it prints component values before anything else.
"""

import json
import re
from dataclasses import dataclass, field

from ohmwork.llm import get_provider
from ohmwork.targets import get_target

#: Bounded, because a model that cannot satisfy the gate in a few tries is not
#: going to on the tenth, and each attempt costs tokens and time.
DEFAULT_ATTEMPTS = 3


class ExtractionError(Exception):
    """The model did not produce a question this gate will accept."""


@dataclass
class Extraction:
    data: dict
    attempts: int
    model: str
    provider: str
    warnings: list = field(default_factory=list)
    #: Populated by extract_twice. Empty means the two passes agreed, which
    #: is reassurance and not proof.
    disagreements: list = field(default_factory=list)


# ------------------------------------------------------------- the prompt


def schema_description(target_name: str = "ltspice") -> str:
    """The schema, read out of the gate rather than typed out beside it."""
    from ohmwork import question as gate

    target = get_target(target_name)
    component_keys = sorted(gate._COMPONENT_KEYS | set(target.extra_component_keys))
    return f"""\
Top-level keys (only these): {sorted(gate._TOP_KEYS)}
  "question"      the question text, VERBATIM
  "target"        {target.name!r}
  "circuit"       keys: {sorted(gate._CIRCUIT_KEYS)}
  "asks"          list; keys {sorted(gate._ASK_KEYS)}, "kind" in {sorted(gate._ASK_KINDS)}
  "source"        keys: {sorted(gate._SOURCE_KEYS)}
  "design_notes"  list; keys {sorted(gate._DESIGN_NOTE_KEYS)}

Component keys (only these): {component_keys}
Component types (only these): {sorted(target.known_types())}
Pin names are fixed per type; a net lists "<ref>.<pin>" entries.

"origin" is one of {sorted(gate._ORIGINS)} and says where a VALUE came from:
  "stated"   the question or its image gave this value
  "designed" you chose it — a "rationale" is then REQUIRED
  "default"  a library or policy fallback
A designed value that looks stated submits your judgement as the student's own.
"""


def build_prompt(text, *, target: str = "ltspice", images_supplied: bool = False,
                 previous_error: str | None = None) -> str:
    source = ("The question is in the attached image(s). Transcribe it."
              if images_supplied and not text else
              "The question text is given below.")
    body = f"\nQUESTION TEXT:\n{text}\n" if text else ""
    retry = ""
    if previous_error:
        retry = f"""
YOUR PREVIOUS ANSWER WAS REJECTED BY THE SCHEMA VALIDATOR:

    {previous_error}

Fix exactly that and return the whole object again.
"""
    return f"""\
You are transcribing an electronics lab question into a strict JSON format.
{source}

RULES, in order of importance:

1. TRANSCRIBE, DO NOT SUMMARISE. The "question" field must be the question's
   words VERBATIM — same wording, same order, same units, same symbols. Do not
   summarise, do not tidy, do not fix grammar, do not expand abbreviations, do
   not convert units. A tidied question is not the question.
2. Do not invent values. Every component value must come from the text or the
   image. If a value is unreadable, leave the component out and say so in
   "source"."annotations_unused" rather than guessing.
3. Values you CHOOSE rather than read must carry "origin": "designed" and a
   "rationale". Values read from the source are "origin": "stated".
4. Return ONE JSON object and nothing else. No prose, no markdown fences.

SCHEMA:
{schema_description(target)}
{body}{retry}"""


# ------------------------------------------------------- parsing the reply


_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)


def parse_reply(text: str) -> dict:
    """The JSON object out of a reply, fences and chatter tolerated.

    Models wrap JSON in fences however firmly they are asked not to, and
    failing the whole extraction over a markdown artefact would be a bad
    trade. What is NOT tolerated is guessing at malformed JSON.
    """
    candidate = text.strip()
    match = _FENCE.search(candidate)
    if match:
        candidate = match.group(1).strip()
    elif not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start:end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ExtractionError(
            f"the model's reply is not JSON ({e}). It began: "
            f"{text.strip()[:160]!r}"
        ) from None
    if not isinstance(data, dict):
        raise ExtractionError(
            f"expected a JSON object, got {type(data).__name__}")
    return data


# ---------------------------------------------------------------- extract


def _restore_transcription(data: dict, text: str | None, warnings: list) -> None:
    """The supplied text wins over the model's copy of it.

    Not a warning, a replacement. When we HAVE the authoritative wording there
    is no reason to carry the model's version of it, and overwriting removes
    every silent corruption at once — the smart quote, the dropped unit, the
    helpfully tidied clause.
    """
    if text is None:
        if data.get("question"):
            warnings.append(
                "the question text is the model's TRANSCRIPTION of an image, "
                "not text you supplied — read it against the original before "
                "trusting anything downstream of it"
            )
        return
    if data.get("question") != text:
        warnings.append(
            "the model altered the question wording; the verbatim text you "
            "supplied was restored"
        )
    data["question"] = text


def extract(text: str | None, images=(), *, provider=None,
            target: str = "ltspice", attempts: int = DEFAULT_ATTEMPTS,
            source_file: str | None = None) -> Extraction:
    """One extraction pass, retried against the gate's own complaints."""
    from ohmwork.question import QuestionError, load_question

    provider = provider or get_provider(vision=bool(images))
    images = list(images)
    last_error = None
    warnings: list[str] = []

    for attempt in range(1, attempts + 1):
        prompt = build_prompt(text, target=target,
                              images_supplied=bool(images),
                              previous_error=last_error)
        reply = provider.complete(prompt, images=images, max_tokens=8000)
        try:
            data = parse_reply(reply.text)
        except ExtractionError as e:
            last_error = str(e)
            continue

        warnings = []
        _restore_transcription(data, text, warnings)
        data.setdefault("target", target)
        # The REPLY names which model answered, not the provider object: a
        # pool answers from whichever member was not rate limited, and
        # "pool" is not an extractor anyone can re-run.
        data["source"] = _source_block(data, reply, attempt, source_file,
                                       data.get("question"))
        try:
            load_question(data)
        except QuestionError as e:
            last_error = str(e)
            continue
        except Exception as e:                          # noqa: BLE001
            # Deliberately broad. The gate is strict about SHAPE, but model
            # output is untrusted input and a malformed object can raise
            # almost anything on the way in (a list where a dict belongs, a
            # missing key three levels down). Whatever it raises is a
            # rejection, and telling the model what broke is more useful than
            # a traceback here.
            last_error = f"{type(e).__name__}: {e}"
            continue
        return Extraction(data=data, attempts=attempt, model=reply.model,
                          provider=reply.provider, warnings=warnings)

    raise ExtractionError(
        f"the model did not produce a question this gate accepts in "
        f"{attempts} attempts. Last rejection: {last_error}"
    )


def _source_block(data, reply, attempt, source_file, question_text) -> dict:
    """Provenance, in the schema the gate already knows.

    Keeps whatever the model reported about its own uncertainty
    (`confidence`, `annotations_unused`) and overwrites the facts we know
    better than it does.
    """
    source = dict(data.get("source") or {})
    source["file"] = source_file or source.get("file") or "supplied text"
    source["extractor"] = f"{reply.provider}/{reply.model}"
    source["attempts"] = attempt
    if question_text:
        source["question_chars"] = len(question_text)
    return source


# ------------------------------------------------------- the double pass


def _fingerprint(data: dict) -> dict:
    """The facts worth comparing between two passes: values and asks."""
    circuit = data.get("circuit") or {}
    return {
        "values": {c.get("ref"): c.get("value") or c.get("part")
                   for c in circuit.get("components") or []},
        "types": {c.get("ref"): c.get("type")
                  for c in circuit.get("components") or []},
        "asks": [a.get("text") for a in data.get("asks") or []],
        "nets": {n: sorted(p) for n, p in (circuit.get("nets") or {}).items()},
    }


def _diff(first: dict, second: dict) -> list[str]:
    a, b = _fingerprint(first), _fingerprint(second)
    out = []

    for ref in sorted(set(a["values"]) | set(b["values"])):
        if ref not in a["values"]:
            out.append(f"component {ref} appears only in the second pass")
        elif ref not in b["values"]:
            out.append(f"component {ref} appears only in the first pass")
        elif a["values"][ref] != b["values"][ref]:
            out.append(
                f"component {ref}: first pass read {a['values'][ref]!r}, "
                f"second read {b['values'][ref]!r} — one of them is a misread"
            )
        elif a["types"][ref] != b["types"][ref]:
            out.append(f"component {ref}: type {a['types'][ref]!r} vs "
                       f"{b['types'][ref]!r}")

    for ask in [x for x in a["asks"] if x not in b["asks"]]:
        out.append(f'ask "{ask}" was found only by the first pass — a dropped '
                   f"ask is the failure that leaves every screen looking clean")
    for ask in [x for x in b["asks"] if x not in a["asks"]]:
        out.append(f'ask "{ask}" was found only by the second pass')

    if a["nets"] != b["nets"]:
        out.append("the two passes recovered different connectivity")
    return out


def extract_twice(text: str | None, images=(), **kwargs) -> Extraction:
    """Two independent passes, and what they disagreed about.

    The result of the FIRST pass is returned regardless. Two passes
    disagreeing means a human has to look — not that there is nothing to look
    at, and withholding the extraction would remove the very thing they need
    in order to judge.
    """
    first = extract(text, images, **kwargs)
    second = extract(text, images, **kwargs)
    first.disagreements = _diff(first.data, second.data)
    first.attempts += second.attempts
    if first.disagreements:
        first.warnings.append(
            f"two independent extractions disagreed in "
            f"{len(first.disagreements)} place(s) — the source is ambiguous "
            f"somewhere, and simulation cannot tell you where"
        )
    return first
