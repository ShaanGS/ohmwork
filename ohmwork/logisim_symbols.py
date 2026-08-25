"""Logisim component geometry: port offsets, and a refusal to guess.

Every offset here was MEASURED from a real hand-drawn .circ (Logisim 2.7.1).
The fixtures are in tests/fixtures/logisim/ and the measurements are pinned
independently in tests/test_logisim_geometry.py, which imports nothing from
this package. That file is the evidence; this module must agree with it, and
test_logisim_symbols.py asserts that it does.

This is the Logisim twin of symbols.py, and it inherits the same warning: a
round trip CANNOT catch a wrong offset, because the emitter would place a
wire with it and the parser would look for the wire with the same wrong
number. Only a real-file measurement can.

WHAT THIS MODULE REFUSES TO DO
------------------------------
Gate input spacing follows no single rule. Measured: two inputs sit 40 apart
at (-50, -+20); four sit 10 apart at -20, -10, +10, +20, straddling the axis
with no input on it. Any formula fitting both would be invented. So an
unmeasured (component, input count) pair raises UnmeasuredGeometryError. No
interpolation, no nearest match, no "close enough". The same applies to a
gate with a non-default size= or facing=, since every gate measured is
default size facing east.
"""

import re
from typing import NamedTuple

#: A label Logisim will NOT rewrite.
#:
#: Logisim rewrites pin labels to VHDL-safe names and appends a hash: a pin
#: labelled "E IN" comes back as "E_IN_ef467da7". The hash is not
#: reproducible by us, so a label we emit that triggers the rewrite becomes
#: unmatchable in our own results. Reading a foreign file we prefix-match
#: around it; emitting one, we simply must not produce it.
SAFE_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class UnmeasuredGeometryError(KeyError):
    """Geometry was requested for something no real file has measured.

    Always a hard stop. The fix is to obtain a real .circ containing the
    shape, measure it, and pin the measurement -- never to interpolate.
    """


class Port(NamedTuple):
    name: str
    dx: int          # offset from the component's loc
    dy: int
    kind: str        # "in" or "out"

    @property
    def offset(self) -> tuple[int, int]:
        return (self.dx, self.dy)


# Which attribute changes a component's port layout. None means the layout
# does not vary, so the component takes no discriminator.
DISCRIMINATOR = {
    "Pin": None,
    "NOT Gate": None,
    "AND Gate": "inputs",
    "OR Gate": "inputs",
    "XOR Gate": "inputs",
    "Adder": "width",
    "Priority Encoder": "select",
}

# Attributes that invalidate the measurements if present, per component.
# Every gate measured is default size, facing east. A Pin is a single port
# AT its loc and was measured facing both east (input) and west (output),
# so its facing is allowed -- but only those two.
_MEASURED_PIN_FACINGS = {None, "east", "west"}

# (component name, discriminator value) -> ports.
#
# There is an output AT loc for every component measured, but do NOT assume
# it is the only one or the last one: Adder and Priority Encoder each have a
# second output beside it. Read `kind`, never position in the list.
#
# Port NAMES and KINDS for gates and Pin are safe: an AND gate's inputs are
# interchangeable, and the wire leaving loc is by definition the driver.
# Adder and Priority Encoder names and kinds are INFERRED from how the
# students wired them (the carry chain between adder stages, the pin labels
# at each end) -- their geometry is measured, their semantics are not
# derivable from geometry. Do not rely on those names for behaviour.
PORTS: dict[tuple[str, str | None], list[Port]] = {
    # a Pin is one port at loc; whether it sources or sinks depends on its
    # output= attribute, not on its geometry, so it is neither here.
    ("Pin", None): [Port("pin", 0, 0, "in")],

    ("NOT Gate", None): [Port("in0", -30, 0, "in"), Port("out", 0, 0, "out")],

    ("AND Gate", "2"): [Port("in0", -50, -20, "in"), Port("in1", -50, 20, "in"),
                        Port("out", 0, 0, "out")],
    ("OR Gate", "2"): [Port("in0", -50, -20, "in"), Port("in1", -50, 20, "in"),
                       Port("out", 0, 0, "out")],
    ("XOR Gate", "2"): [Port("in0", -50, -20, "in"), Port("in1", -50, 20, "in"),
                        Port("out", 0, 0, "out")],

    ("OR Gate", "4"): [Port("in0", -50, -20, "in"), Port("in1", -50, -10, "in"),
                       Port("in2", -50, 10, "in"), Port("in3", -50, 20, "in"),
                       Port("out", 0, 0, "out")],

    # TWO outputs: cout and sum. Names/kinds inferred from the carry chain.
    ("Adder", "1"): [Port("a", -40, -10, "in"), Port("b", -40, 10, "in"),
                     Port("cin", -20, -20, "in"), Port("cout", -20, 20, "out"),
                     Port("sum", 0, 0, "out")],

    # TWO outputs: out and gs. Names/kinds inferred; priority order is NOT
    # derivable from geometry. Present so the parser can recognise and
    # REJECT it under primitives_only, not so the emitter can place one.
    ("Priority Encoder", "2"): [Port("d0", -40, -10, "in"), Port("d1", -40, 0, "in"),
                                Port("d2", -40, 10, "in"), Port("d3", -40, 20, "in"),
                                Port("en", -20, 30, "in"), Port("out", 0, 0, "out"),
                                Port("gs", 0, 10, "out")],
}

