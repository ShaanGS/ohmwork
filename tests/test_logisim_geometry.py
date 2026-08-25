"""Real-file measurements for the Logisim .circ format.

Same discipline as test_symbols.py: every number in this file was MEASURED
from a real hand-drawn .circ, never taken from documentation or inference.
No ohmwork code is imported, so these tests are pure evidence about the file
format and stay valid no matter what we build on top of them.

HOW THESE WERE DERIVED -- the method, which matters more than the numbers.

A port is a coordinate where EXACTLY ONE WIRE TERMINATES. Nothing else is
evidence. In particular, proximity to a component is not: a human routes
wires around and into component bodies, so "the endpoint nearest the gate"
finds corners and stray stubs as readily as pins.

Worked example, the four XOR gates in adder_subtractor.circ, showing the
number of wires touching each candidate offset:

  (-50,-20)   degree 1, 1, 1, 1     <- port
  (-50,+20)   degree 2, 1, 1, 0     <- port
  (-60,-20)   degree 2, 2, 2, 2     <- NOT a port: a bend
  (-60,+20)   degree 2, 2, 2, 2     <- NOT a port: a bend

Degree 2 with both wires ending there is a corner, where a horizontal and a
vertical segment meet. Proximity filtering picks those up and lands on
(-60,+-20), which is wrong.

One caution the example also shows: degree 1 PROVES a port, but degree != 1
does not disprove one. (-50,+20) is degree 2 on the first XOR, because the
human happened to route a corner exactly onto the pin, and degree 0 on the
fourth, because that input is simply unwired. So dead ends give you
CANDIDATES; confirmation comes from the hypothesis holding across every
instance, and from a file being explained with nothing left over.

Fixtures, redacted (see tests/fixtures/README.md) but otherwise byte-for-byte
copies of files drawn by hand in a lab:

  exp8_gates.circ         was "EXP 8.circ" -- gate-level 4-to-2 priority
                          encoder with enable and valid. Primitives only.
  priority_plexers.circ   was "4_to_2_priority.circ" -- the same question
                          solved with the built-in Plexers Priority Encoder,
                          i.e. the exact trap CLAUDE.md predicted for Q2.
  adder_subtractor.circ   was "open ended logisim.circ" -- 4-bit adder /
                          subtractor. Has drawing errors (see below); kept
                          because a parser must survive real input.
  shuffled_libs.circ      DERIVED, not hand-drawn: priority_plexers.circ with
                          its <lib> indices permuted. Used in
                          test_logisim_symbols.py. Never opened in Logisim.

ALL THREE ARE LOGISIM 2.7.1, which is now the build target (Q4 and Logisim
Evolution are parked -- see CLAUDE.md). Nothing here may be assumed to carry
over to Evolution if it ever comes back.

STILL UNVERIFIED, do not guess (nothing in these files exercises it):
  - Tunnels. Zero occurrences across all three files, so the load-bearing
    question -- is a tunnel a net or a point-to-point link -- is still open.
  - Gate "size" attribute. Every gate here is default size; narrow/wide
    gates will have different input offsets.
  - Gate "facing". Every component here faces east.
  - NAND / NOR gates, and XOR with more than 2 inputs.
  - Splitter port geometry (one instance, and its ports are ambiguous
    against a neighbouring pin -- deliberately not measured).
  - Whether the <options>/<toolbar>/<mappings> boilerplate is required.
"""

import re
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "logisim"

WIRE = re.compile(r'<wire from="\((-?\d+),(-?\d+)\)" to="\((-?\d+),(-?\d+)\)"')
COMP = re.compile(r'<comp lib="(\d+)" loc="\((-?\d+),(-?\d+)\)" name="([^"]+)"(/?)>')
ATTR = re.compile(r'<a name="([^"]+)" val="([^"]*)"/>')
LIB = re.compile(r'<lib desc="([^"]+)" name="(\d+)"')


# --------------------------------------------------------------- reading

