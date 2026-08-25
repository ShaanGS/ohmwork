"""The .circ emitter, judged by Logisim rather than by our own parser.

THE ACCEPTANCE TEST is test_emitted_q2_evaluates_identically_to_the_human_file.
It emits Q2 from our JSON, runs `--tty table` on the result, and requires the
same 32 rows Logisim produced from exp8_gates.circ — a file a student drew.

That single comparison checks the emitter, the placement, the routing, the
crossing rule and the label constraint at once, against a tool we did not
write, using a reference we did not compute. It is strictly stronger than
"our parser recovers what we intended", which is what the LTspice round trip
gives and which cannot catch a shared misunderstanding.

`--tty stats` is asserted too: a truth table can be right while a gate is
missing or invented, if the extra gate happens not to change the function.
"""

import json
from pathlib import Path

import pytest

from ohmwork.logisim_backend import LogisimBackend, locate_logisim
from ohmwork.logisim_emitter import (RoutingError, emit_circ, place,
                                     validate_wiring, write_circ)
from ohmwork.question import load_question

EXAMPLES = Path(__file__).parent.parent / "examples"
FIXTURES = Path(__file__).parent / "fixtures" / "logisim"


def _logisim_available():
    try:
        locate_logisim()
        return True
    except FileNotFoundError:
        return False


needs_logisim = pytest.mark.skipif(
    not _logisim_available(), reason="Logisim Evolution not installed")


def q2_circuit():
    """The resolved circuit description. The emitter takes THIS, not a
    Question: it has no business knowing about the input gate."""
    return load_question(
        json.loads((EXAMPLES / "q2.json").read_text(encoding="utf-8"))
    ).circuit


#: Our Q2 names against the student's. Recorded in q2.json's design_notes
#: ("signal naming") precisely so this comparison rests on a stated mapping
#: rather than an assumed one.
OURS_TO_THEIRS = {
    "EN": "E IN", "I3": "D3", "I2": "D2", "I1": "D1", "I0": "D0",
    "Y1": "OUT 1", "Y0": "OUT 2", "V": "V",
}
OUR_INPUTS = ("EN", "I3", "I2", "I1", "I0")
OUR_OUTPUTS = ("Y1", "Y0", "V")


# ------------------------------------------------------ THE acceptance test

@needs_logisim
def test_emitted_q2_evaluates_identically_to_the_human_file(tmp_path):
    """Two independently built circuits, one external evaluator, 32 rows."""
    circuit = q2_circuit()
    emitted = tmp_path / "q2.circ"
    write_circ(circuit, emitted)

    backend = LogisimBackend()
    ours = backend.truth_table(emitted, OUR_INPUTS, OUR_OUTPUTS)
    theirs = backend.truth_table(
        FIXTURES / "exp8_gates.circ",
        tuple(OURS_TO_THEIRS[n] for n in OUR_INPUTS),
        tuple(OURS_TO_THEIRS[n] for n in OUR_OUTPUTS))

    assert ours.verification == "external"
    assert len(ours.rows) == 32
    assert ours.rows == theirs.rows


@needs_logisim
def test_emitted_component_census_matches_our_json(tmp_path):
    """A correct truth table can still hide an invented or dropped gate.

    An extra gate whose output goes nowhere, or a duplicated one, changes
    nothing about the function. Logisim counts what is actually in the file.
    """
    circuit = q2_circuit()
    emitted = tmp_path / "q2.circ"
    write_circ(circuit, emitted)

    census = LogisimBackend().component_census(emitted)
    assert census == {"Pin": 8, "NOT Gate": 1, "AND Gate": 4, "OR Gate": 3}

    declared = [c["type"] for c in circuit["components"]]
    assert declared.count("input_pin") + declared.count("output_pin") == 8
    assert declared.count("not") == 1
    assert declared.count("and2") == 4
    assert declared.count("or2") + declared.count("or4") == 3


