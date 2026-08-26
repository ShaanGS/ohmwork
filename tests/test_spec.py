"""The digital spec oracle: this file is its spec.

WHY THIS LAYER EXISTS. Everything else in this project verifies that the FILE
matches the ANSWER: emit a circuit, hand that exact file to Logisim, read the
numbers back out of what Logisim produced. That catches an emitter bug, a
routing bug, a wrong pin. It cannot catch the circuit implementing the wrong
FUNCTION, because Logisim will evaluate a wrong encoder exactly as happily as
a right one and report its truth table with perfect fidelity.

For the three questions solved by hand that gap was covered by a human writing
the expected table from the question's wording. That does not scale to a
question nobody has seen, which is exactly what a live site would face.

THE ORACLE. From the question's words alone, produce one boolean expression
per output — no gates, no netlist, no coordinates. Evaluate it exhaustively
here. That expected table is then compared against what Logisim computes from
the emitted circuit, and a mismatch is a real signal:

    the spec says WHAT the question asks for
    the circuit says HOW
    Logisim says what the HOW actually does

Two artefacts from different reasoning, judged by an outside tool. If the gate
network does not implement what was asked, the two disagree.

WHAT IT STILL CANNOT CATCH, stated here so nobody later mistakes this for
proof: if the model MISREADS the question — decides I3 is the lowest priority
input when the question meant highest — the spec and the circuit agree, both
are wrong, and Logisim confirms them. That is the same class as misreading
1.8k as 1.8M, and it has the same answer: show the reading to a human. So the
resolved spec is rendered in the output rather than kept internal.

THE EXPRESSION VOCABULARY IS CLOSED, deliberately. NOT/AND/XOR/OR, parens,
constants, and identifiers that must be declared inputs. No eval(), no
attribute access, no function calls. `prose.py` made the same choice for its
row filters and the reason is the same: a second, looser evaluator would undo
the first one's guarantee. An expression outside the vocabulary is an error
naming the offending token — and that error is the most useful thing a model
can be told, because it is fed straight back to it.
"""

import pytest

from ohmwork.spec import (Spec, SpecError, compare_tables, evaluate_spec,
                          parse_expression)


# ------------------------------------------------------- the evaluator

def ev(text, **values):
    return parse_expression(text, sorted(values)).evaluate(values)


def test_the_operators_each_work():
    assert ev("A & B", A=1, B=1) == 1
    assert ev("A & B", A=1, B=0) == 0
    assert ev("A | B", A=0, B=1) == 1
    assert ev("A ^ B", A=1, B=1) == 0
    assert ev("~A", A=0) == 1


def test_the_spellings_a_model_actually_writes_are_accepted():
    """A model asked for boolean algebra writes it half a dozen ways. Every
    spelling here is one that turned up in real replies; accepting them costs
    nothing and rejecting them burns a retry on notation rather than logic."""
    for text in ("A AND B", "A . B", "A * B", "A&B"):
        assert ev(text, A=1, B=1) == 1, text
    for text in ("A OR B", "A + B", "A|B"):
        assert ev(text, A=1, B=0) == 1, text
    for text in ("NOT A", "!A", "~A", "A'"):
        assert ev(text, A=0) == 1, text
    for text in ("A XOR B", "A ^ B"):
        assert ev(text, A=1, B=0) == 1, text


def test_precedence_is_not_and_xor_or():
    # ~A & B  parses as (~A) & B, not ~(A & B)
    assert ev("~A & B", A=1, B=1) == 0
    # A | B & C parses as A | (B & C)
    assert ev("A | B & C", A=0, B=1, C=0) == 0
    assert ev("(A | B) & C", A=0, B=1, C=1) == 1


def test_constants_are_allowed():
    assert ev("0", A=0) == 0
    assert ev("1 & A", A=1) == 1


def test_an_unknown_identifier_is_an_error_that_names_it():
    """The model referring to a signal it never declared is the single most
    likely spec bug, and the message is what gets fed back to it."""
    with pytest.raises(SpecError) as excinfo:
        parse_expression("A & Q7", ["A", "B"])
    assert "Q7" in str(excinfo.value)
    assert "A" in str(excinfo.value) and "B" in str(excinfo.value)


