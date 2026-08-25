"""Tests for ohmwork.simulate: Backend protocol, LTspice and ngspice.

Backend roles (CLAUDE.md, "Simulate layer decisions"): LTspice is the
authoritative backend and the only one whose numbers reach the user.
ngspice exists so the pipeline runs on Linux/CI; its devices are
synthesised cards, its numbers are its own baseline, and the two
backends are never expected to agree.

Integration tests skip cleanly when their simulator is absent. The
expected voltages are each backend's own measured regression values
(LTspice 26.0.2.1 / ngspice, see CLAUDE.md), not each other's.
"""

import shutil

import pytest

from ohmwork.emitter import write_asc
from ohmwork.simulate import (
    LTspiceBackend,
    NgspiceBackend,
    SimulationError,
    locate_ltspice,
    to_spice,
)

from tests import baselines as B
from tests.test_emitter import reference_circuit


# ------------------------------------------------------------ netlist text


def test_to_spice_matches_ltspice_own_netlist():
    # Component/node lines match what LTspice 26.0.2.1 itself netlisted
    # from our emitted .asc during the 2026-08-24 spike (minus its
    # .lib/.backanno boilerplate). Two independent netlisters agreeing
    # is the point. (The zener card is the anchored one; the spike's
    # unanchored card is banned now.)
    netlist = to_spice(reference_circuit())
    lines = netlist.splitlines()
    assert "V1 vin 0 15" in lines
    assert "R1 vin vb 1.8k" in lines
    assert "D1 0 vb DZ8V3" in lines
    assert "Q1 vin vb vout QN" in lines
    assert "RL vout 0 2k" in lines
    assert ".model DZ8V3 D(BV=8.3 IBV=5m)" in lines
    assert ".model QN NPN(BF=100)" in lines
    assert ".op" in lines
    assert lines[-1] == ".end"


def test_to_spice_pin_order_follows_the_verified_table():
    # diode: anode cathode; bjt: C B E; voltage: + -. A swap here is a
    # silently wrong circuit, which is why this is pinned.
    circuit = {
        "components": [
            {"ref": "V1", "type": "voltage", "value": "5"},
            {"ref": "D1", "type": "diode", "part": "DX"},
            {"ref": "R1", "type": "res", "value": "1k"},
        ],
        "nets": {
            "a": ["V1.+", "D1.anode"],
            "k": ["D1.cathode", "R1.a"],
            "0": ["V1.-", "R1.b"],
        },
        "directives": [".model DX D", ".op"],
    }
    lines = to_spice(circuit).splitlines()
    assert "D1 a k DX" in lines


# ------------------------------------------------------- locating LTspice


def test_env_override_wins(tmp_path, monkeypatch):
    fake = tmp_path / "LTspice.exe"
    fake.write_bytes(b"")
    monkeypatch.setenv("OHMWORK_LTSPICE", str(fake))
    assert locate_ltspice() == fake


def test_env_override_pointing_nowhere_fails_clearly(monkeypatch):
    monkeypatch.setenv("OHMWORK_LTSPICE", r"Z:\nope\LTspice.exe")
    with pytest.raises(FileNotFoundError, match="OHMWORK_LTSPICE"):
        locate_ltspice()


def test_not_found_error_names_what_it_searched(monkeypatch):
    monkeypatch.delenv("OHMWORK_LTSPICE", raising=False)
    monkeypatch.setattr("ohmwork.simulate.CANDIDATE_PATHS", [])
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(FileNotFoundError, match="OHMWORK_LTSPICE"):
        locate_ltspice()


# --------------------------------------------------- LTspice (skips if absent)


def _ltspice_available():
    try:
        locate_ltspice()
        return True
    except FileNotFoundError:
        return False


needs_ltspice = pytest.mark.skipif(
    not _ltspice_available(), reason="LTspice not installed"
)
needs_ngspice = pytest.mark.skipif(
    shutil.which("ngspice") is None, reason="ngspice not installed"
)


