"""Asks no measurement can answer, and the strongest framing available.

Q2 asks "Explain your design choices" and "Discuss how your circuit behaves
when multiple inputs are active and when the enable signal is disabled".
Nothing in a simulation answers either. This module is what happens instead,
and tests/test_prose.py is its spec.

THE CENTRAL IDEA. Grounding does not make prose verified. It makes it LOCALLY
FALSIFIABLE: "discuss how the circuit behaves when multiple inputs are active"
is answerable FROM the truth table, so we select the rows where two or more
inputs are high, print them, and the prose becomes a caption over a computed
selection the reader can check without leaving the page. Everything here is
arranged to maximise how often that applies, and to make the exceptions
visible when it does not.

THREE TIERS, descending trust:

    prose_from_design   quotes design_notes rationales. ZERO generation.
    prose_from_results  computed rows, then a caption beneath them.
    prose_free          nothing supports it. Allowed, labeled hardest,
                        and counted in the dry run.

TWO THINGS THIS MODULE IS CAREFUL ABOUT, because both are easy to get subtly
wrong in a way that reads as more solid than it is:

1. **The chain is only as strong as its weakest link, and the weak link must
   be visible.** Rows from Logisim are externally computed; rows from the
   internal fallback are not. Prose standing on the second must never look
   like prose standing on the first, so every evidence group renders the
   verification status it inherits from the Measurement behind it -- and the
   external case SAYS "external" rather than merely omitting a warning. A
   reader cannot tell a missing warning from a missing check.

2. **Absent authorship is never assumed human.** Same rule as
   rationale_origin: a hand-written answer with no `answer_origin` renders as
   "[authorship not recorded -- review]", not as "[human-written]".
"""

import hashlib
import json
import textwrap

TIERS = {"prose_from_design", "prose_from_results", "prose_free"}

#: The closed filter vocabulary. Three kinds cover every ask in all four
#: sample questions. It is deliberately NOT an expression language: the
#: arithmetic evaluator in analysis.py has a whitelist for a reason, and a
#: second, looser evaluator here would undo it.
FILTER_KINDS = {"equals", "min_high", "value_range"}


class ProseError(Exception):
    """A prose ask's grounding contract is broken."""


def is_prose(ask: dict) -> bool:
    """Does this ask expect prose rather than a measurement?

    Used by the coverage check: a prose ask reported as possible dropped work
    is a permanent false alarm on the one screen designed to surface real
    drops, and a reader who learns to skip that line stops seeing the true
    positives too.
    """
    return ask.get("kind") == "prose"


def _spec(ask: dict) -> dict:
    return ask.get("prose") or {}


def tier_of(ask: dict) -> str:
    return _spec(ask).get("tier", "prose_free")


# ---------------------------------------------------------- row selection


def _column_index(table: dict, name: str) -> int:
    try:
        return table["columns"].index(name)
    except ValueError:
        raise ProseError(
            f"column {name!r} is not in the table; it has "
            f"{table['columns']}"
        ) from None


def select_rows(table: dict, select: dict) -> list[list[int]]:
    """Rows matching EVERY filter in `select`. An empty select takes all.

    A conjunction rather than a single predicate, because Q2's own first
    evidence group is "multiple inputs active AND enable on" -- and the ask's
    whole point is the contrast with enable off. A bare min_high would pull
    in the disabled rows that belong to the other half of the sentence, so
    the evidence would have contradicted the caption it sits above.
    """
    unknown = sorted(set(select) - FILTER_KINDS)
    if unknown:
        raise ProseError(
            f"unknown filter kind(s) {unknown}; the vocabulary is closed: "
            f"{sorted(FILTER_KINDS)}. It is not an expression language on "
            f"purpose."
        )

    rows = [list(row) for row in table["rows"]]
    for kind, spec in sorted(select.items()):
        rows = [row for row in rows if _matches(table, row, kind, spec)]
    return rows


def _matches(table, row, kind, spec) -> bool:
    if kind == "equals":
        return all(row[_column_index(table, col)] == value
                   for col, value in spec.items())
    if kind == "min_high":
        high = sum(row[_column_index(table, col)] for col in spec["columns"])
        return high >= spec["count"]
    # value_range: the named columns read MSB-first as one binary number.
    # Q4's shape -- "which codes are valid BCD (0-9) and which are not".
    value = 0
    for col in spec["columns"]:
        value = value * 2 + row[_column_index(table, col)]
    return spec["min"] <= value <= spec["max"]


