"""Verifying an IC question against the PART, not against a recollection.

THE INCIDENT THAT PUT THIS HERE. Q4 -- "design a BCD-to-seven-segment display
circuit using the 7447-decoder IC" -- got all the way to the comparison and
failed there, for a reason that was not a bug. The loop's reference was the
model's SPEC: seven boolean expressions written from the question's words,
which for a named chip means written from the model's MEMORY of the datasheet.
For input 0000 that memory said every segment off. The chip shows a nought.
The chip is right.

Verifying an IC against a recollection is backwards. So for a question that
names a part, the reference is THE PART:

    1. PROBE    a bare 7447, one Pin on every port, handed to Logisim. That
                table is the chip's own behaviour, measured in the same
                evaluator that will judge the answer. No recollection.
    2. WIRING   read out of the design's own nets: which question signal
                reaches which pin, which pins are held at a level, which pin
                drives which output.
    3. PREDICT  push the probe's table through that wiring.
    4. COMPARE  against what Logisim makes of the emitted FILE.

WHAT THIS PROVES, precisely: that the file handed over routes the question's
signals through a real 7447 exactly as the design says, and that the part in
it decodes as a bare one does.

WHAT IT DOES NOT PROVE, and this must never be blurred: that the wiring is the
right reading of the question. A design that swaps two signals is a different
design, and steps 3 and 4 both read the same nets, so they agree about it.
That is why the wiring map is OUTPUT -- the same role `spec.render()` plays
for a gate-level question.

The one part of the misreading that IS caught mechanically: `name_conflicts`
refuses a design that wires the question's signal `A` to the part's pin `D`.
A signal whose own name names a pin must be on that pin. It is a naming check
and not a behavioural one, and it is written down as such.

STEP 3 WALKS THE NETLIST rather than only looking up direct connections, and
that was decided by running the loop rather than by reasoning about it: the
first live 7447 solve failed four times over because the model put inverters
on the segment outputs, which is a fair reading of "the 7447 has active-low
outputs". So gates are evaluated here, from logic written in `GATE_LOGIC` and
imported from nowhere -- Logisim has its own implementation, and two of them
disagreeing is a finding.
"""

import json
from pathlib import Path

import pytest

from ohmwork.partcheck import (GATE_LOGIC, PASSIVE_TYPES, PartWiring,
                               WiringError, derive_wiring, name_conflicts,
                               part_basis, predict_table, probe_circuit,
                               probeable)
from ohmwork.targets import get_target

LOGISIM = get_target("logisim")


def q4_circuit():
    """The committed Q4 answer: 7447 + display, wired straight through."""
    data = json.loads((Path(__file__).parent.parent / "examples" / "q4.json")
                      .read_text(encoding="utf-8"))
    return data["circuit"]


# ------------------------------------------------------------- the probe

def test_only_a_part_that_produces_something_can_be_its_own_reference():
    """A seven-segment display has eight ports and all of them are inputs.

    Probing it would produce a truth table with no output columns, which is
    not a reference to anything. The distinction is structural rather than a
    list of part names, so a part added later sorts itself out.
    """
    assert probeable("ttl7447", LOGISIM)
    assert not probeable("seven_segment", LOGISIM)


def test_the_probe_puts_a_pin_on_every_port_and_nothing_else():
    circuit = probe_circuit("ttl7447", LOGISIM)
    ports = LOGISIM.pin_names("ttl7447")

    parts = [c for c in circuit["components"] if c["type"] == "ttl7447"]
    assert len(parts) == 1
    pins = [c for c in circuit["components"] if c["type"] != "ttl7447"]
    assert {c["type"] for c in pins} == {"input_pin", "output_pin"}
    assert len(pins) == len(ports)

    # Every pin is named for the port it sits on, so the evaluator's columns
    # come back as the part's own pin names and no mapping is needed.
    assert {c["ref"] for c in pins} == set(ports)
    for port in ports:
        net = [n for n, members in circuit["nets"].items()
               if f"{parts[0]['ref']}.{port}" in members]
        assert len(net) == 1, f"port {port} is on {len(net)} nets"
        assert f"{port}.pin" in circuit["nets"][net[0]]