def read(name):
    """Pull libraries, wires and components out of a .circ.

    Returns (libs, wires, comps) where libs maps the numeric index used by
    comp/@lib onto the library's real name ("#Gates"), because the index is
    file-local and means nothing on its own.
    """
    text = (FIXTURES / name).read_text(encoding="utf-8")
    libs = {index: desc for desc, index in LIB.findall(text)}
    wires = [((int(a), int(b)), (int(c), int(d))) for a, b, c, d in WIRE.findall(text)]
    comps = []
    for m in COMP.finditer(text):
        lib, x, y, cname, selfclose = m.groups()
        attrs = {}
        if not selfclose:
            attrs = dict(ATTR.findall(text[m.end():text.index("</comp>", m.end())]))
        comps.append({"lib": libs[lib], "name": cname,
                      "loc": (int(x), int(y)), "attrs": attrs})
    return libs, wires, comps


def touches(wire, point):
    """How a wire meets a point: at an 'end', passing 'thru', or not at all.

    Logisim connects geometrically. A wire ending on another wire's middle
    (a T) connects; two wires merely crossing in an X do not.
    """
    (x1, y1), (x2, y2) = wire
    if point in ((x1, y1), (x2, y2)):
        return "end"
    px, py = point
    if x1 == x2 == px and min(y1, y2) < py < max(y1, y2):
        return "thru"
    if y1 == y2 == py and min(x1, x2) < px < max(x1, x2):
        return "thru"
    return None


def discriminator(comp):
    """The one attribute that changes a component's port layout, if any."""
    for k in ("inputs", "select", "width"):
        if k in comp["attrs"]:
            return comp["attrs"][k]
    return None


# ------------------------------------------------- THE MEASURED PIN TABLE
#
# Offsets are relative to the component's loc, which for every component
# measured is also its OUTPUT pin. Measured at default size, facing east.
#
#   component            instances  measured in
#   Pin                  30         all three files
#   NOT Gate              1         exp8_gates
#   AND Gate (2 in)       4         exp8_gates
#   OR Gate  (2 in)       2         exp8_gates
#   XOR Gate (2 in)       4         adder_subtractor
#   OR Gate  (4 in)       1         exp8_gates
#   Adder    (width 1)    4         adder_subtractor
#   Priority Encoder      1         priority_plexers

PORTS = {
    ("Pin", None): [(0, 0)],
    ("NOT Gate", None): [(-30, 0), (0, 0)],
    ("AND Gate", "2"): [(-50, -20), (-50, 20), (0, 0)],
    ("OR Gate", "2"): [(-50, -20), (-50, 20), (0, 0)],
    ("XOR Gate", "2"): [(-50, -20), (-50, 20), (0, 0)],
    ("OR Gate", "4"): [(-50, -20), (-50, -10), (-50, 10), (-50, 20), (0, 0)],
    ("Adder", "1"): [(-40, -10), (-40, 10), (-20, -20), (-20, 20), (0, 0)],
    ("Priority Encoder", "2"): [(-40, -10), (-40, 0), (-40, 10), (-40, 20),
                                (-20, 30), (0, 0), (0, 10)],
}

ALL_FILES = ["exp8_gates.circ", "priority_plexers.circ", "adder_subtractor.circ"]


# ---------------------------------------------------------------- format

def test_every_fixture_is_logisim_2_7_1():
    # 2.7.1 is the build target. If this ever fails because someone added an
    # Evolution fixture, the pin table above must be re-derived against it
    # rather than assumed to carry over.
    for name in ALL_FILES:
        text = (FIXTURES / name).read_text(encoding="utf-8")
        assert '<project source="2.7.1" version="1.0">' in text


def test_line_endings_are_not_an_invariant():
    # CLAUDE.md recorded "files use CRLF" from a single file. Two of these
    # three are CRLF and one is bare LF, and all three are real files that
    # opened in Logisim. So the emitter must not treat CRLF as required.
    def endings(name):
        raw = (FIXTURES / name).read_bytes()
        crlf = raw.count(b"\r\n")
        return crlf, raw.count(b"\n") - crlf

    # counts are post-redaction (see tests/fixtures/README.md); the mix of
    # CRLF and LF across real files is the point, not the exact totals
    assert endings("exp8_gates.circ") == (196, 0)
    assert endings("adder_subtractor.circ") == (229, 0)
    assert endings("priority_plexers.circ") == (0, 128)