# ----------------------------------------------------------- fingerprints


def evidence_fingerprint(groups) -> str:
    """Identify the ROWS a caption was written over.

    A stored answer outlives the evidence it describes. Change a gate,
    re-run, and the same sentence sits over different rows while still
    looking grounded — which is worse than no caption, because nothing in
    the rendering gives it away. So the answer records what it was written
    for and the renderer checks.

    Deliberately NOT covering backend or verification. A caption describes
    rows; if identical rows arrive from a different evaluator the sentence
    is still true of them, and what changed — their standing — is rendered
    live on every run and so cannot itself go stale. Including it here would
    invalidate good captions for a reason the page already shows.
    """
    payload = [[g["label"], list(g["columns"]),
                [list(row) for row in g["rows"]]] for g in groups]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _freshness(spec, groups) -> str | None:
    """fresh / stale / unknown — never silently fresh.

    "unknown" is the unrun-check rule one level down: no fingerprint was
    recorded, so the comparison could not run, and a check that could not run
    must never be reported as one that passed.
    """
    if not spec.get("answer"):
        return None
    recorded = spec.get("answer_evidence")
    if not recorded:
        return "unknown"
    return "fresh" if recorded == evidence_fingerprint(groups) else "stale"


# ------------------------------------------------------------ validation


def validate_prose_asks(asks, measurements, notes) -> None:
    """Every grounding reference must resolve. Raises ProseError.

    Run at the input gate, so a broken contract fails before anything is
    simulated rather than producing a confident empty section afterwards.
    """
    known_notes = {n["item"] for n in notes or []}
    for i, ask in enumerate(asks or []):
        if not is_prose(ask):
            continue
        where = f'prose ask "{ask["text"]}"'
        spec = _spec(ask)
        tier = spec.get("tier", "prose_free")
        if tier not in TIERS:
            raise ProseError(
                f"{where}: unknown tier {tier!r}; expected one of "
                f"{sorted(TIERS)}"
            )
        if spec.get("answer_origin") not in (None, "human", "generated"):
            raise ProseError(
                f"{where}: answer_origin must be 'human' or 'generated', "
                f"not {spec['answer_origin']!r}"
            )
        if spec.get("answer_evidence"):
            if not spec.get("answer"):
                raise ProseError(
                    f"{where}: has answer_evidence but no answer. The "
                    f"fingerprint identifies the rows an answer was written "
                    f"over; on its own it identifies nothing."
                )
            if tier != "prose_from_results":
                raise ProseError(
                    f"{where}: answer_evidence is meaningless on a {tier} "
                    f"ask — there are no evidence rows for it to go stale "
                    f"against."
                )

        if tier == "prose_from_design":
            items = spec.get("notes")
            if not items:
                raise ProseError(
                    f"{where}: a prose_from_design ask must name the design "
                    f"notes it quotes, in 'notes'. Quoting them IS the answer, "
                    f"so an empty list means there is no answer."
                )
            for item in items:
                if item not in known_notes:
                    raise ProseError(
                        f"{where}: names design note {item!r}, which does not "
                        f"exist. Known: {sorted(known_notes)}"
                    )

        elif tier == "prose_from_results":
            groups = spec.get("evidence")
            if not groups:
                raise ProseError(
                    f"{where}: a prose_from_results ask must carry 'evidence'; "
                    f"without rows to stand on it is prose_free, and should "
                    f"say so"
                )
            for group in groups:
                name = group.get("measurement")
                if name not in set(measurements):
                    raise ProseError(
                        f"{where}: evidence group {group.get('label')!r} cites "
                        f"measurement {name!r}, which is not in the plan. "
                        f"Known: {sorted(measurements)}"
                    )
                unknown = sorted(set(group.get("select") or {}) - FILTER_KINDS)
                if unknown:
                    raise ProseError(
                        f"{where}: evidence group {group.get('label')!r} uses "
                        f"unknown filter kind(s) {unknown}"
                    )


# ---------------------------------------------------------- dry-run preview


_PREVIEW_LABEL = {
    "prose_from_design": "quoted, not generated (design notes)",
    "prose_from_results": "computed evidence + generated caption",
    "prose_free": "generated, ungrounded",
}


