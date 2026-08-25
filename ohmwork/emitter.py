"""JSON circuit description -> LTspice .asc text.

Input shape (produced later by the LLM layer, or written by hand):

    {
      "components": [{"ref": "R1", "type": "res", "value": "1.8k"}, ...],
      "nets": {"vin": ["V1.+", "R1.a"], "0": [...], ...},
      "directives": [".model DZ D(BV=8.3 N=1.2)", ".op"]
    }

No coordinates in the input, ever. Placement is decided here, on a
grid, and connectivity is expressed purely as net labels: each pin gets
a 16-unit stub wire with a FLAG on its free end. Same flag name = same
net. Nothing is ever routed.
"""

from ohmwork.parts import unanchored_diode_card
from ohmwork.symbols import (
    PART_TYPES,
    VALUE_TYPES,
    pin_positions,
    pins_of,
    stub_directions,
)

STUB_LEN = 16
GRID_X0 = 112        # anchor of the first component
GRID_Y0 = 160
COL_SPACING = 224    # wide enough for an npn plus its base stub and label
TEXT_X = 112         # directives go below the component row
TEXT_Y0 = 480
TEXT_SPACING = 32


class CircuitError(ValueError):
    """The circuit description is malformed. Always fail loudly."""


def emit(circuit: dict) -> str:
    """Render a circuit description to .asc text (CRLF line endings)."""
    _validate(circuit)
    anchors = _place(circuit["components"])

    body = _symbol_lines(circuit["components"], anchors)
    body += _stub_and_flag_lines(circuit, anchors)
    body += _directive_lines(circuit.get("directives", []))
    lines = ["Version 4.1", _sheet_line(body)] + body
    return "\r\n".join(lines) + "\r\n"


def _sheet_line(body: list[str]) -> str:
    """Size the sheet to cover every coordinate in the drawing.

    Scans the already-emitted body lines rather than trusting separate
    bookkeeping, so the sheet can never disagree with the drawing. All
    coordinate-bearing lines start 'KEYWORD x y ...'.
    """
    coord_slice = {"SYMBOL": slice(2, 4), "WIRE": slice(1, 5),
                   "FLAG": slice(1, 3), "TEXT": slice(1, 3)}
    xs, ys = [880], [680]  # LTspice's default minimum sheet
    for line in body:
        parts = line.split()
        if parts[0] in coord_slice:
            nums = [int(p) for p in parts[coord_slice[parts[0]]]]
            xs += nums[0::2]
            ys += nums[1::2]
    margin = 128  # room for symbol bodies and label text past their anchors
    return f"SHEET 1 {max(xs) + margin} {max(ys) + margin}"


def write_asc(circuit: dict, path: str) -> None:
    with open(path, "w", encoding="ascii", newline="") as f:
        f.write(emit(circuit))


# ---------------------------------------------------------------- placement


def _place(components: list[dict]) -> dict[str, tuple[int, int]]:
    """One row, fixed column spacing, all R0. Dumb on purpose."""
    return {
        comp["ref"]: (GRID_X0 + i * COL_SPACING, GRID_Y0)
        for i, comp in enumerate(components)
    }


# ----------------------------------------------------------------- emission


def _symbol_lines(components, anchors) -> list[str]:
    lines = []
    for comp in components:
        x, y = anchors[comp["ref"]]
        lines.append(f"SYMBOL {comp['type']} {x} {y} R0")
        lines.append(f"SYMATTR InstName {comp['ref']}")
        # The .asc format has one attribute slot for both scalar values
        # and part names; validation guarantees exactly one is set.
        token = comp.get("value") or comp.get("part")
        lines.append(f"SYMATTR Value {token}")
    return lines


def _stub_and_flag_lines(circuit, anchors) -> list[str]:
    types = {c["ref"]: c["type"] for c in circuit["components"]}
    wires, flags = [], []
    for net, pins in circuit["nets"].items():
        for entry in pins:
            ref, pin = entry.split(".", 1)
            positions = pin_positions(types[ref], anchors[ref], "R0")
            directions = stub_directions(types[ref], "R0")
            px, py = positions[pin]
            dx, dy = directions[pin]
            fx, fy = px + dx * STUB_LEN, py + dy * STUB_LEN
            wires.append(f"WIRE {px} {py} {fx} {fy}")
            flags.append(f"FLAG {fx} {fy} {net}")
    return wires + flags