def test_nothing_outside_the_vocabulary_survives():
    """No eval(), so none of this is dangerous — but each must FAIL rather
    than silently parse as something else."""
    for text in ("__import__('os')", "A.b", "f(A)", "A ** B", "A if B else 0"):
        with pytest.raises(SpecError):
            parse_expression(text, ["A", "B"])


def test_unbalanced_parentheses_are_rejected():
    with pytest.raises(SpecError):
        parse_expression("(A & B", ["A", "B"])
    with pytest.raises(SpecError):
        parse_expression("A & B)", ["A", "B"])


def test_an_empty_expression_is_rejected_rather_than_read_as_zero():
    """Silently reading a missing expression as constant 0 would publish a
    spec the model never wrote, and it would be a VALID-looking one."""
    with pytest.raises(SpecError):
        parse_expression("   ", ["A"])


# ------------------------------------------------ evaluating a whole spec

DECODER = Spec(
    inputs=("EN", "A1", "A0"),
    outputs=("Y3", "Y2", "Y1", "Y0"),
    expressions={
        "Y3": "EN & A1 & A0",
        "Y2": "EN & A1 & ~A0",
        "Y1": "EN & ~A1 & A0",
        "Y0": "EN & ~A1 & ~A0",
    },
)


def test_a_spec_evaluates_to_every_input_combination():
    table = evaluate_spec(DECODER)
    assert table.inputs == ("EN", "A1", "A0")
    assert table.outputs == ("Y3", "Y2", "Y1", "Y0")
    assert len(table.rows) == 8


def test_the_decoder_spec_actually_decodes():
    by_inputs = {row[:3]: row[3:] for row in evaluate_spec(DECODER).rows}
    assert by_inputs[(1, 0, 0)] == (0, 0, 0, 1)      # Y0
    assert by_inputs[(1, 0, 1)] == (0, 0, 1, 0)      # Y1
    assert by_inputs[(1, 1, 0)] == (0, 1, 0, 0)      # Y2
    assert by_inputs[(1, 1, 1)] == (1, 0, 0, 0)      # Y3
    for a1 in (0, 1):
        for a0 in (0, 1):
            assert by_inputs[(0, a1, a0)] == (0, 0, 0, 0), "disabled"


def test_rows_are_ordered_by_the_input_tuple_not_by_chance():
    """Incident 10: never trust the order a tool emits rows in. This side of
    the comparison is ours, so it is defined rather than observed."""
    rows = evaluate_spec(DECODER).rows
    assert [r[:3] for r in rows] == sorted(r[:3] for r in rows)


def test_an_output_named_as_an_input_is_rejected():
    with pytest.raises(SpecError):
        evaluate_spec(Spec(inputs=("A",), outputs=("A",),
                           expressions={"A": "A"}))


def test_an_output_casefolded_to_an_input_is_rejected():
    with pytest.raises(SpecError, match="case-insensitively"):
        evaluate_spec(Spec(inputs=("A",), outputs=("a",),
                           expressions={"a": "A"}))


def test_an_output_with_no_expression_is_rejected():
    with pytest.raises(SpecError) as excinfo:
        evaluate_spec(Spec(inputs=("A",), outputs=("Y", "Z"),
                           expressions={"Y": "A"}))
    assert "Z" in str(excinfo.value)


def test_a_spec_with_no_inputs_is_rejected():
    with pytest.raises(SpecError):
        evaluate_spec(Spec(inputs=(), outputs=("Y",), expressions={"Y": "1"}))


def test_an_absurdly_wide_spec_is_refused_rather_than_hanging():
    """2**n rows. A model that writes 30 inputs gets an error, not a machine
    that stops responding."""
    wide = Spec(inputs=tuple(f"I{i}" for i in range(25)), outputs=("Y",),
                expressions={"Y": "I0"})
    with pytest.raises(SpecError) as excinfo:
        evaluate_spec(wide)
    assert "25" in str(excinfo.value)


