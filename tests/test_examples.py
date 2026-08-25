"""Policy compliance over every example JSON in the repo.

The regression this guards against actually happened: the Q1
experiment's deliverable and all four pinned baselines were built from
a circuit carrying an unanchored zener card the device policy had
already outlawed — because the policy was applied in new code paths but
never to the existing inputs. The file emitted, simulated, converged,
and reported plausible wrong numbers.

So: every example must pass emit() (which now enforces anchoring at
the chokepoint), and the anchoring rule is additionally asserted here
directly, so a future emitter refactor cannot silently drop it.
"""

import json
from pathlib import Path

import pytest

from ohmwork.emitter import emit
from ohmwork.parts import unanchored_diode_card
from ohmwork.question import load_question

from tests.test_parts import mini_library

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.json"))


def test_there_are_examples_to_check():
    assert EXAMPLES, "examples/ has no JSON circuits; this suite is vacuous"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_validates_under_current_policy(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if "circuit" in data:
        # Question format: the input gate runs the whole chain,
        # including emit's anchored-model check on the resolved circuit.
        load_question(data, library=mini_library())
    else:
        # Resolved-circuit format: emit validates directly.
        emit(data)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_has_no_unanchored_diode_cards(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    directives = list(data.get("directives", []))
    directives += data.get("circuit", {}).get("directives", [])
    for directive in directives:
        assert not unanchored_diode_card(directive), (
            f"{path.name}: {directive!r} has BV without IBV — it puts Vz "
            "at SPICE's 1 mA default, not the datasheet test current"
        )


# ------------------------------------- the verbatim question text is evidence
#
# CLAUDE.md, "Verification limits": the coverage check compares model output
# against model output, and the ONE defence sitting outside the system is the
# verbatim question text. examples/q3.json shipped a PARAPHRASE of it for a
# while -- shorter, tidied, "using LTspice" dropped, "470 uF" written "470uF".
#
# The damage is measurable and it points the flattering way. Against the
# paraphrase the asks claimed 74% of the words; against the real text, 59%.
# A summary written alongside the asks agrees with the asks by construction,
# so the screen designed to reveal dropped work was quietly grading itself.

VERBATIM_MARKERS = {
    "q1_anchored.json": [
        "Calculate the output voltage in both load and line regulation",
        "in the regulator circuit shown below using LTspice.",
    ],
    "q3.json": [
        "Design and simulate a regulated 6.2 V DC power supply using LTspice.",
        "C = 470 \u00b5F and L = 1 mH",          # micro sign, spaced as printed
        "a 1 k\u03a9 resistive load",             # ohm sign
        "and load current waveform using LTspice.",
    ],
}


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_question_text_is_the_verbatim_wording(path):
    markers = VERBATIM_MARKERS.get(path.name)
    if markers is None:
        return
    text = json.loads(path.read_text(encoding="utf-8"))["question"]
    for marker in markers:
        assert marker in text, f"{path.name}: question text drifted from the manual"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_transcribed_questions_record_where_they_came_from(path):
    # A verbatim text with no provenance cannot be re-checked against the
    # original. Both solved examples were transcribed by hand from screenshots.
    if path.name not in VERBATIM_MARKERS:
        return
    source = json.loads(path.read_text(encoding="utf-8"))["source"]
    assert source["file"]
    assert source["extractor"]


def test_dry_run_survives_greek_characters_in_the_question():
    """The confirmation gate must not die on the manual's own notation.

    Lab manuals are full of 470 uF, 1 kOhm, beta = 100. On Windows the console
    is cp1252 and cannot encode an ohm sign, so printing a faithfully
    transcribed question raised UnicodeEncodeError and the dry run died before
    displaying anything -- on precisely the questions whose values most need
    checking by eye.
    """
    import io

    from ohmwork.question import dry_run_report

    data = json.loads((Path(__file__).parent.parent / "examples" / "q3.json")
                      .read_text(encoding="utf-8"))
    report = dry_run_report(load_question(data, library=mini_library()))
    assert "\u03a9" in report and "\u00b5" in report

    # a cp1252 stream is what the report actually meets on Windows
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252",
                              errors="backslashreplace")
    stream.write(report)          # must not raise
    stream.flush()