# Libraries whose components count as primitives for `primitives_only`.
# #Plexers is the one that matters: its built-in Priority Encoder answers Q2
# and defeats the exercise.
PRIMITIVE_LIBS = frozenset({"#Wiring", "#Gates", "#Base"})

#: Which library each measured component belongs to, BY NAME. The non-
#: primitives are listed too, and deliberately: a primitives_only check with
#: nothing outside PRIMITIVE_LIBS in its vocabulary can never fail, and a
#: check that cannot fail is worth nothing.
#:
#: Note this is the library NAME, never a file's lib="N" index. Those indices
#: are per-file (tests/fixtures/logisim/shuffled_libs.circ exists to prove a
#: check written against the number passes on one file and fails on another).
LIB_OF = {
    "Pin": "#Wiring",
    "NOT Gate": "#Gates",
    "AND Gate": "#Gates",
    "OR Gate": "#Gates",
    "XOR Gate": "#Gates",
    "Adder": "#Arithmetic",
    "Priority Encoder": "#Plexers",
}


def _describe(name, attrs):
    disc = DISCRIMINATOR.get(name)
    if disc is None:
        return name
    return f"{name} with {disc}={attrs.get(disc)!r}"


def ports_of(name: str, attrs: dict[str, str] | None = None) -> list[Port]:
    """The measured ports of a component.

    Raises UnmeasuredGeometryError for anything no real file has pinned --
    an unknown component, an unmeasured input count, or a gate carrying a
    size or facing we have never seen.
    """
    attrs = attrs or {}

    if name not in DISCRIMINATOR:
        raise UnmeasuredGeometryError(
            f"no measured geometry for component {name!r}. Add it only from a "
            f"real .circ: place one in Logisim, save, and measure its ports "
            f"against the wire endpoints. Never from documentation."
        )

    if name == "Pin":
        facing = attrs.get("facing")
        if facing not in _MEASURED_PIN_FACINGS:
            raise UnmeasuredGeometryError(
                f"Pin with facing={facing!r} has no measured geometry; only "
                f"east and west were measured. A real file is required."
            )
    else:
        for attr in ("size", "facing"):
            if attr in attrs:
                raise UnmeasuredGeometryError(
                    f"{name} with {attr}={attrs[attr]!r} has no measured "
                    f"geometry. Every gate measured is default size facing "
                    f"east; a different {attr} moves the pins. A real .circ "
                    f"containing this shape is required."
                )

    disc = DISCRIMINATOR[name]
    key = (name, attrs.get(disc) if disc else None)
    if key not in PORTS:
        measured = sorted(v for k, v in PORTS if k == name and v is not None)
        raise UnmeasuredGeometryError(
            f"no measured geometry for {_describe(name, attrs)}. Measured "
            f"{disc} values for {name}: {measured or 'none'}. Input spacing "
            f"follows no single rule -- 2 inputs sit 40 apart, 4 sit 10 apart "
            f"straddling the axis -- so this cannot be interpolated. A real "
            f".circ containing this shape is required."
        )
    return PORTS[key]


def port_positions(name: str, loc: tuple[int, int],
                   attrs: dict[str, str] | None = None) -> dict[str, tuple[int, int]]:
    """Absolute coordinates of every port of a component placed at loc."""
    x, y = loc
    return {p.name: (x + p.dx, y + p.dy) for p in ports_of(name, attrs)}


def check_label(label: str) -> None:
    """Raise unless Logisim would leave this label alone."""
    if not SAFE_LABEL.match(label or ""):
        raise UnmeasuredGeometryError(
            f"label {label!r} is not VHDL-safe: Logisim would rewrite it and "
            f"append an unreproducible hash, making it unmatchable in our own "
            f"results. Labels must match {SAFE_LABEL.pattern}"
        )


def resolve_lib_indices(text: str) -> dict[str, str]:
    """Map this file's <lib> numbers onto library names.

    comp/@lib is an index into the file's own <lib> block, so the same number
    means different libraries in different files. Anything deciding whether a
    component is a primitive MUST go through this; matching the literal
    lib="2" is a bug that passes on the file it was written against.
    """
    return {index: desc
            for desc, index in re.findall(r'<lib desc="([^"]+)" name="(\d+)"', text)}
