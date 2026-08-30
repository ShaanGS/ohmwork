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

from ohmwork.emitter import STUB_LEN as STUB, CircuitError, emit
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


# ------------------------------------------------------------------ layout
#
# The owner's requirement, 2026-08-31, after opening the first solved Q3
# .asc: "it should of course look the human way". The layout reads like a
# hand-drawn schematic -- signal flowing left to right along a rail, shunt
# parts hanging vertically with ground below, the source at the far left --
# while CONNECTIVITY stays label-based: no routed wires, ever, because
# routing is where silent errors live. These tests assert orientation and
# ordering, never exact coordinates.


def q3_circuit():
    """The solved Q3 shape: bridge + C-L-C + damped zener regulator."""
    return {
        "components": [
            {"ref": "V1", "type": "voltage", "value": "SINE(0 16.97 50)"},
            {"ref": "D1", "type": "diode", "part": "1N4007"},
            {"ref": "D2", "type": "diode", "part": "1N4007"},
            {"ref": "D3", "type": "diode", "part": "1N4007"},
            {"ref": "D4", "type": "diode", "part": "1N4007"},
            {"ref": "C1", "type": "cap", "value": "470u"},
            {"ref": "L1", "type": "ind", "value": "1m"},
            {"ref": "R2", "type": "res", "value": "10"},
            {"ref": "C2", "type": "cap", "value": "470u"},
            {"ref": "R1", "type": "res", "value": "390"},
            {"ref": "DZ1", "type": "zener", "part": "DZ6V2"},
            {"ref": "RL", "type": "res", "value": "1k"},
        ],
        "nets": {
            "vin": ["V1.+", "D1.anode", "D3.cathode"],
            "vin2": ["V1.-", "D2.anode", "D4.cathode"],
            "vrect": ["D1.cathode", "D2.cathode", "C1.a", "L1.a"],
            "nlx": ["L1.b", "R2.a"],
            "vfilt": ["R2.b", "C2.a", "R1.a"],
            "vout": ["R1.b", "DZ1.cathode", "RL.a"],
            "0": ["D3.anode", "D4.anode", "C1.b", "C2.b", "DZ1.anode",
                  "RL.b"],
        },
        "directives": [".model DZ6V2 D(BV=6.2 IBV=5m)",
                       ".tran 0 200m 100m 100u"],
    }


def placed_pins(circuit):
    """ref -> {pin: (x, y)} recovered from the emitted SYMBOL lines."""
    symbols, _, _ = parse(emit(circuit).split("\r\n"))
    refs = [c["ref"] for c in circuit["components"]]
    return {ref: pin_positions(sym, anchor, rot)
            for ref, (sym, anchor, rot) in zip(refs, symbols)}


def test_a_grounded_component_hangs_vertical_with_ground_down():
    """A shunt element reads as one: upright, ground symbol below it."""
    pins = placed_pins(reference_circuit())
    # D1 is the shunt zener: anode on ground, so cathode above anode.
    assert pins["D1"]["anode"][0] == pins["D1"]["cathode"][0]
    assert pins["D1"]["anode"][1] > pins["D1"]["cathode"][1]
    # RL: b on ground, below a.
    assert pins["RL"]["a"][0] == pins["RL"]["b"][0]
    assert pins["RL"]["b"][1] > pins["RL"]["a"][1]


def test_a_series_component_lies_horizontal_facing_downstream():
    """R1 carries vin to vb: it lies flat, upstream pin on the left."""
    pins = placed_pins(reference_circuit())
    assert pins["R1"]["a"][1] == pins["R1"]["b"][1]
    assert pins["R1"]["a"][0] < pins["R1"]["b"][0]


def test_the_source_sits_leftmost():
    for circuit in (reference_circuit(), q3_circuit()):
        pins = placed_pins(circuit)
        v1_x = max(x for x, _ in pins["V1"].values())
        for ref, positions in pins.items():
            if ref == "V1":
                continue
            assert v1_x < min(x for x, _ in positions.values()), (
                f"{ref} sits at or left of the source")


