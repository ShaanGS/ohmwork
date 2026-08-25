"""Circuit description -> Logisim 2.7.1 `.circ`. Mechanical placement, real wires.

WHAT VERIFIES THIS. Not our own parser. The acceptance test emits Q2, runs
`logisim-evolution --tty table` on the result, and requires the same 32 rows
Logisim produced from a student's hand-drawn encoder. An outside tool, a
reference we did not compute. A geometric round trip through our own parser
would only prove self-consistency, and both halves would share any
misunderstanding of the format.

LAYOUT IS MECHANICAL AND SAYS SO. Inputs in a left column, outputs in a right
column, gates in columns by logic depth, orthogonal routes, wires crossing
freely. It will not resemble a hand-drawn schematic and the CLI states that
when it writes one. Layout quality is v1.1; do not add a placer that tries to
look good.

THE TWO ROUTING HAZARDS, both consequences of Logisim connecting by geometry:

1. **Never split a wire at a crossing.** A segment boundary at an
   intersection turns an X into a junction — same picture, different
   circuit. Guarded by emitting each straight run as ONE segment and by
   test_the_router_never_splits_a_wire_at_a_crossing.

2. **An endpoint on another wire's span IS a connection.** A route that
   terminates mid-span of an unrelated net shorts them with no visual cue at
   all: on screen it looks like a wire stopping near another wire. This is
   the failure the format makes easiest to create and hardest to see, so
   `validate_wiring` runs on every emitted circuit and refuses it.

HOW THE LAYOUT MAKES HAZARD 2 STRUCTURALLY IMPOSSIBLE, rather than merely
checked:

- **Every component gets its own row**, and the row is as tall as the
  component needs. Rows are stacked from each component's OWN port span
  rather than by a fixed pitch, so no two components share a port y and a
  horizontal run sits at a y no foreign port occupies.

  This used to be a constant, ROW_PITCH = 60, justified as "> the 40-unit
  span of a 2-input gate's input pins". That reasoning was sound and the
  constant was a trap: a 7447 spans exactly 60, so consecutive DIPs put a
  port of one at the same y as a port of the next. `validate_wiring` caught
  it on the first BCD question -- the argument for the layout had quietly
  stopped being true while the layout still looked deliberate.
- **Every net gets its own vertical channel**, in the gap immediately right
  of its source's column. Channels sit strictly inside gaps, never at a
  column's x, so a vertical cannot pass through a port either. Distinct
  channels stop two nets' verticals from lying on top of each other.
- Sinks are always in a later column than their source, because a gate's
  depth is one more than its deepest driver. So a route only ever runs
  rightwards and the channel is always between the two.

The check still runs, because a structural argument that is never tested is
an assumption.
"""

from pathlib import Path

from ohmwork import logisim_symbols
from ohmwork.targets import LogisimTarget

ROW_GAP = 20            # clear space between one component's ports and the next's
STUB_STEP = 10          # per-port escape length for pins that share a row
COLUMN_GAP = 20         # minimum clear space either side of a channel band
ORIGIN = (100, 100)


class RoutingError(Exception):
    """Emitted geometry would not mean what the circuit description says."""


# ------------------------------------------------------------- geometry

def _depths(components, nets):
    """Logic depth per component: inputs 0, a gate one past its deepest driver.

    Also the acyclicity check — a combinational loop has no finite depth, and
    saying so here is better than emitting a file Logisim shows as an error.
    """
    target = LogisimTarget()
    by_ref = {c["ref"]: c for c in components}
    driver_of = {}          # ref -> ref that drives its inputs
    for members in nets.values():
        source = None
        sinks = []
        for entry in members:
            ref, pin = entry.split(".", 1)
            comp = by_ref[ref]
            if _is_source(target, comp, pin):
                source = ref
            else:
                sinks.append(ref)
        for sink in sinks:
            driver_of.setdefault(sink, []).append(source)

    depth, visiting = {}, set()

    def resolve(ref):
        if ref in depth:
            return depth[ref]
        if ref in visiting:
            raise RoutingError(
                f"combinational loop through {ref!r}: a gate network with a "
                f"cycle has no logic depth, and Logisim would evaluate it as "
                f"an error state"
            )
        visiting.add(ref)
        drivers = [d for d in driver_of.get(ref, []) if d is not None]
        depth[ref] = 1 + max((resolve(d) for d in drivers), default=-1)
        visiting.discard(ref)
        return depth[ref]

    for comp in components:
        resolve(comp["ref"])
    return depth


def _is_source(target, comp, pin):
    """Does this port drive its net? The target owns the answer."""
    return target.is_source(comp["type"], pin)


