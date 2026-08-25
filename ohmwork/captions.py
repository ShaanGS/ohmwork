"""The one seam where a language model writes text a student will read.

Everything else in ohmwork is deterministic Python over simulator output. The
whole project rests on the model never producing anything that cannot be
checked downstream — so this module is built to keep that true even though
what it produces is, by nature, uncheckable prose.

THREE CONSTRAINTS, and each one is load-bearing:

1. **The generator sees only the resolved evidence.** The ask, and the rows
   selected for it. Not the circuit, not the netlist, not even the rest of
   the truth table. That is what makes "check it against the rows above" a
   real instruction: the model had nothing else to work from, so any claim
   the rows do not support is visible as unsupported. A generator that could
   see the netlist would write captions true of the CIRCUIT rather than true
   of the printed rows, and a reader could not tell the two apart.

2. **The result is stored, not regenerated.** A caption written fresh on
   every run would make the published manifest churn, and a library that
   churns cannot be reviewed. So a caption goes into the question JSON — the
   same `answer` / `answer_origin` mechanism a hand-written one uses — and
   subsequent runs read it back. Which means it must also be able to go
   stale, and `prose.evidence_fingerprint` is how it is caught doing so.

3. **Nothing is written without a human looking.** `apply_captions` returns a
   new question dict; the CLI shows the text and asks before saving. The
   dry-run gate is what makes a generated rationale trustworthy anywhere else
   in this project, and prose gets no weaker a rule.

WHY THE MODEL IS NOT ALLOWED TO SAY "the circuit works correctly". The prompt
below forbids evaluative claims for the same reason the tool refuses to grade
a student's work: this is a study aid for ungraded self-study, and a sentence
asserting correctness is exactly the kind of thing a reader would trust
without checking. Describe the rows; let the reader draw the conclusion.
"""

import copy
import warnings
from dataclasses import dataclass

from ohmwork.llm import LLMError, get_provider

DEFAULT_MAX_TOKENS = 4000


class CaptionError(Exception):
    """The caption generator could not be reached or configured."""


class CaptionWarning(UserWarning):
    """One caption failed. The run continues; the ask reports no answer.

    A warning rather than an error on purpose: losing a whole experiment's
    measured results because a sentence could not be written would be a bad
    trade, and an ask with no caption already renders honestly.
    """


@dataclass(frozen=True)
class CaptionRequest:
    """Everything the generator is permitted to see. Deliberately two fields.

    tests/test_captions.py asserts this set is exactly {ask, groups}, because
    a later refactor that helpfully threads "a bit more context" through here
    would silently break the grounding claim without breaking anything else.
    """

    ask: str
    groups: list


PROMPT = """\
You are writing one short caption for a student's electronics lab report.

Below is a question the student was asked, and rows of evidence computed by a
circuit simulator. The rows are ALL you know. You cannot see the circuit, the
netlist, or any other rows.

Write 2-5 sentences that describe what these rows show, in a way that answers
the question. Rules:

- Every claim must be checkable against the rows printed below. If you cannot
  point to rows that support a sentence, do not write that sentence.
- Cite specific rows or counts where it helps ("all six rows with EN = 0
  read Y = 0").
- Do NOT say the circuit is correct, works, is right, or passes. You are
  describing evidence, not grading it. The student draws that conclusion.
- Do not invent component names, values, or behaviour not visible in the rows.
- DO NOT GUESS WHAT A COLUMN NAME STANDS FOR. Refer to a column by its name
  exactly as printed ("V = 1", "EN = 0"). Never expand it into a meaning you
  inferred: a column called V may be a valid flag, a voltage, or anything
  else, and the rows do not tell you which. Naming it wrongly is the one way
  a caption can be false while every number in it is right.
- Number rows within their own group, and say which group you mean. The
  groups are separate selections, not one continuous table.
- Plain prose. No headings, no bullet points, no markdown, no preamble.

Question:
{ask}

Evidence:
{evidence}
"""