def test_the_probe_is_a_circuit_this_project_will_actually_emit():
    """Built as a circuit description and routed by the real emitter.

    A probe that only this module could write would be a second emitter,
    free to disagree with the one that writes the answer.
    """
    from ohmwork.logisim_emitter import emit_circ

    text = emit_circ(probe_circuit("ttl7447", LOGISIM))
    assert 'name="7447"' in text


def test_a_part_with_no_measured_geometry_is_refused_not_guessed():
    with pytest.raises(WiringError):
        probe_circuit("no_such_part", LOGISIM)


# ------------------------------------------------------- reading the wiring

def test_the_wiring_is_read_out_of_the_nets():
    wiring = derive_wiring(q4_circuit(), "ttl7447", LOGISIM)

    assert isinstance(wiring, PartWiring)
    assert wiring.ref == "U1"
    assert wiring.part_name == "7447"
    for name in ("A", "B", "C", "D"):
        assert wiring.inputs[name] == ("signal", name)
    # Q4 ties the three control pins to one enable input rather than to
    # constants, which is a legal design and must read as one.
    for name in ("LT", "BI", "RBI"):
        assert wiring.inputs[name] == ("signal", "EN")
    for letter in "abcdefg":
        assert wiring.outputs[f"Q{letter}"] == ("port", f"Q{letter.upper()}")


def test_a_control_pin_held_at_a_level_reads_as_that_level():
    circuit = q4_circuit()
    circuit["components"] = [c for c in circuit["components"]
                             if c["ref"] != "EN"]
    circuit["components"].append({"ref": "HI", "type": "high"})
    circuit["nets"]["n_en"] = ["HI.out", "U1.LT", "U1.BI", "U1.RBI", "DS1.dp"]

    wiring = derive_wiring(circuit, "ttl7447", LOGISIM)
    assert wiring.inputs["LT"] == ("level", 1)


def test_a_gate_in_the_path_reads_as_logic_rather_than_as_a_signal():
    """The map must not claim a pin carries a signal that was inverted first.

    A reader checking "did my signals land on the right pins" is entitled to
    know that one of them passed through a gate on the way, so the entry
    names the gate instead of the signal behind it.
    """
    circuit = q4_circuit()
    circuit["components"].append({"ref": "G1", "type": "not"})
    circuit["nets"]["n_a"] = ["A.pin", "G1.in0"]
    circuit["nets"]["n_ga"] = ["G1.out", "U1.A"]

    wiring = derive_wiring(circuit, "ttl7447", LOGISIM)
    # Expanded one level, so the entry says what the gate was fed. "through
    # G1" alone tells a reader something happened without telling them what.
    assert wiring.inputs["A"] == ("logic", "G1 (not) of A")
    # ...and it is NOT reported as the signal A, which would let a naming
    # check pass over a path that no longer carries A's value.
    assert name_conflicts(wiring, LOGISIM) == []


def test_a_second_driving_part_is_refused_rather_than_predicted():
    """The remaining honest limit, enforced rather than described.

    A gate's behaviour is written down in this module. A second measured
    part's is not: it would need its own probe, and predicting two chips
    together is work nobody has done. Refused, with the reason.
    """
    circuit = q4_circuit()
    circuit["components"].append({"ref": "U2", "type": "seven_segment"})
    # A display listens and is fine. Make it drive something instead, which
    # is the case that cannot be predicted.
    circuit["components"][-1] = {"ref": "U2", "type": "ttl7447"}
    with pytest.raises(WiringError) as exc:
        derive_wiring(circuit, "ttl7447", LOGISIM)
    assert "U2" in str(exc.value) or "2 ttl7447" in str(exc.value)


def test_two_of_the_same_part_are_refused_rather_than_half_checked():
    circuit = q4_circuit()
    circuit["components"].append({"ref": "U2", "type": "ttl7447"})
    with pytest.raises(WiringError):
        derive_wiring(circuit, "ttl7447", LOGISIM)


# ------------------------------------ the one misreading that IS caught

