"""The 7447 and the seven-segment display: geometry, and proof of it.

Q4 -- "design a BCD-to-seven-segment display circuit using the 7447-decoder
IC" -- was blocked on FIXTURE for as long as this project has existed. Not on
code: the emitter, the gate and the evaluator were all ready. What was missing
was a measurement, because the pin table refuses to guess and those two parts
live in Logisim Evolution's TTL and I/O libraries, which no file here
contained.

THE EVIDENCE, and the reason it is worth trusting. Three sources, and no two
of them share an implementation:

1. **Geometry**, by the dead-end method, across five 7447 instances and three
   display instances in public Evolution files (cited in fixtures/README).
   A port is a coordinate where exactly one wire terminates.

2. **Port order**, read out of Evolution's own class constants:
   `com/cburch/logisim/std/ttl/Ttl7447` declares PORT_INDEX_B, C, LT, BI,
   RBI, D, A, QE, QD, QC, QB, QA, QG, QF -- fourteen ports for a sixteen-pin
   package, because pin 8 (GND) and pin 16 (Vcc) are not connectable.

3. **Behaviour**, by writing a circuit FROM the pin table and handing it to
   Evolution. This is the one that cannot be fooled: a wrong offset leaves a
   pin unconnected, and an unconnected BCD input does not decode.

The datasheet is a fourth, outside check on top of that -- see
`test_the_valid_BCD_digits_decode_exactly_as_the_datasheet_says`.

WHAT IS STILL WEAKER THAN IT LOOKS, stated because a table that hides its
assumptions is worse than one that has none: the display's decimal-point port
was never wired in any file examined, so its coordinate follows the port
order rather than a measurement. `ASSUMED_PORTS` carries that in data, and a
test below asserts the list has not grown quietly.
"""

import subprocess
from pathlib import Path

import pytest

from ohmwork.logisim_symbols import ASSUMED_PORTS, PORTS, ports_of

FIXTURES = Path(__file__).parent / "fixtures" / "logisim"
DISPLAY_FIXTURE = FIXTURES / "evolution_7447_display.circ"


# --------------------------------------------------- geometry, offline


def dead_ends(text):
    """Coordinates where exactly one wire terminates and none passes through.

    The method, unchanged: degree 1 PROVES a port; degree != 1 does not
    disprove one, so this yields candidates that a hypothesis must explain.
    """
    import re
    from collections import Counter

    wires = [tuple(int(n) for n in m)
             for m in re.findall(
                 r'<wire\s+from="\((-?\d+),(-?\d+)\)"\s+to="\((-?\d+),(-?\d+)\)"',
                 text)]
    degree = Counter()
    spans = set()
    for x1, y1, x2, y2 in wires:
        degree[(x1, y1)] += 1
        degree[(x2, y2)] += 1
        if x1 == x2:
            spans.update((x1, y) for y in range(min(y1, y2) + 10, max(y1, y2), 10))
        elif y1 == y2:
            spans.update((x, y1) for x in range(min(x1, x2) + 10, max(x1, x2), 10))
    return {point for point, count in degree.items()
            if count == 1 and point not in spans}


def locations(text, component):
    import re
    return [(int(x), int(y)) for x, y, name in re.findall(
        r'<comp\s+lib="[^"]+"\s+loc="\((-?\d+),(-?\d+)\)"\s+name="([^"]+)"', text)
        if name == component]


def test_the_display_ports_are_dead_ends_in_a_real_evolution_file():
    """Pure evidence: imports the fixture, not our emitter.

    Seven of the eight ports are wired here. The eighth is the decimal point,
    which nobody wired -- and that absence is the reason it is flagged as an
    assumption rather than quietly listed beside the measured ones.
    """
    text = DISPLAY_FIXTURE.read_text(encoding="utf-8", errors="replace")
    ends = dead_ends(text)
    places = locations(text, "7-Segment Display")
    assert places, "the fixture no longer contains a seven-segment display"

    measured = [port for port in ports_of("7-Segment Display")
                if port.name != "dp"]
    for (cx, cy) in places:
        for port in measured:
            assert (cx + port.dx, cy + port.dy) in ends, (
                f"segment {port.name} at offset {port.offset} is not a wire "
                f"endpoint on the display at ({cx},{cy})")


