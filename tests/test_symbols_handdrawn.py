"""The pin table checked against .asc files nobody on this project drew.

CLAUDE.md, "Verification limits": the round trip cannot catch a wrong pin
offset, because emitter and parser share symbols.py and would agree on the
same wrong coordinate. The only real ground truth is a real-file measurement.

test_symbols.py already pins npn/R270 and zener/R180, but both came from ONE
file (EXP2). These fixtures are four separate hand-drawn lab files by three
different students, and they exercise res, cap, voltage, diode at R0, R90 and
R270. If an offset in SYMBOLS were wrong, a computed pin here would land on
empty canvas instead of on the wire the student actually drew.

  handdrawn_pn_reverse.asc         voltage R0, diode R90, res R0
  handdrawn_pn_forward.asc         voltage R0, diode R270, res R0
  handdrawn_voltage_multiplier.asc voltage R0, diode R90 + R270, cap R0 x2
  handdrawn_series_regulator.asc   npn R270, zener R180, res R0 x2, voltage R0
                                   (a student's own take on the Q1 circuit)
"""

import re
from pathlib import Path

import pytest

from ohmwork.symbols import pin_positions

FIXTURES = Path(__file__).parent / "fixtures" / "ltspice"

FILES = [
    "handdrawn_pn_reverse.asc",
    "handdrawn_pn_forward.asc",
    "handdrawn_voltage_multiplier.asc",
    "handdrawn_series_regulator.asc",
]

SYMBOL = re.compile(r"^SYMBOL (\S+) (-?\d+) (-?\d+) (\S+)$")
WIRE = re.compile(r"^WIRE (-?\d+) (-?\d+) (-?\d+) (-?\d+)$")
FLAG = re.compile(r"^FLAG (-?\d+) (-?\d+) (\S+)$")


def read_asc(name):
    """Read a real .asc and return (symbols, attachment points).

    Decoded as cp1252, not ascii and not utf-8: LTspice writes a micro sign
    as the single byte 0xB5, so handdrawn_voltage_multiplier.asc ("100u")
    is not valid UTF-8 and would raise on an ascii read. Generated files are
    pure ascii, which is why ohmwork/parser.py can be stricter than this.
    """
    text = (FIXTURES / name).read_bytes().decode("cp1252")
    symbols, points = [], set()
    for line in text.splitlines():
        m = SYMBOL.match(line)
        if m:
            symbols.append((m.group(1), (int(m.group(2)), int(m.group(3))), m.group(4)))
            continue
        m = WIRE.match(line)
        if m:
            x1, y1, x2, y2 = (int(g) for g in m.groups())
            points.add((x1, y1))
            points.add((x2, y2))
            continue
        m = FLAG.match(line)
        if m:
            points.add((int(m.group(1)), int(m.group(2))))
    return symbols, points


def test_the_micro_sign_is_a_single_high_byte():
    # Recorded because it is a live trap: an ascii or utf-8 read of a real
    # file with a 470uF in it raises. Q3's filter is 470uF.
    raw = (FIXTURES / "handdrawn_voltage_multiplier.asc").read_bytes()
    assert b"\xb5" in raw
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    assert "100µ" in raw.decode("cp1252")


@pytest.mark.parametrize("name", FILES)
def test_every_pin_lands_on_something_the_student_drew(name):
    """The measurement that the round trip cannot make.

    For every symbol in a file we did not create, compute its pins from
    SYMBOLS + the rotation table and require each one to coincide with a wire
    endpoint or a flag. A wrong offset puts the pin on blank canvas.
    """
    symbols, points = read_asc(name)
    assert symbols, "fixture parsed as empty"
    for symbol, anchor, rotation in symbols:
        for pin, xy in pin_positions(symbol, anchor, rotation).items():
            assert xy in points, (name, symbol, anchor, rotation, pin, xy)


def test_diode_r90_and_r270_from_two_different_files():
    # R90: (x,y) -> (-y,x).  diode anode (16,0), cathode (16,64).
    # pn_reverse: SYMBOL diode 224 96 R90, wired at (224,112) and (160,112).
    pins = pin_positions("diode", (224, 96), "R90")
    assert pins["anode"] == (224, 112)
    assert pins["cathode"] == (160, 112)

    # R270: (x,y) -> (y,-x).
    # PN FORWARD: SYMBOL diode 272 64 R270, wired at (272,48) and (336,48).
    pins = pin_positions("diode", (272, 64), "R270")
    assert pins["anode"] == (272, 48)
    assert pins["cathode"] == (336, 48)


def test_cap_and_res_and_voltage_at_r0():
    # cap 368 0 R0 -> (384,0) and (384,64); both are wire endpoints.
    pins = pin_positions("cap", (368, 0), "R0")
    assert pins["a"] == (384, 0)
    assert pins["b"] == (384, 64)

    # res 320 144 R0 -> (336,160) and (336,240). Note res is 96 tall and cap
    # is 64: the inconsistency CLAUDE.md warns about, seen in real files.
    pins = pin_positions("res", (320, 144), "R0")
    assert pins["a"] == (336, 160)
    assert pins["b"] == (336, 240)

    # voltage 64 128 R0 -> + at (64,144), - at (64,224).
    pins = pin_positions("voltage", (64, 128), "R0")
    assert pins["+"] == (64, 144)
    assert pins["-"] == (64, 224)


def test_no_hand_drawn_fixture_uses_a_mirrored_placement():
    # Mirrors (M0/M90/M180/M270) are deferred and the parser refuses them.
    # If a future fixture trips this, do the derivation in CLAUDE.md
    # ("Deferred: mirrored placements") before touching the parser.
    for name in FILES:
        symbols, _ = read_asc(name)
        assert all(not r.startswith("M") for _, _, r in symbols), name
