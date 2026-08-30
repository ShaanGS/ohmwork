"""Tests for ohmwork.emitter: JSON circuit description -> LTspice .asc text.

The main fixture is the known-good series regulator from CLAUDE.md.
The tests do not assume any particular placement. Instead they re-derive
pin positions from the SYMBOL lines in the output (using the verified
pin table) and check structural invariants:

  - every pin has exactly one 16-unit stub wire attached
  - the far end of every stub carries a FLAG with the right net name
  - nothing else exists (no routed wires)

This is deliberately a miniature version of what parser.py will do in
build step 2.
"""

import re

import pytest

from ohmwork.emitter import CircuitError, emit
from ohmwork.symbols import pin_positions

SYMBOL_RE = re.compile(r"^SYMBOL (\S+) (-?\d+) (-?\d+) (R\d+)$")
WIRE_RE = re.compile(r"^WIRE (-?\d+) (-?\d+) (-?\d+) (-?\d+)$")
FLAG_RE = re.compile(r"^FLAG (-?\d+) (-?\d+) (\S+)$")


def reference_circuit():
    """The series voltage regulator from CLAUDE.md, as emitter input."""
    return {
        "components": [
            {"ref": "V1", "type": "voltage", "value": "15"},
            {"ref": "R1", "type": "res", "value": "1.8k"},
            {"ref": "D1", "type": "zener", "part": "DZ8V3"},
            {"ref": "Q1", "type": "npn", "part": "QN"},
            {"ref": "RL", "type": "res", "value": "2k"},
        ],
        "nets": {
            "vin": ["V1.+", "R1.a", "Q1.C"],
            "vb": ["R1.b", "D1.cathode", "Q1.B"],
            "vout": ["Q1.E", "RL.a"],
            "0": ["V1.-", "D1.anode", "RL.b"],
        },
        "directives": [
            # Anchored per device policy path (b): BV is defined at IBV,
            # so a card without IBV is not the device the question asked
            # for. The emitter refuses unanchored cards outright.
            ".model DZ8V3 D(BV=8.3 IBV=5m)",
            ".model QN NPN(BF=100)",
            ".op",
        ],
    }


def emitted_lines():
    return emit(reference_circuit()).split("\r\n")


def parse(lines):
    """Pull SYMBOL/WIRE/FLAG facts out of emitted text."""
    symbols, wires, flags = [], [], {}
    for line in lines:
        if m := SYMBOL_RE.match(line):
            symbols.append((m[1], (int(m[2]), int(m[3])), m[4]))
        elif m := WIRE_RE.match(line):
            wires.append(((int(m[1]), int(m[2])), (int(m[3]), int(m[4]))))
        elif m := FLAG_RE.match(line):
            flags[(int(m[1]), int(m[2]))] = m[3]
    return symbols, wires, flags


# ---------------------------------------------------------------- structure


def test_header():
    lines = emitted_lines()
    assert lines[0] == "Version 4.1"
    assert lines[1].startswith("SHEET 1 ")


def test_crlf_line_endings():
    assert "\r\n" in emit(reference_circuit())


def test_all_components_emitted():
    text = emit(reference_circuit())
    symbols, _, _ = parse(text.split("\r\n"))
    assert [s[0] for s in symbols] == ["voltage", "res", "zener", "npn", "res"]
    for ref in ["V1", "R1", "D1", "Q1", "RL"]:
        assert f"SYMATTR InstName {ref}" in text


def test_values_and_parts_emitted():
    # Both scalar values and part/model names land in the single Value
    # attribute slot the .asc format has; the schema distinction is ours.
    text = emit(reference_circuit())
    for value in ["15", "1.8k", "DZ8V3", "QN", "2k"]:
        assert f"SYMATTR Value {value}" in text


def test_rejects_part_on_a_value_component():
    circuit = reference_circuit()
    circuit["components"][1] = {"ref": "R1", "type": "res", "part": "1.8k"}
    with pytest.raises(CircuitError, match="R1"):
        emit(circuit)


def test_rejects_value_on_a_part_component():
    circuit = reference_circuit()
    circuit["components"][2] = {"ref": "D1", "type": "zener", "value": "8.3"}
    with pytest.raises(CircuitError, match="D1"):
        emit(circuit)


def test_rejects_missing_value():
    circuit = reference_circuit()
    circuit["components"][1] = {"ref": "R1", "type": "res"}
    with pytest.raises(CircuitError, match="R1"):
        emit(circuit)


