"""The static viewer: a library directory in, a folder of plain HTML out.

WHY IT IS THIS DUMB. LTspice is a Windows GUI application and a hosted
ohmwork cannot simulate (see library.py). So the site is not an application.
It has no backend, no database, no model in the request path, and it computes
nothing. It renders manifests that a human already reviewed at the dry-run
gate on a machine with the simulators.

THE ONE RULE: **the viewer adds no facts.** Every claim on a page comes from
a manifest field. It does not compute a number, does not round one, does not
infer a verification status from a backend name, does not decide a result
looks fine. If the manifest does not say it, the page does not say it.

That is also why the manifest is copied into the site beside its page: the
rendering is auditable against the record it was rendered from, by anyone,
without running this code.

Two rules inherited from the rest of the project, and worth restating because
the site is where they finally reach a reader who is not the author:

- An unrun check must announce itself. `checks_skipped` renders with its
  reason, and a question with none says so in words. Silence is
  indistinguishable from a pass.
- And its mirror: a check that passed must say what it examined. Regime
  checks that HELD are rendered with their `examined` line, not omitted.

`tests/test_viewer.py` is the spec.
"""

import hashlib
import html
import json
import shutil
from pathlib import Path

from .library import (INDEX_NAME, MANIFEST_NAME, MANIFEST_VERSION,
                      QUESTION_NAME, ManifestError, build_index,
                      validate_manifest)

PAGE_NAME = "index.html"


class ViewerError(Exception):
    """The site cannot be published as asked.

    Every use is a refusal, never a degradation: an invalid manifest, a
    missing deliverable, a deliverable whose bytes no longer match the
    published sha256, a stale index, or a manifest version this viewer does
    not know how to read. Rendering a page anyway would serve a claim the
    library cannot back.
    """


# --------------------------------------------------------------- escaping

def esc(value) -> str:
    """Manifest text is DATA, never markup.

    Today it is human-transcribed lab-manual prose. But the whole design of
    the input gate assumes model output eventually lands in these fields, and
    a viewer that interpreted it would be the one place in the pipeline where
    generated text got to act rather than merely be read.

    Quotes are deliberately NOT escaped here: this is text content, where an
    apostrophe is just an apostrophe, and a reader comparing a published
    reason against the manifest beside it should find the same characters.
    Attribute values go through `attr` instead, which does escape them.
    """
    return html.escape("" if value is None else str(value), quote=False)


def attr(value) -> str:
    """Escaping for an attribute value, where a quote would end the attribute."""
    return html.escape("" if value is None else str(value), quote=True)


def number(value) -> str:
    """Render a published number EXACTLY as the manifest carries it.

    No rounding, no unit guessing, no significant-figure policy. Rounding is
    adding a fact — it asserts a precision the manifest did not claim — and a
    reader comparing the page against the manifest beside it must find the
    same digits in both.
    """
    return esc(value)


# ------------------------------------------------------------ small parts

def badge(text: str, kind: str) -> str:
    return f'<span class="badge {kind}">{esc(text)}</span>'


def verification_badge(verification: str) -> str:
    """Rendered FROM the field, never inferred from the backend name.

    An internal result names a backend too, and inferring the label from that
    name would silently upgrade the weakest results in the project.
    """
    if verification == "external":
        return badge("EXTERNAL", "ok") + (
            ' <span class="note">an outside tool computed this, so a bug of '
            'ours shows up as disagreement</span>')
    if verification == "internal":
        return badge("INTERNAL", "warn") + (
            ' <span class="note">ohmwork\'s own evaluator produced this AND '
            'anything it would be checked against</span>')
    # A manifest that validated cannot reach here. Say so rather than
    # rendering an empty cell that reads as "fine".
    return badge(f"UNKNOWN STATUS: {verification}", "bad")


