"""ohmwork.logisim_symbols must agree with the measurements, and refuse to guess.

The measurements live in test_logisim_geometry.py, which imports no ohmwork
code and derives everything from the real fixtures. This file checks that the
module carries the same numbers and that it hard-fails on anything no real
file has pinned.
"""

from pathlib import Path

import pytest

from ohmwork.logisim_symbols import (
    PORTS,
    PRIMITIVE_LIBS,
    UnmeasuredGeometryError,
    port_positions,
    ports_of,
    resolve_lib_indices,
)
from test_logisim_geometry import PORTS as MEASURED, FIXTURES


def test_module_table_matches_the_independent_measurements():
    """The module may not carry a number the evidence file does not.

    test_logisim_geometry.py measured these against real files without
    importing anything from ohmwork. If the two ever disagree, the module is
    wrong -- the evidence is not.
    """
    module = {key: [(p.dx, p.dy) for p in ports] for key, ports in PORTS.items()}

    # The 2.7.1 components, which that file measures, must match EXACTLY.
    covered = {key: value for key, value in module.items() if key in MEASURED}
    assert covered == MEASURED

    # Everything else must be covered by other evidence, named here. This is
    # the half that stops the check going quiet: without it, adding a new
    # component with invented offsets would pass, because the equality above
    # only sees the keys the old fixtures happen to contain.
    from ohmwork.logisim_symbols import GATE_INPUT_COUNTS
    ELSEWHERE = {
        **{(name, n): "tests/test_logisim_gates.py — a Pin placed exactly on "
                      "each port with no wire, evaluated by Evolution 4.1.0"
           for name, counts in GATE_INPUT_COUNTS.items() for n in counts},
        ("7447", None): "tests/test_logisim_ttl.py — Evolution evaluates a "
                        "circuit built from these offsets and decodes BCD",
        ("7-Segment Display", None): "tests/test_logisim_ttl.py — dead ends in "
                                     "evolution_7447_display.circ",
        ("Constant", None): "tests/test_logisim_ttl.py — one port at loc, "
                            "measured across 14 instances and confirmed by "
                            "Evolution evaluating a circuit driven by it",
    }
    unexplained = set(module) - set(MEASURED) - set(ELSEWHERE)
    assert not unexplained, (
        f"{unexplained} is in the pin table with no evidence file named for "
        f"it. Add the measurement, or say where it was measured.")


# ------------------------------------------------- refusing to guess

def test_unmeasured_input_count_raises():
    # 5-input AND was probed only through wires, which cannot separate x
    # candidates 10 apart, so it is not in the table. Interpolating from the
    # measured counts would be inventing geometry.
    with pytest.raises(UnmeasuredGeometryError) as excinfo:
        ports_of("AND Gate", {"inputs": "5"})
    message = str(excinfo.value)
    assert "AND Gate" in message and "'5'" in message
    assert "['2', '3', '4', '8']" in message   # names what HAS been measured
    assert "real" in message and "required" in message


def test_measured_input_counts_still_work():
    assert len(ports_of("AND Gate", {"inputs": "2"})) == 3
    assert len(ports_of("OR Gate", {"inputs": "4"})) == 5
    assert len(ports_of("NAND Gate", {"inputs": "8"})) == 9
    # ...and a 3-input XOR is not placed even though 3-input AND is: its
    # semantics in Evolution are not the textbook's.
    with pytest.raises(UnmeasuredGeometryError):
        ports_of("XOR Gate", {"inputs": "3"})


def test_unknown_component_raises():
    with pytest.raises(UnmeasuredGeometryError) as excinfo:
        ports_of("Buffer", {})
    assert "Buffer" in str(excinfo.value)


def test_non_default_size_or_facing_raises():
    # Every gate measured is default size, facing east.
    with pytest.raises(UnmeasuredGeometryError) as excinfo:
        ports_of("AND Gate", {"inputs": "2", "size": "30"})
    assert "size" in str(excinfo.value)

    with pytest.raises(UnmeasuredGeometryError) as excinfo:
        ports_of("AND Gate", {"inputs": "2", "facing": "north"})
    assert "facing" in str(excinfo.value)


def test_pin_facing_east_and_west_are_measured_but_north_is_not():
    # Input pins face east, output pins face west; both were measured, and
    # both put the single port at loc.
    assert port_positions("Pin", (130, 170)) == {"pin": (130, 170)}
    assert port_positions("Pin", (670, 190), {"facing": "west"}) == {"pin": (670, 190)}
    with pytest.raises(UnmeasuredGeometryError):
        ports_of("Pin", {"facing": "north"})