def test_rejects_missing_part():
    circuit = reference_circuit()
    circuit["components"][3] = {"ref": "Q1", "type": "npn"}
    with pytest.raises(CircuitError, match="Q1"):
        emit(circuit)


def test_directives_emitted():
    text = emit(reference_circuit())
    assert "!.model DZ8V3 D(BV=8.3 IBV=5m)" in text
    assert "!.model QN NPN(BF=100)" in text
    assert "!.op" in text


def test_rejects_unanchored_diode_model_card():
    # The regression this guards against actually happened: the Q1
    # experiment, deliverable and all four pinned baselines were built
    # on D(BV=8.3 N=1.2) — a card the policy had already outlawed —
    # because an old fixture was reused as input. The file emitted,
    # simulated, converged, and reported a plausible wrong answer.
    # Enforcement therefore lives here, at the chokepoint every .asc
    # passes through, not in reviewer memory.
    circuit = reference_circuit()
    circuit["directives"][0] = ".model DZ8V3 D(BV=8.3 N=1.2)"
    with pytest.raises(CircuitError, match="IBV"):
        emit(circuit)


def test_anchored_diode_model_card_accepted():
    emit(reference_circuit())  # fixture itself is anchored; must pass


def test_directive_starting_with_semicolon_becomes_a_comment():
    # The deliverable carries inactive runs as comment lines the student
    # can uncomment in LTspice; they must land as ';' TEXT payloads.
    circuit = reference_circuit()
    circuit["directives"].append(";.dc V1 12 20 1  uncomment me")
    text = emit(circuit)
    assert "2 ;.dc V1 12 20 1  uncomment me" in text
    assert "!;.dc" not in text


def test_all_coordinates_on_16_grid():
    symbols, wires, flags = parse(emitted_lines())
    coords = [s[1] for s in symbols]
    coords += [p for w in wires for p in w]
    coords += list(flags)
    for x, y in coords:
        assert x % 16 == 0 and y % 16 == 0


def test_every_pin_has_one_stub_ending_in_the_right_flag():
    """The core invariant: connectivity is pin -> 16-unit stub -> FLAG."""
    circuit = reference_circuit()
    symbols, wires, flags = parse(emitted_lines())

    # Rebuild pin -> net from the input, keyed by "REF.pin".
    pin_net = {}
    for net, pins in circuit["nets"].items():
        for pin in pins:
            pin_net[pin] = net

    # Rebuild absolute pin positions from the emitted SYMBOL lines.
    refs = [c["ref"] for c in circuit["components"]]
    all_pins = {}  # (x, y) -> "REF.pin"
    for ref, (sym, anchor, rot) in zip(refs, symbols):
        for name, pos in pin_positions(sym, anchor, rot).items():
            all_pins[pos] = f"{ref}.{name}"

    assert len(wires) == len(all_pins) == len(flags) == 11

    seen_pins = set()
    for a, b in wires:
        # Exactly one end of each wire is a pin, the other is a flag.
        pin_end = a if a in all_pins else b
        flag_end = b if a in all_pins else a
        assert pin_end in all_pins, f"wire {a}-{b} touches no pin"
        assert flag_end in flags, f"wire {a}-{b} has no flag on its free end"
        # Stub is 16 units long and axis-aligned.
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 16
        # Flag carries the net the input assigned to this pin.
        assert flags[flag_end] == pin_net[all_pins[pin_end]]
        seen_pins.add(pin_end)

    assert len(seen_pins) == 11, "some pin has no stub of its own"


def test_stub_does_not_overlap_symbol_body():
    """The free (flag) end of a stub must sit outside the symbol's bounding
    box, so the label doesn't land on the symbol drawing."""
    symbols, wires, flags = parse(emitted_lines())

    boxes = []
    for sym, anchor, rot in symbols:
        pins = list(pin_positions(sym, anchor, rot).values())
        xs = [p[0] for p in pins]
        ys = [p[1] for p in pins]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))

    for a, b in wires:
        flag_end = a if a in flags else b
        for x1, y1, x2, y2 in boxes:
            strictly_inside = x1 < flag_end[0] < x2 and y1 < flag_end[1] < y2
            assert not strictly_inside