def test_signal_flows_left_to_right():
    """Down the Q3 chain -- bridge, filter, regulator, load -- x rises."""
    pins = placed_pins(q3_circuit())

    def x_of(ref):
        return min(x for x, _ in pins[ref].values())

    assert x_of("V1") < x_of("D1") < x_of("L1") < x_of("R1")
    assert x_of("R1") <= x_of("RL")


def test_bridge_diodes_agree_with_the_current_direction():
    """D1 conducts vin -> vrect (anode left); D3 returns ground to vin
    (a shunt, cathode up)."""
    pins = placed_pins(q3_circuit())
    assert pins["D1"]["anode"][0] < pins["D1"]["cathode"][0]
    assert pins["D3"]["cathode"][1] < pins["D3"]["anode"][1]


def test_no_two_symbols_share_an_anchor():
    for circuit in (reference_circuit(), q3_circuit()):
        symbols, _, _ = parse(emit(circuit).split("\r\n"))
        anchors = [anchor for _, anchor, _ in symbols]
        assert len(anchors) == len(set(anchors))


def test_layout_coordinates_stay_on_grid_and_non_negative():
    for circuit in (reference_circuit(), q3_circuit()):
        symbols, wires, flags = parse(emit(circuit).split("\r\n"))
        coords = [a for _, a, _ in symbols]
        coords += [p for w in wires for p in w]
        coords += list(flags)
        for x, y in coords:
            assert x % 16 == 0 and y % 16 == 0
            assert x >= 0 and y >= 0


# ------------------------------------------------------------------ routing
#
# The owner's second requirement, same day, after seeing the label-only
# layout beside a classmate's hand-drawn file: real wires. The router is
# deliberately conservative: wires run along the pin line, climb to an
# overhead lane when the direct line is blocked, and any net with no clean
# path FALLS BACK to labels -- a labelled net is ugly and correct, a
# clever route is where silent shorts live. The proof is the geometric
# round trip: the parser rebuilds connectivity from the wires alone.


def normalized(nets):
    return {net: sorted(pins) for net, pins in nets.items()}


def test_routed_output_round_trips_to_the_same_netlist():
    """THE acceptance test: whatever the router drew, the parser must
    recover exactly the input connectivity from geometry alone."""
    from ohmwork.parser import parse_asc

    for circuit in (reference_circuit(), q3_circuit()):
        recovered = parse_asc(emit(circuit))
        assert normalized(recovered["nets"]) == normalized(circuit["nets"])


def test_the_output_contains_real_wires_beyond_the_stubs():
    """More wires than pins means actual routing happened."""
    circuit = q3_circuit()
    _, wires, _ = parse(emit(circuit).split("\r\n"))
    pin_count = sum(len(pins) for pins in circuit["nets"].values())
    assert len(wires) > pin_count


def test_a_routed_net_carries_one_label_not_one_per_pin():
    """A wired net reads as wired: a single name on the wire, like a human
    labels a node -- not a flag repeated on every pin."""
    circuit = q3_circuit()
    _, _, flags = parse(emit(circuit).split("\r\n"))
    names = list(flags.values())
    # nlx is a simple two-pin series net: it must be wired, one label.
    assert names.count("nlx") == 1


def test_component_bodies_never_overlap():
    """The owner's screenshot: D1 and D2 drawn into each other, labels
    colliding. Placement must keep every component's body region (its
    pin hull, padded for the drawing and its labels) disjoint from every
    other's."""
    for circuit in (reference_circuit(), q3_circuit()):
        symbols, _, _ = parse(emit(circuit).split("\r\n"))
        boxes = []
        for sym, anchor, rot in symbols:
            pins = list(pin_positions(sym, anchor, rot).values())
            xs = [p[0] for p in pins]
            ys = [p[1] for p in pins]
            boxes.append((min(xs) - 24, min(ys) - 24,
                          max(xs) + 24, max(ys) + 24))
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                overlap = (a[0] < b[2] and b[0] < a[2]
                           and a[1] < b[3] and b[1] < a[3])
                assert not overlap, (i, a, b)


