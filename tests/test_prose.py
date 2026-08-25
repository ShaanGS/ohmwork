"""Executable spec for ohmwork.prose: asks no measurement can answer.

## The problem

Q2 asks "Explain your design choices" and "Discuss how your circuit behaves
when multiple inputs are active and when the enable signal is disabled". No
measurement can answer either. Warning about them as unmapped forever is
wrong -- they were not dropped, they are genuinely prose -- but the fix is
NOT "add kind 'prose' and print model text". Prose is the one place the
tool's output is entirely unverifiable, so it needs the strongest available
framing.

## The key idea: grounding does not make prose verified, it makes it
## locally falsifiable

"Discuss how your circuit behaves when multiple inputs are active" is
answerable FROM the truth table: select the rows where two or more inputs are
high, print them, and the prose becomes a caption over a computed selection.
The reader can check the sentence against the evidence WITHOUT LEAVING THE
PAGE. That is strictly better than free generation, and it is the most we can
offer, so the design maximises how often it applies.

## Three tiers, in descending order of trust

  prose_from_design  - answered by quoting design_notes rationales. ZERO
                       generation: listing the choices and their rationales
                       IS the explanation. NOTE what makes this trustworthy:
                       NOT that a human authored the rationale (most are
                       model-written -- examples/q3.json's RS=220 rationale
                       already is), but that a human REVIEWED it at the
                       dry-run gate. So authorship is recorded and rendered
                       honestly: [human-written] vs [generated, reviewed at
                       input gate].
  prose_from_results - computed evidence rows + a caption over them. The rows
                       are pinnable baselines; only the connecting sentences
                       are written, and they sit directly beneath the rows
                       they describe. GROUNDING IS A CHAIN AND ITS WEAKEST
                       LINK MUST BE VISIBLE: "locally falsifiable" means the
                       reader can check the caption against the rows, NOT
                       that the rows are trustworthy. So every evidence group
                       renders the verification status of its source,
                       inherited from the Measurement that produced it.
  prose_free         - nothing in the run supports it. Allowed, but labeled
                       hardest, and the dry run counts these so the human
                       sees how much unverifiable text is coming.

## Architecture consequence

Prose text cannot live in the question JSON as an EXTRACTED field: extraction
happens before simulation, so it cannot cite results that do not exist yet.
The JSON declares only the GROUNDING CONTRACT (which measurement, which rows,
which notes). A hand-written `answer` IS accepted, because every question so
far was completed by hand, and it carries `answer_origin` for the same reason
`rationale_origin` exists -- absent authorship is never assumed human.

## TWO SHAPE CHANGES FROM THE ORIGINAL SPEC, and why

This file was written as a design document before ohmwork/prose.py existed.
Two things in it did not survive contact with the question it was designed
for, and both are recorded rather than quietly amended:

1. **One filter per evidence group could not express Q2's own first group.**
   The original shape was `filter: {"kind": "min_high", ...}` -- exactly one
   predicate. But "multiple inputs active" in Q2 means multiple inputs active
   AND ENABLE ON; the ask draws its whole distinction between that case and
   the enable-off case. A bare min_high over all 32 rows selects 22, twelve
   of which are disabled rows that belong to the OTHER half of the same
   sentence. The evidence would have contradicted the caption. So `select` is
   now a conjunction: a dict of filter-kind -> params, all ANDed. The
   vocabulary is still closed (three kinds), which was the actual point --
   deliberately not an expression language, because the arithmetic
   evaluator's whitelist is a feature and a second looser evaluator would
   undo it.

2. **`tables` + `sources` collapsed into `results`.** The original passed the
   rows and their provenance as two separate arguments, which is two places
   for the same fact to live and one opportunity for them to disagree about
   which backend produced which rows. A Measurement already carries the
   table, the backend and the verification status together.
"""

import pytest

from ohmwork import prose
from ohmwork.analysis import Measurement


# ------------------------------------------------------------- fixtures