def section(title: str, body: str) -> str:
    return f"<section><h2>{esc(title)}</h2>\n{body}\n</section>"


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Cells are pre-escaped by the caller, because some of them are badges."""
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "\n".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
        for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead>' \
           f"<tbody>\n{body}\n</tbody></table></div>"


# ------------------------------------------------------------- the page

def render_question_text(manifest: dict) -> str:
    question = manifest["question"]
    parts = [f'<blockquote>{esc(question["text"])}</blockquote>']
    source = question.get("source") or {}
    if source:
        rows = "".join(
            f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>"
            for k, v in sorted(source.items()) if not isinstance(v, (dict, list)))
        if rows:
            parts.append(f"<dl class=\"kv\">{rows}</dl>")
    asks = question.get("asks") or []
    if asks:
        items = "".join(
            "<li>{}{}</li>".format(
                esc(a.get("text", "")),
                f' <span class="note">answered by {esc(a["answered_by"])}</span>'
                if a.get("answered_by") else
                ' <span class="note">prose</span>')
            for a in asks)
        parts.append(f"<p>What it asks for:</p><ul>{items}</ul>")
    return section("The question, verbatim", "\n".join(parts))


def render_standing_notices(manifest: dict) -> str:
    """What a reader must know before reading any number below.

    Each line is derived from a manifest field, and the absence of a line
    means the field said there was nothing to report — which is why the
    checks section below states positively that nothing was skipped, rather
    than relying on this banner being empty.
    """
    notices = []
    if any(r["verification"] == "internal" for r in manifest["results"]):
        notices.append(
            "Some results on this page are INTERNAL: ohmwork's own evaluator "
            "computed them and also computes anything they would be checked "
            "against. They are weaker than the rest and are labelled "
            "individually.")
    if any(not r["reliable"] for r in manifest["results"]):
        notices.append(
            "Some results are marked UNRELIABLE, with a reason. They are shown "
            "flagged rather than hidden — data from outside the intended "
            "operating regime is worth seeing, as long as nobody mistakes it "
            "for a valid answer.")
    if manifest["checks_skipped"]:
        notices.append(
            "Some checks did not run. They are named below with the reason, "
            "because a page with no warnings because nothing was examined must "
            "not look like a page with no warnings because everything passed.")
    if any(not c["held"] for c in manifest["regime_checks"]):
        notices.append(
            "A regime assertion was VIOLATED. Convergence is not correctness: "
            "a circuit outside its intended operating regime simulates fine "
            "and reports a confident, meaningless number.")
    stale = [p for p in manifest["prose"]
             if p.get("answer_freshness") in ("stale", "unknown")]
    if stale:
        notices.append(
            "A written answer on this page could not be confirmed against the "
            "evidence it describes. See the explanations section.")
    if not notices:
        return ""
    items = "".join(f"<li>{esc(n)}</li>" for n in notices)
    return f'<section class="notices"><h2>Read this first</h2><ul>{items}</ul></section>'


def render_results(manifest: dict) -> str:
    rows = []
    for result in manifest["results"]:
        detail = []
        if result.get("formula"):
            detail.append(f'<div class="mono">{esc(result["formula"])}</div>')
        if result.get("definition"):
            detail.append(f'<div class="note">{esc(result["definition"])}</div>')
        if result.get("at") is not None:
            at = result["at"]
            # A dict here is {source: value} -- the sweep point this number
            # was read at. Rendering it as a Python repr puts braces and
            # quotes in front of the one detail that says WHICH point.
            shown = (", ".join(f"{esc(k)} = {number(v)}"
                               for k, v in sorted(at.items()))
                     if isinstance(at, dict) else esc(at))
            detail.append(f'<div class="note">at {shown}</div>')
        if result.get("stats"):
            stats = ", ".join(f"{esc(k)} = {number(v)}"
                              for k, v in sorted(result["stats"].items()))
            detail.append(f'<div class="mono">{stats}</div>')
        for warning in result["warnings"]:
            detail.append(f'<div class="bad">{esc(warning)}</div>')

        status = verification_badge(result["verification"])
        if not result["reliable"]:
            status = badge("UNRELIABLE", "bad") + " " + status

        value = (number(result["value"]) if result["value"] is not None
                 else '<span class="note">no single value — see the table '
                      'below</span>')
        # A waveform measurement's headline value is one of the statistics
        # published beside it, and which one changes what it means: the mean
        # of a symmetric AC waveform is near zero and answers nothing. Say
        # which, when THIS MANIFEST shows a match. Not looked up from
        # analysis.py -- a page must not keep asserting a convention after the
        # code that set it has moved on.
        matches = [key for key, stat in sorted((result.get("stats") or {}).items())
                   if stat == result["value"]]
        if matches:
            value += ('<div class="note">= '
                      + ", ".join(f"stats.{esc(k)}" for k in matches)
                      + "</div>")
        rows.append([
            f'<span class="mono">{esc(result["name"])}</span>',
            value,
            esc(result["run"]),
            esc(result["source"]) + " / " + esc(result["backend"]),
            status + "".join(detail),
        ])

    body = table(["measurement", "value", "run", "how", "standing"], rows)

    # A table measurement has no single number, so it renders in full below
    # rather than being collapsed into a cell.
    for result in manifest["results"]:
        published = result.get("table")
        if not published:
            continue
        body += f'<h3>{esc(result["name"])}</h3>'
        body += table(published["columns"],
                      [[esc(cell) for cell in row] for row in published["rows"]])
        for note in published.get("notes", []):
            body += f'<p class="note">{esc(note)}</p>'
    return section("Results", body)


def render_regime_checks(manifest: dict) -> str:
    """Every check that RAN, held or not, with what it looked at.

    A check whose only output is a warning is invisible when it passes, which
    makes a passing check indistinguishable from one nobody evaluated.
    """
    checks = manifest["regime_checks"]
    if not checks:
        return section(
            "Regime checks",
            '<p class="warn">No regime assertion was evaluated for this '
            'question. Convergence is not correctness.</p>')
    rows = []
    for check in checks:
        held = (badge("held", "ok") if check["held"]
                else badge("VIOLATED", "bad"))
        reasons = "".join(f'<div class="bad">{esc(r)}</div>'
                          for r in check["reasons"])
        rows.append([
            f'<span class="mono">{esc(check["assertion"])}</span>',
            esc(check["run"]) + (f' / {esc(check["device"])}'
                                 if check["device"] else ""),
            held + reasons,
            esc(check["examined"]),
        ])
    return section("Regime checks",
                   table(["assertion", "run", "verdict", "what it examined"],
                         rows))


def render_skipped_checks(manifest: dict) -> str:
    skipped = manifest["checks_skipped"]
    if not skipped:
        return section(
            "Checks that did not run",
            "<p>Every check this target defines ran: "
            "<strong>no checks were skipped</strong>. Stated positively "
            "rather than left blank, because an empty section reads the same "
            "as an unexamined one.</p>")
    items = "".join(
        f'<li>{badge("SKIPPED", "warn")} '
        f'<span class="mono">{esc(c["name"])}</span> — {esc(c["reason"])}</li>'
        for c in skipped)
    return section("Checks that did not run", f"<ul>{items}</ul>")


def render_deliverables(manifest: dict) -> str:
    """The files a student opens, each with the claim made about it.

    `verified` is never rendered as a bare word. A verified file states HOW,
    and an unverified one states WHY it could not be checked — the `.plt` case,
    where no machine check exists at all.
    """
    items = []
    for item in manifest["deliverables"]:
        if item["verified"]:
            claim = (badge("verified", "ok") + " " +
                     f'<span class="note">{esc(item["verified_by"])}</span>')
        else:
            claim = (badge("UNVERIFIED", "warn") + " " +
                     f'<span class="note">{esc(item["unverified_reason"])}</span>')
        items.append(
            f'<li><a href="{attr(item["path"])}" download>'
            f'<span class="mono">{esc(item["path"])}</span></a> '
            f'<span class="note">({esc(item["kind"])})</span><br>{claim}'
            f'<div class="hash mono">sha256 {esc(item["sha256"])}</div></li>')
    note = ("<p class=\"note\">The layout in these files is generated "
            "mechanically — a grid for LTspice, columns by logic depth for "
            "Logisim. It is correct, not pretty, and it will not resemble a "
            "hand-drawn schematic.</p>")
    return section("Download", f"<ul class=\"files\">{''.join(items)}</ul>{note}")


def render_devices_and_choices(manifest: dict) -> str:
    body = []
    devices = manifest["devices"]
    if devices:
        rows = [[f'<span class="mono">{esc(ref)}</span>',
                 f'<span class="mono">{esc(d["part"])}</span>',
                 esc(d["policy"]),
                 f'<span class="mono">{esc(d["directive"])}</span>',
                 esc(d["report"])]
                for ref, d in sorted(devices.items())]
        body.append(table(["ref", "part", "policy", "model card", "why"], rows))

    designed = manifest["designed_values"]
    if designed:
        rows = []
        for value in designed:
            authorship = value.get("rationale_origin")
            label = {"human": "[human-written]",
                     "generated": "[generated, reviewed at input gate]"}.get(
                         authorship, "[authorship not recorded — review]")
            rows.append([
                f'<span class="mono">{esc(value["ref"])}</span>',
                f'<span class="mono">{esc(value["value"])}</span>',
                esc(value["origin"]),
                esc(value["rationale"]) + f' <span class="note">{esc(label)}</span>',
            ])
        body.append(
            "<h3>Values that are choices, not givens</h3>"
            '<p class="note">These were not stated by the question. They are '
            "this tool's engineering judgement, and they are published "
            "separately so they are never mistaken for the question's own "
            "numbers.</p>"
            + table(["ref", "value", "origin", "rationale"], rows))

    # A prose_from_design ask ANSWERS itself by quoting these rationales, so
    # printing them again here would put the same four paragraphs on the page
    # twice. Point at the copy that is doing work instead: this page's job is
    # to let a reader find the things that need their judgement, and padding
    # it is the cheapest way to stop them looking.
    quoted = {note["item"] for entry in manifest["prose"]
              for note in entry.get("quoted_notes", [])}
    notes = [n for n in manifest["design_notes"] if n["item"] not in quoted]
    if manifest["design_notes"] and not notes:
        body.append(
            '<h3>Design notes</h3><p class="note">All '
            f'{len(manifest["design_notes"])} design notes are quoted in full '
            "under Explanations below, where quoting them is the answer to an "
            "ask. They are not repeated here.</p>")
    if notes:
        items = []
        for note in notes:
            authorship = note.get("rationale_origin")
            label = {"human": "[human-written]",
                     "generated": "[generated, reviewed at input gate]"}.get(
                         authorship, "[authorship not recorded — review]")
            items.append(
                f'<li><strong>{esc(note["item"])}</strong>: '
                f'{esc(note["choice"])}<br>'
                f'<span class="note">{esc(note["rationale"])} {esc(label)}</span>'
                "</li>")
        body.append("<h3>Design notes</h3><ul>" + "".join(items) + "</ul>")

    if not body:
        return ""
    return section("Devices and design choices", "\n".join(body))


def render_prose(manifest: dict) -> str:
    """Last on the page, and never interleaved with computed results.

    Prose is the one place the output is entirely unverifiable, so it gets the
    strongest available framing rather than the authority of the numbers above
    it. Grounded prose prints the rows it describes FIRST, with the
    verification status those rows inherit, so a reader can check the sentence
    against them without leaving the page.
    """
    entries = manifest["prose"]
    if not entries:
        return ""
    blocks = []
    for entry in entries:
        block = [f'<h3>{esc(entry["text"])}</h3>',
                 f'<p class="note">tier: {esc(entry["tier"])}</p>']

        for note in entry.get("quoted_notes", []):
            block.append(
                f'<div class="quoted"><strong>{esc(note["item"])}</strong>: '
                f'{esc(note["choice"])}<br>{esc(note["rationale"])} '
                f'<span class="note">{esc(note["authorship"])}</span></div>')

        for group in entry.get("evidence", []):
            block.append(f'<h4>{esc(group["label"])}</h4>')
            block.append(
                '<p class="note">{} of {} rows of <span class="mono">{}</span>'
                ", computed by {}. {}</p>".format(
                    len(group["rows"]), esc(group["total_rows"]),
                    esc(group["measurement"]), esc(group["backend"]),
                    verification_badge(group["verification"])))
            if group["rows"]:
                block.append(table(
                    group["columns"],
                    [[esc(cell) for cell in row] for row in group["rows"]]))
            else:
                block.append('<p class="warn">This selection is empty: no row '
                             "matched. Stated rather than omitted.</p>")

        answer = entry.get("answer")
        if entry["tier"] == "prose_from_design" and not answer:
            # NOT a gap. This tier generates nothing by design: listing the
            # choices and their rationales IS the answer, which is what makes
            # it the most trustworthy prose on the site. Reporting it as a
            # missing answer would be a false alarm on the one part of the
            # page built to surface real ones.
            block.append('<p class="note">This tier generates no text. The '
                         "choices above, and the reasons for them, are the "
                         "answer — which is why it is the only prose here "
                         "that nothing had to write.</p>")
        elif not answer:
            block.append('<p class="warn">No answer has been written for this '
                         "ask. The evidence above is what the library has.</p>")
        else:
            freshness = entry.get("answer_freshness")
            # BEFORE the sentence, deliberately. A reader who has already read
            # it has already been misled.
            if freshness == "stale":
                block.append(
                    f'<p class="bad">{badge("STALE", "bad")} This text was '
                    "written against different evidence from the rows above. "
                    "Do not read it as a caption over them.</p>")
            elif freshness == "unknown":
                block.append(
                    f'<p class="warn">{badge("FRESHNESS UNKNOWN", "warn")} No '
                    "fingerprint was recorded for this text, so whether it "
                    "matches the rows above <strong>could not be "
                    "checked</strong>. That is not the same as fresh.</p>")
            block.append(f'<p class="answer">{esc(answer)}</p>')
            authorship = entry.get("answer_authorship")
            block.append(f'<p class="note">{esc(authorship)}</p>')
            if freshness == "fresh":
                block.append('<p class="note">A human confirmed this text '
                             "against exactly these rows. Check it against "
                             "them yourself — that is what it is for.</p>")
        blocks.append("".join(block))

    intro = ('<p class="note">These answers are prose. Nothing can verify them '
             "the way a simulator verifies a number, so each one shows what it "
             "rests on: quoted design rationales, or the computed rows it "
             "describes.</p>")
    return section("Explanations", intro + "".join(blocks))


def render_warnings(manifest: dict) -> str:
    warnings = manifest["warnings"]
    if not warnings:
        return ""
    items = "".join(f"<li>{esc(w)}</li>" for w in warnings)
    return section("Warnings from the input gate", f"<ul>{items}</ul>")


def render_question_page(manifest: dict) -> str:
    head = (
        f'<p class="note">Generated {esc(manifest["generated"])} · evaluated '
        f'by <span class="mono">{esc(manifest["backend"])}</span> · manifest '
        f'version {esc(manifest["manifest_version"])}</p>')
    tail = (
        f'<section class="note"><h2>The record</h2><p>This page is a rendering '
        f'and adds nothing to it. The record it renders is '
        f'<a href="{MANIFEST_NAME}">{MANIFEST_NAME}</a>, and the input it was '
        f'generated from is <a href="{QUESTION_NAME}">{QUESTION_NAME}</a>. '
        f"Both travel with this folder.</p></section>")
    body = "\n".join(part for part in [
        f'<p><a href="../{PAGE_NAME}">&larr; all questions</a></p>',
        f'<h1>{esc(manifest["question_id"])}</h1>',
        head,
        render_standing_notices(manifest),
        render_question_text(manifest),
        render_results(manifest),
        render_regime_checks(manifest),
        render_skipped_checks(manifest),
        render_deliverables(manifest),
        render_devices_and_choices(manifest),
        render_prose(manifest),
        render_warnings(manifest),
        tail,
    ] if part)
    return _document(manifest["question_id"], body)


# ------------------------------------------------------------- the index

_FLAGS = [
    ("has_internal_results", "internal results", "warn"),
    ("has_unreliable_results", "unreliable results", "bad"),
    ("has_violated_regimes", "violated regime", "bad"),
    ("has_skipped_checks", "skipped checks", "warn"),
    ("has_stale_prose", "prose not confirmed", "warn"),
    ("has_ungrounded_prose", "ungrounded prose", "warn"),
]


def render_index_page(index: dict) -> str:
    rows = []
    for entry in index["questions"]:
        slug = entry["question_id"]
        flags = "".join(
            badge(label, kind) + " "
            for key, label, kind in _FLAGS if entry.get(key))
        rows.append([
            f'<a href="{attr(slug)}/{PAGE_NAME}">'
            f'<span class="mono">{esc(slug)}</span></a>',
            esc(entry["backend"]),
            esc(entry["result_count"]),
            esc(entry["prose_ask_count"]),
            esc(entry["generated"]),
            flags or '<span class="note">nothing flagged</span>',
        ])
    listing = table(
        ["question", "evaluated by", "results", "prose asks", "generated",
         "before you trust this page"], rows)

    explainer = """
