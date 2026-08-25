"""Logisim as an EXTERNAL evaluator, and the spike that established it.

The headline test is test_logisim_agrees_with_the_geometrically_recovered_logic.
It runs a file we did not create through a tool we did not write, and checks
the result against logic recovered from that file's coordinates. Nothing in
the comparison shares an implementation with anything else in it.

That is what closes the evaluator asymmetry, and it is worth stating what it
would take to break: our parser would have to recover the wrong netlist AND
Logisim would have to independently compute matching wrong values. Compare
with the situation before, where a single broken evaluator produced both
sides and they agreed by construction.

These tests skip cleanly when Logisim is absent, exactly like the LTspice
ones -- the pipeline has to stay workable offline.
"""

from pathlib import Path

import pytest

from ohmwork.logisim_backend import (
    DigitalEvaluationError,
    InternalLogicBackend,
    LogisimBackend,
    SAFE_LABEL,
    best_available_backend,
    locate_logisim,
    parse_tty_table,
)

FIXTURES = Path(__file__).parent / "fixtures" / "logisim"

INPUTS = ("E IN", "D3", "D2", "D1", "D0")
OUTPUTS = ("OUT 1", "OUT 2", "V")


def _logisim_available():
    try:
        locate_logisim()
        return True
    except FileNotFoundError:
        return False


needs_logisim = pytest.mark.skipif(
    not _logisim_available(), reason="Logisim Evolution not installed"
)


def recovered_logic(en, d3, d2, d1, d0):
    """The logic read out of exp8_gates.circ's GEOMETRY, in test_logisim_geometry.

        OUT 1 = E . (D3 + D2)
        OUT 2 = E . (D3 + D1 . ~D2)
        V     = E . (D3 + D2 + D1 + D0)

    Logisim has never seen this. It is the claim under test.
    """
    return (en & (d3 | d2),
            en & (d3 | (d1 & (1 - d2))),
            en & (d3 | d2 | d1 | d0))


# ------------------------------------------------- the spike, as a test

@needs_logisim
def test_logisim_agrees_with_the_geometrically_recovered_logic():
    """32 rows, 3 outputs, no shared implementation anywhere in the chain."""
    table = LogisimBackend().truth_table(
        FIXTURES / "exp8_gates.circ", INPUTS, OUTPUTS)

    assert len(table.rows) == 32
    assert table.verification == "external"

    mismatches = []
    for row in table.as_dicts():
        expected = recovered_logic(row["E IN"], row["D3"], row["D2"],
                                   row["D1"], row["D0"])
        actual = (row["OUT 1"], row["OUT 2"], row["V"])
        if actual != expected:
            mismatches.append((row, actual, expected))
    assert mismatches == []


@needs_logisim
def test_it_is_a_correct_priority_encoder():
    """Read the external result as behaviour, not just as agreement.

    Checked against the DEFINITION of a priority encoder rather than against
    our expressions, so this test would survive our expressions being wrong.
    """
    table = LogisimBackend().truth_table(
        FIXTURES / "exp8_gates.circ", INPUTS, OUTPUTS)

    for row in table.as_dicts():
        data = [row["D3"], row["D2"], row["D1"], row["D0"]]
        code = (row["OUT 1"] << 1) | row["OUT 2"]
        if not row["E IN"]:
            assert (row["OUT 1"], row["OUT 2"], row["V"]) == (0, 0, 0), row
        elif not any(data):
            assert row["V"] == 0, row
        else:
            highest = 3 - data.index(1)      # D3 first in the list
            assert row["V"] == 1, row
            assert code == highest, row


@needs_logisim
def test_component_census_matches_our_geometric_parse():
    """An independent count of what is in the file.

    Our parse says 8 Pins, 1 NOT, 4 AND, 3 OR. Logisim counts them itself.
    This catches a dropped or invented component, which a truth table alone
    might not.
    """
    census = LogisimBackend().component_census(FIXTURES / "exp8_gates.circ")
    assert census["Pin"] == 8
    assert census["NOT Gate"] == 1
    assert census["AND Gate"] == 4
    assert census["OR Gate"] == 3


@needs_logisim
def test_evolution_reads_the_2_7_1_dialect_and_says_so():
    """The emit-2.7.1 / verify-with-Evolution split depends on this.

    Evolution opens original-Logisim files in a compatibility mode and warns.
    The warning is expected and is surfaced as a note, not treated as failure.
    """
    table = LogisimBackend().truth_table(
        FIXTURES / "exp8_gates.circ", INPUTS, OUTPUTS)
    assert any("compatibility mode" in note for note in table.notes)