def _format_groups(groups) -> str:
    out = []
    for group in groups:
        width = max(3, max(len(c) for c in group["columns"]))
        header = "  ".join(c.rjust(width) for c in group["columns"])
        rows = "\n".join("  ".join(str(v).rjust(width) for v in row)
                         for row in group["rows"])
        out.append(
            f"{group['label']} ({len(group['rows'])} of "
            f"{group['total_rows']} rows, computed by {group['backend']}, "
            f"{group['verification']} verification)\n{header}\n{rows}"
        )
    return "\n\n".join(out)


def _needs_caption(entry, regenerate: bool) -> bool:
    if entry["tier"] != "prose_from_results":
        return False                    # quoting design notes IS the answer
    if not any(group["rows"] for group in entry["evidence"]):
        return False                    # no rows: nothing to caption
    if regenerate:
        return True
    # A fresh stored answer is text a human already reviewed. Regenerating it
    # would churn the library and throw that review away.
    return entry.get("answer_freshness") != "fresh"


def generate_captions(entries, generator, *, regenerate: bool = False) -> dict:
    """ask text -> caption, for every resolved entry that needs one.

    `generator` is any callable taking a CaptionRequest and returning a
    string. ModelCaptioner below is one; tests pass a fake, which is why no
    test in this repo touches the network.
    """
    out = {}
    for entry in entries:
        if not _needs_caption(entry, regenerate):
            continue
        request = CaptionRequest(ask=entry["text"], groups=entry["evidence"])
        try:
            text = generator(request)
        except Exception as e:                     # noqa: BLE001 - see below
            # Any generator failure, from any client, must degrade to "no
            # caption" rather than losing the measured results alongside it.
            warnings.warn(
                f'caption generation failed for ask "{entry["text"]}": '
                f"{type(e).__name__}: {e}",
                CaptionWarning, stacklevel=2,
            )
            continue
        if text and text.strip():
            out[entry["text"]] = text.strip()
    return out


def apply_captions(data: dict, entries, generated: dict) -> dict:
    """A NEW question dict with the captions stored, ready for review.

    Three things are recorded together, because a stored caption is only
    reviewable if you have all three: the text, who wrote it, and — via
    `prose.evidence_fingerprint` — which rows it was written over.
    """
    from ohmwork import prose

    out = copy.deepcopy(data)
    by_text = {entry["text"]: entry for entry in entries}
    for ask in out.get("asks", []):
        text = ask.get("text")
        if text not in generated or ask.get("kind") != "prose":
            continue
        spec = ask.setdefault("prose", {})
        spec["answer"] = generated[text]
        spec["answer_origin"] = "generated"
        spec["answer_evidence"] = prose.evidence_fingerprint(
            by_text[text]["evidence"])
    return out


# ------------------------------------------------------- the real client
#
# Everything above is deterministic and testable offline. This is where the
# process boundary is, and it is deliberately the last thing in the file.


class ModelCaptioner:
    """Turns a CaptionRequest into a prompt and hands it to a provider.

    Vendor-agnostic on purpose. Which model writes the caption is a
    configuration question (OHMWORK_LLM / OHMWORK_LLM_MODEL); WHAT the model
    is allowed to see is not, and that is enforced here rather than by any
    provider — the prompt is built from the request's rows and nothing else.
    """

    def __init__(self, provider=None, max_tokens: int = DEFAULT_MAX_TOKENS):
        self.provider = provider or get_provider()
        self.max_tokens = max_tokens

    @property
    def describe(self) -> str:
        return f"{self.provider.name}/{self.provider.model}"

    def __call__(self, request: CaptionRequest) -> str:
        prompt = PROMPT.format(ask=request.ask,
                               evidence=_format_groups(request.groups))
        try:
            reply = self.provider.complete(prompt, max_tokens=self.max_tokens)
        except LLMError as e:
            raise CaptionError(str(e)) from None
        return reply.text