def place(circuit):
    """Assign coordinates and route. Returns (placements, wires, ports).

    Takes the CIRCUIT DESCRIPTION, not a Question: the emitter has no business
    knowing about the input gate, and analysis.execute has a circuit dict and
    no Question when it comes to write a run's file.

    placements: [{ref, type, label, loc}]   wires: [(net, ((x1,y1),(x2,y2)))]
    """
    target = LogisimTarget()
    components = circuit["components"]
    nets = circuit["nets"]
    by_ref = {c["ref"]: c for c in components}
    depth = _depths(components, nets)

    # output pins sit past every gate, so the eye reads left to right
    last = max(depth.values(), default=0)
    for comp in components:
        if comp["type"] == "output_pin":
            depth[comp["ref"]] = last + 1
    last = max(depth.values(), default=0)

    # how many nets are SOURCED at each depth: that sets each gap's width
    net_source_depth = {}
    for net, members in nets.items():
        for entry in members:
            ref, pin = entry.split(".", 1)
            if _is_source(target, by_ref[ref], pin):
                net_source_depth[net] = depth[ref]
                break

    per_depth = {}
    for net, d in net_source_depth.items():
        per_depth.setdefault(d, []).append(net)
    for d in per_depth:
        per_depth[d].sort()

    # Column x is the ANCHOR, which is where outputs sit; inputs are 50 to
    # the left of the next anchor. The channel band must fall strictly
    # between the two, so the 50 belongs to the NEXT column's body, not to
    # the start of the gap. Getting that wrong put the band on top of the
    # 4-input OR's input ports at x=190 and shorted two data inputs — caught
    # by validate_wiring, which is what it is for.
    # How far each component's ports reach from its anchor. Read from the
    # measured table rather than assumed: a gate's inputs sit 50 to the LEFT
    # of its anchor, while a 7447's pins are all to the RIGHT of one and
    # reach 150 out. A column width computed for gates puts the next
    # column's channel band straight through a DIP's pins.
    def extent(comp):
        name, attrs = target.TYPE_MAP[comp["type"]]
        offsets = logisim_symbols.ports_of(name, attrs)
        top, bottom = min(p.dy for p in offsets), max(p.dy for p in offsets)
        # Room for the escape stubs of pins that share a row -- without it a
        # 7447's stubs reach into the next component's band and the whole
        # no-shared-row argument is void again, one layer up.
        rows = {}
        for port in offsets:
            rows.setdefault(port.dy, 0)
            rows[port.dy] += 1
        top -= STUB_STEP * rows.get(top, 1) if rows.get(top, 1) > 1 else 0
        bottom += STUB_STEP * rows.get(bottom, 1) if rows.get(bottom, 1) > 1 else 0
        return (min(p.dx for p in offsets), max(p.dx for p in offsets),
                top, bottom)

    reach = {c["ref"]: extent(c) for c in components}
    in_column = {}
    for c in components:
        in_column.setdefault(depth[c["ref"]], []).append(c["ref"])

    def right_of(d):
        return max((reach[ref][1] for ref in in_column.get(d, [])), default=0)

    def left_of(d):
        return min((reach[ref][0] for ref in in_column.get(d, [])), default=0)

    # The channel band starts after the column's RIGHTMOST PORT, not after
    # its anchor. This is the second time that distinction has bitten: the
    # first was a 4-input OR whose inputs sit at -50, and the band landed on
    # them. A 7447 is the mirror image -- its pins run 150 units to the RIGHT
    # of its anchor, so a band placed a gap past the anchor sits inside the
    # chip. Same rule, opposite direction, same silent short.
    column_x, channel_base, x = {}, {}, ORIGIN[0]
    for d in range(last + 1):
        column_x[d] = x
        channels = len(per_depth.get(d, []))
        channel_base[d] = x + max(0, right_of(d)) + COLUMN_GAP
        x = (channel_base[d] + 10 * channels + COLUMN_GAP
             - min(0, left_of(d + 1)))

    channel_x = {}
    for d, net_list in per_depth.items():
        for k, net in enumerate(net_list):
            channel_x[net] = channel_base[d] + 10 * k

    # ONE ROW PER COMPONENT, globally. Wasteful and deliberate: it is what
    # makes a horizontal run unable to pass through a foreign port.
    order = sorted(components, key=lambda c: (depth[c["ref"]], c["ref"]))

    # Stack the rows from each component's OWN port span, so the band a
    # component occupies is exactly as tall as it needs and no two
    # components can share a port y whatever shapes are in the circuit.
    anchor, y = {}, ORIGIN[1]
    for c in order:
        _, _, top, bottom = reach[c["ref"]]
        anchor_y = y - top
        anchor[c["ref"]] = (column_x[depth[c["ref"]]], anchor_y)
        y = anchor_y + bottom + ROW_GAP

    placements = [
        {"ref": c["ref"], "type": c["type"],
         "label": c.get("label", c["ref"]), "loc": anchor[c["ref"]]}
        for c in order
    ]

    # A port's position, and the point a route may meet it at.
    #
    # For a gate they are the same: its ports face left and right, so a
    # horizontal run reaches one without touching another. A DIP is the case
    # that breaks: SEVEN of a 7447's pins sit on one row at the same y, and
    # routing to them along that y puts one net's endpoint on another net's
    # wire -- a silent short, and the exact hazard validate_wiring exists to
    # catch. It caught it.
    #
    # So a port that shares its row with siblings gets a short vertical stub
    # of its OWN length, pointing away from the body, and the route meets it
    # at the far end. Distinct lengths give each net a distinct horizontal
    # lane, and the stubs themselves sit at distinct x because the pins do.
    ports, meet, stubs = {}, {}, []
    for comp in components:
        name, attrs = target.TYPE_MAP[comp["type"]]
        ax, ay = anchor[comp["ref"]]
        offsets = logisim_symbols.ports_of(name, attrs)
        rows = {}
        for port in offsets:
            rows.setdefault(port.dy, []).append(port)
        middle = (min(p.dy for p in offsets) + max(p.dy for p in offsets)) / 2

        for port in offsets:
            here = (ax + port.dx, ay + port.dy)
            ports[f"{comp['ref']}.{port.name}"] = here
            siblings = sorted(rows[port.dy], key=lambda p: p.dx)
            if len(siblings) == 1:
                meet[f"{comp['ref']}.{port.name}"] = here
                continue
            # Away from the body: a row above the middle escapes upwards.
            direction = -1 if port.dy <= middle else 1
            length = STUB_STEP * (siblings.index(port) + 1)
            far = (here[0], here[1] + direction * length)
            meet[f"{comp['ref']}.{port.name}"] = far
            stubs.append((f"{comp['ref']}.{port.name}", (here, far)))

    wires = []
    stub_owner = {}
    for entry, segment in stubs:
        stub_owner[entry] = segment

    for net, members in sorted(nets.items()):
        source_xy, sinks = None, []
        for entry in members:
            ref, pin = entry.split(".", 1)
            # The escape stub belongs to the net the port is on, and is
            # emitted only for ports a net actually uses -- an unused pin
            # gets no wire, exactly as before.
            if entry in stub_owner:
                wires.append((net, stub_owner[entry]))
            if _is_source(target, by_ref[ref], pin):
                source_xy = meet[entry]
            else:
                sinks.append(meet[entry])
        if source_xy is None:
            raise RoutingError(f"net {net!r} has no driving port")
        cx = channel_x[net]
        sx, sy = source_xy
        # source stub, then ONE unsplit vertical, then a stub per sink
        wires.append((net, ((sx, sy), (cx, sy))))
        ys = [sy] + [y for _, y in sinks]
        if min(ys) != max(ys):
            wires.append((net, ((cx, min(ys)), (cx, max(ys)))))
        for tx, ty in sinks:
            if (cx, ty) != (tx, ty):
                wires.append((net, ((cx, ty), (tx, ty))))

    return placements, wires, set(ports.values())