<p>Each entry below is one lab open ended question, solved once on a machine
with the real simulators and reviewed by a human before it was published. The
circuit file, every measured number with its provenance, and the explanation
are all here.</p>

<p><strong>This site cannot simulate.</strong> LTspice is a Windows GUI
application; it does not run on a server, and substituting a simulator that
could would answer a slightly different question with different device
models. So the generator runs locally and the site is only a window onto what
it produced. Every number here is traceable to a real simulator run.</p>

<p><strong>A question that is not listed is not solved yet.</strong> That is a
real answer rather than a failure state. There is deliberately no path here
that looks like an instant answer but is secretly unverified generation.</p>
"""

    tail = ('<section class="note"><h2>The machine-readable list</h2>'
            f'<p>The same listing, as data: <a href="{INDEX_NAME}">'
            f"{INDEX_NAME}</a>. Each question folder also carries its own "
            "manifest.</p></section>")
    return _document("ohmwork — solved questions",
                     f"<h1>ohmwork</h1>{explainer}{listing}{tail}")


# ---------------------------------------------------------------- shell

#: Inlined deliberately. A page that fetches a stylesheet or a font is a page
#: that can render differently tomorrow, and one that cannot be opened from a
#: memory stick with no network. Same reasoning as the rest of the project:
#: an artefact should not depend on something nobody can check later.
CSS = """
:root { color-scheme: light dark; }
body { max-width: 60rem; margin: 2rem auto; padding: 0 1rem;
       font: 16px/1.55 system-ui, sans-serif; }
