"""Tests for ohmwork.parts: matching question specs to real LTspice parts.

The fixture files in tests/fixtures/ are verbatim entries copied from the
LTspice 26.0.2.1 install on this machine, encodings included: mini.dio is
plain ASCII, mini.bjt is UTF-16LE *without a BOM*, exactly like the real
standard.bjt. Both quirks are empirical facts, not choices.

Integration tests at the bottom run against the real install and skip
cleanly where LTspice's libraries are absent.
"""

from pathlib import Path

import pytest

from ohmwork.parts import (
    PartsLibrary,
    UnknownPartError,
    synthesize_zener,
)

# ---------------------------------------------------------------- policy
#
# Three ways a question can specify a zener, three different answers
# (see CLAUDE.md, "Device models"):
#   a. names a part            -> that real part, no substitution
#   b. names a parameter value -> synthesised model anchored at exactly
#                                 that value (the common lab-manual case)
#   c. vague                   -> nearest real part, substitution reported
# Whichever path, the choice is always reported, never silent.


def test_policy_a_named_part_is_used_verbatim():
    choice = mini_library().choose_zener(part="UMZ8_2T")
    assert choice.part == "UMZ8_2T"
    assert choice.directive is None  # real part: no model card needed
    assert choice.policy == "named"
    assert "UMZ8_2T" in choice.report


def test_policy_a_unknown_named_part_fails_loudly():
    with pytest.raises(UnknownPartError, match="BZV55C8V2"):
        mini_library().choose_zener(part="BZV55C8V2")


def test_policy_b_explicit_value_synthesises_anchored_model():
    choice = mini_library().choose_zener(vz=8.3)
    assert choice.policy == "synthesized"
    assert choice.directive == f".model {choice.part} D(BV=8.3 IBV=5m)"
    # The report must say what was made and how it is anchored.
    assert "8.3" in choice.report
    assert "5m" in choice.report or "5 mA" in choice.report


def test_policy_b_wins_even_when_a_nearby_real_part_exists():
    # 8.2 V parts exist in the library; an explicit 8.3 V question must
    # NOT be silently answered with an 8.2 V device.
    choice = mini_library().choose_zener(vz=8.3)
    assert choice.part not in {"BZX84C8V2L", "UMZ8_2T"}


def test_policy_c_vague_spec_takes_nearest_real_part():
    choice = mini_library().choose_zener(vz=8.3, exact=False)
    assert choice.policy == "nearest"
    assert choice.part == "BZX84C8V2L"
    assert choice.directive is None
    assert "8.2" in choice.report and "8.3" in choice.report


def test_policy_requires_some_spec():
    with pytest.raises(ValueError):
        mini_library().choose_zener()

FIXTURES = Path(__file__).parent / "fixtures"


def mini_library():
    return PartsLibrary(
        dio_path=FIXTURES / "mini.dio", bjt_path=FIXTURES / "mini.bjt"
    )


# ------------------------------------------------------------------ parsing


def test_zener_inventory_from_ascii_dio():
    zeners = {z.name: z.vpk for z in mini_library().zeners()}
    assert zeners == {
        "BZX84C6V2L": 6.2,
        "BZX84C8V2L": 8.2,
        "UMZ8_2T": 8.2,
        "1N750": 4.7,
    }


def test_non_zener_diode_excluded_despite_vpk():
    # 1N4148 has Vpk=75 but type=silicon: Vpk alone must not qualify it.
    names = [z.name for z in mini_library().zeners()]
    assert "1N4148" not in names


def test_bjt_inventory_from_utf16_bjt():
    bjts = {(b.name, b.polarity) for b in mini_library().bjts()}
    assert bjts == {
        ("2N2222", "npn"),
        ("2N3904", "npn"),
        ("2N2907", "pnp"),
    }


# ----------------------------------------------------------------- matching


