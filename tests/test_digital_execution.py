"""Executing a digital experiment: the plan through Logisim into Measurements.

WHAT THIS FILE IS FOR. Until now Q2 could be LOADED (schema, types, plan,
regimes all validated) and EMITTED (a .circ Logisim evaluates correctly), but
the two were never joined: no Measurement ever carried a truth table, and the
three digital regime assertions were declared and never evaluated. A declared
check that never runs is the failure CLAUDE.md calls the worst available.

THREE INDEPENDENT THINGS AGREE HERE, and it is worth being precise about what
each one buys, because they are not interchangeable:

1. `spec_oracle` -- the 4-to-2 priority encoder written from the QUESTION's
   own wording plus the priority order stated in q2.json's design_notes.
   Four lines, no gates, no netlist. It is the only check here that could
   catch our gate network implementing the wrong FUNCTION -- Logisim would
   evaluate a wrong encoder just as happily as a right one. It cannot catch
   a misreading of the question that also went into the design notes.
2. `baselines.Q2_TRUTH_TABLE` -- what Logisim actually printed, pinned with
   provenance. Catches drift in the emitter, the placement or the router.
3. `exp8_gates.circ` -- a student's hand-drawn encoder, an independent
   IMPLEMENTATION evaluated through the same external evaluator. Pinned in
   test_logisim_emitter.py; not repeated here.

The oracle check runs with no tools installed. The Logisim ones skip.
"""

import copy
import json
from pathlib import Path

import pytest

from ohmwork import analysis
from ohmwork.logisim_backend import LogisimBackend, locate_logisim
from ohmwork.question import load_question
from tests import baselines

EXAMPLES = Path(__file__).parent.parent / "examples"


def _logisim_available():
    try:
        locate_logisim()
        return True
    except FileNotFoundError:
        return False


needs_logisim = pytest.mark.skipif(
    not _logisim_available(), reason="Logisim Evolution not installed")


def q2_data():
    return json.loads(
        (EXAMPLES / "q2.json").read_text(encoding="utf-8"))


def q2():
    return load_question(q2_data())


def run_q2(tmp_path):
    q = q2()
    return analysis.execute(q.circuit, q.plan, LogisimBackend(), tmp_path)


# ------------------------------------------------------------ the oracle


def spec_oracle(en, i3, i2, i1, i0):
    """A 4-to-2 priority encoder, from the question, not from our circuit.

    The question: "a 4-to-2 priority encoder ... an enable input and a
    'valid output' signal". q2.json's design_notes add the two things the
    question leaves open -- I3 is highest priority, and the enable gates all
    three outputs.

    So: the outputs are the INDEX of the highest-numbered active input, the
    valid flag says whether any input is active, and everything is ANDed with
    enable. No gate appears in this function, which is the point.
    """
    active = [i for i, bit in enumerate((i0, i1, i2, i3)) if bit]
    if not en or not active:
        return 0, 0, 0
    highest = max(active)
    return (highest >> 1) & 1, highest & 1, 1


def test_the_pinned_table_is_what_the_question_asks_for():
    """Runs with nothing installed: the pin is checked against the spec."""
    table = baselines.Q2_TRUTH_TABLE
    assert table.columns == ("EN", "I3", "I2", "I1", "I0", "Y1", "Y0", "V")
    assert len(table.rows) == 32
    for row in table.rows:
        en, i3, i2, i1, i0 = row[:5]
        assert row[5:] == spec_oracle(en, i3, i2, i1, i0), row


def test_the_oracle_is_not_vacuous():
    """A test that cannot fail is worth nothing (CLAUDE.md, the mutation
    check). Reversing the priority order must break the comparison."""
    swapped = tuple(row[:5] + spec_oracle(row[0], row[4], row[3], row[2],
                                          row[1])
                    for row in baselines.Q2_TRUTH_TABLE.rows)
    assert swapped != baselines.Q2_TRUTH_TABLE.rows


# --------------------------------------------------------- the execution


@needs_logisim
def test_q2_executes_into_a_measurement_carrying_the_table(tmp_path):
    results = run_q2(tmp_path)
    table = results["truth_table"]
    assert table.table["columns"] == list(baselines.Q2_TRUTH_TABLE.columns)
    assert [tuple(r) for r in table.table["rows"]] == \
        list(baselines.Q2_TRUTH_TABLE.rows)


@needs_logisim
def test_the_table_measurement_carries_external_provenance(tmp_path):
    table = run_q2(tmp_path)["truth_table"]
    assert table.backend == "logisim-evolution"
    assert table.verification == "external"
    assert table.run == "exhaustive"
    assert table.source == "simulation"
    # A table has no scalar value, and must not pretend to have one.
    assert table.value is None


@needs_logisim
def test_the_compatibility_note_travels_with_the_table(tmp_path):
    """We emit 2.7.1 and verify with Evolution, which warns. Expected, and
    surfaced as a note rather than hidden or treated as failure."""
    notes = run_q2(tmp_path)["truth_table"].table["notes"]
    assert any("compatibility mode" in n for n in notes)