h1 { font-size: 1.6rem; } h2 { font-size: 1.2rem; margin-top: 2.5rem; }
h3 { font-size: 1rem; margin-top: 1.6rem; } h4 { font-size: .95rem; }
.mono { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
        font-size: .9em; }
.note { opacity: .75; font-size: .9em; }
blockquote { margin: 0; padding: .6rem 1rem; border-left: 3px solid currentColor;
             opacity: .9; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin: .6rem 0; }
th, td { text-align: left; vertical-align: top; padding: .3rem .6rem;
         border-bottom: 1px solid rgba(128,128,128,.35); font-size: .92em; }
th { font-weight: 600; }
.badge { display: inline-block; padding: 0 .4rem; border-radius: .25rem;
         font-size: .78em; font-weight: 700; letter-spacing: .02em;
         border: 1px solid currentColor; }
.badge.ok { color: #1a7f37; } .badge.warn { color: #9a6700; }
.badge.bad { color: #b3261e; }
.bad { color: #b3261e; } .warn { color: #9a6700; }
.notices { border: 1px solid currentColor; padding: .2rem 1rem 1rem;
           border-radius: .4rem; }
.answer { padding: .6rem .8rem; border-left: 3px solid currentColor; }
.quoted { padding: .4rem .8rem; border-left: 3px solid currentColor;
          margin: .5rem 0; }
.hash { opacity: .6; font-size: .78em; word-break: break-all; }
ul.files { list-style: none; padding: 0; }
ul.files li { margin-bottom: 1rem; }
dl.kv { display: grid; grid-template-columns: max-content 1fr; gap: .1rem .8rem; }
dl.kv dt { font-weight: 600; } dl.kv dd { margin: 0; }
"""


def _document(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)}</title>\n<style>{CSS}</style>\n"
        f"</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


# --------------------------------------------------------------- building

def _read_manifest(directory: Path) -> dict:
    path = directory / MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ViewerError(f"{path}: cannot be read as a manifest: {exc}") from exc

    version = manifest.get("manifest_version")
    if version != MANIFEST_VERSION:
        raise ViewerError(
            f"{path}: manifest_version {version!r}, but this viewer reads "
            f"{MANIFEST_VERSION}. The version is bumped exactly when an "
            f"existing viewer could misread a manifest, so this one stops "
            f"rather than guessing.")
    try:
        validate_manifest(manifest)
    except ManifestError as exc:
        raise ViewerError(f"{path}: {exc}") from exc
    return manifest


def _copy_deliverables(manifest: dict, source: Path, destination: Path) -> None:
    """Copy each file, and re-check the hash the manifest published.

    The sha256 is the entire promise the manifest makes about a download: the
    file you get is the file that was evaluated. A site that serves bytes the
    manifest does not describe is worse than a site that serves nothing, so a
    mismatch is a refusal and not a warning.
    """
    for item in manifest["deliverables"]:
        origin = source / item["path"]
        if not origin.is_file():
            raise ViewerError(
                f"{source}: deliverable {item['path']!r} is named in the "
                f"manifest but is not on disk. The page would offer a "
                f"download that does not exist.")
        digest = hashlib.sha256(origin.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise ViewerError(
                f"{origin}: sha256 is {digest}, but the manifest published "
                f"{item['sha256']}. The file has changed since it was "
                f"evaluated, so serving it would break the one promise the "
                f"manifest makes about a download.")
        target = destination / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)


def _check_index_lists_what_is_on_disk(library: Path, on_disk: dict) -> None:
    """The published index and the folders must name the same questions.

    Only the LIST is compared, not the flags: the flags shown on the site are
    derived here from the manifests actually being rendered, so a page can
    never disagree with its own listing. A stale flag in the committed
    index.json is a library problem, caught where index.json is written. A
    stale LIST is a viewer problem, because it means links to questions the
    site does not serve.
    """
    path = library / INDEX_NAME
    if not path.is_file():
        raise ViewerError(
            f"{path}: no index. The library is the product and the index is "
            f"how a reader finds it; run the CLI with --library to write one.")
    try:
        published = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ViewerError(f"{path}: cannot be read: {exc}") from exc

    listed = {e["question_id"] for e in published.get("questions", [])}
    present = {e["question_id"] for e in on_disk["questions"]}
    if listed != present:
        raise ViewerError(
            f"{path} is stale: it lists {sorted(listed)} but the library "
            f"holds {sorted(present)}. A site built from a stale index links "
            f"to questions it does not serve, or hides ones it does.")


def build_site(library_dir, out_dir) -> list[Path]:
    """Render `library_dir` into a folder of static HTML at `out_dir`.

    Returns every path written, sorted, so a caller can report or diff them.
    Nothing here reads the clock: building twice produces identical bytes,
    for the same reason `generated` is passed into a manifest rather than
    taken from the system time. A site that churns cannot be reviewed.
    """
    library = Path(library_dir)
    out = Path(out_dir)
    if not library.is_dir():
        raise ViewerError(f"{library}: no such library directory")

    # Read every manifest FIRST, one at a time, so a rejection names the file
    # it came from. build_index validates too, but it reports the offending
    # KEY without the path, and "manifest.results[0].backend" does not tell a
    # publisher which question to go and fix.
    manifests = {}
    for path in sorted(library.glob(f"*/{MANIFEST_NAME}")):
        manifests[path.parent.name] = _read_manifest(path.parent)

    # build_index additionally refuses a directory whose name does not match
    # its question_id -- a mismatch silently resolves old links to the wrong
    # question -- and derives the flags shown on the listing.
    try:
        index = build_index(library)
    except ManifestError as exc:
        raise ViewerError(str(exc)) from exc
    _check_index_lists_what_is_on_disk(library, index)

    out.mkdir(parents=True, exist_ok=True)
    written = []

    for entry in index["questions"]:
        slug = entry["question_id"]
        source = library / slug
        destination = out / slug
        destination.mkdir(parents=True, exist_ok=True)

        manifest = manifests[slug]
        _copy_deliverables(manifest, source, destination)

        # The record travels with the rendering, so the page is auditable
        # against the manifest by anyone, without running this code.
        for name in (MANIFEST_NAME, QUESTION_NAME):
            origin = source / name
            if not origin.is_file():
                raise ViewerError(f"{source}: {name} is missing")
            shutil.copyfile(origin, destination / name)

        page = destination / PAGE_NAME
        page.write_text(render_question_page(manifest), encoding="utf-8")
        written += [page, destination / MANIFEST_NAME,
                    destination / QUESTION_NAME]
        written += [destination / d["path"] for d in manifest["deliverables"]]

    index_json = out / INDEX_NAME
    index_json.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    index_page = out / PAGE_NAME
    index_page.write_text(render_index_page(index), encoding="utf-8")
    written += [index_json, index_page]
    return sorted(written)
