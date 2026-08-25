"""Registry of measured baselines. THE rule: never pin a plausible
number. Measure or delete.

Every simulation-derived expected value in the suite lives here as a
Baseline carrying its provenance: which backend and version produced
it, when, and via what fixture/command. A bare float as an expected
simulation value is banned — it nearly happened once (estimated
line/load regulation values typed straight into a test as pins), and
unlike a wrong device model, a wrong pin corrupts the reference itself,
after which every downstream check agrees with the error.

Scope: simulation-derived numbers. The pin-geometry expectations in
test_symbols.py are measurements too, but of a different kind — their
provenance (which real hand-drawn .asc, which placement) is recorded in
CLAUDE.md's "Verified format facts" and in their tests' comments.
Library facts asserted in test_parts.py (Vpk values etc.) come verbatim
from fixture files extracted from the real install; the fixture file is
their provenance.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Baseline:
    value: float
    unit: str
    source: str    # backend-version (or who ran it, if not this suite)
    measured: str  # ISO date
    via: str       # fixture/command that generated it


_LTSPICE = "ltspice-26.0.2.1"
_Q1_RUN = (
    "analysis.execute(tests.test_emitter.reference_circuit(), "
    "tests.test_analysis.q1_analysis()) via F:\\LTspice.exe -b -ascii"
)

# --------------------------- anchored reference regulator (policy path b)
# Devices synthesised at exactly the question's values:
# .model DZ8V3 D(BV=8.3 IBV=5m), .model QN NPN(BF=100).

VB_ANCHORED_NGSPICE = Baseline(
    8.292262, "V", "ngspice (user-run; version unreported)", "2026-08-24",
    "project owner's ngspice experiment with D(BV=8.3 IBV=5m), "
    "reported in review. The cross-simulator anchor: LTspice must land "
    "within a few mV of this or the device model is wrong.",
)
VOUT_ANCHORED = Baseline(
    7.48403, "V", _LTSPICE, "2026-08-24", _Q1_RUN + " (nominal op)")
VB_ANCHORED = Baseline(
    8.292139, "V", _LTSPICE, "2026-08-24", _Q1_RUN + " (nominal op)")
IZ_ANCHORED = Baseline(
    -3.68954e-3, "A", _LTSPICE, "2026-08-24", _Q1_RUN + " (nominal op)")
LINE_REG_ANCHORED = Baseline(
    0.399224, "%", _LTSPICE, "2026-08-24",
    _Q1_RUN + " (linesweep 12->20 V, normalised to V1=12)")
LOAD_REG_ANCHORED = Baseline(
    1.847626, "%", _LTSPICE, "2026-08-24",
    _Q1_RUN + " (loadsweep 100k->500, normalised to full load)")

# The load-regulation MECHANISM, which is the actual learning content of Q1:
# across the full load range vb barely moves while vout drops a hundred times
# further, so essentially all of the regulation loss is Vbe rising with
# emitter current, not the zener sagging. Measured rather than recalled: an
# earlier version of this observation came from an ngspice run three rounds
# and one device policy ago, and is not evidence about what this build does.
_Q1_VB_SWEEP = (
    "examples/q1_anchored.json with two extra guard measurements, V(vb) at "
    r"RL=100k and RL=500 of the loadsweep run, via F:\LTspice.exe -b -ascii"
)
VB_NOLOAD = Baseline(8.292391, "V", _LTSPICE, "2026-08-25", _Q1_VB_SWEEP)
VB_FULLLOAD = Baseline(8.291356, "V", _LTSPICE, "2026-08-25", _Q1_VB_SWEEP)
VOUT_NOLOAD = Baseline(7.585122, "V", _LTSPICE, "2026-08-25", _Q1_VB_SWEEP)
VOUT_FULLLOAD = Baseline(7.447520, "V", _LTSPICE, "2026-08-25", _Q1_VB_SWEEP)

# ------------------------------------ real-parts variant (policy path c)
# BZX84B8V2LY + 2N3904 from the bundled libraries, no model cards.

# ------------------------------- Q3 bridge + C-L-C + zener (examples/q3.json)
# Full design question: 1N4007 bridge, 470u/1m/470u pi filter, RS=220
# (designed), anchored DZ6V2, .tran 0 200m 100m 100u (post-settle window).

_Q3_RUN = ("load_question(examples/q3.json) -> analysis.execute via "
           "F:\\LTspice.exe -b -ascii, tran waveforms run")

Q3_VIN_RMS = Baseline(
    12.000141, "V", _LTSPICE, "2026-08-24",
    _Q3_RUN + " (V(ac1)-V(ac2) rms; recovers the question's 12 V RMS "
    "and closes the loop on the sine rms->peak conversion)")
Q3_VOUT_MEAN = Baseline(
    6.249993, "V", _LTSPICE, "2026-08-24", _Q3_RUN + " (V(vout) mean)")
Q3_VOUT_RIPPLE_PP = Baseline(
    0.00398779, "V", _LTSPICE, "2026-08-24",
    _Q3_RUN + " (V(vout) ripple pk-pk)")
Q3_VFILT_MEAN = Baseline(
    15.2289, "V", _LTSPICE, "2026-08-24", _Q3_RUN + " (V(vfilt) mean)")

VOUT_REAL_PARTS = Baseline(
    7.5059, "V", _LTSPICE, "2026-08-24",
    "reference topology with D1=BZX84B8V2LY, Q1=2N3904, .op run "
    "via F:\\LTspice.exe -b -ascii")
VB_REAL_PARTS = Baseline(
    8.1943, "V", _LTSPICE, "2026-08-24",
    "reference topology with D1=BZX84B8V2LY, Q1=2N3904, .op run "
    "via F:\\LTspice.exe -b -ascii")


# --------------------------------------------- Q2 priority encoder (digital)
# A truth table is a measured baseline like any other, but it is not a
# float, so it gets its own record rather than being flattened into one.


@dataclass(frozen=True)
class TableBaseline:
    columns: tuple[str, ...]
    rows: tuple[tuple[int, ...], ...]
    source: str
    measured: str
    via: str


_LOGISIM = "logisim-evolution-4.1.0"

Q2_TRUTH_TABLE = TableBaseline(
    columns=("EN", "I3", "I2", "I1", "I0", "Y1", "Y0", "V"),
    rows=(
        (0, 0, 0, 0, 0, 0, 0, 0), (0, 0, 0, 0, 1, 0, 0, 0),
        (0, 0, 0, 1, 0, 0, 0, 0), (0, 0, 0, 1, 1, 0, 0, 0),
        (0, 0, 1, 0, 0, 0, 0, 0), (0, 0, 1, 0, 1, 0, 0, 0),
        (0, 0, 1, 1, 0, 0, 0, 0), (0, 0, 1, 1, 1, 0, 0, 0),
        (0, 1, 0, 0, 0, 0, 0, 0), (0, 1, 0, 0, 1, 0, 0, 0),
        (0, 1, 0, 1, 0, 0, 0, 0), (0, 1, 0, 1, 1, 0, 0, 0),
        (0, 1, 1, 0, 0, 0, 0, 0), (0, 1, 1, 0, 1, 0, 0, 0),
        (0, 1, 1, 1, 0, 0, 0, 0), (0, 1, 1, 1, 1, 0, 0, 0),
        (1, 0, 0, 0, 0, 0, 0, 0), (1, 0, 0, 0, 1, 0, 0, 1),
        (1, 0, 0, 1, 0, 0, 1, 1), (1, 0, 0, 1, 1, 0, 1, 1),
        (1, 0, 1, 0, 0, 1, 0, 1), (1, 0, 1, 0, 1, 1, 0, 1),
        (1, 0, 1, 1, 0, 1, 0, 1), (1, 0, 1, 1, 1, 1, 0, 1),
        (1, 1, 0, 0, 0, 1, 1, 1), (1, 1, 0, 0, 1, 1, 1, 1),
        (1, 1, 0, 1, 0, 1, 1, 1), (1, 1, 0, 1, 1, 1, 1, 1),
        (1, 1, 1, 0, 0, 1, 1, 1), (1, 1, 1, 0, 1, 1, 1, 1),
        (1, 1, 1, 1, 0, 1, 1, 1), (1, 1, 1, 1, 1, 1, 1, 1),
    ),
    source=_LOGISIM,
    measured="2026-08-24",
    via=("analysis.execute(load_question(examples/q2.json)) -> "
         "logisim-evolution --tty table on the emitted .circ. Independently "
         "reproduced by the same command on tests/fixtures/logisim/"
         "exp8_gates.circ, a student's hand-drawn encoder, and checked "
         "against a spec oracle written from the question's own wording "
         "(see test_digital_execution.spec_oracle)"),
)