# ----------------------------------------------------------- the check

def _touches(wire, point):
    (x1, y1), (x2, y2) = wire
    if point in ((x1, y1), (x2, y2)):
        return "end"
    px, py = point
    if x1 == x2 == px and min(y1, y2) < py < max(y1, y2):
        return "thru"
    if y1 == y2 == py and min(x1, x2) < px < max(x1, x2):
        return "thru"
    return None


def validate_wiring(wires, ports) -> None:
    """Refuse geometry that would not mean what the description says.

    Two rules, both from Logisim connecting geometrically:

    - every endpoint must land on a port or on another wire's endpoint;
      a wire ending in empty space is a dangling route
    - no endpoint may lie mid-span of a wire on a DIFFERENT net; that is a
      short with no visual cue. Mid-span of the SAME net is a T-junction and
      is exactly what fan-out looks like.
    """
    # Shorts first: they are the serious failure, and reporting a dangling
    # route instead would bury the real one.
    for i, (net, wire) in enumerate(wires):
        for point in wire:
            for j, (other_net, other) in enumerate(wires):
                if i == j or other_net == net:
                    continue
                if _touches(other, point) == "thru":
                    raise RoutingError(
                        f"wire on net {net!r} ends at {point}, which is "
                        f"mid-span of a wire on net {other_net!r}. In Logisim "
                        f"an endpoint on another wire's span IS a connection, "
                        f"so this silently shorts two nets with nothing "
                        f"visible on the canvas."
                    )

    # Then dangling routes. An endpoint is accounted for if it is a port, if
    # another wire also ends there, or if it lands mid-span of a wire on the
    # SAME net -- that last one is a T-junction, which is what fan-out looks
    # like and what a human draws.
    for i, (net, wire) in enumerate(wires):
        for point in wire:
            if point in ports:
                continue
            explained = False
            for j, (other_net, other) in enumerate(wires):
                if i == j:
                    continue
                how = _touches(other, point)
                if how == "end" or (how == "thru" and other_net == net):
                    explained = True
                    break
            if not explained:
                raise RoutingError(
                    f"wire on net {net!r} ends at {point}, which is neither a "
                    f"component port, nor another wire's endpoint, nor a "
                    f"junction on its own net — a dangling route"
                )