def preview(asks) -> str:
    """What kind of answer each prose ask will get, BEFORE anything runs.

    The ungrounded count is the point: a human deciding whether to trust this
    run should see how much unverifiable text is coming, while they can still
    do something about it.
    """
    prose_asks = [a for a in asks or [] if is_prose(a)]
    if not prose_asks:
        return ""
    lines = ["prose asks — answered by text, not by measurement"]
    ungrounded = 0
    for ask in prose_asks:
        tier = tier_of(ask)
        if tier == "prose_free":
            ungrounded += 1
        lines.append(f'  "{ask["text"]}"')
        lines.append(f"      -> {_PREVIEW_LABEL[tier]}")
    if ungrounded:
        lines.append(
            f"  !! {ungrounded} ungrounded — nothing in this run supports "
            f"them, so nothing on the page lets you check them"
        )
    return "\n".join(lines)


# ------------------------------------------------------------- rendering


_HEADER = [
    "=" * 68,
    "GENERATED TEXT — not verified, and not of the same kind as the",
    "numbers above. Simulation cannot check a sentence. What follows is",
    "arranged so that as much of it as possible sits on top of computed",
    "evidence you can check for yourself, right here on the page.",
    "=" * 68,
]


def resolve_prose(asks, results, notes, answers=None) -> list[dict]:
    """Turn each prose ask's grounding CONTRACT into resolved content.

    One step, two consumers: the terminal renderer below and the published
    manifest. Building the evidence twice would let the page a student reads
    and the page the site serves disagree about which rows support which
    sentence, and nothing would catch it.

    `results` maps measurement name -> analysis.Measurement, so rows and
    provenance arrive together. `answers` maps ask text -> generated text; an
    ask carrying its own `prose.answer` uses that and is labeled by its
    recorded authorship.
    """
    answers = answers or {}
    by_item = {n["item"]: n for n in notes or []}
    out = []
    for ask in asks or []:
        if not is_prose(ask):
            continue
        spec = _spec(ask)
        tier = tier_of(ask)
        entry = {"text": ask["text"], "tier": tier,
                 "quoted_notes": [], "evidence": []}

        if tier == "prose_from_design":
            for item in spec.get("notes", []):
                note = by_item.get(item)
                if note is None:      # validate_prose_asks catches this first
                    raise ProseError(
                        f'prose ask "{ask["text"]}" quotes design note '
                        f"{item!r}, which does not exist"
                    )
                entry["quoted_notes"].append({
                    "item": note["item"], "choice": note["choice"],
                    "rationale": note["rationale"],
                    "authorship": _authorship(note),
                })
        elif tier == "prose_from_results":
            for group in spec.get("evidence", []):
                entry["evidence"].append(_resolve_group(group, results))

        # A design-tier ask needs no answer: quoting the notes IS the answer.
        if tier != "prose_from_design":
            stored = spec.get("answer")
            entry["answer"] = stored or answers.get(ask["text"])
            entry["answer_authorship"] = (
                _authorship(spec) if stored
                else ("[generated]" if entry["answer"] else None)
            )
            # A caption just generated from THESE rows is fresh by
            # construction; only a STORED one can have outlived them.
            entry["answer_freshness"] = (
                _freshness(spec, entry["evidence"]) if stored
                else ("fresh" if entry["answer"] else None)
            )
        out.append(entry)
    return out


def _resolve_group(group, results) -> dict:
    name = group["measurement"]
    measurement = results.get(name)
    if measurement is None or measurement.table is None:
        raise ProseError(
            f"evidence group {group.get('label')!r} cites {name!r}, which is "
            f"not a table measurement. There are no rows to show, and a "
            f"caption over nothing is what this design exists to prevent."
        )
    table = measurement.table
    return {
        "label": group["label"],
        "measurement": name,
        "columns": list(table["columns"]),
        "rows": select_rows(table, group.get("select") or {}),
        "total_rows": len(table["rows"]),
        "backend": measurement.backend,
        "verification": measurement.verification,
    }


def render_prose_section(asks, results, notes, answers=None) -> str:
    """Resolve and render in one step. Convenience for callers holding raw
    asks; the CLI resolves once and calls render_prose directly."""
    return render_prose(resolve_prose(asks, results, notes, answers))


def render_prose(resolved) -> str:
    """The prose section: after all computed results, never interleaved.

    Takes ALREADY-RESOLVED entries so that the terminal output, the caption
    generator and the published manifest all render from one resolution.
    """
    if not resolved:
        return ""

    lines = list(_HEADER)
    for entry in resolved:
        lines.append("")
        lines.append(entry["text"])
        lines.append("-" * len(entry["text"]))
        if entry["tier"] == "prose_from_design":
            lines += _render_design(entry)
            continue
        if entry["tier"] == "prose_from_results":
            lines += _render_evidence(entry)
        else:
            lines += [
                "  ungrounded: nothing in this run supports this ask, so "
                "there is no",
                "  evidence on this page to check the text against. This is "
                "the weakest",
                "  thing the tool produces.",
            ]
        lines += _render_answer(entry)
    return "\n".join(lines) + "\n"