def test_component_element_shape():
    # <comp lib="N" loc="(x,y)" name="Name"> with <a name= val=/> children,
    # or self-closing when every attribute is default.
    _, _, comps = read("exp8_gates.circ")
    and_gates = [c for c in comps if c["name"] == "AND Gate"]
    assert len(and_gates) == 4
    assert all(c["attrs"] == {"inputs": "2"} for c in and_gates)
    nots = [c for c in comps if c["name"] == "NOT Gate"]
    assert nots[0]["attrs"] == {}          # self-closing, all defaults


def test_lib_index_is_file_local_and_must_be_resolved():
    # comp/@lib is an index into this file's <lib> block, not a global id.
    # Resolve through the block; never hardcode "1" as Gates.
    for name in ALL_FILES:
        libs, _, _ = read(name)
        assert libs["0"] == "#Wiring" and libs["1"] == "#Gates"
        assert libs["2"] == "#Plexers" and libs["6"] == "#Base"
    _, _, comps = read("exp8_gates.circ")
    assert {c["lib"] for c in comps} == {"#Gates", "#Wiring"}


def test_pin_input_and_output_encoding():
    # An output Pin carries output=true (plus facing=west, labelloc=east as
    # drawn). Absence of output= means INPUT: the default is not neutral.
    _, _, comps = read("exp8_gates.circ")
    pins = {c["attrs"]["label"]: c["attrs"] for c in comps if c["name"] == "Pin"}
    assert pins["OUT 1"]["output"] == "true"
    assert pins["OUT 1"]["facing"] == "west"
    assert "output" not in pins["D3"]
    assert pins["D3"]["tristate"] == "false"


# Coordinates of the six Text elements redacted from these fixtures (they
# carried students' names and registration numbers -- see
# tests/fixtures/README.md). Kept because they are the evidence for the
# measured fact below: Text is the ONE element type not on the grid, and all
# six were off it. The text they contained is gone and is not needed.
REDACTED_TEXT_LOCS = [(376, 274), (372, 258), (463, 115), (210, 116)]


def test_text_was_the_only_thing_off_the_ten_unit_grid():
    # Components and wires snap to a 10-unit grid; the text tool places
    # freely. Every redacted Text element was off-grid, and every component
    # that survives redaction is on it.
    assert all(x % 10 or y % 10 for x, y in REDACTED_TEXT_LOCS)

    for name in ALL_FILES:
        _, wires, comps = read(name)
        assert not any(c["name"] == "Text" for c in comps), "redaction incomplete"
        for c in comps:
            assert c["loc"][0] % 10 == 0 and c["loc"][1] % 10 == 0, (name, c)
        for a, b in wires:
            assert a[0] % 10 == 0 and a[1] % 10 == 0
            assert b[0] % 10 == 0 and b[1] % 10 == 0


def test_no_personal_data_survives_in_any_fixture():
    # Redaction guard. If a new real file is added, redact it the same way.
    import re
    pattern = re.compile(r"RA\d{10,}|name\s*:-", re.IGNORECASE)
    for path in sorted(FIXTURES.glob("*.circ")):
        text = path.read_text(encoding="utf-8")
        assert not pattern.search(text), path.name
        assert 'name="Text"' not in text, path.name


def test_no_tunnels_anywhere():
    # The reason the Logisim emitter cannot copy the LTspice no-routing
    # strategy yet. Delete this test the day a fixture with tunnels arrives.
    for name in ALL_FILES:
        assert "Tunnel" not in (FIXTURES / name).read_text(encoding="utf-8")


# ------------------------------------------------------------- pin table

@pytest.mark.parametrize("name", ALL_FILES)
def test_measured_ports_land_on_real_wire_geometry(name):
    """Every predicted port coordinate is a point the human actually wired.

    One documented exception: adder_subtractor has a genuinely floating XOR
    input, which is a mistake in the drawing, not in the table.
    """
    _, wires, comps = read(name)
    drawn = {p for w in wires for p in w}
    floating = []
    for c in comps:
        key = (c["name"], discriminator(c))
        if key not in PORTS:
            continue
        for dx, dy in PORTS[key]:
            p = (c["loc"][0] + dx, c["loc"][1] + dy)
            if p not in drawn:
                floating.append((c["name"], c["loc"], p))
    if name == "adder_subtractor.circ":
        assert floating == [("XOR Gate", (410, 250), (360, 270))]
    else:
        assert floating == []


