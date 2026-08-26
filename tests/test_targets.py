"""Each target owns its own checks, vocabulary, and round trip.

The regression this prevents is the one the Q2 probe exposed: a gate whose
checks were LTspice-SEMANTIC while pretending to be general. A Logisim
circuit was rejected for having no SPICE ground net, and told its components
were "not in the verified pin table" — meaning LTspice's table.
"""

import copy
import json
from pathlib import Path

import pytest

from ohmwork.question import QuestionError, load_question
from ohmwork.targets import (LogisimTarget, LTspiceTarget, TargetError,
                             UnknownTargetError, check_component_types,
                             get_target, valid_pins)

EXAMPLES = Path(__file__).parent.parent / "examples"


def q2_draft():
    return json.loads((EXAMPLES / "q2.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------ selection

def test_default_target_is_ltspice():
    # Every question written before targets existed is an LTspice one.
    assert get_target(None).name == "ltspice"
    assert isinstance(get_target(None), LTspiceTarget)


def test_logisim_target_selectable_by_either_name():
    assert isinstance(get_target("logisim"), LogisimTarget)
    assert isinstance(get_target("logisim-2.7.1"), LogisimTarget)


def test_unknown_target_is_rejected_by_name():
    with pytest.raises(UnknownTargetError) as excinfo:
        get_target("verilog")
    assert "verilog" in str(excinfo.value)


def test_question_declaring_an_unknown_target_is_rejected_at_the_gate():
    data = q2_draft()
    data["target"] = "quartus"
    with pytest.raises(QuestionError) as excinfo:
        load_question(data)
    assert "unknown target" in str(excinfo.value)


# ------------------------------------------- the LTspice semantics move

def test_ground_is_an_LTSPICE_rule_not_a_universal_one():
    assert LTspiceTarget.requires_ground is True
    assert LogisimTarget.requires_ground is False


def test_device_policy_is_an_LTSPICE_rule_not_a_universal_one():
    # Gates take neither value nor part, so the policy is a no-op for them.
    assert LTspiceTarget.uses_device_policy is True
    assert LogisimTarget.uses_device_policy is False


def test_a_logisim_question_is_no_longer_rejected_for_lacking_a_ground():
    # It must fail LATER, on something real, not on a SPICE reference node.
    data = q2_draft()
    assert "0" not in data["circuit"]["nets"]
    try:
        load_question(data)
    except QuestionError as e:
        assert "ground" not in str(e).lower(), e


def test_unknown_types_are_reported_in_the_targets_vocabulary():
    logisim, ltspice = get_target("logisim"), get_target("ltspice")
    comps = [{"ref": "G1", "type": "nand2"}]

    digital = "; ".join(check_component_types(logisim, comps))
    assert "Logisim component" in digital
    assert "and2" in digital                      # lists what IT knows
    assert "zener" not in digital                 # never LTspice's table

    analog = "; ".join(check_component_types(ltspice, [{"ref": "D1", "type": "and2"}]))
    assert "LTspice pin table" in analog
    assert "zener" in analog


def test_each_target_knows_its_own_pin_names():
    assert get_target("ltspice").pin_names("res") == ["a", "b"]
    assert get_target("logisim").pin_names("and2") == ["in0", "in1", "out"]
    assert get_target("logisim").pin_names("input_pin") == ["pin"]


def test_logisim_pin_names_route_through_the_measured_table():
    # or4's inputs are measured; a 3-input OR is not, and asking must fail
    # rather than interpolate.
    from ohmwork.logisim_symbols import UnmeasuredGeometryError
    target = get_target("logisim")
    assert len(target.pin_names("or4")) == 5
    target.TYPE_MAP["or3_probe"] = ("OR Gate", {"inputs": "3"})
    try:
        with pytest.raises(UnmeasuredGeometryError):
            target.pin_names("or3_probe")
    finally:
        del target.TYPE_MAP["or3_probe"]


def test_valid_pins_uses_the_targets_table():
    comps = [{"ref": "G1", "type": "and2"}, {"ref": "I0", "type": "input_pin"}]
    assert valid_pins(get_target("logisim"), comps) == {
        "G1.in0", "G1.in1", "G1.out", "I0.pin"}


# ----------------------------------------------------- the label rule

def test_emitted_labels_must_be_vhdl_safe():
    """Not a convention: a hard validation error.

    Logisim rewrites an unsafe label and appends a hash we cannot reproduce,
    so a label we emit that triggers it becomes unmatchable in our own
    results. Reading a foreign file we prefix-match around it; emitting one,
    we must not produce it.
    """
    target = get_target("logisim")
    assert target.check_labels({"components": [{"ref": "EN"}]}) == []
    assert target.check_labels(
        {"components": [{"ref": "G1", "label": "OUT_1"}]}) == []

    problems = target.check_labels(
        {"components": [{"ref": "G1", "label": "OUT 1"}]})
    assert len(problems) == 1
    assert "VHDL-safe" in problems[0] and "unreproducible hash" in problems[0]


def test_a_bad_label_is_rejected_at_the_gate():
    data = q2_draft()
    data["circuit"]["components"][0]["label"] = "I 3"
    with pytest.raises(QuestionError) as excinfo:
        load_question(data)
    assert "VHDL-safe" in str(excinfo.value)


def test_ltspice_has_no_label_rule():
    assert get_target("ltspice").check_labels({"components": [{"ref": "R 1"}]}) == []


# ------------------------------------------------------- the round trip

def test_ltspice_round_trip_runs_and_says_so():
    from tests.test_emitter import reference_circuit
    trip = get_target("ltspice").round_trip(reference_circuit())
    assert trip.ran is True


def test_ltspice_round_trip_surfaces_emitter_errors_as_target_errors():
    with pytest.raises(TargetError):
        get_target("ltspice").round_trip(
            {"components": [{"ref": "R1", "type": "res", "value": "1k"}],
             "nets": {"a": ["R1.a", "R1.b"]}, "directives": []})


def test_logisim_round_trip_reports_that_it_did_not_run():
    """An unrun check must never look like a passed one.

    The emitter now exists; the .circ geometric parser is deferred to v1.1
    with check-mine mode, so there is still no round trip. Saying so is the
    whole point: silence here would be indistinguishable from success. The
    reason also has to name what stands in its place, because Logisim
    evaluating the emitted file is a STRONGER check than a round trip through
    our own parser, and a reader who sees only "did not run" would conclude
    the opposite.
    """
    trip = get_target("logisim").round_trip({"components": [], "nets": {}})
    assert trip.ran is False
    assert "geometric parser is not built" in trip.reason
    assert "geometry was not" in trip.reason
    assert "Logisim itself evaluates the emitted file" in trip.reason


# ------------------------------------------- the two small gate fixes

def test_a_prose_ask_is_not_reported_as_dropped_work():
    """The false alarm was the worst kind of bug on that screen.

    Two of Q2's four asks can never map to a measurement. Warning about them
    forever trains the reader to skip the one line designed to catch real
    drops.
    """
    data = q2_draft()
    prose = [a for a in data["asks"] if a.get("kind") == "prose"]
    assert len(prose) == 2

    from ohmwork.question import _coverage_warnings
    warnings = _coverage_warnings(None, data["asks"])
    assert warnings == []

    # ...while a genuinely unmapped MEASUREMENT ask still warns
    noisy = copy.deepcopy(data["asks"])
    noisy[0].pop("answered_by")
    assert any("may have dropped it" in w for w in _coverage_warnings(None, noisy))


def test_a_prose_ask_may_not_claim_a_measurement():
    data = q2_draft()
    for ask in data["asks"]:
        if ask.get("kind") == "prose":
            ask["answered_by"] = "truth_table"
            break
    with pytest.raises(QuestionError) as excinfo:
        load_question(data)
    assert "that is what makes it prose" in str(excinfo.value)


def test_origin_is_rejected_on_a_component_with_no_value_or_part():
    data = q2_draft()
    data["circuit"]["components"][0]["origin"] = "designed"
    data["circuit"]["components"][0]["rationale"] = "because"
    with pytest.raises(QuestionError) as excinfo:
        load_question(data)
    assert "does not apply" in str(excinfo.value)
    assert "design_notes" in str(excinfo.value)


def test_origin_still_applies_where_a_value_exists():
    from ohmwork.question import carries_a_value
    assert carries_a_value({"type": "res"})
    assert carries_a_value({"type": "zener"})
    assert not carries_a_value({"type": "and2"})
    assert not carries_a_value({"type": "input_pin"})


def test_labels_differing_only_by_case_are_rejected():
    """MEASURED, and it cost three design attempts to find.

    A circuit with inputs A, B, C, D and outputs a, b, c, d came back from
    Logisim's --tty table with columns A, B, C, D, x, y, z, u: the clashing
    outputs silently renamed to letters nobody chose. Nothing in the file
    says it happened, and the signal is unmatchable in the results -- the
    same class of hazard as the VHDL rewrite, and invisible in the same way.
    """
    from ohmwork.targets import get_target

    target = get_target("logisim")
    problems = target.check_labels({"components": [
        {"ref": "A", "type": "input_pin"},
        {"ref": "a", "type": "output_pin"},
    ]})

    assert problems, "a case-only clash must be refused"
    assert "only by case" in problems[0]
    assert "x, y, z, u" in problems[0], "say what Logisim actually did"


def test_labels_that_differ_by_more_than_case_are_fine():
    from ohmwork.targets import get_target

    target = get_target("logisim")
    assert target.check_labels({"components": [
        {"ref": "A", "type": "input_pin"},
        {"ref": "Qa", "type": "output_pin"},
        {"ref": "SEG_A", "type": "output_pin"},
    ]}) == []
