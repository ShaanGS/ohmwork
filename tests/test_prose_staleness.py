"""A stored caption belongs to the rows it was written over.

THE FAILURE THIS PREVENTS. A prose_from_results answer is a sentence sitting
directly on top of computed evidence, and its whole claim to being checkable
is that the reader can compare the two without leaving the page. Store that
sentence in the question JSON — which we must, or the library stops
regenerating byte-identically — and it outlives the rows it describes. Change
a gate, re-run, and the same confident sentence now sits over different
evidence.

That is strictly worse than having no caption, because it still LOOKS
grounded. Nothing about the rendering would give it away.

So a stored answer carries a fingerprint of the evidence it was written for,
and rendering compares it against the rows as they are NOW. Three states, and
each one names itself:

    fresh    the fingerprint matches: the caption describes these rows
    STALE    it does not match: say so loudly, do not show the caption as
             though it were grounded
    unknown  no fingerprint recorded at all, so the check could not run —
             the unrun-check rule, applied one level down

The same rule applies to a HUMAN-written answer. A person's caption goes
stale exactly as readily as a model's; authorship and freshness are
independent axes and neither substitutes for the other.
"""

import copy

import pytest

from ohmwork import prose
from ohmwork.analysis import Measurement

ASK = "Discuss how it behaves"


def table_measurement(rows=None):
    return Measurement(
        name="truth_table", value=None, run="exhaustive",
        backend="logisim-evolution", source="simulation",
        verification="external",
        table={
            "columns": ["EN", "I1", "I0", "Y"],
            "rows": rows if rows is not None else [
                [0, 0, 0, 0], [0, 1, 1, 0],
                [1, 0, 1, 0], [1, 1, 0, 1], [1, 1, 1, 1],
            ],
            "notes": [],
        },
    )


def results(rows=None):
    return {"truth_table": table_measurement(rows)}


def ask_with(answer=None, fingerprint=None, origin="generated"):
    spec = {
        "tier": "prose_from_results",
        "evidence": [{"label": "enable on", "measurement": "truth_table",
                      "select": {"equals": {"EN": 1}}}],
    }
    if answer is not None:
        spec["answer"] = answer
        spec["answer_origin"] = origin
    if fingerprint is not None:
        spec["answer_evidence"] = fingerprint
    return [{"text": ASK, "kind": "prose", "prose": spec}]


def resolve(asks, rows=None):
    return prose.resolve_prose(asks, results(rows), notes=[])[0]


# ---------------------------------------------------------- fingerprints


def test_a_fingerprint_is_stable_across_calls():
    """It has to be, or every regeneration would report itself stale."""
    groups = resolve(ask_with())["evidence"]
    assert prose.evidence_fingerprint(groups) == \
        prose.evidence_fingerprint(copy.deepcopy(groups))


def test_changing_a_row_changes_the_fingerprint():
    before = prose.evidence_fingerprint(resolve(ask_with())["evidence"])
    after = prose.evidence_fingerprint(resolve(ask_with(), rows=[
        [0, 0, 0, 0], [0, 1, 1, 0],
        [1, 0, 1, 1], [1, 1, 0, 1], [1, 1, 1, 1],   # third row's Y flipped
    ])["evidence"])
    assert before != after


def test_selecting_different_rows_changes_the_fingerprint():
    """Same table, different selection: the caption was written over one of
    them and is not automatically true of the other."""
    asks = ask_with()
    base = prose.evidence_fingerprint(resolve(asks)["evidence"])
    asks[0]["prose"]["evidence"][0]["select"] = {"equals": {"EN": 0}}
    assert prose.evidence_fingerprint(resolve(asks)["evidence"]) != base


def test_the_backend_is_not_part_of_the_fingerprint():
    """A caption describes ROWS. If the same rows arrive from a different
    evaluator the sentence is still true of them — what changed is their
    standing, and the provenance line is rendered live on every run, so it
    cannot itself go stale. Invalidating here would cry wolf."""
    groups = resolve(ask_with())["evidence"]
    moved = copy.deepcopy(groups)
    moved[0]["backend"] = "ohmwork-logic"
    moved[0]["verification"] = "internal"
    assert prose.evidence_fingerprint(moved) == \
        prose.evidence_fingerprint(groups)


# ------------------------------------------------------------- freshness


def test_a_matching_fingerprint_reports_fresh():
    groups = resolve(ask_with())["evidence"]
    entry = resolve(ask_with("It behaves.",
                             prose.evidence_fingerprint(groups)))
    assert entry["answer_freshness"] == "fresh"


def test_a_mismatched_fingerprint_reports_stale():
    entry = resolve(ask_with("It behaves.", "sha256:deadbeef"))
    assert entry["answer_freshness"] == "stale"