# ------------------------------------------------------- parsing rules

def test_labels_needing_no_rewriting():
    assert SAFE_LABEL.match("EN") and SAFE_LABEL.match("OUT_1")
    assert not SAFE_LABEL.match("E IN")      # space -> Logisim renames it
    assert not SAFE_LABEL.match("1UP")       # must not start with a digit


def test_vhdl_mangled_columns_are_matched_back():
    # A pin labelled "E IN" comes back as "E_IN_ef467da7". The hash is not
    # reproducible by us, so matching is by prefix and must be unambiguous.
    text = "E_IN_ef467da7 D0 OUT_1_140ad176\n0 0 0\n0 1 0\n1 0 0\n1 1 1\n"
    table = parse_tty_table(text, ("E IN", "D0"), ("OUT 1",),
                            backend="x", verification="external")
    assert table.rows[-1] == (1, 1, 1)


def test_a_missing_pin_is_a_hard_error():
    text = "A B\n0 0\n0 1\n1 0\n1 1\n"
    with pytest.raises(DigitalEvaluationError) as excinfo:
        parse_tty_table(text, ("A", "NOPE"), ("B",),
                        backend="x", verification="external")
    assert "does not appear" in str(excinfo.value)


def test_a_short_table_is_rejected():
    # 2 inputs must give 4 rows. Fewer means the circuit is not purely
    # combinational, and quietly accepting it would publish a partial answer.
    text = "A B Y\n0 0 0\n0 1 1\n"
    with pytest.raises(DigitalEvaluationError) as excinfo:
        parse_tty_table(text, ("A", "B"), ("Y",),
                        backend="x", verification="external")
    assert "expected 4 rows" in str(excinfo.value)


def test_a_non_binary_value_is_rejected():
    # Logisim prints E or x for error/floating nodes. Never coerce those.
    text = "A Y\n0 E\n1 1\n"
    with pytest.raises(DigitalEvaluationError) as excinfo:
        parse_tty_table(text, ("A",), ("Y",),
                        backend="x", verification="external")
    assert "floating or conflicting" in str(excinfo.value)


def test_empty_output_is_rejected():
    with pytest.raises(DigitalEvaluationError):
        parse_tty_table("", ("A",), ("Y",), backend="x", verification="external")


# ----------------------------------------------- backend declarations

def test_the_two_backends_declare_different_standing():
    assert LogisimBackend.verification == "external"
    assert InternalLogicBackend.verification == "internal"


def test_internal_engine_refuses_to_pretend():
    # Not built yet, and it says so rather than returning something.
    with pytest.raises(NotImplementedError):
        InternalLogicBackend().truth_table("x.circ", ("A",), ("Y",))


@needs_logisim
def test_best_available_prefers_the_external_evaluator():
    assert best_available_backend().verification == "external"


# ------------------------------------------------- running it on a server
#
# The hosted digital service runs on Linux, where Logisim Evolution is an
# all-in-one JAR rather than the Windows jpackage launcher. That is the one
# platform difference the backend has to know about, and getting it wrong
# means the server cannot verify anything at all -- the failure the whole
# endpoint exists to prevent.


def test_a_jar_is_run_through_java_and_an_exe_is_run_directly(tmp_path,
                                                              monkeypatch):
    from ohmwork.logisim_backend import logisim_command

    monkeypatch.setenv("OHMWORK_JAVA", "/usr/bin/java")
    jar = tmp_path / "logisim-evolution.jar"
    jar.write_bytes(b"")
    assert logisim_command(jar, ["--tty", "table"]) == [
        "/usr/bin/java", "-jar", str(jar), "--tty", "table"]

    exe = tmp_path / "logisim-evolution.exe"
    exe.write_bytes(b"")
    # The Windows launcher bundles its own Java and contains no java.exe, so
    # it must NOT be handed to a JVM.
    assert logisim_command(exe, ["--tty", "table"]) == [str(exe), "--tty",
                                                        "table"]


def test_a_jar_with_no_java_anywhere_says_so(tmp_path, monkeypatch):
    import ohmwork.logisim_backend as backend

    monkeypatch.delenv("OHMWORK_JAVA", raising=False)
    monkeypatch.setattr(backend.shutil, "which", lambda name: None)
    jar = tmp_path / "logisim-evolution.jar"
    jar.write_bytes(b"")
    with pytest.raises(FileNotFoundError, match="OHMWORK_JAVA"):
        backend.logisim_command(jar, [])