def test_exp8_geometry_is_completely_explained():
    """The strongest evidence for the table: one whole file, nothing left over.

    Every dead-end wire endpoint in exp8_gates.circ is a port in the table,
    and every port in the table is wired. A wrong offset would leave either
    an unexplained dead end or an unwired port.
    """
    _, wires, comps = read("exp8_gates.circ")
    ports = set()
    for c in comps:
        key = (c["name"], discriminator(c))
        if key in PORTS:
            ports |= {(c["loc"][0] + dx, c["loc"][1] + dy) for dx, dy in PORTS[key]}

    endpoints = {p for w in wires for p in w}
    dead_ends = {p for p in endpoints
                 if sum(1 for w in wires if touches(w, p)) == 1}

    # The two sets are not merely compatible, they are identical: 33 ports,
    # 33 dead-end wire endpoints, perfect bijection.
    assert dead_ends == ports
    assert len(ports) == 33


def test_gate_input_spacing_differs_by_input_count():
    # Two inputs sit 40 apart; four sit at -20,-10,+10,+20 -- i.e. 10 apart
    # but straddling the axis, with no input on it. Measured, not assumed:
    # a linear "spacing * index" rule would be wrong for one or the other.
    assert PORTS[("AND Gate", "2")][:2] == [(-50, -20), (-50, 20)]
    assert PORTS[("OR Gate", "4")][:4] == [(-50, -20), (-50, -10),
                                           (-50, 10), (-50, 20)]
    _, wires, comps = read("exp8_gates.circ")
    or4 = next(c for c in comps if c["name"] == "OR Gate"
               and c["attrs"].get("inputs") == "4")
    assert or4["loc"] == (370, 200)
    drawn = {p for w in wires for p in w}
    assert {(320, 180), (320, 190), (320, 210), (320, 220)} <= drawn


# --------------------------------------------------------- connectivity

def orientation(wire):
    (x1, y1), (x2, y2) = wire
    if x1 == x2 and y1 != y2:
        return "v"
    if y1 == y2 and x1 != x2:
        return "h"
    return None


def true_crossings(wires):
    """Every (horizontal, vertical) pair meeting strictly INSIDE both spans.

    Not a T and not a shared endpoint: an X, where neither wire ends. This is
    the case the connection rule has to get right, and where getting it wrong
    is invisible -- the drawing looks the same either way.
    """
    found = []
    for h in wires:
        if orientation(h) != "h":
            continue
        (hx1, hy), (hx2, _) = h
        for v in wires:
            if orientation(v) != "v":
                continue
            (vx, vy1), (_, vy2) = v
            if min(hx1, hx2) < vx < max(hx1, hx2) and min(vy1, vy2) < hy < max(vy1, vy2):
                found.append((h, v, (vx, hy)))
    return found


def connectivity(name, crossings_connect=False):
    """Group every coordinate in a file. Returns (find, wires, ports).

    `find(point)` gives the net a coordinate belongs to, so a test can ask
    about WIRES as well as about ports.

    crossings_connect exists ONLY so a test can build the wrong model and
    show what it does to a known-good circuit. Real parsing never sets it.
    """
    _, wires, comps = read(name)
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    points = set()
    for w in wires:
        points |= {w[0], w[1]}
        union(w[0], w[1])

    ports = []
    for c in comps:
        key = (c["name"], discriminator(c))
        if key not in PORTS:
            continue
        for i, (dx, dy) in enumerate(PORTS[key]):
            p = (c["loc"][0] + dx, c["loc"][1] + dy)
            ports.append((p, c, i))
            points.add(p)

    for p in points:
        for w in wires:
            if touches(w, p) == "thru":
                union(p, w[0])

    if crossings_connect:                      # the WRONG model, for contrast
        for h, v, _ in true_crossings(wires):
            union(h[0], v[0])

    return find, wires, ports


