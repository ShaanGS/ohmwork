"""The digital half of the analysis schema: truth_table runs, table
measurements, and digital regime assertions.

The analog regimes (zener_in_breakdown, bjt_active) guard
convergence-without-correctness: a circuit that solves cleanly while sitting
outside the operating region the answer assumes. The digital ones guard the
same class of failure in a different dress — a gate network that evaluates
cleanly while being structurally wrong, because an input floats, an output is
undriven, or a loop makes the "answer" depend on evaluation order.
"""

import copy
import json
from pathlib import Path

import pytest

from ohmwork.analysis import (ANALOG_REGIMES, DIGITAL_REGIMES, REGIME_ASSERTS,
                              RUN_TYPES, AnalysisError, validate_plan)
from ohmwork.question import QuestionError, load_question

EXAMPLES = Path(__file__).parent.parent / "examples"


def q2():
    return json.loads((EXAMPLES / "q2.json").read_text(encoding="utf-8"))


def circuit_and_plan():
    data = q2()
    question = load_question(data)
    return question.circuit, question.plan


# ---------------------------------------------------------- the run type

def test_truth_table_is_a_run_type():
    assert "truth_table" in RUN_TYPES


def test_q2_loads_end_to_end_through_the_gate():
    """The acceptance case for 6a/6b: schema, types, plan, regimes."""
    question = load_question(q2())
    run = question.plan["runs"][0]
    assert run["type"] == "truth_table"
    assert run["inputs"] == ["EN", "I3", "I2", "I1", "I0"]
    assert 2 ** len(run["inputs"]) == 32


def test_a_truth_table_run_needs_inputs():
    circuit, plan = circuit_and_plan()
    plan["runs"][0].pop("inputs")
    with pytest.raises(AnalysisError) as excinfo:
        validate_plan(circuit, plan)
    assert "needs 'inputs'" in str(excinfo.value)


def test_inputs_must_be_real_components():
    circuit, plan = circuit_and_plan()
    plan["runs"][0]["inputs"].append("I9")
    with pytest.raises(AnalysisError) as excinfo:
        validate_plan(circuit, plan)
    assert "I9" in str(excinfo.value)


def test_a_repeated_input_is_rejected():
    # Silently deduplicating would halve the row count without saying so.
    circuit, plan = circuit_and_plan()
    plan["runs"][0]["inputs"].append("EN")
    with pytest.raises(AnalysisError) as excinfo:
        validate_plan(circuit, plan)
    assert "repeats an input" in str(excinfo.value)


# ------------------------------------------------- the table measurement

def test_a_table_needs_a_truth_table_run():
    circuit, plan = circuit_and_plan()
    plan["runs"][0]["type"] = "op"
    plan["runs"][0].pop("inputs")
    with pytest.raises(AnalysisError) as excinfo:
        validate_plan(circuit, plan)
    assert "needs a truth_table run" in str(excinfo.value)


def test_a_table_needs_outputs():
    circuit, plan = circuit_and_plan()
    table = next(m for m in plan["measurements"] if m.get("kind") == "table")
    table["outputs"] = []
    with pytest.raises(AnalysisError) as excinfo:
        validate_plan(circuit, plan)
    assert "needs 'outputs'" in str(excinfo.value)


def test_outputs_must_be_real_components():
    circuit, plan = circuit_and_plan()
    table = next(m for m in plan["measurements"] if m.get("kind") == "table")
    table["outputs"].append("Y9")
    with pytest.raises(AnalysisError) as excinfo:
        validate_plan(circuit, plan)
    assert "Y9" in str(excinfo.value)


def test_a_pin_cannot_be_both_driven_and_measured():
    """Listing an input as an output would report back what we just set.

    It would produce a table that always agrees with itself — a column of
    results that cannot fail, which is the digital form of a self-confirming
    measurement.
    """
    circuit, plan = circuit_and_plan()
    table = next(m for m in plan["measurements"] if m.get("kind") == "table")
    table["outputs"].append("EN")
    with pytest.raises(AnalysisError) as excinfo:
        validate_plan(circuit, plan)
    assert "drives them as inputs" in str(excinfo.value)


# -------------------------------------------------------- digital regimes

def test_the_two_regime_families_are_disjoint_and_both_registered():
    assert ANALOG_REGIMES & DIGITAL_REGIMES == set()
    assert ANALOG_REGIMES | DIGITAL_REGIMES == REGIME_ASSERTS
    assert DIGITAL_REGIMES == {"no_floating_inputs", "all_outputs_driven",
                               "no_combinational_loops"}


def test_q2_asserts_all_three_digital_regimes():
    _, plan = circuit_and_plan()
    asserted = {m["assert"] for m in plan["measurements"]
                if m.get("kind") == "regime"}
    assert asserted == DIGITAL_REGIMES


def test_a_digital_regime_names_no_device():
    # It is a property of the whole circuit; naming a device is meaningless.
    circuit, plan = circuit_and_plan()
    regime = next(m for m in plan["measurements"] if m.get("kind") == "regime")
    regime["device"] = "G5"
    with pytest.raises(AnalysisError) as excinfo:
        validate_plan(circuit, plan)
    assert "property of the whole circuit" in str(excinfo.value)


def test_a_digital_regime_needs_a_truth_table_run():
    circuit, plan = circuit_and_plan()
    plan["runs"][0]["type"] = "op"
    plan["runs"][0].pop("inputs")
    plan["measurements"] = [m for m in plan["measurements"]
                            if m.get("kind") != "table"]
    with pytest.raises(AnalysisError) as excinfo:
        validate_plan(circuit, plan)
    assert "applies to a truth_table run" in str(excinfo.value)


def test_an_analog_regime_still_needs_its_device():
    circuit, plan = circuit_and_plan()
    regime = next(m for m in plan["measurements"] if m.get("kind") == "regime")
    regime["assert"] = "zener_in_breakdown"
    with pytest.raises(AnalysisError):
        validate_plan(circuit, plan)


# ---------------------------------------------------------- the dry run

def test_dry_run_states_the_row_count_not_just_the_input_count():
    """"5 inputs" and "32 rows" are the same fact, but only one of them is
    checkable at a glance against "test all possible input combinations"."""
    from ohmwork.question import dry_run_report
    report = dry_run_report(load_question(q2()))
    assert "32 rows over EN, I3, I2, I1, I0" in report


def test_dry_run_lists_prose_asks_separately_from_unmapped():
    from ohmwork.question import dry_run_report
    report = dry_run_report(load_question(q2()))
    assert "prose asks — answered by text, not by measurement" in report
    # Each prose ask says HOW it will be answered, so a reader can see before
    # anything runs which parts of the output will be checkable.
    assert "quoted, not generated (design notes)" in report
    assert "computed evidence + generated caption" in report
    unmapped = report.split("unmapped")[1].splitlines()[1]
    assert "(none)" in unmapped


def test_dry_run_reports_the_unrun_round_trip():
    from ohmwork.question import dry_run_report
    report = dry_run_report(load_question(q2()))
    assert "SKIPPED  geometric round trip" in report
    assert "a skipped check is not a passed check" in report
