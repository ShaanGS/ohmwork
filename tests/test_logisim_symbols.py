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
    assert module == MEASURED


# ------------------------------------------------- refusing to guess

def test_unmeasured_input_count_raises():
    # 3-input AND appears in no real file we have. Interpolating between the
    # 2-input and 4-input layouts would be inventing geometry.
    with pytest.raises(UnmeasuredGeometryError) as excinfo:
        ports_of("AND Gate", {"inputs": "3"})
    message = str(excinfo.value)
    assert "AND Gate" in message and "'3'" in message
    assert "['2']" in message           # names what HAS been measured
    assert "real" in message and "required" in message


def test_measured_input_counts_still_work():
    assert len(ports_of("AND Gate", {"inputs": "2"})) == 3
    assert len(ports_of("OR Gate", {"inputs": "4"})) == 5
    # ...and 4-input AND is not measured even though 4-input OR is
    with pytest.raises(UnmeasuredGeometryError):
        ports_of("AND Gate", {"inputs": "4"})


def test_unknown_component_raises():
    with pytest.raises(UnmeasuredGeometryError) as excinfo:
        ports_of("NAND Gate", {"inputs": "2"})
    assert "NAND Gate" in str(excinfo.value)


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


def test_there_is_always_a_port_at_loc():
    # Measured across every component: loc is itself a port. For gates it is
    # the single output. Adder and Priority Encoder each carry a SECOND
    # output beside it, which is why nothing may assume "output last".
    for (name, disc), ports in PORTS.items():
        assert (0, 0) in [p.offset for p in ports], (name, disc)

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