@needs_logisim
def test_the_report_prints_every_row_not_a_summary(tmp_path):
    results = run_q2(tmp_path)
    text = analysis.render_report(results, q2().plan)
    assert "EN" in text and "Y1" in text
    assert "logisim-evolution" in text
    assert text.count("\n") > 32


# ------------------------------------------------- regimes, EVALUATED


@needs_logisim
def test_all_three_digital_regimes_are_evaluated_and_hold(tmp_path):
    regimes = run_q2(tmp_path).regimes
    assert {r.assertion for r in regimes} == {
        "no_floating_inputs", "all_outputs_driven", "no_combinational_loops"}
    assert all(r.held for r in regimes)


@needs_logisim
def test_a_regime_that_held_says_what_it_examined(tmp_path):
    """An unrun check must announce itself, and so must a passing one: a
    silent regime is indistinguishable from one nobody evaluated."""
    regimes = {r.assertion: r for r in run_q2(tmp_path).regimes}
    # 3 output pins + NOT(1) + five 2-input gates(10) + 4-input OR(4) +
    # two more 2-input gates(2) = 20 sinks across 16 components.
    assert "20 input port(s) across 16 components" in \
        regimes["no_floating_inputs"].examined
    assert "3 output pin(s)" in regimes["all_outputs_driven"].examined
    assert "16 components" in regimes["no_combinational_loops"].examined
    for r in regimes.values():
        assert r.examined


@needs_logisim
def test_the_report_states_every_regime_it_checked(tmp_path):
    text = analysis.render_report(run_q2(tmp_path), q2().plan)
    assert "no_floating_inputs" in text
    assert "all_outputs_driven" in text
    assert "no_combinational_loops" in text


# The three violations, checked WITHOUT Logisim: they are properties of the
# circuit description, so they are decided before anything is evaluated.


def _detached(net_key, pin):
    """q2's circuit with one pin removed from one net."""
    q = q2()
    circuit = copy.deepcopy(q.circuit)
    circuit["nets"][net_key].remove(pin)
    return circuit, q.plan


def test_a_floating_gate_input_is_caught_and_names_the_pin():
    circuit, plan = _detached("i1", "G2.in0")
    outcome = analysis.check_regimes(circuit, plan)
    floating = next(r for r in outcome
                    if r.assertion == "no_floating_inputs")
    assert not floating.held
    assert any("G2.in0" in reason for reason in floating.reasons)


def test_an_undriven_output_pin_is_caught_and_names_it():
    circuit, plan = _detached("v", "V.pin")
    driven = next(r for r in analysis.check_regimes(circuit, plan)
                  if r.assertion == "all_outputs_driven")
    assert not driven.held
    assert any("V" in reason for reason in driven.reasons)


def test_a_combinational_loop_is_caught_and_names_the_cycle():
    q = q2()
    circuit = copy.deepcopy(q.circuit)
    # feed G3's output back into G2, which already feeds G3: a two-gate loop
    circuit["nets"]["y0pre"].append("G2.in0")
    circuit["nets"]["i1"].remove("G2.in0")
    loop = next(r for r in analysis.check_regimes(circuit, q.plan)
                if r.assertion == "no_combinational_loops")
    assert not loop.held
    assert any("G2" in reason or "G3" in reason for reason in loop.reasons)


def test_a_violated_digital_regime_marks_the_table_unreliable(tmp_path):
    """Same doctrine as a violated analog regime: the result is shown
    flagged with its reason, never hidden and never unexplained."""
    circuit, plan = _detached("i1", "G2.in0")

    class Stub:
        """Stands in for Logisim so the flagging is testable offline. The
        point of the test is what happens to the RESULT once a regime is
        violated, not what Logisim would say about this circuit."""

        name, verification = "stub", "external"

        def truth_table(self, path, inputs, outputs, timeout=120):
            from ohmwork.logisim_backend import TruthTable
            return TruthTable(tuple(inputs), tuple(outputs),
                              tuple(baselines.Q2_TRUTH_TABLE.rows),
                              backend=self.name,
                              verification=self.verification)

    results = analysis.execute(circuit, plan, Stub(), tmp_path)
    table = results["truth_table"]
    assert not table.reliable
    assert any("G2.in0" in w for w in table.warnings)


# ------------------------------------------------------- plan homogeneity


def test_a_plan_may_not_mix_analog_and_digital_runs():
    """A truth_table run belongs to a Logisim question and a .op run to an
    LTspice one; one plan containing both has no single evaluator and no
    single file to run."""
    q = q2()
    plan = copy.deepcopy(q.plan)
    plan["runs"].append({"id": "bias", "type": "op"})
    with pytest.raises(analysis.AnalysisError) as excinfo:
        analysis.validate_plan(q.circuit, plan)
    assert "digital" in str(excinfo.value)
