""".asc text -> recovered circuit description, from geometry alone.

This is the verification half of the round trip. It must not share any
placement knowledge with the emitter: it reads SYMBOL anchors and
rotations, WIRE segments, and FLAG coordinates off the file, rebuilds
which points are electrically connected, and demands that every pin of
every symbol lands in a group that carries exactly one net label.

Anything unaccounted for is a hard failure. A parser that guesses is
worse than no parser, because its output feeds the simulator.

Connectivity rules (matching how LTspice reads a schematic):
  - a wire connects its two endpoints
  - a point sitting mid-span of an axis-aligned wire joins that wire
    (T junctions)
  - a FLAG names every point in its group; two different names in one
    group is a short between nets and an error
"""

import re

from ohmwork.symbols import PART_TYPES, UnknownSymbolError, pin_positions

SYMBOL_RE = re.compile(r"^SYMBOL (\S+) (-?\d+) (-?\d+) (\S+)\s*$")
SYMATTR_RE = re.compile(r"^SYMATTR (\w+) (.+?)\s*$")
WIRE_RE = re.compile(r"^WIRE (-?\d+) (-?\d+) (-?\d+) (-?\d+)\s*$")
FLAG_RE = re.compile(r"^FLAG (-?\d+) (-?\d+) (\S+)\s*$")
TEXT_RE = re.compile(r"^TEXT (-?\d+) (-?\d+) \S+ -?\d+ (.+?)\s*$")

VALID_ROTATIONS = {"R0", "R90", "R180", "R270"}


class ParseError(Exception):
    """The .asc file could not be fully accounted for."""


def parse_asc(text: str) -> dict:
    """Recover {components, nets, directives} from .asc text."""
    symbols, wires, flags, directives = _scan(text)
    groups = _connect(wires, [pos for pos, _ in flags])
    net_of = _name_groups(groups, flags)
    components, nets = _recover_components(symbols, wires, groups, net_of)
    return {"components": components, "nets": nets, "directives": directives}


#: LTspice writes .asc as single-byte text, not UTF-8: a micro sign is the
#: one byte 0xB5. So a real file containing 470u -- Q3's own filter -- is
#: not valid UTF-8, and an ascii read of it raises UnicodeDecodeError before
#: any geometry is looked at. cp1252 decodes every byte, which is what we
#: want: the parser's job is to reject files on GEOMETRY it cannot account
#: for, with a message that says so, never to die on an encoding technicality
#: while reading a perfectly good schematic.
ASC_ENCODING = "cp1252"


def parse_asc_file(path: str) -> dict:
    with open(path, encoding=ASC_ENCODING) as f:
        return parse_asc(f.read())


# ----------------------------------------------------------------- scanning


def _scan(text):
    symbols, wires, flags, directives = [], [], [], []
    for line in text.splitlines():
        if m := SYMBOL_RE.match(line):
            symbols.append({
                "type": m[1],
                "anchor": (int(m[2]), int(m[3])),
                "rotation": m[4],
            })
        elif m := SYMATTR_RE.match(line):
            if not symbols:
                raise ParseError(f"SYMATTR before any SYMBOL: {line!r}")
            key = {"InstName": "ref", "Value": "value"}.get(m[1])
            if key:
                symbols[-1][key] = m[2]
        elif m := WIRE_RE.match(line):
            wires.append(((int(m[1]), int(m[2])), (int(m[3]), int(m[4]))))
        elif m := FLAG_RE.match(line):
            flags.append(((int(m[1]), int(m[2])), m[3]))
        elif m := TEXT_RE.match(line):
            payload = m[3]
            if payload.startswith("!"):
                directives.append(payload[1:])
            # ';' payloads are comments; anything else is decoration
    return symbols, wires, flags, directives


# ------------------------------------------------------------- connectivity


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def add(self, p):
        self.parent.setdefault(p, p)

    def find(self, p):
        self.add(p)
        while self.parent[p] != p:
            self.parent[p] = self.parent[self.parent[p]]
            p = self.parent[p]
        return p

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)

    def __contains__(self, p):
        return p in self.parent


def _on_segment(p, a, b) -> bool:
    """Is p strictly on the axis-aligned segment a-b (endpoints included)?

    Diagonal wires are legal in LTspice but rare and unverified here;
    for those only exact endpoint contact connects.
    """
    (px, py), (ax, ay), (bx, by) = p, a, b
    if ax == bx and px == ax:
        return min(ay, by) <= py <= max(ay, by)
    if ay == by and py == ay:
        return min(ax, bx) <= px <= max(ax, bx)
    return False


def _connect(wires, flag_points) -> _UnionFind:
    uf = _UnionFind()
    for a, b in wires:
        uf.union(a, b)
    for p in flag_points:
        uf.add(p)
    # T junctions: any known point sitting mid-span of a wire joins it.
    for p in list(uf.parent):
        for a, b in wires:
            if _on_segment(p, a, b):
                uf.union(p, a)
    return uf


def _name_groups(groups, flags) -> dict:
    net_of = {}
    for pos, name in flags:
        root = groups.find(pos)
        if root in net_of and net_of[root] != name:
            raise ParseError(
                f"nets {net_of[root]!r} and {name!r} are shorted: both "
                f"label the connected group containing {pos}"
            )
        net_of[root] = name
    return net_of


# ----------------------------------------------------------------- symbols


def _recover_components(symbols, wires, groups, net_of):
    components, nets = [], {}
    for sym in symbols:
        ref = sym.get("ref")
        if not ref:
            raise ParseError(f"symbol {sym['type']} at {sym['anchor']} "
                             "has no InstName")
        if sym["rotation"] not in VALID_ROTATIONS:
            raise ParseError(
                f"{ref}: placement {sym['rotation']!r} is not a verified "
                f"rotation (mirrors are not supported yet)"
            )
        try:
            pins = pin_positions(sym["type"], sym["anchor"], sym["rotation"])
        except UnknownSymbolError:
            raise ParseError(
                f"{ref} uses symbol {sym['type']!r}, which is not in the "
                "verified pin table"
            ) from None

        comp = {"ref": ref, "type": sym["type"]}
        if "value" in sym:
            # The file has one Value slot; classify it back into the
            # schema's value/part distinction by component type.
            key = "part" if sym["type"] in PART_TYPES else "value"
            comp[key] = sym["value"]
        components.append(comp)

        for pin_name, pos in pins.items():
            net = _net_at(pos, wires, groups, net_of)
            if net is None:
                raise ParseError(
                    f"pin {ref}.{pin_name} at {pos} is connected to no "
                    "net label"
                )
            nets.setdefault(net, []).append(f"{ref}.{pin_name}")
    return components, nets


def _net_at(pos, wires, groups, net_of):
    """Net name at a coordinate, or None if nothing labeled touches it."""
    if pos in groups:
        return net_of.get(groups.find(pos))
    for a, b in wires:
        if _on_segment(pos, a, b):
            return net_of.get(groups.find(a))
    return None