def test_a_signal_whose_name_names_a_pin_must_be_on_that_pin():
    """The swapped A and D, which is the whole worry about this basis.

    Prediction and evaluation both read the same nets, so a swap agrees with
    itself. This check does not: it comes from the NAMES, which are outside
    the wiring.
    """
    circuit = q4_circuit()
    circuit["nets"]["n_d"] = ["D.pin", "U1.A"]
    circuit["nets"]["n_a"] = ["A.pin", "U1.D"]

    wiring = derive_wiring(circuit, "ttl7447", LOGISIM)
    conflicts = name_conflicts(wiring, LOGISIM)
    assert conflicts, "a swapped A and D must not pass unremarked"
    assert any("A" in c and "D" in c for c in conflicts)


def test_a_signal_that_names_no_pin_is_left_alone():
    """EN drives LT, BI and RBI and is not a pin name. The check must not
    fire on it: a naming rule that refuses ordinary designs gets deleted."""
    assert name_conflicts(derive_wiring(q4_circuit(), "ttl7447", LOGISIM),
                          LOGISIM) == []


def test_a_swapped_OUTPUT_is_caught_the_same_way():
    circuit = q4_circuit()
    circuit["nets"]["n_qa"] = ["U1.QA", "Qb.pin", "DS1.a"]
    circuit["nets"]["n_qb"] = ["U1.QB", "Qa.pin", "DS1.b"]
    conflicts = name_conflicts(derive_wiring(circuit, "ttl7447", LOGISIM),
                               LOGISIM)
    assert conflicts


def test_segment_output_names_do_not_collide_with_7447_input_names():
    """`a` names a display segment, not the 7447's BCD input `A`.

    The guard protects a question output that shares the name of a *part
    output* (for example QA wired to QB). Applying it to all part pins made a
    conventional seven-segment output name look like a pin swap.
    """
    wiring = derive_wiring(q4_circuit(), "ttl7447", LOGISIM)
    segment_outputs = {
        letter: ("port", f"Q{letter.upper()}") for letter in "abcdefg"
    }
    renamed = PartWiring(
        ref=wiring.ref, type_name=wiring.type_name, part_name=wiring.part_name,
        inputs=wiring.inputs, outputs=segment_outputs, sinks=wiring.sinks,
        netlist=wiring.netlist,
    )
    assert name_conflicts(renamed, LOGISIM) == []


# ------------------------------------------------------------ the prediction
#
# The reference here is a STAND-IN chip, not the real 7447. What these tests
# exercise is the prediction machinery, and a hand-copied datasheet in a test
# file would be a second, unmeasured copy of the one thing this project
# refuses to keep in two places. The REAL chip's behaviour is measured against
# Logisim Evolution in tests/test_logisim_ttl.py, and read live from a probe
# by `probe_table`.


def stand_in_decoder(a, b, c, d, lt, bi, rbi):
    """A chip in the SHAPE a 7447 has: active low, blankable, lamp-testable.

    Active low on purpose: it is the property the model's recollection got
    wrong, and a stand-in that was active high would let a sign error pass
    every test below.
    """
    if bi == 0:
        return dict.fromkeys("ABCDEFG", 1)
    if lt == 0:
        return dict.fromkeys("ABCDEFG", 0)
    value = d * 8 + c * 4 + b * 2 + a
    return {segment: (0 if (value >> index) % 2 else 1)
            for index, segment in enumerate("ABCDEFG")}


def fake_probe_table():
    """What `probe_table` would return for the stand-in chip.

    Over the 7447's REAL port names and its real 2**7 rows, so the prediction
    is exercised against the shape it meets in production.
    """
    from itertools import product

    from ohmwork.logisim_backend import TruthTable

    ports = [(name, LOGISIM.is_source("ttl7447", name))
             for name in LOGISIM.pin_names("ttl7447")]
    ins = tuple(name for name, is_out in ports if not is_out)
    outs = tuple(name for name, is_out in ports if is_out)

    rows = []
    for combination in product((0, 1), repeat=len(ins)):
        values = dict(zip(ins, combination))
        segments = stand_in_decoder(values["A"], values["B"], values["C"],
                                    values["D"], values["LT"], values["BI"],
                                    values["RBI"])
        rows.append(combination + tuple(segments[name[1:]] for name in outs))
    return TruthTable(inputs=ins, outputs=outs, rows=tuple(rows),
                      backend="fake-logisim", verification="external")


