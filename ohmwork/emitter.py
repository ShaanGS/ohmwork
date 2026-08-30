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
    rotate,
    stub_directions,
)

STUB_LEN = 16
GRID_X0 = 112        # the source column, left of every net column
GRID_Y0 = 160
COL_SPACING = 288    # room for a body plus its labels on both sides
TEXT_X = 112         # directives go below the drawing
TEXT_Y0 = 672
TEXT_SPACING = 32

RAIL_Y = GRID_Y0         # the top series row
#: Series rows, top first. A second row exists because parallel branches
#: (a bridge's two feed diodes) overlap in x on one row -- the owner's
#: screenshot showed D1 drawn into D2. A human draws a bridge as two
#: rows of diodes; so does this.
ROW_ANCHORS = (RAIL_Y, RAIL_Y + 96)
#: Shunt parts hang well below both rows, ground pointing down. Far
#: enough that a row-2 wire never touches a shunt's top pin.
SHUNT_Y = RAIL_Y + 256
NET_X0 = GRID_X0 + COL_SPACING   # first net column, right of the source


class CircuitError(ValueError):
    """The circuit description is malformed. Always fail loudly."""


def emit(circuit: dict) -> str:
    """Render a circuit description to .asc text (CRLF line endings)."""
    _validate(circuit)
    anchors = _place(circuit["components"], circuit["nets"])

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