def _directive_lines(directives) -> list[str]:
    # A leading ';' means "emit as an inactive comment": how the
    # deliverable carries alternative runs for the student to uncomment.
    lines = []
    for i, d in enumerate(directives):
        prefix = "" if d.startswith(";") else "!"
        lines.append(
            f"TEXT {TEXT_X} {TEXT_Y0 + i * TEXT_SPACING} Left 2 {prefix}{d}"
        )
    return lines


# --------------------------------------------------------------- validation


def _validate(circuit: dict) -> None:
    components = circuit.get("components") or []
    nets = circuit.get("nets") or {}
    if not components:
        raise CircuitError("circuit has no components")

    _check_refs_unique(components)
    _check_value_part_fields(components)
    _check_anchored_models(circuit.get("directives") or [])
    valid_pins = _all_pins(components)          # {"R1.a", "V1.+", ...}
    used_pins = _check_net_entries(nets, valid_pins)
    _check_pin_coverage(valid_pins, used_pins)
    _check_net_sizes(nets)
    if "0" not in nets:
        raise CircuitError("no net named '0': circuit has no ground reference")


def _check_refs_unique(components) -> None:
    seen = set()
    for comp in components:
        ref = comp.get("ref")
        if not ref:
            raise CircuitError(f"component with no ref: {comp}")
        if ref in seen:
            raise CircuitError(f"duplicate ref {ref}")
        seen.add(ref)


def _check_anchored_models(directives) -> None:
    # Enforced at this chokepoint because every .asc — deliverable,
    # scratch run, example — passes through emit(). A policy that only
    # lives in the choose_zener() happy path can be bypassed by reusing
    # an old circuit dict, which is exactly what happened once.
    for d in directives:
        if unanchored_diode_card(d):
            raise CircuitError(
                f"unanchored diode model {d!r}: a card with BV but no "
                "IBV puts Vz at SPICE's 1 mA default, not the datasheet "
                "test current. Anchor it, e.g. D(BV=8.3 IBV=5m)."
            )


def _check_value_part_fields(components) -> None:
    for comp in components:
        ref, ctype = comp.get("ref"), comp.get("type")
        if ctype in VALUE_TYPES:
            required, forbidden = "value", "part"
        elif ctype in PART_TYPES:
            required, forbidden = "part", "value"
        else:
            continue  # unknown type: _all_pins reports it with more context
        if not comp.get(required):
            raise CircuitError(f"{ref} ({ctype}) needs a {required!r} field")
        if comp.get(forbidden):
            raise CircuitError(
                f"{ref} ({ctype}) takes {required!r}, not {forbidden!r}"
            )


def _all_pins(components) -> set[str]:
    pins = set()
    for comp in components:
        # pins_of raises UnknownSymbolError on a type not in the verified
        # table; re-raise as CircuitError so callers have one error type.
        try:
            for pin in pins_of(comp["type"]):
                pins.add(f"{comp['ref']}.{pin.name}")
        except KeyError:
            raise CircuitError(
                f"{comp['ref']} has unknown type {comp['type']!r}: "
                "not in the verified pin table"
            ) from None
    return pins


def _check_net_entries(nets, valid_pins) -> set[str]:
    used = set()
    for net, entries in nets.items():
        for entry in entries:
            if entry not in valid_pins:
                raise CircuitError(
                    f"net {net!r} references {entry!r}, which is not "
                    "a pin of any component"
                )
            if entry in used:
                raise CircuitError(f"pin {entry} appears in more than one net")
            used.add(entry)
    return used


def _check_pin_coverage(valid_pins, used_pins) -> None:
    unconnected = valid_pins - used_pins
    if unconnected:
        raise CircuitError(
            f"unconnected pins: {', '.join(sorted(unconnected))}"
        )


def _check_net_sizes(nets) -> None:
    for net, entries in nets.items():
        if len(entries) < 2:
            raise CircuitError(
                f"net {net!r} has only {len(entries)} pin(s): floating node"
            )
