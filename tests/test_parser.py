"""Tests for ohmwork.parser: .asc text -> recovered circuit description.

The parser is the verification half of the round trip. It must rebuild
connectivity purely from geometry (SYMBOL anchors + rotations, WIRE
segments, FLAG coordinates) and hard-fail on anything unaccounted for.

Remember the limit documented in CLAUDE.md: because parser and emitter
share symbols.py, a round trip cannot catch a wrong pin offset. What it
does catch is emitter placement bugs, dropped flags, shorted nets, and
LLM-vs-file topology mismatches.
"""

from pathlib import Path

import pytest

from ohmwork.emitter import emit
from ohmwork.parser import ASC_ENCODING, ParseError, parse_asc, parse_asc_file

from tests.test_emitter import reference_circuit


def crlf(lines):
    return "\r\n".join(lines) + "\r\n"


# --------------------------------------------------------------- round trip


def normalized_nets(nets):
    return {net: sorted(pins) for net, pins in nets.items()}


def test_round_trip_recovers_reference_circuit():
    original = reference_circuit()
    recovered = parse_asc(emit(original))

    assert recovered["components"] == original["components"]
    assert normalized_nets(recovered["nets"]) == normalized_nets(
        original["nets"]
    )
    assert recovered["directives"] == original["directives"]


def test_round_trip_of_transistorless_circuit():
    circuit = {
        "components": [
            {"ref": "V1", "type": "voltage", "value": "5"},
            {"ref": "R1", "type": "res", "value": "10k"},
            {"ref": "C1", "type": "cap", "value": "100n"},
        ],
        "nets": {
            "in": ["V1.+", "R1.a"],
            "out": ["R1.b", "C1.a"],
            "0": ["V1.-", "C1.b"],
        },
        "directives": [".tran 0 10m 0"],
    }
    recovered = parse_asc(emit(circuit))
    assert recovered["components"] == circuit["components"]
    assert normalized_nets(recovered["nets"]) == normalized_nets(
        circuit["nets"]
    )


# ------------------------------------------------------- hand-built inputs
# These exercise geometry the emitter never produces, because the parser
# must also read files a human drew or edited in LTspice.


def test_flag_directly_on_pin_no_wire():
    # res at (0,0) R0: pins at (16,16) and (16,96). Flags sit right on
    # the pins with no stub wires at all.
    text = crlf([
        "Version 4.1",
        "SHEET 1 880 680",
        "SYMBOL res 0 0 R0",
        "SYMATTR InstName R1",
        "SYMATTR Value 1k",
        "SYMBOL voltage 224 0 R0",
        "SYMATTR InstName V1",
        "SYMATTR Value 5",
        "FLAG 16 16 a",
        "FLAG 16 96 0",
        "FLAG 224 16 a",
        "FLAG 224 96 0",
    ])
    recovered = parse_asc(text)
    assert normalized_nets(recovered["nets"]) == {
        "a": ["R1.a", "V1.+"],
        "0": ["R1.b", "V1.-"],
    }


def test_rotated_symbol_pins_recovered():
    # The verified real-file case: npn at (144,208) R270 has
    # C=(144,144), B=(192,208), E=(240,144).
    text = crlf([
        "Version 4.1",
        "SHEET 1 880 680",
        "SYMBOL npn 144 208 R270",
        "SYMATTR InstName Q1",
        "SYMBOL res 400 0 R0",
        "SYMATTR InstName R1",
        "SYMATTR Value 1k",
        "SYMBOL res 600 0 R0",
        "SYMATTR InstName R2",
        "SYMATTR Value 1k",
        "FLAG 144 144 c",
        "FLAG 192 208 b",
        "FLAG 240 144 0",
        "FLAG 416 16 c",
        "FLAG 416 96 b",
        "FLAG 616 16 b",
        "FLAG 616 96 0",
    ])
    recovered = parse_asc(text)
    nets = normalized_nets(recovered["nets"])
    assert nets["c"] == ["Q1.C", "R1.a"]
    assert nets["b"] == ["Q1.B", "R1.b", "R2.a"]
    assert nets["0"] == ["Q1.E", "R2.b"]


def test_wire_chain_carries_net_to_pin():
    # Flag at the far end of a two-segment wire chain still names the pin.
    text = crlf([
        "Version 4.1",
        "SHEET 1 880 680",
        "SYMBOL res 0 0 R0",
        "SYMATTR InstName R1",
        "SYMATTR Value 1k",
        "WIRE 16 16 16 -32",
        "WIRE 16 -32 128 -32",
        "FLAG 128 -32 top",
        "WIRE 16 96 16 128",
        "FLAG 16 128 0",
        "SYMBOL res 224 0 R0",
        "SYMATTR InstName R2",
        "SYMATTR Value 1k",
        "FLAG 240 16 top",
        "FLAG 240 96 0",
    ])
    recovered = parse_asc(text)
    assert normalized_nets(recovered["nets"]) == {
        "top": ["R1.a", "R2.a"],
        "0": ["R1.b", "R2.b"],
    }