def _table_measurement(verification="internal", rows=None):
    """A 5-input priority-encoder truth table, abbreviated: enough rows to
    exercise every filter. Columns are EN I3 I2 I1 I0 | Y1 Y0 V."""
    return Measurement(
        name="truth_table", value=None, run="exhaustive",
        backend="ohmwork-logic" if verification == "internal"
        else "logisim-evolution",
        source="simulation", verification=verification,
        table={
            "columns": ["EN", "I3", "I2", "I1", "I0", "Y1", "Y0", "V"],
            "rows": rows if rows is not None else [
                [0, 1, 1, 1, 1, 0, 0, 0],   # disabled, all inputs high
                [0, 0, 0, 0, 0, 0, 0, 0],   # disabled, no inputs
                [1, 0, 0, 0, 0, 0, 0, 0],   # enabled, none active
                [1, 0, 0, 0, 1, 0, 0, 1],   # I0 only
                [1, 0, 0, 1, 0, 0, 1, 1],   # I1 only
                [1, 0, 1, 1, 0, 1, 0, 1],   # I2 wins over I1
                [1, 1, 0, 1, 1, 1, 1, 1],   # I3 wins over I1, I0
            ],
            "notes": [],
        },
    )


def results(verification="internal"):
    return {"truth_table": _table_measurement(verification)}


def table():
    return _table_measurement().table


def design_notes():
    return [
        {"item": "priority order", "choice": "I3 highest, I0 lowest",
         "rationale": "conventional MSB-first priority; the question "
                      "does not state an order",
         "rationale_origin": "generated"},
        {"item": "enable gating", "choice": "AND enable into all outputs",
         "rationale": "EN=0 forces Y1=Y0=V=0, so a disabled encoder is "
                      "unambiguous",
         "rationale_origin": "human"},
    ]


DISCUSS = ("Discuss how your circuit behaves when multiple inputs are "
           "active and when the enable signal is disabled")


def prose_asks():
    return [
        {"text": "Explain your design choices", "kind": "prose",
         "prose": {"tier": "prose_from_design",
                   "notes": ["priority order", "enable gating"]}},
        {"text": DISCUSS, "kind": "prose",
         "prose": {"tier": "prose_from_results", "evidence": [
             {"label": "multiple inputs active",
              "measurement": "truth_table",
              "select": {"equals": {"EN": 1},
                         "min_high": {"columns": ["I3", "I2", "I1", "I0"],
                                      "count": 2}}},
             {"label": "enable disabled",
              "measurement": "truth_table",
              "select": {"equals": {"EN": 0}}},
         ]}},
    ]


# ---------------------------------------------------------- row selection
# Closed filter vocabulary -- three kinds cover every ask in all four sample
# questions -- combined by conjunction. Deliberately NOT an expression
# language.


def test_filter_equals():
    rows = prose.select_rows(table(), {"equals": {"EN": 0}})
    assert len(rows) == 2
    assert all(r[0] == 0 for r in rows)


def test_filter_min_high():
    rows = prose.select_rows(table(), {
        "min_high": {"columns": ["I3", "I2", "I1", "I0"], "count": 2}})
    # 2+ inputs high: the all-high disabled row, I2-over-I1, I3-over-I1-and-I0
    assert len(rows) == 3


def test_filters_combine_by_conjunction():
    """The change that forced the shape: Q2's first evidence group is
    "multiple inputs active AND enable on", and the ask's whole point is the
    contrast with enable off. One predicate could not say it."""
    both = prose.select_rows(table(), {
        "equals": {"EN": 1},
        "min_high": {"columns": ["I3", "I2", "I1", "I0"], "count": 2}})
    assert len(both) == 2                       # the disabled all-high row is out
    assert all(r[0] == 1 for r in both)


def test_filter_value_range_reads_columns_as_msb_first_number():
    # Q4's shape: D C B A as a 4-bit number, invalid BCD is 10..15.
    rows = prose.select_rows(table(), {
        "value_range": {"columns": ["I3", "I2", "I1", "I0"],
                        "min": 10, "max": 15}})
    # 1111 = 15 and 1011 = 11 qualify; 0110 = 6 does not.
    assert len(rows) == 2