SEGMENTS = ("Qa", "Qb", "Qc", "Qd", "Qe", "Qf", "Qg")


def predicted(circuit, inputs, outputs=SEGMENTS):
    wiring = derive_wiring(circuit, "ttl7447", LOGISIM)
    return predict_table(wiring, fake_probe_table(), inputs, outputs)


def test_the_prediction_is_the_part_pushed_through_the_wiring():
    """Every row, against the chip model written independently above."""
    table = predicted(q4_circuit(), ("D", "C", "B", "A", "EN"))

    assert table.inputs == ("D", "C", "B", "A", "EN")
    assert table.outputs == SEGMENTS
    assert len(table.rows) == 32
    for row in table.rows:
        d, c, b, a, en = row[:5]
        # Q4 ties LT, BI and RBI to the one enable input.
        wanted = stand_in_decoder(a, b, c, d, en, en, en)
        assert row[5:] == tuple(wanted[letter] for letter in "ABCDEFG")


def test_a_held_level_collapses_the_rows_it_no_longer_varies():
    """A constant holds a control pin without becoming an input pin, which is
    the whole reason `high` and `low` exist: an input pin would turn 16 rows
    into 128 and the table would stop describing the question."""
    circuit = q4_circuit()
    circuit["components"] = [c for c in circuit["components"]
                             if c["ref"] != "EN"]
    circuit["components"].append({"ref": "HI", "type": "high"})
    circuit["nets"]["n_en"] = ["HI.out", "U1.LT", "U1.BI", "U1.RBI", "DS1.dp"]

    table = predicted(circuit, ("D", "C", "B", "A"))
    assert len(table.rows) == 16
    for row in table.rows:
        d, c, b, a = row[:4]
        wanted = stand_in_decoder(a, b, c, d, 1, 1, 1)
        assert row[4:] == tuple(wanted[letter] for letter in "ABCDEFG")


def test_a_gate_in_the_path_is_EVALUATED_rather_than_refused():
    """Found by running the real loop, not by reasoning about it.

    The first live 7447 solve failed four times because the model put
    inverters on the segment outputs -- a fair reading of "the 7447 has
    active-low outputs". Refusing a sound design because the checker is thin
    is the checker's problem, so gates in the path are evaluated.
    """
    circuit = q4_circuit()
    circuit["components"].append({"ref": "N_a", "type": "not"})
    circuit["nets"]["n_qa"] = ["U1.QA", "N_a.in0", "DS1.a"]
    circuit["nets"]["n_qa_hi"] = ["N_a.out", "Qa.pin"]

    table = predicted(circuit, ("D", "C", "B", "A", "EN"))
    plain = predicted(q4_circuit(), ("D", "C", "B", "A", "EN"))
    for inverted, direct in zip(table.rows, plain.rows):
        assert inverted[5] == 1 - direct[5], "Qa must come back inverted"
        assert inverted[6:] == direct[6:], "and nothing else may change"


def test_the_gate_logic_here_covers_everything_the_emitter_can_place():
    """The tripwire on this module's blind spot.

    Every component the target can place is one of three things: a pin or a
    level, a gate whose logic is written here, or a measured part modelled by
    probing it. A type added to the emitter's vocabulary without being sorted
    into one of those buckets would become silently unpredictable, so this
    fails the moment TYPE_MAP grows.
    """
    parts = {"ttl7447", "seven_segment", "seven_segment_active_low"}
    assert (set(GATE_LOGIC) | set(PASSIVE_TYPES) | parts
            == LOGISIM.known_types())


# --------------------------------------------- display polarity (issue #1)
#
# Found by a student, reported as the repo's first issue: a 7447's
# active-low outputs wired straight to a display whose polarity was left to
# Logisim's default rendered every digit as its photographic negative --
# under a green "verified" that was true of the output pins and silent
# about the screen. The truth table can never catch it (the display is not
# in the table), so the polarity is now an explicit choice, gated against
# the question's own words and disclosed in the wiring map.