def test_the_display_is_a_two_row_block_and_the_7447_a_DIP16():
    """The shape itself, so a single transcription slip is visible.

    Both layouts are regular, and stating the regularity separately from the
    coordinates means a typo in one offset breaks a test that reads like a
    sentence rather than like a table.
    """
    display = {port.offset for port in ports_of("7-Segment Display")}
    assert display == {(x, y) for y in (0, 60) for x in (0, 10, 20, 30)}

    ttl = ports_of("7447")
    assert len(ttl) == 14, "a DIP-16 minus GND and Vcc"
    bottom = sorted(p.dx for p in ttl if p.dy == 30)
    top = sorted(p.dx for p in ttl if p.dy == -30)
    assert bottom == [10, 30, 50, 70, 90, 110, 130]      # pins 1-7
    assert top == [30, 50, 70, 90, 110, 130, 150]        # pins 9-15
    # Pin 8 (GND, bottom right at x=150) and pin 16 (Vcc, top left at x=10)
    # are absent BY DESIGN, not by oversight.
    assert 150 not in bottom and 10 not in top


def test_the_7447_inputs_and_outputs_are_on_the_sides_a_DIP_puts_them():
    ttl = {port.name: port for port in ports_of("7447")}
    for name in ("A", "B", "C", "D", "LT", "BI", "RBI"):
        assert ttl[name].kind == "in" and ttl[name].dy == 30
    for name in ("QA", "QB", "QC", "QD", "QE", "QF", "QG"):
        assert ttl[name].kind == "out" and ttl[name].dy == -30


def test_the_assumed_ports_list_does_not_grow_quietly():
    """One assumption is a labelled weakness. Three would be a habit."""
    assert set(ASSUMED_PORTS) == {("7-Segment Display", "dp")}
    for reason in ASSUMED_PORTS.values():
        assert reason.strip(), "an assumption with no reason is just a guess"


def test_a_south_facing_7447_is_REFUSED_rather_than_assumed():
    """Two instances in the derivation sample carried facing="south", and
    their geometry is not this one. Measuring one shape does not licence the
    other -- the same rule that made an unmeasured gate width a hard error."""
    from ohmwork.logisim_symbols import UnmeasuredGeometryError

    with pytest.raises(UnmeasuredGeometryError):
        ports_of("7447", {"facing": "south"})


# ------------------------------------------- behaviour, against Evolution


def evolution():
    try:
        from ohmwork.logisim_backend import locate_logisim
        return locate_logisim()
    except Exception:                                   # noqa: BLE001
        return None


needs_evolution = pytest.mark.skipif(
    evolution() is None,
    reason="Logisim Evolution is not installed, so the 7447's geometry cannot "
           "be checked by evaluating a circuit built from it. The offline "
           "tests above cover the display only.")


def write_probe(path):
    """A 7447 with a labelled Pin on every port, built FROM the pin table.

    Deliberately not built by the emitter: this must fail if the TABLE is
    wrong, and routing it through the emitter would only prove the emitter
    and the table agree with each other.
    """
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
             '<project source="3.8.0" version="1.0">',
             '  <lib desc="#Wiring" name="0"/>',
             '  <lib desc="#Gates" name="1"/>',
             '  <lib desc="#TTL" name="6"/>',
             '  <lib desc="#Base" name="8"/>',
             '  <main name="main"/>',
             '  <circuit name="main">',
             '    <comp lib="6" loc="(300,300)" name="7447"/>']
    for port in ports_of("7447"):
        px, py = 300 + port.dx, 300 + port.dy
        out = port.kind == "out"
        ex, ey = (px, py - 60) if out else (px, py + 60)
        lines.append(f'    <comp lib="0" loc="({ex},{ey})" name="Pin">')
        lines.append(f'      <a name="facing" val="{"south" if out else "north"}"/>')
        if out:
            lines.append('      <a name="output" val="true"/>')
        lines.append(f'      <a name="label" val="{port.name}"/>')
        lines.append('    </comp>')
        lines.append(f'    <wire from="({px},{py})" to="({ex},{ey})"/>')
    lines += ['  </circuit>', '</project>']
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def evolution_table(path):
    proc = subprocess.run(
        [str(evolution()), "--no-splash", "--tty", "table", str(path)],
        capture_output=True, text=True, timeout=300)
    lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
    assert lines, f"Evolution printed no table. stderr: {proc.stderr[:400]}"
    header = lines[0].split()
    return [dict(zip(header, line.split())) for line in lines[1:]]


#: The 7447 as the datasheet defines it, for the ten VALID BCD digits, in
#: segment order a..g. Active LOW: 0 lights a segment.
#:
#: These ten are quoted because they are the ones the question is about and
#: because they are stable across every 7447 datasheet. The six invalid codes
#: are deliberately NOT asserted here: Evolution's model and the value
#: recalled for code 14 disagreed, and this project does not pin a number it
#: cannot check -- see the note in the test below.
DATASHEET_VALID_BCD = {
    0: "0000001", 1: "1001111", 2: "0010010", 3: "0000110", 4: "1001100",
    5: "0100100", 6: "1100000", 7: "0001111", 8: "0000000", 9: "0001100",
}