def build_nets(name, crossings_connect=False):
    """Nets as {root: [(component, port index)]}, ports only."""
    find, _, ports = connectivity(name, crossings_connect)
    nets = {}
    for p, c, i in ports:
        nets.setdefault(find(p), []).append((c, i))
    return nets


def drivers_per_net(name, crossings_connect=False):
    """How many ports SOURCE each net. A working circuit has exactly one."""
    def is_source(comp, index):
        if comp["name"] == "Pin":
            return comp["attrs"].get("output") != "true"
        return PORTS[(comp["name"], discriminator(comp))][index] == (0, 0)

    counts = []
    for members in build_nets(name, crossings_connect).values():
        sources = [c["attrs"].get("label") or c["name"]
                   for c, i in members if is_source(c, i)]
        counts.append(sources)
    return counts


def test_exp8_netlist_rebuilds_with_nothing_floating():
    # The Logisim equivalent of the .asc round trip: connectivity recovered
    # from coordinates only. 13 nets, every gate pin on one of them.
    nets = build_nets("exp8_gates.circ")
    assert len(nets) == 13
    assert all(len(members) >= 2 for members in nets.values())
    assert sum(len(m) for m in nets.values()) == 33


def test_exp8_recovered_structure_is_a_priority_encoder():
    """Read the recovered graph back as boolean logic.

    This checks STRUCTURE, not simulated values -- it asserts which gate feeds
    which, which is a property of the parse, not of any evaluator. The
    resulting expressions still need a human to compare against a textbook:

        OUT 1 = E . (D3 + D2)
        OUT 2 = E . (D3 + D1 . ~D2)
        V     = E . (D3 + D2 + D1 + D0)

    WHAT THIS FILE IS AND IS NOT, for the evaluator asymmetry: it is an
    independent IMPLEMENTATION, not an independent EVALUATOR. Running it
    through our logic engine and comparing against our own generated Q2
    answer cross-checks two implementations THROUGH ONE EVALUATOR. That
    catches a bug in either implementation and catches nothing whatsoever in
    the evaluator, because a broken evaluator breaks both sides identically
    and they agree anyway. The only outside check on the evaluator remains a
    truth table computed by hand. See CLAUDE.md, "the evaluator asymmetry".
    """
    nets = build_nets("exp8_gates.circ")

    def is_source(comp, index):
        """Does this port DRIVE its net, rather than listen to it?

        An input Pin drives; an output Pin listens. A gate drives on the port
        at offset (0,0), i.e. at loc. Test the OFFSET, not the position in
        the list: Adder and Priority Encoder each have a second output, so
        "the last port is the output" is false in general and would be a
        silent error the day this file grows past gates.

        Getting this wrong is easy and quiet in a different way too: on a
        fan-out net, picking "some other member" hands you a fellow listener
        instead of the driver.
        """
        if comp["name"] == "Pin":
            return comp["attrs"].get("output") != "true"
        return PORTS[(comp["name"], discriminator(comp))][index] == (0, 0)

    def driver_of(comp, index):
        """The single source on the net that comp's port `index` sits on."""
        target = (comp["loc"], index)
        for members in nets.values():
            if not any((c["loc"], i) == target for c, i in members):
                continue
            sources = [(c, i) for c, i in members if is_source(c, i)]
            assert len(sources) == 1, (target, sources)
            c, _ = sources[0]
            return c["attrs"]["label"] if c["name"] == "Pin" else (c["name"], c["loc"])
        raise AssertionError("no net contains {}".format(target))

    _, _, comps = read("exp8_gates.circ")
    by_loc = {c["loc"]: c for c in comps}

    def drivers(comp, count):
        return sorted(str(driver_of(comp, i)) for i in range(count))

    or4 = by_loc[(370, 200)]                      # 4-input OR: any input high
    assert drivers(or4, 4) == ["D0", "D1", "D2", "D3"]

    valid = by_loc[(500, 130)]                    # AND -> V
    assert drivers(valid, 2) == ["('OR Gate', (370, 200))", "E IN"]

    or_hi = by_loc[(490, 360)]                    # D3 + D2
    assert drivers(or_hi, 2) == ["D2", "D3"]

    out1 = by_loc[(620, 280)]                     # AND -> OUT 1 (MSB)
    assert drivers(out1, 2) == ["('OR Gate', (490, 360))", "E IN"]

    inv = by_loc[(410, 480)]                      # ~D2
    assert driver_of(inv, 0) == "D2"

    and_lo = by_loc[(640, 430)]                   # D1 . ~D2
    assert drivers(and_lo, 2) == ["('NOT Gate', (410, 480))", "D1"]

    or_lo = by_loc[(790, 430)]                    # D3 + D1.~D2
    assert drivers(or_lo, 2) == ["('AND Gate', (640, 430))", "D3"]

    out2 = by_loc[(950, 430)]                     # AND -> OUT 2 (LSB)
    assert drivers(out2, 2) == ["('OR Gate', (790, 430))", "E IN"]

    # and the three output pins take those three gates
    assert driver_of(by_loc[(670, 140)], 0) == ("AND Gate", (500, 130))   # V
    assert driver_of(by_loc[(670, 190)], 0) == ("AND Gate", (620, 280))   # OUT 1
    assert driver_of(by_loc[(670, 230)], 0) == ("AND Gate", (950, 430))   # OUT 2