def test_every_q3_net_routes_with_wires():
    """The owner's screenshot again: vin2 fell back to scattered labels
    and the bridge read as nonsense. On the placement built for it, every
    non-ground net of the flagship circuit must actually wire: exactly
    one label per net, not one per pin."""
    circuit = q3_circuit()
    _, _, flags = parse(emit(circuit).split("\r\n"))
    names = list(flags.values())
    for net in circuit["nets"]:
        if net == "0":
            continue
        assert names.count(net) == 1, f"net {net} fell back to labels"


def test_ground_pins_keep_their_own_ground_symbols():
    """Net 0 stays one flag per pin: LTspice renders each as the ground
    triangle, which is exactly how a hand drawing shows ground."""
    circuit = q3_circuit()
    _, _, flags = parse(emit(circuit).split("\r\n"))
    grounds = [pos for pos, name in flags.items() if name == "0"]
    assert len(grounds) == len(circuit["nets"]["0"])


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


def test_every_pin_has_exactly_one_stub_of_its_own():
    """The pin-level invariant that survived the router: every pin exits
    through one 16-unit axis-aligned stub. What used to be asserted here
    about flags (one per pin) belongs to the pre-routing world; a routed
    net carries one label, and the connectivity claim now lives in
    test_routed_output_round_trips_to_the_same_netlist, which is
    strictly stronger -- the parser proves it from geometry alone."""
    circuit = reference_circuit()
    symbols, wires, flags = parse(emitted_lines())

    refs = [c["ref"] for c in circuit["components"]]
    all_pins = {}  # (x, y) -> "REF.pin"
    for ref, (sym, anchor, rot) in zip(refs, symbols):
        for name, pos in pin_positions(sym, anchor, rot).items():
            all_pins[pos] = f"{ref}.{name}"

    stubs = [w for w in wires
             if (w[0] in all_pins) != (w[1] in all_pins)
             and abs(w[0][0] - w[1][0]) + abs(w[0][1] - w[1][1]) == STUB]
    seen = {a if a in all_pins else b for a, b in stubs}
    assert seen == set(all_pins), "some pin has no stub of its own"


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


def test_a_ground_flavoured_net_name_is_told_ground_is_named_0():
    """Measured twice, runs 8 and 9 of Q3: the model wrote single-pin nets
    named '0_zener' and '0_dz' -- clearly meaning "this pin is grounded" --
    and was told only "floating node", which does not say that ground is
    spelled exactly '0'."""
    circuit = reference_circuit()
    circuit["nets"]["0"].remove("RL.b")
    circuit["nets"]["0_rl"] = ["RL.b"]
    with pytest.raises(CircuitError) as caught:
        emit(circuit)
    message = str(caught.value)
    assert "'0_rl'" in message
    assert "named exactly '0'" in message


def test_a_net_name_written_as_a_pin_teaches_that_nets_do_not_nest():
    """Measured on the eighth Q3 run, which DIED on this: the model twice
    wrote the ground net "0" as a member of net 'zener_anode' -- trying to
    say "this net is grounded" -- was told only "no component named 0",
    repeated itself, and the identical-failure stop ended the run. The
    reader can see what it meant; the message must say what to do."""
    circuit = reference_circuit()
    circuit["nets"]["vout"].append("0")
    with pytest.raises(CircuitError) as caught:
        emit(circuit)
    message = str(caught.value)
    assert "is a NET" in message and "not a pin" in message
    assert "'0'" in message and "'vout'" in message
    assert "do not nest" in message


def test_a_non_ground_net_name_written_as_a_pin_says_merge():
    """The same mistake with an ordinary net: attempt 3 of the same run
    wrote net 'RL_a' referencing 'vfilt', a net this circuit had."""
    circuit = reference_circuit()
    circuit["nets"]["vin"].append("vout")
    with pytest.raises(CircuitError) as caught:
        emit(circuit)
    message = str(caught.value)
    assert "is a NET" in message and "not a pin" in message
    assert "merge" in message.lower()