def test_find_zener_picks_nearest_vpk():
    sub = mini_library().find_zener(8.3)
    assert sub.nominal == 8.2
    # Two parts tie at 8.2; the tie-break is alphabetical for determinism.
    assert sub.part == "BZX84C8V2L"
    assert not sub.exact


def test_find_zener_exact_match():
    sub = mini_library().find_zener(4.7)
    assert sub.part == "1N750"
    assert sub.exact


def test_substitution_is_reported_never_silent():
    sub = mini_library().find_zener(8.3)
    text = sub.describe()
    assert "BZX84C8V2L" in text
    assert "8.2" in text
    assert "8.3" in text  # what the question asked for must be named


def test_find_bjt_by_name():
    assert mini_library().find_bjt("npn", "2N3904") == "2N3904"


def test_find_bjt_unknown_name_fails_loudly():
    with pytest.raises(UnknownPartError, match="BC547B"):
        mini_library().find_bjt("npn", "BC547B")


def test_find_bjt_wrong_polarity_fails():
    with pytest.raises(UnknownPartError, match="2N2907"):
        mini_library().find_bjt("npn", "2N2907")


def test_find_bjt_default_is_deterministic():
    lib = mini_library()
    assert lib.find_bjt("npn") == lib.find_bjt("npn")
    assert lib.find_bjt("pnp") == "2N2907"


def test_choose_bjt_named_part():
    choice = mini_library().choose_bjt("npn", part="2N3904")
    assert choice.policy == "named"
    assert choice.part == "2N3904"
    assert choice.directive is None


def test_choose_bjt_params_synthesises():
    choice = mini_library().choose_bjt("npn", params={"BF": 100})
    assert choice.policy == "synthesized"
    assert choice.directive == f".model {choice.part} NPN(BF=100)"
    assert "BF=100" in choice.report


def test_choose_bjt_vague_takes_default_and_reports():
    choice = mini_library().choose_bjt("pnp")
    assert choice.policy == "nearest"
    assert choice.part == "2N2907"
    assert "2N2907" in choice.report


def test_choose_diode_named():
    choice = mini_library().choose_diode(part="1N4148")
    assert choice.policy == "named"
    assert choice.part == "1N4148"


def test_choose_diode_default_reports():
    # Mini fixture lacks 1N4007, so the preferred-list fallback lands
    # on 1N4148; either way the pick is reported, never silent.
    choice = mini_library().choose_diode()
    assert choice.policy == "nearest"
    assert choice.part == "1N4148"
    assert "1N4148" in choice.report


# ------------------------------------------------------------ synthesis


def test_synthesize_zener_anchors_ibv_at_test_current():
    name, directive = synthesize_zener(8.3)
    # BV must be anchored at the datasheet test current (5 mA default),
    # not left at SPICE's 1 mA default. That was the root cause of the
    # 8.34-vs-8.3 discrepancy documented in CLAUDE.md.
    assert directive == f".model {name} D(BV=8.3 IBV=5m)"
    assert name.isidentifier() or name.isalnum()


def test_synthesize_zener_custom_test_current():
    _, directive = synthesize_zener(6.2, test_current="20m")
    assert "IBV=20m" in directive


# ---------------------------------------------------- real install (skips)

real = pytest.mark.skipif(
    not PartsLibrary.locate_lib_dir(),
    reason="LTspice component libraries not installed",
)


@real
def test_real_install_has_a_rich_zener_population():
    lib = PartsLibrary.locate()
    assert len(lib.zeners()) > 100
    assert len(lib.bjts()) > 100


@real
def test_real_install_default_diode_is_a_rectifier():
    assert PartsLibrary.locate().choose_diode().part == "1N4007"


@real
def test_real_install_resolves_the_reference_question():
    lib = PartsLibrary.locate()
    sub = lib.find_zener(8.3)
    assert sub.nominal == pytest.approx(8.2)
    assert lib.find_bjt("npn", "2N3904") == "2N3904"
