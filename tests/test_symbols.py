"""Tests for ohmwork.symbols: pin offsets and rotation math.

The expected coordinates here come from real hand-drawn .asc files
(see CLAUDE.md, "Verified format facts"). If a test in this file fails,
either the code regressed or someone changed the pin table without
re-deriving it from a real file. Both are errors.
"""

import pytest

from ohmwork.symbols import SYMBOLS, UnknownSymbolError, pin_positions


def test_npn_r270_matches_real_file():
    # Verified: npn at (144,208) R270 in a real file has
    # C=(144,144), B=(192,208), E=(240,144).
    pins = pin_positions("npn", (144, 208), "R270")
    assert pins["C"] == (144, 144)
    assert pins["B"] == (192, 208)
    assert pins["E"] == (240, 144)


def test_zener_r180_matches_real_file():
    # Verified: zener at (208,432) R180 has pins at (192,432) and (192,368).
    # anode offset (16,0) -> R180 -> (-16,0) -> (192,432)
    # cathode offset (16,64) -> R180 -> (-16,-64) -> (192,368)
    pins = pin_positions("zener", (208, 432), "R180")
    assert pins["anode"] == (192, 432)
    assert pins["cathode"] == (192, 368)


def test_r0_is_identity():
    pins = pin_positions("res", (100, 200), "R0")
    assert pins["a"] == (100 + 16, 200 + 16)
    assert pins["b"] == (100 + 16, 200 + 96)


def test_r90_transform():
    # R90: (x, y) -> (-y, x). res pin a offset (16,16) -> (-16,16).
    pins = pin_positions("res", (0, 0), "R90")
    assert pins["a"] == (-16, 16)
    assert pins["b"] == (-96, 16)


def test_voltage_pin_names_are_plus_minus():
    pins = pin_positions("voltage", (0, 0), "R0")
    assert set(pins) == {"+", "-"}
    assert pins["+"] == (0, 16)
    assert pins["-"] == (0, 96)


def test_all_symbol_types_present():
    assert set(SYMBOLS) == {
        "res", "cap", "ind", "voltage", "diode", "zener", "npn", "pnp",
    }


def test_unknown_symbol_raises():
    with pytest.raises(UnknownSymbolError):
        pin_positions("op07", (0, 0), "R0")


def test_unknown_rotation_raises():
    with pytest.raises(ValueError):
        pin_positions("res", (0, 0), "R45")