# ------------------------------------------------------ primitives_only

def test_plexers_priority_encoder_signature():
    """The XML that primitives_only must mechanically reject.

    A classmate solved the same question with the built-in part, which is the
    failure CLAUDE.md predicted. Enforcement resolves comp/@lib through the
    <lib> block and rejects anything from #Plexers -- it must not match on
    the literal string lib="2", which is only this file's index.
    """
    _, _, comps = read("priority_plexers.circ")
    builtin = [c for c in comps if c["lib"] == "#Plexers"]
    assert len(builtin) == 1
    assert builtin[0]["name"] == "Priority Encoder"
    assert builtin[0]["attrs"] == {"select": "2"}

    _, _, gate_comps = read("exp8_gates.circ")
    assert [c for c in gate_comps if c["lib"] == "#Plexers"] == []


def test_hand_drawn_files_are_not_necessarily_valid():
    """A parser must not assume real input is correct.

    adder_subtractor.circ declares no output pins at all -- S0..S3 and Cout
    are input Pins being driven by adder outputs -- and one XOR input is
    unwired. Both would show as errors in Logisim.
    """
    _, _, comps = read("adder_subtractor.circ")
    pins = [c for c in comps if c["name"] == "Pin"]
    assert len(pins) == 14
    assert all("output" not in c["attrs"] for c in pins)


# ------------------------------------------- the crossing rule (decisive)
#
# CONNECT:     two wires sharing an endpoint
# CONNECT:     an endpoint lying on another wire's span (a T)
# DO NOT:      two spans intersecting where NEITHER wire ends (an X)
#
# This is not a style preference, it changes what the circuit IS. The
# evidence is below: exp8_gates.circ is a correct, working, human-built
# priority encoder, and it CANNOT be one if crossings connect.


def test_true_crossings_are_common_in_hand_drawn_files():
    # A human routing by hand crosses wires constantly. Any parser that gets
    # this wrong gets these files wrong, not in an edge case but throughout.
    assert len(true_crossings(read("exp8_gates.circ")[1])) == 20
    assert len(true_crossings(read("adder_subtractor.circ")[1])) == 4


@pytest.mark.parametrize("name,total,merging", [
    ("exp8_gates.circ", 20, 19),
    ("adder_subtractor.circ", 4, 4),
])
def test_crossings_would_merge_otherwise_distinct_nets(name, total, merging):
    """The crossings are not harmless coincidences: they carry real signals.

    19 of exp8's 20 crossings join wires that are on DIFFERENT nets. (The
    twentieth joins two wires already connected by another path -- which is
    exactly why "it looks joined on screen" is not evidence either way.)
    """
    find, wires, _ = connectivity(name)               # correct model
    crossings = true_crossings(wires)
    assert len(crossings) == total

    # a crossing "merges" if, under the correct model, its two wires are on
    # genuinely different nets -- so treating it as a connection would short
    # two real signals together
    assert sum(1 for h, v, _ in crossings if find(h[0]) != find(v[0])) == merging

    assert len(build_nets(name, crossings_connect=True)) < len(build_nets(name))