ACTIVE_LOW_WORDING = (
    "Using Logisim Evolution, design a BCD-to-seven-segment display "
    "circuit using the 7447-decoder IC. Caution: The 7447 decoder has "
    "active-low segment outputs; therefore, a logic 0 turns a segment ON.")


def display_circuit(display_type):
    return {
        "components": [
            {"ref": "U1", "type": "ttl7447"},
            {"ref": "DS1", "type": display_type},
        ],
        "nets": {
            "n_qa": ["U1.QA", "DS1.a"],
            "n_qb": ["U1.QB", "DS1.b"],
        },
    }


def test_an_active_high_display_on_stated_active_low_outputs_is_refused():
    from ohmwork.partcheck import polarity_conflicts

    problems = polarity_conflicts(
        ACTIVE_LOW_WORDING, display_circuit("seven_segment"), LOGISIM)
    assert len(problems) == 1
    message = problems[0]
    assert "DS1" in message
    assert "seven_segment_active_low" in message
    assert "photographic negative" in message
    assert "truth table cannot catch this" in message


def test_an_active_low_display_passes_the_same_question():
    from ohmwork.partcheck import polarity_conflicts

    assert polarity_conflicts(
        ACTIVE_LOW_WORDING,
        display_circuit("seven_segment_active_low"), LOGISIM) == []


def test_a_question_asking_for_a_display_refuses_a_design_without_one():
    """Measured on the live repro after the polarity fix: the model simply
    LEFT THE DISPLAY OUT, verified on the pins alone, and the answer
    quietly under-delivered the question. A drop, the same species the
    coverage checks exist for."""
    from ohmwork.partcheck import polarity_conflicts

    circuit = {
        "components": [{"ref": "U1", "type": "ttl7447"}],
        "nets": {"n_qa": ["U1.QA"]},
    }
    problems = polarity_conflicts(ACTIVE_LOW_WORDING, circuit, LOGISIM)
    assert len(problems) == 1
    assert "this design has none" in problems[0]
    assert "seven_segment_active_low" in problems[0]


def test_a_question_not_mentioning_a_display_needs_none():
    from ohmwork.partcheck import polarity_conflicts

    circuit = {
        "components": [{"ref": "U1", "type": "ttl7447"}],
        "nets": {"n_qa": ["U1.QA"]},
    }
    assert polarity_conflicts(
        "wire a 7447 and report its outputs", circuit, LOGISIM) == []


def test_the_gate_disarms_when_the_question_does_not_say_active_low():
    """A false refusal costs the product; without the words the check has
    no ground to stand on, and the wiring map line is the defence."""
    from ohmwork.partcheck import polarity_conflicts

    assert polarity_conflicts(
        "design a display circuit using the 7447",
        display_circuit("seven_segment"), LOGISIM) == []


def test_a_display_fed_through_inverters_is_not_direct_and_not_refused():
    from ohmwork.partcheck import polarity_conflicts

    circuit = {
        "components": [
            {"ref": "U1", "type": "ttl7447"},
            {"ref": "G1", "type": "not"},
            {"ref": "DS1", "type": "seven_segment"},
        ],
        "nets": {
            "n_qa": ["U1.QA", "G1.in0"],
            "n_a": ["G1.out", "DS1.a"],
        },
    }
    assert polarity_conflicts(ACTIVE_LOW_WORDING, circuit, LOGISIM) == []


def test_the_wiring_map_discloses_the_display_polarity():
    """The map is what the human checks; a listener that changes what the
    screen SHOWS belongs on it."""
    from ohmwork.partcheck import PartWiring, render_wiring

    wiring = PartWiring(
        ref="U1", type_name="ttl7447", part_name="7447",
        inputs={}, outputs={}, sinks=("DS1",),
        sink_types=(("DS1", "seven_segment_active_low"),))
    rendered = render_wiring(wiring)
    assert "DS1 lights a segment on LOW" in rendered

    wiring_high = PartWiring(
        ref="U1", type_name="ttl7447", part_name="7447",
        inputs={}, outputs={}, sinks=("DS1",),
        sink_types=(("DS1", "seven_segment"),))
    assert "DS1 lights a segment on HIGH" in render_wiring(wiring_high)