def _authorship(spec: dict) -> str:
    """Same rule as rationale_origin: never assume a person wrote it."""
    origin = spec.get("rationale_origin") or spec.get("answer_origin")
    if origin == "human":
        return "[human-written]"
    if origin == "generated":
        return "[generated, reviewed at input gate]"
    return "[authorship not recorded — review]"


def _wrap(text: str, indent: str) -> list[str]:
    """Prose is meant to be READ, and an unwrapped paragraph in a terminal
    is not. The tables above are never wrapped -- a row must stay one line."""
    return textwrap.wrap(text, width=76, initial_indent=indent,
                         subsequent_indent=indent) or [indent.rstrip()]


def _render_design(entry) -> list[str]:
    """Zero generation: the choices and their rationales ARE the answer."""
    lines = ["  [quoted from design notes — nothing here is generated prose;",
             "   these are the choices made when the circuit was designed]"]
    for note in entry["quoted_notes"]:
        lines.append("")
        lines.append(f"  {note['item']}: {note['choice']}")
        lines += _wrap(note["rationale"], "      ")
        lines.append(f"      {note['authorship']}")
    return lines


def _render_evidence(entry) -> list[str]:
    lines = []
    for group in entry["evidence"]:
        lines.append("")
        lines.append(f"  {group['label']}  ({len(group['rows'])} of "
                     f"{group['total_rows']} rows)")
        lines.append(f"    {provenance_line(group)}")
        if not group["rows"]:
            lines.append("    no rows matched this selection — there is no "
                         "evidence for this point")
            continue
        width = max(3, max(len(c) for c in group["columns"]))
        lines.append("    " + "  ".join(c.rjust(width)
                                        for c in group["columns"]))
        for row in group["rows"]:
            lines.append("    " + "  ".join(str(v).rjust(width) for v in row))
    return lines


def provenance_line(group) -> str:
    """Name the source AND its standing. Both cases say which they are.

    An external group renders as "EXTERNAL" rather than simply omitting the
    internal warning: a reader cannot distinguish a missing warning from a
    missing check, and Q2's evidence being externally computed is a real
    difference in standing that should be visible as one.
    """
    if group["verification"] == "external":
        return (f"rows computed by {group['backend']} [EXTERNAL: an outside "
                f"tool produced them, so an ohmwork bug shows up as "
                f"disagreement]")
    return (f"rows computed by {group['backend']} [INTERNAL: ohmwork's own "
            f"evaluator, with no external simulator checking it — see "
            f"CLAUDE.md, \"The evaluator asymmetry\"]")


def _render_answer(entry) -> list[str]:
    """The sentence, then who wrote it, then whether it still fits the rows.

    Three independent facts, rendered as three independent labels rather than
    braided into one phrase. Authorship does not imply freshness (a person's
    caption goes stale exactly as readily as a model's) and freshness does not
    imply trust, so collapsing them would let one stand in for the other.
    """
    if not entry.get("answer"):
        return ["", "  (no answer generated — run with --write-prose, or "
                    "write one by hand)"]

    freshness = entry.get("answer_freshness")
    grounded = entry["tier"] == "prose_from_results"

    lines = [""]
    if freshness == "stale":
        # Loud, and BEFORE the sentence: a reader who stops at the first line
        # must not have taken a stale claim for a grounded one.
        lines += _wrap(
            "!! STALE — this answer was written over different evidence than "
            "the rows above. The circuit or the selection has changed since. "
            "Re-generate it or re-review it; do not read it as describing "
            "what is printed here.", "  ")
        lines.append("")
    lines += _wrap(entry["answer"], "  ")
    lines.append(f"    {entry['answer_authorship']}")

    if freshness == "stale":
        lines.append("    [evidence has changed — NOT a description of the "
                     "rows above]")
    elif freshness == "unknown":
        lines.append("    [evidence not recorded — cannot tell whether these "
                     "are the rows it was written over]")
    elif grounded:
        lines.append("    [check it against the rows above]")
    else:
        lines.append("    [ungrounded — nothing here supports it]")
    return lines