def test_exp8_is_only_a_working_circuit_if_crossings_do_not_connect():
    """The decisive argument, stated as an assertion.

    Under the correct model exp8 has 13 nets, each with exactly one driver.
    Under "crossings connect" it collapses to 5 nets, two of which have FIVE
    drivers each -- all four data inputs shorted together, and the enable pin
    shorted to three gate outputs. A human's working priority encoder cannot
    be that. Therefore crossings do not connect.
    """
    correct = drivers_per_net("exp8_gates.circ")
    assert len(correct) == 13
    assert all(len(sources) == 1 for sources in correct)

    wrong = drivers_per_net("exp8_gates.circ", crossings_connect=True)
    assert len(wrong) == 5
    shorted = sorted(sorted(s) for s in wrong if len(s) > 1)
    assert len(shorted) == 2
    assert shorted[0] == ["AND Gate", "E IN", "OR Gate", "OR Gate", "OR Gate"]
    assert shorted[1] == ["D0", "D1", "D2", "D3", "NOT Gate"]


def test_crossing_model_shorts_the_adder_inputs_too():
    # Second file, same conclusion. adder_subtractor already has 4 bad nets
    # of its own (S0-S3 declared as input Pins while driven -- see
    # test_hand_drawn_files_are_not_necessarily_valid); the crossing model
    # adds a fifth by shorting A3, B3 and an XOR output together.
    correct = drivers_per_net("adder_subtractor.circ")
    wrong = drivers_per_net("adder_subtractor.circ", crossings_connect=True)
    assert sum(1 for s in correct if len(s) > 1) == 4
    assert sum(1 for s in wrong if len(s) > 1) == 5
    assert ["A3", "B3", "XOR Gate"] in [sorted(s) for s in wrong]


# ---- both directions, minimal and explicit ----
#
# The connect/no-connect distinction turns entirely on whether an endpoint
# exists at a coordinate. The same picture on screen is two different
# circuits depending on how the segments were split, so both directions get
# a test that does not depend on any fixture.

def _tiny_nets(wires, probes):
    """Group `probes` by connectivity over `wires`, using the real rule."""
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    points = set(probes)
    for w in wires:
        points |= {w[0], w[1]}
        union(w[0], w[1])
    for p in points:
        for w in wires:
            if touches(w, p) == "thru":
                union(p, w[0])
    groups = {}
    for p in probes:
        groups.setdefault(find(p), []).append(p)
    return list(groups.values())


def test_a_crossing_does_not_connect():
    # horizontal (0,50)-(100,50) crossed by vertical (50,0)-(50,100).
    # Neither ends at (50,50). Two separate nets.
    wires = [((0, 50), (100, 50)), ((50, 0), (50, 100))]
    groups = _tiny_nets(wires, [(0, 50), (50, 0)])
    assert len(groups) == 2


def test_a_t_junction_does_connect():
    # Same picture, but the vertical STOPS on the horizontal's span.
    # One net.
    wires = [((0, 50), (100, 50)), ((50, 0), (50, 50))]
    groups = _tiny_nets(wires, [(0, 50), (50, 0)])
    assert len(groups) == 1


def test_splitting_a_wire_at_the_crossing_changes_the_circuit():
    """The silent-change hazard, made explicit.

    Drawing the same horizontal line as two segments that meet at the
    crossing point turns an X into a junction. Identical on screen,
    different circuit. An emitter must never split a wire at a crossing.
    """
    crossing = [((0, 50), (100, 50)), ((50, 0), (50, 100))]
    split = [((0, 50), (50, 50)), ((50, 50), (100, 50)), ((50, 0), (50, 100))]
    assert len(_tiny_nets(crossing, [(0, 50), (50, 0)])) == 2
    assert len(_tiny_nets(split, [(0, 50), (50, 0)])) == 1