def test_a_combinational_loop_is_NAMED_rather_than_crashing():
    """This runs on model output, and a checker that dies on bad input is not
    a checker. The emitter refuses loops too; this must not need it to."""
    circuit = q4_circuit()
    circuit["components"].append({"ref": "N_a", "type": "not"})
    circuit["nets"]["n_qa"] = ["U1.QA", "DS1.a"]
    circuit["nets"]["n_loop"] = ["N_a.out", "N_a.in0", "Qa.pin"]

    with pytest.raises(WiringError) as exc:
        predicted(circuit, ("D", "C", "B", "A", "EN"), ("Qa",))
    assert "loop" in str(exc.value)


def test_the_prediction_disagrees_when_the_evaluator_does():
    """The check must be able to fail, and this is what failing looks like:
    the same comparison the spec basis uses, over a different reference."""
    from ohmwork.logisim_backend import TruthTable
    from ohmwork.spec import compare_tables

    table = predicted(q4_circuit(), ("D", "C", "B", "A", "EN"))
    width = len(table.inputs)
    broken = TruthTable(
        inputs=table.inputs, outputs=table.outputs,
        rows=tuple(row[:width] + (1 - row[width],) + row[width + 1:]
                   for row in table.rows),
        backend="fake", verification="external")

    assert not compare_tables(table, broken).agrees
    assert compare_tables(table, TruthTable(
        inputs=table.inputs, outputs=table.outputs, rows=table.rows,
        backend="fake", verification="external")).agrees


# ------------------------------------------------------------- the basis

def test_the_basis_says_what_was_checked_and_what_was_not():
    """A part-verified answer and a spec-verified one must not look the same.

    The two make different claims, and a reader who cannot tell them apart
    has been told the stronger one.
    """
    basis = part_basis(derive_wiring(q4_circuit(), "ttl7447", LOGISIM),
                       fake_probe_table())

    assert basis.kind == "part"
    assert "7447" in basis.headline
    assert "fake-logisim" in basis.headline
    # The reading a human is asked to check is the WIRING, not algebra.
    assert "U1" in basis.reading and "QA" in basis.reading
    assert basis.limit, "a basis with no stated limit is a claim with no edge"
    assert "reading of the question" in basis.limit


def test_the_map_says_when_a_value_passes_through_logic():
    """A gate in the path changes what a pin means, so the map must show it.
    A reader checking "did my signals land on the right pins" is entitled to
    know that one of them was inverted on the way."""
    circuit = q4_circuit()
    circuit["components"].append({"ref": "N_a", "type": "not"})
    circuit["nets"]["n_qa"] = ["U1.QA", "N_a.in0", "DS1.a"]
    circuit["nets"]["n_qa_hi"] = ["N_a.out", "Qa.pin"]

    reading = part_basis(derive_wiring(circuit, "ttl7447", LOGISIM),
                         fake_probe_table()).reading
    assert "N_a (not)" in reading


def test_the_choices_the_question_left_open_travel_with_the_map():
    """MEASURED on the first live 7447 solve, which tied the ripple-blanking
    pin LOW -- blanking a leading zero, which is a real decision about what
    the answer means. The map showed it as "held LOW"; a note saying so in
    words is what makes a reader notice."""
    basis = part_basis(derive_wiring(q4_circuit(), "ttl7447", LOGISIM),
                       fake_probe_table(),
                       ("RBI is tied low, so the digit 0 blanks",))
    assert "note: RBI is tied low" in basis.reading


def test_an_unknown_component_type_is_named_in_the_TARGET_s_words():
    """Also measured live: a model wrote a nonsense type and got back
    "'type' is not a part this build can place: 'type'" -- a helper's
    KeyError wearing a sentence, naming nothing it could act on. A rejection
    is fed straight back to the model, so it has to name the ref and say what
    the vocabulary is."""
    circuit = q4_circuit()
    circuit["components"].append({"ref": "X1", "type": "nand3"})

    with pytest.raises(WiringError) as exc:
        derive_wiring(circuit, "ttl7447", LOGISIM)
    message = str(exc.value)
    assert "X1" in message and "nand3" in message
    assert "and2" in message, "it must list what IS available"
