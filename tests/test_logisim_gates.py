"""The gate families, measured with Logisim Evolution as the instrument.

The 2.7.1 fixtures hold two-input AND/OR/XOR, a four-input OR and NOT. A
NAND-only adder, a 4:1 mux (3-input AND) or an 8:1 mux (4-input AND, 8-input
OR) need shapes no fixture here contains, and this project does not add a
pin offset from documentation. What it does instead is what settled the 7447:
build a probe FROM the table and let the evaluator say whether every input
is really connected.

The probe here is stricter than the 7447's. A Pin is placed EXACTLY on each
port coordinate with NO wire, because the wire-based probe could not tell
x=-50 from x=-60: a wire ending at -50 that comes from the left passes over a
port at -60 and connects to it (the T rule). That ambiguity is how the XOR2
entry stayed wrong for two months -- the fixture's dead ends really are at
-50, ten units past the port at -60 -- and it is why this file exists.

Without Evolution these tests skip and say so; the offline check below pins
the table's shape as data, so a silent edit still fails somewhere.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from ohmwork.logisim_backend import locate_logisim, logisim_command
from ohmwork.logisim_symbols import (GATE_INPUT_COUNTS, GATE_INPUT_X,
                                     GATE_INPUT_Y, PORTS, ports_of)

FUNCTION = {
    "AND Gate": lambda bits: int(all(bits)),
    "OR Gate": lambda bits: int(any(bits)),
    "NAND Gate": lambda bits: int(not all(bits)),
    "NOR Gate": lambda bits: int(not any(bits)),
    "XOR Gate": lambda bits: bits[0] ^ bits[1],
    "XNOR Gate": lambda bits: 1 - (bits[0] ^ bits[1]),
}

SHAPES = [(name, n) for name, counts in GATE_INPUT_COUNTS.items()
          for n in counts]


def _evolution():
    try:
        return locate_logisim()
    except FileNotFoundError:
        return None


needs_evolution = pytest.mark.skipif(
    _evolution() is None,
    reason="Logisim Evolution is not installed; the gate-table probe needs "
           "the evaluator itself (OHMWORK_LOGISIM points at it)")


def write_probe(path, name, inputs, shift=0):
    """A gate at (300,200) with a Pin ON each port. No wires, on purpose.

    `shift` moves every input Pin that many units in x. The probe must FAIL
    for shift=+10 and shift=-10, or it is not measuring anything.
    """
    lx, ly = 300, 200
    lines = ['<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
             '<project source="2.7.1" version="1.0">',
             '<lib desc="#Wiring" name="0"/>',
             '  <lib desc="#Gates" name="1"/>',
             '  <lib desc="#Base" name="6"/>',
             '  <main name="main"/>',
             '  <circuit name="main">',
             f'    <comp lib="1" loc="({lx},{ly})" name="{name}">',
             f'      <a name="inputs" val="{inputs}"/>',
             '    </comp>']
    for port in ports_of(name, {"inputs": inputs}):
        px, py = lx + port.dx, ly + port.dy
        if port.kind == "in":
            px += shift
            lines += [f'    <comp lib="0" loc="({px},{py})" name="Pin">',
                      '      <a name="tristate" val="false"/>',
                      f'      <a name="label" val="{port.name}"/>',
                      '    </comp>']
        else:
            lines += [f'    <comp lib="0" loc="({px},{py})" name="Pin">',
                      '      <a name="facing" val="west"/>',
                      '      <a name="output" val="true"/>',
                      '      <a name="label" val="Y"/>',
                      '    </comp>']
    lines += ['  </circuit>', '</project>']
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def evolution_rows(path):
    proc = subprocess.run(logisim_command(_evolution(), ["--tty", "table", str(path)]),
                          capture_output=True, text=True, timeout=300)
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    header = lines[0].split()
    rows = []
    for line in lines[1:]:
        cells = line.split()
        if len(cells) != len(header) or not all(c in "01" for c in cells):
            return None            # an error marker: an input is floating
        rows.append(dict(zip(header, (int(c) for c in cells))))
    return rows


def _agrees(name, inputs, rows):
    n = int(inputs)
    if rows is None or len(rows) != 2 ** n:
        return False
    names = [f"in{i}" for i in range(n)]
    f = FUNCTION[name]
    return all(row["Y"] == f([row[k] for k in names]) for row in rows)


# ------------------------------------------------------------- the probe

@needs_evolution
@pytest.mark.parametrize("name,inputs", SHAPES,
                         ids=[f"{n.split()[0]}{c}" for n, c in SHAPES])
def test_a_pin_on_every_port_drives_the_gate_and_nothing_else_does(tmp_path, name, inputs):
    """The measurement. Pins sitting exactly on the table's coordinates make
    the gate compute its function on every row; the same pins shifted ten
    units either way do not. Both halves matter: a probe that cannot fail
    is not a measurement."""
    on = tmp_path / "on.circ"
    write_probe(on, name, inputs)
    assert _agrees(name, inputs, evolution_rows(on)), (
        f"{name} inputs={inputs}: Evolution did not compute the gate with "
        f"Pins on the table's ports -- an offset is wrong")

    for shift in (-10, 10):
        off = tmp_path / f"off{shift}.circ"
        write_probe(off, name, inputs, shift=shift)
        assert not _agrees(name, inputs, evolution_rows(off)), (
            f"{name} inputs={inputs}: Pins shifted {shift} ALSO drove the "
            f"gate, so this probe cannot discriminate at that offset")


# ------------------------------------------------- offline: the table's shape

def test_the_families_differ_only_in_input_x():
    """What the measurement found, as data: bubbles and XOR arcs widen the
    body by 10 each, the y layout depends on the input count alone."""
    assert GATE_INPUT_X == {"AND Gate": -50, "OR Gate": -50,
                            "NAND Gate": -60, "NOR Gate": -60,
                            "XOR Gate": -60, "XNOR Gate": -70}
    for name, counts in GATE_INPUT_COUNTS.items():
        for n in counts:
            ports = PORTS[(name, n)]
            ins = [p for p in ports if p.kind == "in"]
            assert {p.dx for p in ins} == {GATE_INPUT_X[name]}
            assert tuple(p.dy for p in ins) == GATE_INPUT_Y[n]
            assert [p for p in ports if p.kind == "out"][0].offset == (0, 0)


def test_even_input_counts_leave_the_axis_empty():
    for n, ys in GATE_INPUT_Y.items():
        assert len(ys) == int(n)
        assert ys == tuple(sorted(ys))
        assert (0 in ys) == (int(n) % 2 == 1)


def test_multi_input_xor_is_deliberately_absent():
    """Evolution's 3+ input XOR evaluated to 'exactly one input high', which
    is not what a textbook XOR means. Until that has an independent
    reference it is not placed, and a design asking for one is refused."""
    assert GATE_INPUT_COUNTS["XOR Gate"] == ("2",)
    assert GATE_INPUT_COUNTS["XNOR Gate"] == ("2",)
    assert ("XOR Gate", "3") not in PORTS


def test_the_fixture_dead_ends_are_ten_units_past_the_xor_ports():
    """The record of how the old XOR2 entry went wrong, kept as a test so the
    dead-end method is never again read as 'the port is where the wire
    ends'. In adder_subtractor.circ every XOR input wire ends at x-50 and a
    wire also touches x-60, the true port."""
    import re
    fixture = Path(__file__).parent / "fixtures" / "logisim" / "adder_subtractor.circ"
    text = fixture.read_text(encoding="utf-8")
    xors = [(int(a), int(b)) for a, b in
            re.findall(r'loc="\((-?\d+),(-?\d+)\)" name="XOR Gate"', text)]
    wires = [tuple(map(int, w)) for w in re.findall(
        r'<wire from="\((-?\d+),(-?\d+)\)" to="\((-?\d+),(-?\d+)\)"', text)]

    def touched(x, y):
        return any(w[1] == y == w[3] and min(w[0], w[2]) <= x <= max(w[0], w[2])
                   for w in wires)

    assert len(xors) == 4
    for x, y in xors:
        for dy in (-20, 20):
            assert touched(x - 60, y + dy), (x, y, dy)   # the port is wired
    # and the input the old table called floating is not
    assert touched(410 - 60, 250 + 20)


def test_evolution_is_named_when_absent():
    if shutil.which("logisim-evolution") is None and _evolution() is None:
        assert "not installed" in needs_evolution.kwargs["reason"]