def test_an_empty_select_takes_every_row():
    assert len(prose.select_rows(table(), {})) == 7


def test_filter_unknown_kind_rejected():
    with pytest.raises(prose.ProseError, match="wobble"):
        prose.select_rows(table(), {"wobble": {}})


def test_filter_unknown_column_rejected():
    with pytest.raises(prose.ProseError, match="ENABLE"):
        prose.select_rows(table(), {"equals": {"ENABLE": 0}})


def test_empty_selection_is_reported_not_silent():
    # A filter matching nothing means the prose has NO evidence; that must
    # surface rather than producing a confident empty caption.
    assert prose.select_rows(table(), {"equals": {"V": 7}}) == []


# ------------------------------------------------------------ validation


def test_evidence_referencing_unknown_measurement_is_an_error():
    asks = prose_asks()
    asks[1]["prose"]["evidence"][0]["measurement"] = "no_such_table"
    with pytest.raises(prose.ProseError, match="no_such_table"):
        prose.validate_prose_asks(asks, measurements=["truth_table"],
                                  notes=design_notes())


def test_evidence_referencing_unknown_note_is_an_error():
    asks = prose_asks()
    asks[0]["prose"]["notes"] = ["nonexistent choice"]
    with pytest.raises(prose.ProseError, match="nonexistent choice"):
        prose.validate_prose_asks(asks, measurements=["truth_table"],
                                  notes=design_notes())


def test_an_unknown_tier_is_an_error():
    asks = prose_asks()
    asks[0]["prose"]["tier"] = "prose_from_vibes"
    with pytest.raises(prose.ProseError, match="prose_from_vibes"):
        prose.validate_prose_asks(asks, measurements=["truth_table"],
                                  notes=design_notes())


def test_a_design_tier_ask_must_name_notes():
    asks = prose_asks()
    asks[0]["prose"].pop("notes")
    with pytest.raises(prose.ProseError, match="notes"):
        prose.validate_prose_asks(asks, measurements=["truth_table"],
                                  notes=design_notes())


def test_a_results_tier_ask_must_carry_evidence():
    asks = prose_asks()
    asks[1]["prose"].pop("evidence")
    with pytest.raises(prose.ProseError, match="evidence"):
        prose.validate_prose_asks(asks, measurements=["truth_table"],
                                  notes=design_notes())


def test_valid_prose_asks_pass():
    prose.validate_prose_asks(prose_asks(), measurements=["truth_table"],
                              notes=design_notes())


def test_prose_asks_are_covered_not_unmapped():
    # The coverage section must treat a declared prose ask as answered, not
    # warn about it forever.
    assert prose.is_prose({"text": "x", "kind": "prose",
                           "prose": {"tier": "prose_free"}})
    assert not prose.is_prose({"text": "x"})


# ------------------------------------------------------------- rendering


def rendered(answers=None, verification="internal", asks=None):
    return prose.render_prose_section(
        asks or prose_asks(), results(verification),
        notes=design_notes(), answers=answers or {})


def test_section_header_carries_the_warning():
    text = rendered()
    assert "GENERATED TEXT" in text
    assert "not verified" in text.lower()


def test_design_tier_quotes_rationales_and_generates_nothing():
    text = rendered()
    assert "conventional MSB-first priority" in text
    assert "[quoted from design notes" in text


def test_design_tier_labels_each_rationale_by_authorship():
    # Trust comes from review at the gate, not from authorship, so both
    # labels appear and neither is dressed up as the other.
    text = rendered()
    assert "[human-written]" in text                       # enable gating
    assert "[generated, reviewed at input gate]" in text   # priority order


def test_results_tier_names_its_evidence_verification():
    # Grounding inherits the status of what it is grounded in.
    text = rendered()
    assert "ohmwork-logic" in text
    assert "INTERNAL" in text
    assert "no external" in text.lower()