def test_no_fingerprint_reports_unknown_not_fresh():
    """The unrun-check rule one level down: a check that could not run must
    never be reported as one that passed."""
    entry = resolve(ask_with("It behaves."))
    assert entry["answer_freshness"] == "unknown"


def test_an_ask_with_no_answer_has_no_freshness_claim():
    assert resolve(ask_with())["answer_freshness"] is None


def test_a_human_written_answer_goes_stale_the_same_way():
    """Authorship and freshness are independent. A person's caption over the
    wrong rows is exactly as wrong as a model's."""
    entry = resolve(ask_with("I checked this myself.", "sha256:deadbeef",
                             origin="human"))
    assert entry["answer_authorship"] == "[human-written]"
    assert entry["answer_freshness"] == "stale"


# ------------------------------------------------------------- rendering


def rendered(asks, rows=None):
    return prose.render_prose_section(asks, results(rows), notes=[])


def test_a_stale_answer_is_flagged_loudly_and_not_shown_as_grounded():
    text = rendered(ask_with("The highest input wins.", "sha256:deadbeef"))
    assert "STALE" in text
    assert "different evidence" in text.lower()
    # the sentence is still shown -- hiding it would lose information -- but
    # it must not carry the label that says "check it against the rows above"
    assert "The highest input wins." in text
    assert "check it against the rows above" not in text


def test_an_unfingerprinted_answer_says_the_check_could_not_run():
    text = rendered(ask_with("The highest input wins."))
    assert "not recorded" in text.lower()
    assert "STALE" not in text          # unknown is not the same as stale


def test_a_fresh_answer_renders_normally():
    groups = resolve(ask_with())["evidence"]
    text = rendered(ask_with("The highest input wins.",
                             prose.evidence_fingerprint(groups)))
    assert "STALE" not in text
    assert "check it against the rows above" in text


def test_staleness_survives_the_round_trip_into_the_manifest():
    """The site serves the manifest. A caption that is stale in the terminal
    and silently fresh on the published page would be the worst of both."""
    entry = resolve(ask_with("It behaves.", "sha256:deadbeef"))
    assert entry["answer_freshness"] == "stale"
    assert set(entry) >= {"answer", "answer_authorship", "answer_freshness"}


# ------------------------------------------------ the gate rejects nonsense


def test_a_fingerprint_on_a_design_tier_ask_is_rejected():
    """Design-tier asks quote notes; there are no rows to be stale against,
    so a fingerprint there is a category error, not a harmless extra."""
    asks = [{"text": "Explain", "kind": "prose", "prose": {
        "tier": "prose_from_design", "notes": ["a"],
        "answer_evidence": "sha256:deadbeef"}}]
    with pytest.raises(prose.ProseError, match="answer_evidence"):
        prose.validate_prose_asks(
            asks, measurements=[], notes=[{"item": "a"}])


def test_a_fingerprint_without_an_answer_is_rejected():
    asks = ask_with(fingerprint="sha256:deadbeef")
    with pytest.raises(prose.ProseError, match="answer_evidence"):
        prose.validate_prose_asks(asks, measurements=["truth_table"], notes=[])


def test_a_change_outside_the_selection_does_not_invalidate():
    """The fingerprint is scoped to the EVIDENCE, not to the whole table.

    A caption claims things about the rows printed under it. A row the filter
    never selected is not one of those, so changing it must not mark the
    caption stale — a staleness check that fires on unrelated edits gets
    ignored, and then it does not fire on the edits that matter.
    """
    asks = ask_with("Every enabled row shown reads Y = 1.")
    # the ask selects EN=1; perturb an EN=0 row, which it never shows
    changed = [
        [0, 0, 0, 1], [0, 1, 1, 0],            # first row's Y flipped
        [1, 0, 1, 0], [1, 1, 0, 1], [1, 1, 1, 1],
    ]
    before = prose.evidence_fingerprint(resolve(asks)["evidence"])
    after = prose.evidence_fingerprint(resolve(asks, rows=changed)["evidence"])
    assert before == after


def test_a_change_inside_the_selection_does_invalidate():
    """The other half, so the pair reads as a discrimination rather than as
    a check that happens never to fire."""
    asks = ask_with("Every enabled row shown reads Y = 1.")
    changed = [
        [0, 0, 0, 0], [0, 1, 1, 0],
        [1, 0, 1, 1], [1, 1, 0, 1], [1, 1, 1, 1],   # an EN=1 row's Y flipped
    ]
    before = prose.evidence_fingerprint(resolve(asks)["evidence"])
    after = prose.evidence_fingerprint(resolve(asks, rows=changed)["evidence"])
    assert before != after