# ---------------------------------------------------------------- output

#: Transcribed from a real 2.7.1 file rather than invented. Whether the
#: <options>/<mappings>/<toolbar> boilerplate is required is UNVERIFIED, so
#: it is copied verbatim, same discipline as plt.py.
_HEADER = '''<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<project source="2.7.1" version="1.0">
This file is intended to be loaded by Logisim (http://www.cburch.com/logisim/).
<lib desc="#Wiring" name="0"/>
  <lib desc="#Gates" name="1"/>
  <lib desc="#Plexers" name="2"/>
  <lib desc="#Arithmetic" name="3"/>
  <lib desc="#Memory" name="4"/>
  <lib desc="#I/O" name="5"/>
  <lib desc="#Base" name="6"/>
  <main name="main"/>
  <options>
    <a name="gateUndefined" val="ignore"/>
    <a name="simlimit" val="1000"/>
    <a name="simrand" val="0"/>
  </options>
  <mappings>
    <tool lib="6" map="Button2" name="Menu Tool"/>
    <tool lib="6" map="Button3" name="Menu Tool"/>
    <tool lib="6" map="Ctrl Button1" name="Menu Tool"/>
  </mappings>
  <toolbar>
    <tool lib="6" name="Poke Tool"/>
    <tool lib="6" name="Edit Tool"/>
    <sep/>
    <tool lib="0" name="Pin"/>
    <tool lib="1" name="NOT Gate"/>
    <tool lib="1" name="AND Gate"/>
    <tool lib="1" name="OR Gate"/>
  </toolbar>
  <circuit name="main">
    <a name="circuit" val="main"/>
    <a name="clabel" val=""/>
    <a name="clabelup" val="east"/>
    <a name="clabelfont" val="SansSerif plain 12"/>
'''

#: index into the <lib> block ABOVE. Kept beside it deliberately: these
#: numbers are file-local and mean nothing on their own.
#: Library indices for each header. They are NOT the same, and they are not
#: interchangeable: `lib="6"` is #Base in a 2.7.1 file and #TTL in an
#: Evolution one. Resolving by NAME through the header actually emitted is
#: the same discipline the parser follows when reading a foreign file.
_LIB_INDEX = {
    "2.7.1": {"#Wiring": "0", "#Gates": "1", "#Base": "6"},
    "evolution": {"#Wiring": "0", "#Gates": "1", "#Plexers": "2",
                  "#Arithmetic": "3", "#Memory": "4", "#I/O": "5",
                  "#TTL": "6", "#Base": "8"},
}

#: Which libraries a Logisim 2.7.1 file can declare at all. A circuit using
#: anything else is not a 2.7.1 circuit and must not claim to be one: 2.7.1
#: has no TTL library, so a file with a 7447 in it would not open.
_CLASSIC_LIBS = frozenset({"#Wiring", "#Gates", "#Base"})


def _dialect(circuit, target) -> str:
    """Which Logisim the emitted file has to be.

    2.7.1 by default and by preference -- every geometry measurement behind
    the gate table came from 2.7.1 files, and Evolution opens them in
    compatibility mode. But a 7447 exists ONLY in Evolution, so a circuit
    containing one is an Evolution circuit and saying otherwise would produce
    a file that names a library its own header does not declare.
    """
    for comp in circuit["components"]:
        name, _ = target.TYPE_MAP[comp["type"]]
        if logisim_symbols.LIB_OF[name] not in _CLASSIC_LIBS:
            return "evolution"
    return "2.7.1"