def test_sheet_covers_all_content():
    """SHEET must grow with the drawing: every emitted coordinate, including
    directive TEXT lines, sits inside the declared sheet."""
    # A ring of 8 resistors is wider than the default 880-unit sheet.
    n = 8
    components = [
        {"ref": f"R{i}", "type": "res", "value": "1k"} for i in range(1, n + 1)
    ]
    nets = {
        f"n{i}": [f"R{i}.b", f"R{i + 1}.a"] for i in range(1, n)
    }
    nets["0"] = [f"R{n}.b", "R1.a"]
    circuit = {"components": components, "nets": nets,
               "directives": [".op"]}

    lines = emit(circuit).split("\r\n")
    sheet = re.match(r"^SHEET 1 (\d+) (\d+)$", lines[1])
    assert sheet, "missing or malformed SHEET line"
    width, height = int(sheet[1]), int(sheet[2])

    text_re = re.compile(r"^TEXT (-?\d+) (-?\d+) ")
    coords = []
    symbols, wires, flags = parse(lines)
    coords += [s[1] for s in symbols]
    coords += [p for w in wires for p in w]
    coords += list(flags)
    coords += [
        (int(m[1]), int(m[2])) for m in map(text_re.match, lines) if m
    ]
    assert max(x for x, _ in coords) < width
    assert max(y for _, y in coords) < height
    assert width > 880, "sheet did not grow for a wide circuit"


# --------------------------------------------------------------- validation


def test_rejects_unknown_component_type():
    circuit = reference_circuit()
    circuit["components"][0]["type"] = "flux_capacitor"
    with pytest.raises(CircuitError, match="flux_capacitor"):
        emit(circuit)


def test_rejects_duplicate_ref():
    circuit = reference_circuit()
    circuit["components"][1]["ref"] = "V1"
    with pytest.raises(CircuitError, match="V1"):
        emit(circuit)


def test_rejects_missing_ground_net():
    circuit = reference_circuit()
    circuit["nets"]["gnd"] = circuit["nets"].pop("0")
    with pytest.raises(CircuitError, match="ground"):
        emit(circuit)


def test_rejects_single_pin_net():
    circuit = reference_circuit()
    # Move RL.a out of vout into a net of its own: vout is left floating
    # with one pin, and "float" also has one pin.
    circuit["nets"]["vout"].remove("RL.a")
    circuit["nets"]["float"] = ["RL.a"]
    with pytest.raises(CircuitError):
        emit(circuit)


def test_rejects_unknown_pin_name():
    circuit = reference_circuit()
    circuit["nets"]["vin"][0] = "V1.plus"
    with pytest.raises(CircuitError, match="plus"):
        emit(circuit)


def test_rejects_pin_used_twice():
    """The rejection must TEACH the merge rule, naming both nets.

    Measured 2026-08-30: `pin C1.b appears in more than one net` came back
    in every pooled Q3 run -- six rejected attempts across two runs. The
    model thinks a junction is two nets touching; in this schema two nets
    sharing a pin ARE one net. The old message stated the fact and nothing
    else, and the design loop feeds it back verbatim, so the message is the
    only place the model can learn the rule. Same fix as the Logisim
    router's two-net port error, which already teaches it.
    """
    circuit = reference_circuit()
    circuit["nets"]["vout"].append("R1.a")  # already in vin
    with pytest.raises(CircuitError) as caught:
        emit(circuit)
    message = str(caught.value)
    assert "R1.a" in message
    assert "'vin'" in message and "'vout'" in message
    assert "ONE net" in message
    assert "merge" in message.lower()
    assert "every pin" in message


def test_rejects_pin_listed_twice_in_one_net():
    """Same pin twice in ONE net is a different mistake and must say so.

    Telling someone to merge a net with itself is instructions that cannot
    be followed; here the fix is deleting the duplicate entry.
    """
    circuit = reference_circuit()
    circuit["nets"]["vin"].append("R1.a")  # already in vin itself
    with pytest.raises(CircuitError) as caught:
        emit(circuit)
    message = str(caught.value)
    assert "R1.a" in message
    assert "twice" in message
    assert "'vin'" in message
    assert "merge" not in message.lower()


def test_rejects_unconnected_pin():
    circuit = reference_circuit()
    circuit["nets"]["vin"].remove("Q1.C")
    with pytest.raises(CircuitError, match="Q1.C"):
        emit(circuit)


def test_rejects_net_referencing_unknown_component():
    circuit = reference_circuit()
    circuit["nets"]["vin"].append("R9.a")
    with pytest.raises(CircuitError, match="R9"):
        emit(circuit)