def _place(components: list[dict],
           nets: dict) -> dict[str, tuple[int, int, str]]:
    """Signal-flow layout: ref -> (x, y, rotation).

    The owner's requirement (2026-08-31, on opening the first solved Q3
    file): the schematic should read the human way. So: the source at the
    far left; series elements horizontal along a rail, upstream pin facing
    left, ordered left-to-right by how far each net is from the source;
    shunt elements vertical below the rail with their ground pin pointing
    DOWN, where LTspice renders the net-0 flag as a ground symbol.

    What did NOT change is the correctness story: connectivity is still
    net labels on 16-unit stubs, never routed wires, so the geometry is
    presentation only and the round trip stays meaningful.
    """
    pin_net = {entry: net for net, pins in nets.items() for entry in pins}
    comp_pins = {
        comp["ref"]: {p.name: pin_net.get(f"{comp['ref']}.{p.name}")
                      for p in pins_of(comp["type"])}
        for comp in components
    }
    net_x = _net_columns(components, comp_pins)

    placements: dict[str, tuple[int, int, str]] = {}
    occupied: set[tuple[int, int]] = set()
    #: Occupied x-intervals per series row, so parallel branches (the
    #: bridge's D1/D2) land on separate rows instead of on each other.
    row_spans: dict[int, list[tuple[int, int]]] = {
        y: [] for y in ROW_ANCHORS}

    def claim(x, y, step=(0, 160)):
        while (x, y) in occupied:
            x, y = x + step[0], y + step[1]
        occupied.add((x, y))
        return x, y

    def snap(x):
        return (x // 16) * 16

    def claim_row(x, width=208):
        """A series slot: the first row whose x-range is free, walking
        right on the top row when every row is blocked."""
        while True:
            span = (x - 48, x - 48 + width)
            for y in ROW_ANCHORS:
                if all(span[1] <= s0 or s1 <= span[0]
                       for s0, s1 in row_spans[y]):
                    row_spans[y].append(span)
                    occupied.add((x, y))
                    return x, y
            x += 96

    for comp in components:
        ref, ctype = comp["ref"], comp["type"]
        nets_of = comp_pins[ref]
        top = pins_of(ctype)[0].name   # the pin that sits UP at R0

        if ctype == "voltage":
            # R0 puts '+' up; a source grounded at '+' flips.
            rot = "R180" if nets_of.get("+") == "0" else "R0"
            x, y = claim(GRID_X0, RAIL_Y)
        elif len(nets_of) == 2:
            (p1, n1), (p2, n2) = nets_of.items()
            if "0" in (n1, n2) and n1 != n2:
                gpin, other = (p1, n2) if n1 == "0" else (p2, n1)
                rot = "R180" if gpin == top else "R0"
                x, y = claim(net_x.get(other, NET_X0), SHUNT_Y,
                             step=(96, 0))
                # Anchor so the TOP pin lands on one shared line whatever
                # the rotation: R180 pins extend UP from the anchor, and
                # unnormalized they collided with a row-2 transistor.
                min_dy = min(rotate((p.dx, p.dy), rot)[1]
                             for p in pins_of(ctype))
                y = SHUNT_Y + 16 - min_dy
            else:
                xa = net_x.get(n1, NET_X0)
                xb = net_x.get(n2, NET_X0)
                left_pin = p1 if xa <= xb else p2
                # R270 rotates the R0-top pin onto the LEFT.
                rot = "R270" if left_pin == top else "R90"
                x, y = claim_row(snap((xa + xb) // 2))
                # Both rotations put their pins on the row's pin line
                # (anchor - 16): R90 pins sit at anchor + 16, so drop
                # the anchor to compensate.
                if rot == "R90":
                    y -= 32
        else:
            # Three-terminal devices sit upright at the mean of their
            # nets' columns; R0 keeps C up, B left, E down.
            xs = [net_x[n] for n in nets_of.values() if n in net_x]
            rot = "R0"
            x, y = claim_row(snap(sum(xs) // len(xs)) if xs else NET_X0)
        placements[ref] = (x, y, rot)
    return placements


def _net_columns(components, comp_pins) -> dict[str, int]:
    """One x column per non-ground net, ordered by distance from the source.

    Distance is relaxed over components-as-edges until it stops changing;
    ground gets no column (it is a symbol, not a place), and a net no
    source reaches sorts after everything reached.
    """
    unreached = 10 ** 6
    depth: dict[str, int] = {}
    every: set[str] = set()
    for comp in components:
        for net in comp_pins[comp["ref"]].values():
            if net and net != "0":
                every.add(net)
                if comp["type"] == "voltage":
                    depth[net] = 0

    changed = True
    while changed:
        changed = False
        for comp in components:
            ns = [n for n in comp_pins[comp["ref"]].values()
                  if n and n != "0"]
            if not ns:
                continue
            base = min(depth.get(n, unreached) for n in ns)
            if base >= unreached:
                continue
            for n in ns:
                if depth.get(n, unreached) > base + 1:
                    depth[n] = base + 1
                    changed = True

    order = sorted(every, key=lambda n: (depth.get(n, unreached), n))
    return {net: NET_X0 + i * COL_SPACING for i, net in enumerate(order)}


# ----------------------------------------------------------------- emission


#: Per-rotation label windows for two-terminal parts, so a rotated body
#: keeps HORIZONTAL, non-colliding text. Every line is VERBATIM from a
#: hand-drawn fixture: offsets AND justification live in the SYMBOL'S
#: frame and rotate with it, so the students' `VTop`/`VBottom` on
#: R90/R270 parts render horizontal on screen -- and a `Left` written
#: there renders vertical, which is exactly the mistake the first cut of
#: this table made and the owner's screenshot caught.
LABEL_WINDOWS = {
    "R270": ("WINDOW 0 32 32 VTop 2", "WINDOW 3 0 32 VBottom 2"),
    "R90": ("WINDOW 0 0 32 VBottom 2", "WINDOW 3 32 32 VTop 2"),
    "R180": ("WINDOW 0 24 64 Left 2", "WINDOW 3 24 0 Left 2"),
}

#: What the students' own files carry on every voltage source: two
#: hidden attribute windows, so no extra text crowds the symbol -- plus
#: a value window moved DOWN, clear of the minus pin's stub and the
#: junction square the owner's close-up showed it crowding.
VOLTAGE_WINDOWS = ("WINDOW 123 0 0 Left 0", "WINDOW 39 0 0 Left 0",
                   "WINDOW 3 24 132 Left 2")

_TWO_TERMINAL = {"res", "cap", "ind", "diode", "zener"}


def _symbol_lines(components, anchors) -> list[str]:
    lines = []
    for comp in components:
        x, y, rot = anchors[comp["ref"]]
        lines.append(f"SYMBOL {comp['type']} {x} {y} {rot}")
        if comp["type"] == "voltage":
            lines.extend(VOLTAGE_WINDOWS)
        elif comp["type"] in _TWO_TERMINAL and rot in LABEL_WINDOWS:
            lines.extend(LABEL_WINDOWS[rot])
        lines.append(f"SYMATTR InstName {comp['ref']}")
        # The .asc format has one attribute slot for both scalar values
        # and part names; validation guarantees exactly one is set.
        token = comp.get("value") or comp.get("part")
        lines.append(f"SYMATTR Value {token}")
    return lines


def _stub_and_flag_lines(circuit, anchors) -> list[str]:
    """Stubs for every pin, then REAL WIRES where a clean path exists.

    The owner's requirement, 2026-08-31, on seeing the label-only layout
    beside a classmate's hand-drawn file: real wires. The router is
    deliberately conservative -- a net routes only when its wires touch
    nothing foreign (no shared endpoints, no endpoint on a foreign span,
    no collinear overlap; perpendicular CROSSINGS are safe, LTspice does
    not connect them) -- and any net with no clean path falls back to the
    old per-pin labels. A labelled net is ugly and correct; a clever
    route is where silent shorts live. The geometric round trip remains
    the proof: the parser rebuilds connectivity from these wires alone.

    Ground never routes: one flag per grounded pin renders as the ground
    triangle under each part, which is how a hand drawing shows it.
    """
    types = {c["ref"]: c["type"] for c in circuit["components"]}

    pin_at, stub_end, owner = {}, {}, {}
    for ref, ctype in types.items():
        x, y, rot = anchors[ref]
        positions = pin_positions(ctype, (x, y), rot)
        directions = stub_directions(ctype, rot)
        for name, (px, py) in positions.items():
            dx, dy = directions[name]
            entry = f"{ref}.{name}"
            pin_at[entry] = (px, py)
            stub_end[entry] = (px + dx * STUB_LEN, py + dy * STUB_LEN)
            owner[entry] = ref

    wires = [f"WIRE {pin_at[e][0]} {pin_at[e][1]} "
             f"{stub_end[e][0]} {stub_end[e][1]}" for e in sorted(pin_at)]
    flags = []

    bodies = _body_boxes(types, pin_at)
    committed: list[tuple[tuple, tuple]] = []
    routed: dict[str, list] = {}

    ordered = sorted((net for net in circuit["nets"] if net != "0"),
                     key=lambda n: min(stub_end[e][0]
                                       for e in circuit["nets"][n]))
    for net in ordered:
        entries = circuit["nets"][net]
        points = [stub_end[e] for e in entries]
        my_refs = {owner[e] for e in entries}
        foreign = [p for e, p in pin_at.items() if e not in entries]
        foreign += [p for e, p in stub_end.items() if e not in entries]

        route = _route_net(points, foreign, committed, bodies, my_refs)
        if route is None:
            for e in entries:
                fx, fy = stub_end[e]
                flags.append(f"FLAG {fx} {fy} {net}")
            continue
        committed.extend(route)
        routed[net] = route
        for (ax, ay), (bx, by) in route:
            wires.append(f"WIRE {ax} {ay} {bx} {by}")

    # THE CLOSED LOOP. A hand drawing returns every current to a ground
    # rail along the bottom; parts dangling over unconnected triangles
    # read as parts on strings, which is what the owner rejected. So
    # ground ROUTES: a drop from every grounded pin onto one bottom rail,
    # with a ground flag (the triangle) at each drop.
    ground = circuit["nets"].get("0", ())
    g_points = sorted({stub_end[e] for e in ground})
    g_refs = {owner[e] for e in ground}
    g_foreign = [p for e, p in pin_at.items() if e not in ground]
    g_foreign += [p for e, p in stub_end.items() if e not in ground]
    rail = _ground_rail(g_points, g_foreign, committed, bodies, g_refs)
    if rail is None:
        for e in ground:
            fx, fy = stub_end[e]
            flags.append(f"FLAG {fx} {fy} 0")
    else:
        committed.extend(rail)
        routed["0"] = rail
        for (ax, ay), (bx, by) in rail:
            wires.append(f"WIRE {ax} {ay} {bx} {by}")
        # Each ground triangle hangs from a short stub BELOW the rail,
        # as in the hand drawings -- a flag placed ON the rail puts the
        # wire through the triangle's tip, which the owner's close-up
        # showed.
        for px, _ in g_points:
            wires.append(f"WIRE {px} {GND_Y} {px} {GND_Y + 16}")
            flags.append(f"FLAG {px} {GND_Y + 16} 0")

    # NAME labels are placed only after EVERY wire exists (ground rail
    # included): a spot chosen early could otherwise end up on a later
    # net's crossing wire, which would union the two nets -- a short
    # delivered by a label.
    taken = [tuple(map(int, f.split()[1:3])) for f in flags]
    for net, route in routed.items():
        if net == "0":
            continue
        foreign_segs = [seg for other, r in routed.items() if other != net
                        for seg in r]
        fx, fy = _flag_spot(route, bodies.values(), taken, foreign_segs)
        taken.append((fx, fy))
        flags.append(f"FLAG {fx} {fy} {net}")
    return wires + flags


# ------------------------------------------------------------------ routing


#: Overhead lanes a blocked net may climb to, tried in order after the
#: pin lines themselves. All grid multiples of 16. No underfloor lanes:
#: the ground rail owns the bottom of the sheet, and a lane below it
#: would have to cross it to reach anything.
LANES = (112, 80, 48)

#: The ground rail's y: below every shunt part's bottom stub.
GND_Y = 560


def _ground_rail(points, foreign, committed, bodies, my_refs):
    """A drop from every ground stub end onto one bottom rail, or None."""
    if not points:
        return None
    segs = [((px, py), (px, GND_Y)) for px, py in points if py != GND_Y]
    xs = [px for px, _ in points]
    if min(xs) != max(xs):
        segs.append(((min(xs), GND_Y), (max(xs), GND_Y)))
    obstacles = [box for ref, box in bodies.items() if ref not in my_refs]
    if all(_clear(seg, foreign, committed, obstacles) for seg in segs):
        return segs
    return None


def _flag_spot(route, boxes, taken, foreign_segs):
    """Where the net's one name label goes: the point on the route's own
    wires -- horizontal OR vertical, the way a human names a node beside
    a long riser when the horizontals are hemmed in -- farthest from
    every body and every existing label. The owner's screenshots showed
    'vrect' through a diode's '1N4007', then 'vout' through 'DZ1' when
    only horizontals were considered.

    A candidate lying on ANOTHER net's wire is skipped outright: a flag
    at a crossing point would union the two nets in the parser (and in
    LTspice), turning a legal crossing into a short.
    """
    best, best_score = None, None
    for (ax, ay), (bx, by) in route:
        if ay == by:
            spots = [(x, ay) for x in
                     range(min(ax, bx), max(ax, bx) + 1, 16)]
        else:
            spots = [(ax, y) for y in
                     range(min(ay, by), max(ay, by) + 1, 16)]
        for spot in spots:
            if any(_on_seg(spot, seg) for seg in foreign_segs):
                continue
            # Boxes grow an extra margin here: the label text a body
            # carries extends past its pins, and a net name that clears
            # the body but sits on the body's own text still collides.
            score = min([_box_gap(spot, (b[0] - 40, b[1] - 40,
                                         b[2] + 40, b[3] + 40))
                         for b in boxes]
                        + [abs(spot[0] - tx) + abs(spot[1] - ty)
                           for tx, ty in taken]
                        or [10 ** 6])
            if best_score is None or score > best_score:
                best, best_score = spot, score
    if best is None:
        best = min(p for seg in route for p in seg)
    return best


def _box_gap(p, box) -> int:
    (px, py), (x0, y0, x1, y1) = p, box
    dx = max(x0 - px, 0, px - x1)
    dy = max(y0 - py, 0, py - y1)
    return dx + dy


def _body_boxes(types, pin_at):
    """Per-ref bounding box of its pins, inflated to cover the drawing."""
    boxes = {}
    for entry, (px, py) in pin_at.items():
        ref = entry.split(".", 1)[0]
        x0, y0, x1, y1 = boxes.get(ref, (px, py, px, py))
        boxes[ref] = (min(x0, px), min(y0, py), max(x1, px), max(y1, py))
    return {ref: (x0 - 24, y0 - 24, x1 + 24, y1 + 24)
            for ref, (x0, y0, x1, y1) in boxes.items()}


def _route_net(points, foreign, committed, bodies, my_refs):
    """Wires joining `points`, touching nothing foreign, or None."""
    points = sorted(set(points))
    if len(points) < 2:
        return None
    xs = {x for x, _ in points}
    ys = {y for _, y in points}

    candidates = []
    if len(xs) == 1:
        x = next(iter(xs))
        candidates.append([((x, min(ys)), (x, max(ys)))])
    else:
        # Try the net's own pin lines first (most-shared first, upper
        # first -- a net spanning both series rows has two), then lanes.
        counts = [y for _, y in points]
        pin_lines = sorted(set(ys), key=lambda y: (-counts.count(y), y))
        for line_y in (*pin_lines, *LANES):
            segs = [((px, py), (px, line_y))
                    for px, py in points if py != line_y]
            lo, hi = min(xs), max(xs)
            if lo != hi:
                segs.append(((lo, line_y), (hi, line_y)))
            candidates.append(segs)

    obstacles = [box for ref, box in bodies.items() if ref not in my_refs]
    for segs in candidates:
        if all(_clear(seg, foreign, committed, obstacles) for seg in segs):
            return segs
    return None


def _clear(seg, foreign, committed, obstacles) -> bool:
    for p in foreign:
        if _on_seg(p, seg):
            return False
    for other in committed:
        if _segs_touch(seg, other):
            return False
    (ax, ay), (bx, by) = seg
    x0, x1 = sorted((ax, bx))
    y0, y1 = sorted((ay, by))
    for ox0, oy0, ox1, oy1 in obstacles:
        if x0 <= ox1 and ox0 <= x1 and y0 <= oy1 and oy0 <= y1:
            return False
    return True


def _on_seg(p, seg) -> bool:
    (px, py), ((ax, ay), (bx, by)) = p, seg
    if ax == bx and px == ax:
        return min(ay, by) <= py <= max(ay, by)
    if ay == by and py == ay:
        return min(ax, bx) <= px <= max(ax, bx)
    return False


def _segs_touch(a, b) -> bool:
    """Would LTspice consider these connected? Endpoint contact and
    collinear overlap connect; a perpendicular crossing does not."""
    if any(_on_seg(p, b) for p in a) or any(_on_seg(p, a) for p in b):
        return True
    return False


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


def _unknown_pin(net, entry, valid_pins, nets) -> str:
    """Say which pins the component DOES have, not merely that this is wrong.

    MEASURED on a live analog solve: a model wrote "Q1.base" for a transistor
    whose pins are C, B and E, and the rejection told it the pin did not
    exist without saying what would. That is a whole retry spent guessing at
    a vocabulary the message was already holding.

    And a NET name written where a pin belongs gets the fact it is missing,
    not just the fact it is wrong. MEASURED on the eighth Q3 run, which DIED
    on it: the model twice wrote the ground net "0" inside another net --
    trying to say "this net is grounded" -- was told only "no component
    named 0", repeated itself, and the identical-failure stop ended the run.
    """
    if entry == "0" or entry in nets:
        if entry == "0":
            return (f"net {net!r} lists {entry!r}, which is a NET -- ground "
                    f"-- not a pin. Nets do not nest: to ground these pins, "
                    f"list every one of them in net '0' directly.")
        return (f"net {net!r} lists {entry!r}, which is a NET, not a pin. "
                f"Nets do not nest: if these pins share one node, merge the "
                f"two nets under one name and list every pin there.")
    ref = entry.split(".", 1)[0]
    theirs = sorted(pin.split(".", 1)[1] for pin in valid_pins
                    if pin.startswith(f"{ref}."))
    if theirs:
        return (f"net {net!r} references {entry!r}, which is not a pin of "
                f"{ref}. Its pins are: {', '.join(theirs)}")
    return (f"net {net!r} references {entry!r}, and this circuit has no "
            f"component named {ref}")


def _check_net_entries(nets, valid_pins) -> set[str]:
    # The two-net message TEACHES the merge rule rather than stating the
    # fact: it is fed back verbatim to the design loop, and "pin X appears
    # in more than one net" burned six Q3 attempts across two live runs --
    # the model keeps writing a junction as two nets touching, when two
    # nets sharing a pin ARE one net in this schema.
    owner = {}
    for net, entries in nets.items():
        for entry in entries:
            if entry not in valid_pins:
                raise CircuitError(_unknown_pin(net, entry, valid_pins, nets))
            if owner.get(entry) == net:
                raise CircuitError(
                    f"pin {entry} is listed twice in net {net!r}: "
                    f"delete the duplicate entry"
                )
            if entry in owner:
                raise CircuitError(
                    f"pin {entry} appears in two nets, {owner[entry]!r} and "
                    f"{net!r}. Two nets sharing a pin are ONE net: a "
                    f"junction is a single net with several pins, not two "
                    f"nets touching. Merge them under one name and list "
                    f"every pin there."
                )
            owner[entry] = net
    return set(owner)


def _check_pin_coverage(valid_pins, used_pins) -> None:
    unconnected = valid_pins - used_pins
    if unconnected:
        raise CircuitError(
            f"unconnected pins: {', '.join(sorted(unconnected))}"
        )


def _check_net_sizes(nets) -> None:
    for net, entries in nets.items():
        if len(entries) < 2:
            # A ground-flavoured name says what the model MEANT. Measured
            # twice on live Q3 runs: single-pin nets named '0_zener' and
            # '0_dz', each an attempt to say "this pin is grounded".
            if net != "0" and net.startswith("0"):
                raise CircuitError(
                    f"net {net!r} has only {len(entries)} pin(s): floating "
                    f"node. A net named like ground is not ground -- the "
                    f"ground net is named exactly '0', and every grounded "
                    f"pin belongs in it directly.")
            raise CircuitError(
                f"net {net!r} has only {len(entries)} pin(s): floating node"
            )