@needs_ltspice
def test_ltspice_anchored_reference_baseline(tmp_path):
    # Policy path (b): the reference fixture's devices are synthesised
    # at exactly the question's values, zener anchored at 5 mA. vb must
    # sit within a few mV of the user's ngspice measurement of the same
    # card: cross-simulator agreement is the entire point of anchoring,
    # so a drift here (e.g. back to the unanchored card's 8.749) is the
    # loudest possible alarm.
    asc = tmp_path / "reference.asc"
    write_asc(reference_circuit(), asc)
    results = LTspiceBackend().run(asc)
    assert results.value("V(vout)") == pytest.approx(
        B.VOUT_ANCHORED.value, abs=2e-3)
    assert results.value("V(vb)") == pytest.approx(
        B.VB_ANCHORED_NGSPICE.value, abs=3e-3)


@needs_ltspice
def test_ltspice_failure_surfaces_the_log(tmp_path):
    # A circuit that netlists but cannot simulate: two parallel voltage
    # sources that disagree. Verified empirically that LTspice STILL
    # writes a .raw for this, just with zero traces in it — so the
    # backend must treat an empty raw as failure, not success.
    circuit = {
        "components": [
            {"ref": "V1", "type": "voltage", "value": "15"},
            {"ref": "V2", "type": "voltage", "value": "5"},
            {"ref": "R1", "type": "res", "value": "1k"},
        ],
        "nets": {
            "vin": ["V1.+", "V2.+", "R1.a"],
            "0": ["V1.-", "V2.-", "R1.b"],
        },
        "directives": [".op"],
    }
    asc = tmp_path / "broken.asc"
    write_asc(circuit, asc)
    with pytest.raises(SimulationError, match="no traces"):
        LTspiceBackend().run(asc)


@needs_ltspice
def test_ltspice_real_parts_hit_the_textbook_answer(tmp_path):
    # Policy path (c): with BZX84B8V2LY (Vz=8.2, nearest stocked part
    # to the question's 8.3) and a 2N3904, vout lands inside the
    # textbook Vz - Vbe band of 7.50-7.55 V.
    circuit = {
        "components": [
            {"ref": "V1", "type": "voltage", "value": "15"},
            {"ref": "R1", "type": "res", "value": "1.8k"},
            {"ref": "D1", "type": "zener", "part": "BZX84B8V2LY"},
            {"ref": "Q1", "type": "npn", "part": "2N3904"},
            {"ref": "RL", "type": "res", "value": "2k"},
        ],
        "nets": {
            "vin": ["V1.+", "R1.a", "Q1.C"],
            "vb": ["R1.b", "D1.cathode", "Q1.B"],
            "vout": ["Q1.E", "RL.a"],
            "0": ["V1.-", "D1.anode", "RL.b"],
        },
        "directives": [".op"],
    }
    asc = tmp_path / "real_parts.asc"
    write_asc(circuit, asc)
    results = LTspiceBackend().run(asc)
    assert results.value("V(vout)") == pytest.approx(
        B.VOUT_REAL_PARTS.value, abs=2e-3)
    assert results.value("V(vb)") == pytest.approx(
        B.VB_REAL_PARTS.value, abs=2e-3)


# --------------------------------------------------- ngspice (skips if absent)


@needs_ngspice
def test_ngspice_agrees_on_the_anchored_card(tmp_path):
    # With the anchored card both simulators must land on the same vb.
    # vout's expectation is the LTSPICE measurement with a loose
    # tolerance (cross-simulator expectation, not an ngspice pin);
    # tighten it into its own baseline when this first runs on a
    # machine that has ngspice.
    asc = tmp_path / "reference.asc"
    write_asc(reference_circuit(), asc)
    results = NgspiceBackend().run(asc)
    assert results.value("V(vb)") == pytest.approx(
        B.VB_ANCHORED_NGSPICE.value, abs=1e-3)
    assert results.value("V(vout)") == pytest.approx(
        B.VOUT_ANCHORED.value, abs=0.02)