#: Copied verbatim from a real Logisim Evolution file, the same discipline
#: plt.py follows: transcribe the vendor's own boilerplate rather than invent
#: a minimal one, because which parts of it are load-bearing is unknown.
_EVOLUTION_HEADER = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<project source="3.8.0" version="1.0">\n  This file is intended to be loaded by Logisim-evolution v3.8.0(https://github.com/logisim-evolution/).\n\n  <lib desc="#Wiring" name="0">\n    <tool name="Splitter">\n      <a name="appear" val="right"/>\n      <a name="fanout" val="4"/>\n      <a name="incoming" val="4"/>\n    </tool>\n    <tool name="Pin">\n      <a name="appearance" val="classic"/>\n    </tool>\n    <tool name="Probe">\n      <a name="appearance" val="classic"/>\n      <a name="facing" val="north"/>\n    </tool>\n    <tool name="Constant">\n      <a name="value" val="0x0"/>\n    </tool>\n  </lib>\n  <lib desc="#Gates" name="1"/>\n  <lib desc="#Plexers" name="2"/>\n  <lib desc="#Arithmetic" name="3"/>\n  <lib desc="#Memory" name="4"/>\n  <lib desc="#I/O" name="5"/>\n  <lib desc="#TTL" name="6"/>\n  <lib desc="#TCL" name="7"/>\n  <lib desc="#Base" name="8"/>\n  <lib desc="#BFH-Praktika" name="9"/>\n  <lib desc="#Input/Output-Extra" name="10"/>\n  <lib desc="#Soc" name="11"/>\n  <main name="main"/>\n  <options>\n    <a name="gateUndefined" val="ignore"/>\n    <a name="simlimit" val="1000"/>\n    <a name="simrand" val="0"/>\n  </options>\n  <mappings>\n    <tool lib="8" map="Button2" name="Poke Tool"/>\n    <tool lib="8" map="Button3" name="Menu Tool"/>\n    <tool lib="8" map="Ctrl Button1" name="Menu Tool"/>\n  </mappings>\n  <toolbar>\n    <tool lib="8" name="Poke Tool"/>\n    <tool lib="8" name="Edit Tool"/>\n    <tool lib="8" name="Wiring Tool"/>\n    <tool lib="8" name="Text Tool"/>\n    <sep/>\n    <tool lib="0" name="Pin"/>\n    <tool lib="0" name="Pin">\n      <a name="facing" val="west"/>\n      <a name="output" val="true"/>\n    </tool>\n    <sep/>\n    <tool lib="1" name="NOT Gate"/>\n    <tool lib="1" name="AND Gate"/>\n    <tool lib="1" name="OR Gate"/>\n    <tool lib="1" name="XOR Gate"/>\n    <tool lib="1" name="NAND Gate"/>\n    <tool lib="1" name="NOR Gate"/>\n    <sep/>\n    <tool lib="4" name="D Flip-Flop"/>\n    <tool lib="4" name="Register"/>\n  </toolbar>\n  <circuit name="main">\n'


def emit_circ(circuit) -> str:
    target = LogisimTarget()
    placements, wires, ports = place(circuit)
    validate_wiring(wires, ports)

    for item in placements:
        logisim_symbols.check_label(item["label"])

    dialect = _dialect(circuit, target)
    index = _LIB_INDEX[dialect]
    lines = [_HEADER if dialect == "2.7.1" else _EVOLUTION_HEADER]
    for _, ((x1, y1), (x2, y2)) in wires:
        lines.append(f'    <wire from="({x1},{y1})" to="({x2},{y2})"/>\n')
    for item in placements:
        name, attrs = target.TYPE_MAP[item["type"]]
        lib = index[logisim_symbols.LIB_OF[name]]
        x, y = item["loc"]
        lines.append(f'    <comp lib="{lib}" loc="({x},{y})" name="{name}">\n')
        for key, value in sorted(attrs.items()):
            lines.append(f'      <a name="{key}" val="{value}"/>\n')
        if name == "Pin":
            # absence of output= means INPUT; never rely on the default
            if item["type"] == "input_pin":
                lines.append('      <a name="tristate" val="false"/>\n')
            else:
                lines.append('      <a name="labelloc" val="east"/>\n')
        lines.append(f'      <a name="label" val="{item["label"]}"/>\n')
        lines.append("    </comp>\n")
    lines.append("  </circuit>\n</project>\n")
    return "".join(lines)


def write_circ(circuit, path) -> Path:
    path = Path(path)
    path.write_text(emit_circ(circuit), encoding="utf-8", newline="\n")
    return path
