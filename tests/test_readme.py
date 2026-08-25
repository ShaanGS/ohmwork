"""The README's numbers, checked against the registry rather than re-read.

CLAUDE.md's README spec says: "check every number in it against
tests/baselines.py rather than retyping from memory." Doing that by reading
carefully is exactly the move that has lost to a mechanical check four times
on this project, so it is a test instead.

The README is public and it is the artefact people actually read. A number in
it that has quietly drifted from what the tool now produces is worse than no
number, because it reads as evidence.
"""

from pathlib import Path

import pytest

from tests import baselines

README = Path(__file__).parent.parent / "README.md"

pytestmark = pytest.mark.skipif(not README.is_file(), reason="no README yet")


def text():
    """The README with runs of whitespace flattened.

    Every phrase searched for here is prose, and prose in a README is
    hard-wrapped. Searching the raw text made "our own evaluator" fail purely
    because a newline fell between the second and third word -- a false
    negative on a check whose whole job is catching real omissions.
    """
    import re
    return re.sub(r"\s+", " ", README.read_text(encoding="utf-8"))


def renderings(value):
    """How a value may legitimately appear: the report's 6-significant-figure
    form, or the full measured value. The README quoting more digits than the
    report is fine; quoting DIFFERENT digits is not."""
    return {f"{value:.6g}", repr(value), f"{value}"}


# The claims the worked example makes, each tied to its Baseline. Adding a
# number to the README means adding it here, which is the point.
CLAIMS = [
    (baselines.VOUT_ANCHORED.value, "output voltage at nominal load"),
    (baselines.VB_ANCHORED.value, "base node vb"),
    (abs(baselines.IZ_ANCHORED.value) * 1000, "zener current in mA"),
    (baselines.LINE_REG_ANCHORED.value, "line regulation %"),
    (baselines.LOAD_REG_ANCHORED.value, "load regulation %"),
    (baselines.VB_NOLOAD.value, "vb at no load"),
    (baselines.VB_FULLLOAD.value, "vb at full load"),
    (baselines.VOUT_NOLOAD.value, "vout at no load"),
    (baselines.VOUT_FULLLOAD.value, "vout at full load"),
    (baselines.VB_ANCHORED_NGSPICE.value, "ngspice's anchored vb"),
]


@pytest.mark.parametrize("value,what", CLAIMS,
                         ids=[c[1].replace(" ", "_") for c in CLAIMS])
def test_every_quoted_measurement_matches_its_baseline(value, what):
    body = text()
    options = renderings(value)
    assert any(option in body for option in options), (
        f"README does not quote the current baseline for {what} "
        f"(any of {sorted(options)}); it may have drifted"
    )


def test_the_load_regulation_mechanism_claim_is_arithmetic_not_memory():
    """The README says vout moves 133 times further than vb across the load
    range. That ratio is the actual learning content of Q1, so it must follow
    from the pinned numbers rather than from a recollection of an ngspice run
    three device policies ago."""
    d_vb = abs(baselines.VB_NOLOAD.value - baselines.VB_FULLLOAD.value)
    d_vout = abs(baselines.VOUT_NOLOAD.value - baselines.VOUT_FULLLOAD.value)
    assert f"{d_vout / d_vb:.0f} times further" in text()
    assert f"**{d_vout * 1000:.0f} mV**" in text()


def test_the_cross_simulator_agreement_claim_is_arithmetic():
    gap = abs(baselines.VB_ANCHORED.value - baselines.VB_ANCHORED_NGSPICE.value)
    assert f"{gap * 1e6:.0f} µV apart" in text()


def test_the_truth_table_row_count_matches_the_pinned_table():
    assert f"{len(baselines.Q2_TRUTH_TABLE.rows)} rows" in text()


def test_the_backend_versions_are_named_not_implied():
    """A number with no version behind it cannot be re-run by a reader."""
    body = text()
    assert "LTspice 26.0.2.1" in body
    assert "Logisim Evolution 4.1.0" in body


def test_the_cannot_verify_section_covers_every_known_limit():
    """The README spec lists these explicitly and says do not soften them.
    Each is a limit that a reader could otherwise mistake for a guarantee."""
    body = text().lower()
    for phrase, limit in [
        ("no machine check of any kind", ".plt has no verification path"),
        ("misread question", "a wrong-but-consistent circuit simulates fine"),
        ("self-consistency, not correctness", "the round trip's real limit"),
        ("own evaluator", "the internal digital fallback"),
        ("prose is not verified", "prose"),
    ]:
        assert phrase in body, f"the 'cannot verify' section dropped: {limit}"


def test_the_seeded_library_table_matches_what_is_published():
    import json

    library = Path(__file__).parent.parent / "library"
    if not library.is_dir():
        pytest.skip("no library generated yet")
    body = text()
    for manifest in library.glob("*/manifest.json"):
        question_id = json.loads(
            manifest.read_text(encoding="utf-8"))["question_id"]
        assert question_id in body, (
            f"{question_id} is published but the README does not list it"
        )


def test_no_number_in_the_readme_uses_the_void_unanchored_baselines():
    """vout=7.9392 and vb=8.749 came from the outlawed D(BV=8.3) card and are
    VOID (CLAUDE.md, "Why anchored models matter"). They are recognisable, and
    they must never reappear as though they were results."""
    body = text()
    for void in ("7.9392", "7.939", "8.749", "8.340", "0.4069", "1.7434"):
        assert void not in body, (
            f"{void} is a measurement from the outlawed unanchored device "
            f"card and is void; it must not be quoted as a result"
        )