@needs_logisim
def test_emitted_file_is_read_in_2_7_1_compatibility_mode(tmp_path):
    """We emit the 2.7.1 dialect deliberately, so Evolution SHOULD say so.

    The note is expected, not a defect. If this ever stops appearing, the
    emitter has drifted towards Evolution's native dialect — which nothing in
    this repo has measured, and which would silently invalidate the pin table.
    """
    circuit = q2_circuit()
    emitted = tmp_path / "q2.circ"
    write_circ(circuit, emitted)
    table = LogisimBackend().truth_table(emitted, OUR_INPUTS, OUR_OUTPUTS)
    assert any("compatibility mode" in note for note in table.notes)


# ------------------------------------------------- the two routing hazards

def test_a_wire_may_not_end_mid_span_of_a_foreign_net():
    """The failure this format makes easiest to create and hardest to see.

    An endpoint landing on another wire's span IS a connection in Logisim.
    A route that happens to terminate mid-span of an unrelated net shorts
    them together with no visual cue whatsoever: on screen it looks like a
    wire that stops near another wire.
    """
    wires = [
        ("a", ((0, 0), (100, 0))),        # net a, horizontal
        ("b", ((50, 0), (50, 100))),      # net b, ENDS on net a's span
    ]
    with pytest.raises(RoutingError) as excinfo:
        validate_wiring(wires, ports={(0, 0), (100, 0), (50, 100)})
    assert "mid-span" in str(excinfo.value)
    assert "'b'" in str(excinfo.value) and "'a'" in str(excinfo.value)


def test_a_true_crossing_is_allowed():
    # Neither wire ends at the intersection, so it does not connect.
    wires = [
        ("a", ((0, 50), (100, 50))),
        ("b", ((50, 0), (50, 100))),
    ]
    validate_wiring(wires, ports={(0, 50), (100, 50), (50, 0), (50, 100)})


def test_a_t_junction_within_one_net_is_allowed():
    # Fan-out on the same net: legitimate, and what a human draws.
    wires = [
        ("a", ((0, 50), (100, 50))),
        ("a", ((50, 50), (50, 100))),
    ]
    validate_wiring(wires, ports={(0, 50), (100, 50), (50, 100)})


def test_an_endpoint_must_land_on_a_port_or_another_endpoint():
    # A wire ending in empty space is a dangling route: not a short, but not
    # something we should ever emit either.
    wires = [("a", ((0, 0), (100, 0)))]
    with pytest.raises(RoutingError) as excinfo:
        validate_wiring(wires, ports={(0, 0)})
    assert "(100, 0)" in str(excinfo.value)


def test_the_router_never_splits_a_wire_at_a_crossing():
    """A segment boundary at an intersection converts a crossing into a
    junction: identical drawing, different circuit.

    Guarded structurally rather than by inspection — every emitted segment is
    checked for an endpoint that coincides with a crossing point.
    """
    circuit = q2_circuit()
    _, wires, ports = place(circuit)
    crossings = set()
    for i, (_, a) in enumerate(wires):
        for _, b in wires[i + 1:]:
            (ax1, ay1), (ax2, ay2) = a
            (bx1, by1), (bx2, by2) = b
            if ay1 == ay2 and bx1 == bx2:
                h, v = (a, b)
            elif by1 == by2 and ax1 == ax2:
                h, v = (b, a)
            else:
                continue
            (hx1, hy), (hx2, _) = h
            (vx, vy1), (_, vy2) = v
            if (min(hx1, hx2) < vx < max(hx1, hx2)
                    and min(vy1, vy2) < hy < max(vy1, vy2)):
                crossings.add((vx, hy))
    endpoints = {p for _, w in wires for p in w}
    assert crossings & endpoints == set()


def test_emitted_geometry_passes_its_own_wiring_check():
    circuit = q2_circuit()
    _, wires, ports = place(circuit)
    validate_wiring(wires, ports)          # must not raise


