"""LTspice symbol geometry: pin offsets, rotation, stub directions.

Every offset in PIN table below was measured from a real .asc file
(see CLAUDE.md). Never edit a number here without re-deriving it from
a file LTspice actually saved.

This module is shared ground truth: the emitter uses it to place flags
on pins, and the parser (build step 2) will use the same table to
recover connectivity. If the two ever disagree about where a pin is,
round-trip verification is meaningless.
"""

from typing import NamedTuple


class UnknownSymbolError(KeyError):
    """A component type with no entry in the verified pin table."""


class Pin(NamedTuple):
    name: str
    dx: int          # offset from SYMBOL anchor at rotation R0
    dy: int
    stub: tuple[int, int]  # unit vector pointing away from the body, at R0


# Which of the two mutually-exclusive component fields applies:
# "value" is a scalar like "1.8k"; "part" is a library device (a real
# part number from LTspice's bundled libraries, or the name of a
# synthesised .model card). See CLAUDE.md, "Simulate layer decisions".
VALUE_TYPES = {"res", "cap", "ind", "voltage"}
PART_TYPES = {"diode", "zener", "npn", "pnp"}

_UP = (0, -1)
_DOWN = (0, 1)
_LEFT = (-1, 0)

# symbol name (as written on the SYMBOL line) -> pins
#
# WARNING: the round-trip check CANNOT catch a wrong offset in this table.
# The emitter places flags with it and the parser looks for flags with it,
# so a bad entry is self-consistent and passes every round-trip. The only
# ground truth is (a) the real-file measurement tests in test_symbols.py
# and (b) LTspice simulating the emitted file. Every symbol added here MUST
# come with a real-file measurement test. Never add one from a datasheet,
# documentation, or inference.
SYMBOLS: dict[str, list[Pin]] = {
    "res": [Pin("a", 16, 16, _UP), Pin("b", 16, 96, _DOWN)],
    "cap": [Pin("a", 16, 0, _UP), Pin("b", 16, 64, _DOWN)],
    "ind": [Pin("a", 16, 16, _UP), Pin("b", 16, 96, _DOWN)],
    "voltage": [Pin("+", 0, 16, _UP), Pin("-", 0, 96, _DOWN)],
    "diode": [Pin("anode", 16, 0, _UP), Pin("cathode", 16, 64, _DOWN)],
    "zener": [Pin("anode", 16, 0, _UP), Pin("cathode", 16, 64, _DOWN)],
    "npn": [Pin("C", 64, 0, _UP), Pin("B", 0, 48, _LEFT), Pin("E", 64, 96, _DOWN)],
    "pnp": [Pin("C", 64, 0, _UP), Pin("B", 0, 48, _LEFT), Pin("E", 64, 96, _DOWN)],
}

# rotation -> how an R0 offset (x, y) transforms (verified in CLAUDE.md)
_ROTATE = {
    "R0": lambda x, y: (x, y),
    "R90": lambda x, y: (-y, x),
    "R180": lambda x, y: (-x, -y),
    "R270": lambda x, y: (y, -x),
}


def rotate(offset: tuple[int, int], rotation: str) -> tuple[int, int]:
    """Rotate an R0-relative offset into the given rotation's frame."""
    if rotation not in _ROTATE:
        raise ValueError(f"unsupported rotation {rotation!r}")
    return _ROTATE[rotation](*offset)


def pins_of(symbol: str) -> list[Pin]:
    if symbol not in SYMBOLS:
        raise UnknownSymbolError(symbol)
    return SYMBOLS[symbol]


def pin_positions(
    symbol: str, anchor: tuple[int, int], rotation: str
) -> dict[str, tuple[int, int]]:
    """Absolute (x, y) of each pin for a symbol placed at anchor."""
    ax, ay = anchor
    result = {}
    for pin in pins_of(symbol):
        dx, dy = rotate((pin.dx, pin.dy), rotation)
        result[pin.name] = (ax + dx, ay + dy)
    return result


def stub_directions(symbol: str, rotation: str) -> dict[str, tuple[int, int]]:
    """Unit vector per pin pointing away from the symbol body."""
    return {
        pin.name: rotate(pin.stub, rotation) for pin in pins_of(symbol)
    }