def test_external_evidence_is_named_as_external_not_merely_unwarned():
    """Q2's evidence comes from Logisim, so it is the first prose in this
    project standing on externally computed rows. That is a real difference
    in standing and the rendering must SAY it, not just omit the warning."""
    text = rendered(verification="external")
    assert "no external" not in text.lower()
    assert "logisim-evolution" in text
    assert "EXTERNAL" in text
    assert "outside" in text.lower()


def test_results_tier_prints_evidence_rows_before_any_prose():
    text = rendered({DISCUSS: "The highest-index active input wins."})
    section = text[text.index("multiple inputs active"):]
    # Column header and at least one data row appear...
    assert "EN" in section and "Y1" in section
    # ...before the caption they support.
    assert section.index("Y1") < section.index("highest-index")


def test_generated_prose_is_labeled_and_points_at_its_evidence():
    text = rendered({DISCUSS: "The highest-index active input wins."})
    assert "[generated" in text
    assert "check it against" in text


def test_hand_written_answer_is_labeled_differently():
    text = prose.render_prose_section(
        [{"text": "Speculate about temperature", "kind": "prose",
          "prose": {"tier": "prose_free", "answer": "Written by me.",
                    "answer_origin": "human"}}],
        results={}, notes=[], answers={})
    assert "[human-written]" in text
    assert "[generated" not in text


def test_an_answer_with_no_recorded_authorship_is_never_assumed_human():
    """The rationale_origin rule, applied to prose: absent authorship is the
    unfounded trust being removed, not a default of 'a person wrote it'."""
    text = prose.render_prose_section(
        [{"text": "Speculate", "kind": "prose",
          "prose": {"tier": "prose_free", "answer": "Some text."}}],
        results={}, notes=[], answers={})
    assert "[authorship not recorded" in text
    assert "[human-written]" not in text


def test_missing_answer_says_so_rather_than_inventing():
    assert "no answer generated" in rendered(answers={}).lower()


def test_free_tier_is_labeled_hardest():
    text = prose.render_prose_section(
        [{"text": "Speculate about temperature effects", "kind": "prose",
          "prose": {"tier": "prose_free"}}],
        results={}, notes=[],
        answers={"Speculate about temperature effects": "It gets warm."})
    assert "ungrounded" in text
    assert "nothing in this run" in text


def test_empty_evidence_is_stated_in_the_output():
    asks = prose_asks()
    asks[1]["prose"]["evidence"] = [{
        "label": "impossible case", "measurement": "truth_table",
        "select": {"equals": {"V": 7}}}]
    assert "no rows matched" in rendered(asks=asks).lower()


def test_a_measurement_with_no_table_is_refused_at_render_time():
    """An evidence group pointing at a scalar has no rows to show, and a
    caption over nothing is exactly what this design exists to prevent."""
    scalar = Measurement(name="vout", value=7.5, run="op",
                         backend="ltspice", source="simulation")
    with pytest.raises(prose.ProseError, match="vout"):
        prose.render_prose_section(
            [{"text": "x", "kind": "prose", "prose": {
                "tier": "prose_from_results",
                "evidence": [{"label": "l", "measurement": "vout",
                              "select": {}}]}}],
            results={"vout": scalar}, notes=[], answers={})


# ---------------------------------------------------------- dry-run preview


def test_dry_run_preview_says_how_each_ask_will_be_answered():
    # Before anything runs, the human should see what kind of answer each ask
    # will get -- computed, quoted, or generated.
    preview = prose.preview(prose_asks())
    assert "quoted, not generated" in preview
    assert "computed evidence + generated caption" in preview


def test_dry_run_counts_ungrounded_asks():
    preview = prose.preview(
        prose_asks() + [{"text": "Speculate", "kind": "prose",
                         "prose": {"tier": "prose_free"}}])
    assert "1" in preview and "ungrounded" in preview


def test_preview_of_no_prose_asks_is_empty():
    assert prose.preview([{"text": "x", "answered_by": "vout"}]) == ""