# ---------------------------------------------------------- file shape

def test_every_emitted_label_is_vhdl_safe():
    # Otherwise Logisim renames it and appends a hash we cannot reproduce,
    # making our own results unmatchable.
    from ohmwork.logisim_symbols import SAFE_LABEL
    import re
    text = emit_circ(q2_circuit())
    for label in re.findall(r'<a name="label" val="([^"]*)"/>', text):
        assert SAFE_LABEL.match(label), label


def test_emitted_file_declares_only_primitive_libraries():
    from ohmwork.logisim_symbols import PRIMITIVE_LIBS, resolve_lib_indices
    import re
    text = emit_circ(q2_circuit())
    libs = resolve_lib_indices(text)
    used = {libs[i] for i in re.findall(r'<comp lib="(\d+)"', text)}
    assert used <= PRIMITIVE_LIBS


def test_everything_is_on_the_ten_unit_grid():
    _, wires, ports = place(q2_circuit())
    for _, (a, b) in wires:
        assert a[0] % 10 == 0 and a[1] % 10 == 0
        assert b[0] % 10 == 0 and b[1] % 10 == 0
    for x, y in ports:
        assert x % 10 == 0 and y % 10 == 0


def test_output_pins_are_declared_as_outputs():
    # Absence of output= means INPUT. adder_subtractor.circ got this wrong
    # for all fourteen of its pins; we must not.
    text = emit_circ(q2_circuit())
    blocks = text.split("<comp ")
    outputs = [b for b in blocks if 'val="Y1"' in b or 'val="Y0"' in b
               or 'val="V"' in b]
    assert len(outputs) == 3
    for block in outputs:
        assert 'name="output" val="true"' in block


# ------------------------------------------- can the acceptance test fail?

@needs_logisim
def test_a_miswired_circuit_produces_a_different_table(tmp_path):
    """A test that cannot fail is worth nothing, so check that this one can.

    Swapping which pin drives which net is exactly the bug a placement or
    routing error would produce, and it must change the table. (Swapping two
    NETS' contents between their dict keys does NOT — that only renames them,
    and the connectivity is identical. Worth knowing before reading too much
    into a mutation that shows no difference.)
    """
    import copy

    good = q2_circuit()
    reference = LogisimBackend().truth_table(
        write_circ(good, tmp_path / "good.circ"), OUR_INPUTS, OUR_OUTPUTS)

    data = json.loads(
        (EXAMPLES / "q2.json").read_text(encoding="utf-8"))
    broken = copy.deepcopy(data)
    nets = broken["circuit"]["nets"]
    nets["i1"] = ["I2.pin" if m == "I1.pin" else m for m in nets["i1"]]
    nets["i2"] = ["I1.pin" if m == "I2.pin" else m for m in nets["i2"]]

    mutated = LogisimBackend().truth_table(
        write_circ(load_question(broken).circuit, tmp_path / "bad.circ"),
        OUR_INPUTS, OUR_OUTPUTS)
    assert mutated.rows != reference.rows


@needs_logisim
def test_rows_are_canonically_ordered_so_two_files_are_comparable(tmp_path):
    """Logisim enumerates in ITS column order, which differs between files.

    Our emitted Q2 counts up I3-first; the student's file counts up D0-first.
    Comparing as sequences would report a difference that is not one, so rows
    are sorted by the input tuple in OUR column order. Same lesson as the
    `.step` ascending-order flip: never trust the order a tool emits rows in.
    """
    table = LogisimBackend().truth_table(
        write_circ(q2_circuit(), tmp_path / "q2.circ"),
        OUR_INPUTS, OUR_OUTPUTS)
    inputs_only = [row[:len(OUR_INPUTS)] for row in table.rows]
    assert inputs_only == sorted(inputs_only)
    assert inputs_only[0] == (0, 0, 0, 0, 0)
    assert inputs_only[-1] == (1, 1, 1, 1, 1)