# ------------------------------------------------------- the comparison

def logisim_like(rows, inputs=("EN", "A1", "A0"),
                 outputs=("Y3", "Y2", "Y1", "Y0")):
    from ohmwork.logisim_backend import TruthTable
    return TruthTable(inputs=inputs, outputs=outputs, rows=tuple(rows),
                      backend="logisim-evolution", verification="external")


def test_identical_tables_compare_equal():
    expected = evaluate_spec(DECODER)
    assert compare_tables(expected, logisim_like(expected.rows)).agrees


def test_a_single_wrong_row_is_found_and_reported_in_full():
    expected = evaluate_spec(DECODER)
    rows = [list(r) for r in expected.rows]
    rows[5][3] = 1 - rows[5][3]                     # break one output bit
    result = compare_tables(expected, logisim_like([tuple(r) for r in rows]))

    assert not result.agrees
    assert len(result.differences) == 1
    difference = result.differences[0]
    assert difference.inputs == expected.rows[5][:3]
    assert "Y3" in difference.disagreeing_outputs


def test_row_ORDER_is_not_a_difference():
    """Logisim enumerates in ITS own column order, which differs per file.
    Comparing as sequences reports a difference that is not one -- incident
    10, and the reason this comparison indexes by the input tuple."""
    expected = evaluate_spec(DECODER)
    shuffled = list(reversed(expected.rows))
    assert compare_tables(expected, logisim_like(shuffled)).agrees


def test_a_different_COLUMN_order_is_handled_not_misread():
    """The same fact one level up: Logisim may report the columns in another
    order. Matching by NAME is what makes the row comparison meaningful."""
    expected = evaluate_spec(DECODER)
    swapped_inputs = ("A0", "A1", "EN")
    rows = tuple((r[2], r[1], r[0]) + r[3:] for r in expected.rows)
    actual = logisim_like(rows, inputs=swapped_inputs)
    assert compare_tables(expected, actual).agrees


def test_a_missing_signal_is_a_failure_not_a_silent_pass():
    """If Logisim reports fewer outputs than the spec requires, the rows that
    ARE there might all agree. Agreement over a subset is not agreement."""
    expected = evaluate_spec(DECODER)
    rows = tuple(r[:3] + r[3:6] for r in expected.rows)      # drop Y0
    actual = logisim_like(rows, outputs=("Y3", "Y2", "Y1"))
    result = compare_tables(expected, actual)
    assert not result.agrees
    assert "Y0" in result.summary


def test_a_missing_ROW_is_a_failure(caplog):
    expected = evaluate_spec(DECODER)
    actual = logisim_like(expected.rows[:-1])
    result = compare_tables(expected, actual)
    assert not result.agrees
    assert "row" in result.summary.lower()


def test_the_summary_is_written_to_be_fed_back_to_a_model():
    """The retry loop's whole value is the quality of what it says went
    wrong. A bare 'mismatch' teaches the model nothing."""
    expected = evaluate_spec(DECODER)
    rows = [list(r) for r in expected.rows]
    rows[1][6] = 1 - rows[1][6]
    result = compare_tables(expected, logisim_like([tuple(r) for r in rows]))

    summary = result.summary
    for signal in ("EN", "A1", "A0", "Y1"):
        assert signal in summary
    assert "expected" in summary.lower()


def test_the_number_of_reported_differences_is_capped(caplog):
    """A completely wrong circuit differs on every row. Feeding 256 rows back
    to a model wastes the budget that the retry needs -- but the cap must
    SAY it truncated, or the model is told a smaller problem than it has."""
    expected = evaluate_spec(DECODER)
    inverted = tuple(r[:3] + tuple(1 - bit for bit in r[3:])
                     for r in expected.rows)
    result = compare_tables(expected, logisim_like(inverted), max_differences=3)
    assert len(result.differences) == 8, "all differences are still recorded"
    assert result.summary.count("expected") <= 3
    assert "8" in result.summary