def test_t_junction_connects():
    # A wire endpoint landing mid-span of another wire is a T junction
    # and must join that wire's net, as LTspice treats it.
    text = crlf([
        "Version 4.1",
        "SHEET 1 880 680",
        "SYMBOL res 0 0 R0",
        "SYMATTR InstName R1",
        "SYMATTR Value 1k",
        "SYMBOL res 224 0 R0",
        "SYMATTR InstName R2",
        "SYMATTR Value 1k",
        # Horizontal bus above both resistors.
        "WIRE 0 -32 320 -32",
        # Each pin stubs up onto the middle of the bus.
        "WIRE 16 16 16 -32",
        "WIRE 240 16 240 -32",
        "FLAG 320 -32 bus",
        "FLAG 16 96 0",
        "FLAG 240 96 0",
    ])
    recovered = parse_asc(text)
    assert normalized_nets(recovered["nets"])["bus"] == ["R1.a", "R2.a"]


# ---------------------------------------------------------------- failures


def test_missing_flag_hard_fails_naming_the_pin():
    text = emit(reference_circuit())
    # Delete the flag on Q1's base (the vb flag whose stub points left).
    lines = [l for l in text.split("\r\n") if l != "FLAG 768 208 vb"]
    assert len(lines) == len(text.split("\r\n")) - 1, "test setup broke"
    with pytest.raises(ParseError, match=r"Q1\.B"):
        parse_asc(crlf(lines).rstrip("\r\n") + "\r\n")


def test_two_labels_on_one_net_hard_fails():
    text = crlf([
        "Version 4.1",
        "SHEET 1 880 680",
        "SYMBOL res 0 0 R0",
        "SYMATTR InstName R1",
        "SYMATTR Value 1k",
        "WIRE 16 16 16 -32",
        "FLAG 16 16 neta",
        "FLAG 16 -32 netb",
        "FLAG 16 96 0",
    ])
    with pytest.raises(ParseError, match="neta|netb"):
        parse_asc(text)


def test_unknown_symbol_hard_fails():
    text = crlf([
        "Version 4.1",
        "SHEET 1 880 680",
        "SYMBOL opamp 0 0 R0",
        "SYMATTR InstName U1",
    ])
    with pytest.raises(ParseError, match="opamp"):
        parse_asc(text)


def test_mirrored_symbol_hard_fails():
    # M0 mirror placements exist in real files but are not yet verified,
    # so the parser must refuse rather than guess.
    text = crlf([
        "Version 4.1",
        "SHEET 1 880 680",
        "SYMBOL res 0 0 M0",
        "SYMATTR InstName R1",
        "FLAG 16 16 a",
        "FLAG 16 96 0",
    ])
    with pytest.raises(ParseError, match="M0"):
        parse_asc(text)


# --------------------------------------------------------------- directives


def test_directives_and_comments_separated():
    text = crlf([
        "Version 4.1",
        "SHEET 1 880 680",
        "SYMBOL res 0 0 R0",
        "SYMATTR InstName R1",
        "SYMATTR Value 1k",
        "FLAG 16 16 a",
        "FLAG 16 96 0",
        "SYMBOL cap 224 0 R0",
        "SYMATTR InstName C1",
        "SYMATTR Value 1u",
        "FLAG 240 0 a",
        "FLAG 240 64 0",
        "TEXT 0 200 Left 2 !.op",
        "TEXT 0 232 Left 2 ;just a comment",
    ])
    recovered = parse_asc(text)
    assert recovered["directives"] == [".op"]


# --------------------------------------------------- file encoding

# LTspice does not write UTF-8. A micro sign is the single byte 0xB5, so a
# real .asc containing 470u or 100u is not UTF-8-decodable, and the ascii
# read this parser used originally raised UnicodeDecodeError before it looked
# at any geometry. Q3's filter is 470uF, and check-mine mode reads student
# files, so this was a break with a known trigger rather than a hypothetical.

HANDDRAWN = Path(__file__).parent / "fixtures" / "ltspice"


def test_real_file_with_a_micro_sign_is_not_utf8():
    # Confirms the fixture still demonstrates the problem it was kept for.
    raw = (HANDDRAWN / "handdrawn_voltage_multiplier.asc").read_bytes()
    assert b"\xb5" in raw
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    with pytest.raises(UnicodeDecodeError):
        raw.decode("ascii")


def test_parser_reads_a_micro_sign_file_and_fails_on_geometry_instead():
    """The regression: reach the geometry check, do not die on a byte.

    This hand-drawn file legitimately fails to parse -- it routes with wires
    and puts no flag on every pin, which v1 requires. The point is WHICH
    error comes out. An encoding error would say nothing useful about the
    schematic; a ParseError names the pin.
    """
    path = HANDDRAWN / "handdrawn_voltage_multiplier.asc"
    with pytest.raises(ParseError) as excinfo:
        parse_asc_file(str(path))
    assert "no net label" in str(excinfo.value)


def test_micro_sign_survives_into_a_recovered_value():
    # Decoding must preserve the character, not replace or drop it: the value
    # is what the whole extraction pipeline is trying to protect.
    text = (HANDDRAWN / "handdrawn_voltage_multiplier.asc").read_bytes().decode(
        ASC_ENCODING)
    assert "SYMATTR Value 100\u00b5" in text


def test_generated_files_stay_pure_ascii():
    # The emitter writes ascii and nothing above should have relaxed that.
    # Values we generate use "100u", never the micro sign.
    text = emit(reference_circuit())
    text.encode("ascii")