def test_port_positions_places_ports_absolutely():
    # AND gate at (620,280) in exp8_gates.circ.
    assert port_positions("AND Gate", (620, 280), {"inputs": "2"}) == {
        "in0": (570, 260), "in1": (570, 300), "out": (620, 280),
    }


#: Components whose `loc` is NOT one of their ports.
#:
#: "loc is itself a port" held for every 2.7.1 primitive measured and was
#: written down as if it were a property of the format. It is not: it is a
#: property of small gates. Logisim Evolution's TTL parts are drawn as DIP
#: packages whose loc is the corner of the body, with every pin along the two
#: long sides and nothing at the anchor at all.
#:
#: Listed rather than dropped, because the invariant is still true of
#: everything else and is worth keeping true. An entry here is a claim that
#: the exception was checked, not that the rule was inconvenient.
#:
#: The seven-segment display is deliberately NOT here, and finding that out
#: cost one failing test: it is an Evolution part too, but its loc IS a port
#: (segment g). "Evolution parts are different" was the wrong
#: generalisation -- DIP packages are.
NO_PORT_AT_LOC = {("7447", None)}


def test_there_is_always_a_port_at_loc_EXCEPT_for_the_DIP_packages():
    # For gates loc is the single output. Adder and Priority Encoder each
    # carry a SECOND output beside it, which is why nothing may assume
    # "output last".
    for key, ports in PORTS.items():
        offsets = [p.offset for p in ports]
        if key in NO_PORT_AT_LOC:
            assert (0, 0) not in offsets, (
                f"{key} is listed as having no port at loc, but has one now. "
                f"Either the measurement changed or the list is stale.")
            continue
        assert (0, 0) in offsets, key

    outputs = lambda key: [p.name for p in PORTS[key] if p.kind == "out"]
    assert outputs(("AND Gate", "2")) == ["out"]
    assert outputs(("Adder", "1")) == ["cout", "sum"]
    assert outputs(("Priority Encoder", "2")) == ["out", "gs"]


# ------------------------------------------- library index resolution

def test_lib_indices_resolve_per_file():
    """The same number means different libraries in different files."""
    original = resolve_lib_indices(
        (FIXTURES / "priority_plexers.circ").read_text(encoding="utf-8"))
    shuffled = resolve_lib_indices(
        (FIXTURES / "shuffled_libs.circ").read_text(encoding="utf-8"))

    assert original["2"] == "#Plexers"
    assert shuffled["2"] == "#Memory"        # a hardcoded lib="2" looks here
    assert shuffled["1"] == "#Plexers"       # ...and misses this
    assert original != shuffled


@pytest.mark.parametrize("name", ["priority_plexers.circ", "shuffled_libs.circ"])
def test_primitives_only_catches_the_plexer_through_resolution(name):
    """The enforcement that must not be written against one file's indices.

    shuffled_libs.circ is priority_plexers.circ with every <lib> number
    permuted. A check matching lib="2" passes on one and fails on the other;
    resolving through the <lib> block catches both.
    """
    import re
    text = (FIXTURES / name).read_text(encoding="utf-8")
    libs = resolve_lib_indices(text)

    offenders = [
        (libs[lib], comp)
        for lib, comp in re.findall(r'<comp lib="(\d+)"[^>]*name="([^"]+)"', text)
        if libs[lib] not in PRIMITIVE_LIBS
    ]
    assert offenders == [("#Plexers", "Priority Encoder")]


def test_the_gate_level_answer_passes_primitives_only():
    import re
    text = (FIXTURES / "exp8_gates.circ").read_text(encoding="utf-8")
    libs = resolve_lib_indices(text)
    used = {libs[lib] for lib in re.findall(r'<comp lib="(\d+)"', text)}
    assert used <= PRIMITIVE_LIBS


def test_shuffled_fixture_is_a_pure_permutation_of_the_original():
    """Guard the fixture itself: it must differ ONLY in library numbering."""
    import re
    original = (FIXTURES / "priority_plexers.circ").read_text(encoding="utf-8")
    shuffled = (FIXTURES / "shuffled_libs.circ").read_text(encoding="utf-8")

    def structural(text):
        # blank every library number, on references AND on declarations, so
        # only a non-numbering difference can survive
        text = re.sub(r'\blib="\d+"', 'lib="?"', text)
        text = re.sub(r'(<lib desc="[^"]+" name=")\d+(")', r'\1?\2', text)
        return sorted(line.strip() for line in text.splitlines()
                      if line.strip().startswith(("<wire", "<comp", "<a ", "<lib")))

    assert structural(original) == structural(shuffled)
    assert set(resolve_lib_indices(original).values()) == \
        set(resolve_lib_indices(shuffled).values())