@needs_evolution
def test_the_valid_BCD_digits_decode_exactly_as_the_datasheet_says(tmp_path):
    """The check that cannot be fooled by a wrong coordinate.

    Every input pin is reached through the offset under test. A wrong one
    leaves that pin unconnected, and an unconnected BCD input cannot produce
    the datasheet's pattern for ten different codes by accident.
    """
    probe = tmp_path / "probe7447.circ"
    write_probe(probe)
    rows = evolution_table(probe)
    assert len(rows) == 2 ** 7, "seven inputs: A-D plus LT, BI, RBI"

    checked = 0
    for row in rows:
        if not (row["LT"] == row["BI"] == row["RBI"] == "1"):
            continue                    # the three control pins inactive
        value = (int(row["D"]) * 8 + int(row["C"]) * 4
                 + int(row["B"]) * 2 + int(row["A"]))
        if value not in DATASHEET_VALID_BCD:
            continue
        segments = "".join(row[f"Q{s}"] for s in "ABCDEFG")
        assert segments == DATASHEET_VALID_BCD[value], (
            f"BCD {value}: Evolution decoded {segments}, the datasheet says "
            f"{DATASHEET_VALID_BCD[value]}")
        checked += 1

    assert checked == 10, "all ten valid BCD digits must have been seen"


@needs_evolution
def test_blanking_input_low_turns_every_segment_off(tmp_path):
    """A second, independent behaviour through the same pins.

    BI is on pin 4 and nothing else is; if that offset were wrong, blanking
    could not work while the decode still did.
    """
    probe = tmp_path / "probe7447.circ"
    write_probe(probe)
    rows = evolution_table(probe)

    blanked = [row for row in rows if row["BI"] == "0"]
    assert blanked, "no row had the blanking input asserted"
    for row in blanked:
        assert all(row[f"Q{s}"] == "1" for s in "ABCDEFG"), (
            "BI low must blank the display: every active-low segment high")


@needs_evolution
def test_lamp_test_low_turns_every_segment_on(tmp_path):
    probe = tmp_path / "probe7447.circ"
    write_probe(probe)
    rows = evolution_table(probe)

    # LT is only honoured while BI is inactive.
    tested = [row for row in rows if row["LT"] == "0" and row["BI"] == "1"]
    assert tested, "no row had the lamp test asserted"
    for row in tested:
        assert all(row[f"Q{s}"] == "0" for s in "ABCDEFG"), (
            "lamp test must light every segment")


# ------------------------------------------- holding a wire at a level


@needs_evolution
def test_a_constant_really_drives_the_level_it_claims(tmp_path):
    """The Constant exists so a part's control pins need not become input
    pins -- an input pin doubles the truth table, and a 7447's three would
    turn 16 rows into 128.

    That only helps if the level is actually what the file says, so Evolution
    is asked. Logisim's own default for a Constant is 1, which is exactly why
    the emitter always writes the value out rather than relying on it.
    """
    from ohmwork.logisim_emitter import emit_circ

    circuit = {
        "components": [
            {"ref": "IN", "type": "input_pin"},
            {"ref": "HI", "type": "high"},
            {"ref": "LO", "type": "low"},
            {"ref": "AND_HI", "type": "and2"},
            {"ref": "AND_LO", "type": "and2"},
            {"ref": "WITH_HIGH", "type": "output_pin"},
            {"ref": "WITH_LOW", "type": "output_pin"},
        ],
        "nets": {
            "n_in": ["IN.pin", "AND_HI.in0", "AND_LO.in0"],
            "n_hi": ["HI.out", "AND_HI.in1"],
            "n_lo": ["LO.out", "AND_LO.in1"],
            "n_a": ["AND_HI.out", "WITH_HIGH.pin"],
            "n_b": ["AND_LO.out", "WITH_LOW.pin"],
        },
    }
    path = tmp_path / "constants.circ"
    path.write_text(emit_circ(circuit), encoding="utf-8")

    rows = evolution_table(path)
    assert len(rows) == 2, "one input pin: the constants must NOT add rows"
    for row in rows:
        # AND with a high constant passes the input through; AND with a low
        # one is always 0. If either constant carried the wrong level, or
        # were not connected at all, these would not hold.
        assert row["WITH_HIGH"] == row["IN"]
        assert row["WITH_LOW"] == "0"
