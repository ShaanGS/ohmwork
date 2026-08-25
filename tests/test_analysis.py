"""Executable spec for ohmwork.analysis: the experiment plan.

The circuit schema describes a circuit; a lab question describes an
EXPERIMENT — several runs of different types plus quantities derived
across them, with regime assertions guarding physical validity and
formula transparency in the rendered output.

## Schema

An `analysis` block beside `components`/`nets` in the question JSON:

  runs: what to simulate. Unique `id`, a `type`, optional `label`
  (used in the deliverable's comments and reports):
    op          - single operating point
    dc          - native .dc sweep of a source:
                  {"sweep": {"source": "V1", "start": 12, "stop": 20,
                             "step": 1}}
    param_sweep - N values of one component:
                  {"component": "RL", "values": ["100k", ..., "500"]}

  measurements: three kinds:
    simulated - {"name", "run", "expr"}; sweep runs need "at"
                ({"at": {"V1": 12}} / {"at": {"RL": "500"}}); a sweep
                expr without "at" is an error.
    derived   - {"name", "kind": "derived", "formula"} over earlier
                measurement names, safely evaluated (no eval()), plus
                an optional "definition" string rendered in reports so
                a student can reconcile against their textbook's
                convention.
    regime    - {"kind": "regime", "run", "assert", "device"}:
                physical-validity assertions. Convergence is not
                correctness; a sweep can leave the regulating regime
                and still converge. Implemented asserts:
                  zener_in_breakdown - reverse current at least
                    min_reverse_current (default 100 uA) at every
                    point; optional "vz" also checks the terminal
                    voltage within 20% of nominal.
                  bjt_active - Vce > 0.2 V and Ib > 0 at every point.
                A violated regime does NOT discard numbers: every
                measurement touching that run (transitively through
                derived formulas) is marked unreliable with the reason
                attached. Rationale: dropout data is pedagogically
                interesting and the student should see it flagged, not
                hidden; other runs' results stay valid either way.

## Deliverable vs scratch files

The student receives ONE .asc containing the whole experiment: the
first run's directive active, every other run's directive present as a
commented line to uncomment (deliverable_circuit). Verified empirically
(2026-08-24, 26.0.2.1): .step overrides an active .param, so the
swept component can keep a .param default; but TWO active analysis
directives (.op + .dc) HANG batch mode, so the comments must say
"comment out .op first" and generated files must never contain two
active analyses. The runner's per-run files ("<run id>.asc" in a
scratch dir) are an implementation detail, not the deliverable.

## Other pinned contracts

  - `at` selection: the plan declares WHICH point ({"RL": "500"}), but
    its INDEX comes from the axis trace the raw file itself records
    (the swept source's trace for dc, "<component>step" for
    param_sweep) — the file's own account of what ran, matched after
    SPICE-suffix parsing ("100k" -> 1e5). This is not optional
    robustness: verified empirically (26.0.2.1) that LTspice runs
    .step LIST values in ascending numeric order NO MATTER how the
    list is written, so plan-order indexing silently flips sweeps.
    Missing axis trace, unmatched value, or wrong-length trace =
    hard error.
  - circuit["directives"] carries device/model cards only; analysis
    directives are generated from runs.
  - provenance on every result: name, value, run id (None for
    derived), backend name, source, formula, at, reliability.
"""

import pytest

analysis = pytest.importorskip(
    "ohmwork.analysis", reason="analysis runner not implemented yet (by design)"
)

from ohmwork.emitter import emit  # noqa: E402
from ohmwork.parser import parse_asc  # noqa: E402
from ohmwork.simulate import Results  # noqa: E402

from tests.test_emitter import reference_circuit  # noqa: E402


def q1_analysis():
    """The full experiment for the reference question (Exp 2)."""
    return {
        "runs": [
            {"id": "nominal", "type": "op"},
            {"id": "linesweep", "type": "dc", "label": "line regulation",
             "sweep": {"source": "V1", "start": 12, "stop": 20, "step": 1}},
            {"id": "loadsweep", "type": "param_sweep",
             "label": "load regulation",
             "component": "RL",
             "values": ["100k", "5k", "2k", "1k", "500"]},
        ],
        "measurements": [
            {"name": "vout_nominal", "run": "nominal", "expr": "V(vout)"},
            {"name": "iz_nominal", "run": "nominal", "expr": "I(D1)"},
            {"name": "vout_low", "run": "linesweep", "expr": "V(vout)",
             "at": {"V1": 12}},
            {"name": "vout_high", "run": "linesweep", "expr": "V(vout)",
             "at": {"V1": 20}},
            {"name": "line_reg_pct", "kind": "derived",
             "formula": "100 * (vout_high - vout_low) / vout_low",
             "definition": "12 V to 20 V input, normalised to the 12 V output"},
            {"name": "vout_noload", "run": "loadsweep", "expr": "V(vout)",
             "at": {"RL": "100k"}},
            {"name": "vout_fullload", "run": "loadsweep", "expr": "V(vout)",
             "at": {"RL": "500"}},
            {"name": "load_reg_pct", "kind": "derived",
             "formula": "100 * (vout_noload - vout_fullload) / vout_fullload",
             "definition": "no-load to full-load, normalised to full-load"},
            # Cross-baseline guard: with the anchored zener, vb at the
            # operating point must sit within a few mV of 8.292 V on ANY
            # compliant backend. Measuring it in every experiment makes
            # a silently-wrong device model visible in the results.
            {"name": "vb_nominal", "run": "nominal", "expr": "V(vb)"},
            {"kind": "regime", "run": "loadsweep",
             "assert": "zener_in_breakdown", "device": "D1", "vz": 8.3},
            {"kind": "regime", "run": "loadsweep",
             "assert": "bjt_active", "device": "Q1"},
        ],
    }


# --------------------------------------------------------------- validation


def test_valid_plan_passes():
    analysis.validate_plan(reference_circuit(), q1_analysis())


def test_duplicate_run_ids_rejected():
    plan = q1_analysis()
    plan["runs"][1]["id"] = "nominal"
    with pytest.raises(analysis.AnalysisError, match="nominal"):
        analysis.validate_plan(reference_circuit(), plan)


def test_unknown_run_type_rejected():
    plan = q1_analysis()
    plan["runs"][0]["type"] = "monte_carlo"
    with pytest.raises(analysis.AnalysisError, match="monte_carlo"):
        analysis.validate_plan(reference_circuit(), plan)


def test_measurement_referencing_unknown_run_rejected():
    plan = q1_analysis()
    plan["measurements"][0]["run"] = "nowhere"
    with pytest.raises(analysis.AnalysisError, match="nowhere"):
        analysis.validate_plan(reference_circuit(), plan)


def test_dc_sweep_of_unknown_source_rejected():
    plan = q1_analysis()
    plan["runs"][1]["sweep"]["source"] = "V9"
    with pytest.raises(analysis.AnalysisError, match="V9"):
        analysis.validate_plan(reference_circuit(), plan)


def test_param_sweep_of_unknown_component_rejected():
    plan = q1_analysis()
    plan["runs"][2]["component"] = "R9"
    with pytest.raises(analysis.AnalysisError, match="R9"):
        analysis.validate_plan(reference_circuit(), plan)


def test_sweep_measurement_without_at_rejected():
    plan = q1_analysis()
    del plan["measurements"][2]["at"]
    with pytest.raises(analysis.AnalysisError, match="at"):
        analysis.validate_plan(reference_circuit(), plan)


def test_derived_formula_with_unknown_name_rejected():
    plan = q1_analysis()
    plan["measurements"][4]["formula"] = "vout_high / vout_typo"
    with pytest.raises(analysis.AnalysisError, match="vout_typo"):
        analysis.validate_plan(reference_circuit(), plan)


def test_regime_with_unknown_assert_rejected():
    plan = q1_analysis()
    plan["measurements"][-1]["assert"] = "flux_stable"
    with pytest.raises(analysis.AnalysisError, match="flux_stable"):
        analysis.validate_plan(reference_circuit(), plan)


def test_regime_on_unknown_device_rejected():
    plan = q1_analysis()
    plan["measurements"][-1]["device"] = "Q9"
    with pytest.raises(analysis.AnalysisError, match="Q9"):
        analysis.validate_plan(reference_circuit(), plan)


# --------------------------------------------- safe expression evaluation


def test_evaluate_arithmetic():
    names = {"a": 10.0, "b": 4.0}
    assert analysis.evaluate("(a - b) / b", names) == pytest.approx(1.5)
    assert analysis.evaluate("100 * -b + a", names) == pytest.approx(-390.0)
    assert analysis.evaluate("a ** 2", names) == pytest.approx(100.0)


def test_evaluate_unknown_name_fails():
    with pytest.raises(analysis.AnalysisError, match="missing"):
        analysis.evaluate("missing + 1", {})


@pytest.mark.parametrize("hostile", [
    "abs(a)",                      # no calls
    "a.__class__",                 # no attributes
    "__import__('os')",            # obviously
    "[x for x in (1,)]",           # no comprehensions
    "a if a else a",               # no conditionals; formulas are arithmetic
    "lambda: 1",
])
def test_evaluate_rejects_non_arithmetic(hostile):
    with pytest.raises(analysis.AnalysisError):
        analysis.evaluate(hostile, {"a": 1.0})


def test_evaluate_division_by_zero_is_an_error():
    with pytest.raises(analysis.AnalysisError):
        analysis.evaluate("1 / a", {"a": 0.0})


# ------------------------------------------------- scratch per-run rendering


def test_op_run_renders_a_plain_op_directive():
    circuit = analysis.run_circuit(reference_circuit(), q1_analysis()["runs"][0])
    assert ".op" in circuit["directives"]
    assert ".model DZ8V3 D(BV=8.3 IBV=5m)" in circuit["directives"]


def test_dc_run_renders_a_dc_directive():
    circuit = analysis.run_circuit(reference_circuit(), q1_analysis()["runs"][1])
    assert ".dc V1 12 20 1" in circuit["directives"]
    assert ".op" not in circuit["directives"]  # two active analyses hang


def test_param_sweep_renders_native_step():
    circuit = analysis.run_circuit(reference_circuit(), q1_analysis()["runs"][2])
    rl = next(c for c in circuit["components"] if c["ref"] == "RL")
    assert rl["value"] == "{RLstep}"
    assert ".step param RLstep LIST 100k 5k 2k 1k 500" in circuit["directives"]
    assert ".op" in circuit["directives"]  # stepping still needs an analysis
    parse_asc(emit(circuit))


# -------------------------------------------------- tran + waveform stats


def tran_run():
    return {"id": "waves", "type": "tran", "stop": "200m",
            "settle": "100m", "max_step": "100u"}


def test_tran_run_renders_directive():
    circuit = analysis.run_circuit(reference_circuit(), tran_run())
    assert ".tran 0 200m 100m 100u" in circuit["directives"]


def test_tran_run_without_max_step():
    run = {"id": "waves", "type": "tran", "stop": "200m", "settle": "100m"}
    circuit = analysis.run_circuit(reference_circuit(), run)
    assert ".tran 0 200m 100m" in circuit["directives"]


def tran_plan(**overrides):
    run = {**tran_run(), **overrides}
    return {
        "runs": [run],
        "measurements": [
            {"name": "vout_stats", "run": "waves", "expr": "V(vout)",
             "kind": "waveform_stats"},
            {"kind": "regime", "run": "waves",
             "assert": "zener_in_breakdown", "device": "D1"},
        ],
    }


def test_tran_regime_without_settle_rejected():
    # The regime model assumes the operating regime, but a zener is out
    # of breakdown during startup BY DESIGN; without a settle window the
    # assertion would flag physics working correctly. Enforced, not
    # conventional.
    plan = tran_plan()
    del plan["runs"][0]["settle"]
    with pytest.raises(analysis.AnalysisError, match="settle"):
        analysis.validate_plan(reference_circuit(), plan)


def test_scalar_measurement_on_tran_rejected():
    plan = tran_plan()
    plan["measurements"][0] = {"name": "vout_nominal", "run": "waves",
                               "expr": "V(vout)"}
    with pytest.raises(analysis.AnalysisError, match="waveform_stats"):
        analysis.validate_plan(reference_circuit(), plan)


def test_waveform_stats_on_non_tran_rejected():
    plan = q1_analysis()
    plan["measurements"][0]["kind"] = "waveform_stats"
    with pytest.raises(analysis.AnalysisError, match="tran"):
        analysis.validate_plan(reference_circuit(), plan)


class TranFakeBackend:
    """Non-uniform timestep on purpose: stats must integrate over time,
    not average points, or variable-step simulators bias the result."""

    name = "fake"

    def run(self, asc_path):
        return Results(
            traces={
                "time": [0.0, 1.0, 2.0, 3.0],
                "V(vout)": [0.0, 2.0, 2.0, 0.0],
                "V(vin)": [1.0, 3.0, 4.0, 2.0],
                "I(D1)": [-0.004, -0.004, -0.004, -0.004],
                "V(vb)": [8.3, 8.3, 8.3, 8.3],
            },
            raw_path=asc_path, log_path=asc_path,
        )


def test_waveform_stats_are_time_weighted(tmp_path):
    results = analysis.execute(
        reference_circuit(), tran_plan(), TranFakeBackend(), tmp_path
    )
    m = results["vout_stats"]
    # Trapezoidal over [0,3]: area 4 -> mean 4/3; squared area 8 ->
    # rms sqrt(8/3). Hand-computed, not simulated.
    assert m.value == pytest.approx(4 / 3)
    assert m.stats["mean"] == pytest.approx(4 / 3)
    assert m.stats["ripple_pp"] == pytest.approx(2.0)
    assert m.stats["min"] == 0.0 and m.stats["max"] == 2.0
    assert m.stats["rms"] == pytest.approx((8 / 3) ** 0.5)
    assert m.source == "simulation"
    assert m.run == "waves"


def test_waveform_stats_differential_expression(tmp_path):
    # "input AC waveform" across a floating bridge source is
    # V(ac1)-V(ac2); no single raw trace holds it. waveform_stats
    # accepts the difference form and computes it pointwise.
    plan = tran_plan()
    plan["measurements"][0] = {
        "name": "vdiff_stats", "run": "waves",
        "expr": "V(vin)-V(vout)", "kind": "waveform_stats"}
    results = analysis.execute(
        reference_circuit(), plan, TranFakeBackend(), tmp_path
    )
    # difference wave: [1,1,2,2] -> trapezoidal mean (1+1.5+2)/3 = 1.5
    assert results["vdiff_stats"].value == pytest.approx(1.5)
    assert results["vdiff_stats"].stats["min"] == 1.0
    assert results["vdiff_stats"].stats["max"] == 2.0


def test_waveform_stats_render_in_report(tmp_path):
    results = analysis.execute(
        reference_circuit(), tran_plan(), TranFakeBackend(), tmp_path
    )
    report = analysis.render_report(results, tran_plan())
    assert "vout_stats" in report
    assert "ripple" in report
    assert "rms" in report


# ------------------------------------------------------- the deliverable


def test_deliverable_is_one_file_with_commented_runs():
    circuit = analysis.deliverable_circuit(reference_circuit(), q1_analysis())
    d = circuit["directives"]
    # First run's analysis is the active one.
    assert ".op" in d
    # Other runs are present as comments carrying their label and the
    # comment-out-.op instruction (two active analyses hang LTspice).
    dc_comment = next(x for x in d if x.startswith(";.dc V1 12 20 1"))
    assert "line regulation" in dc_comment
    assert ".op" in dc_comment  # "...comment out .op first"
    step_comment = next(
        x for x in d if x.startswith(";.step param RLstep LIST 100k 5k 2k 1k 500")
    )
    assert "load regulation" in step_comment
    # The swept component uses a parameter with an active default, so
    # the file simulates as handed over AND after uncommenting .step
    # (verified: .step overrides .param).
    rl = next(c for c in circuit["components"] if c["ref"] == "RL")
    assert rl["value"] == "{RLstep}"
    assert ".param RLstep 2k" in d
    # Device cards survive; exactly one active analysis directive.
    assert ".model DZ8V3 D(BV=8.3 IBV=5m)" in d
    active_analyses = [x for x in d if x.split()[0] in (".op", ".dc", ".step",
                                                       ".tran")]
    assert len(active_analyses) == 1
    parse_asc(emit(circuit))


def test_deliverable_emits_comments_not_directives():
    text = emit(analysis.deliverable_circuit(reference_circuit(), q1_analysis()))
    # Commented runs must land as ';' TEXT payloads, not '!' directives.
    assert ";.dc V1 12 20 1" in text
    assert "!;.dc" not in text


# ------------------------------------------------ execution + provenance


class FakeBackend:
    """Canned traces keyed by run id (the runner names files '<id>.asc')."""

    name = "fake"

    def __init__(self):
        self.canned = {
            "nominal": {"V(vout)": [7.484], "I(D1)": [-0.00369],
                        "V(vb)": [8.292]},
            # dc 12..20 step 1: 9 points; the raw records the swept
            # source as its own trace.
            "linesweep": {
                "V1": [12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0],
                "V(vout)": [7.45, 7.46, 7.47, 7.48, 7.484,
                            7.49, 7.50, 7.505, 7.51],
            },
            # param_sweep: LTspice sorts LIST values ascending and
            # records the axis as "<component>step" (here in real
            # LTspice casing, "rlstep"; lookup is case-insensitive).
            # All traces are in that ascending-RL order, healthy.
            "loadsweep": {
                "rlstep": [500.0, 1000.0, 2000.0, 5000.0, 100000.0],
                "V(vout)": [7.35, 7.42, 7.484, 7.55, 7.60],
                "V(vb)": [8.27, 8.28, 8.29, 8.30, 8.30],
                "V(vin)": [15.0] * 5,
                "I(D1)": [-0.0025, -0.0030, -0.0037, -0.0035, -0.0036],
                "Ib(Q1)": [1.5e-4, 7.5e-5, 3.9e-5, 1.5e-5, 1e-6],
            },
        }

    def run(self, asc_path):
        run_id = asc_path.stem
        return Results(
            traces=self.canned[run_id], raw_path=asc_path, log_path=asc_path
        )


def executed(tmp_path, backend=None):
    return analysis.execute(
        reference_circuit(), q1_analysis(), backend or FakeBackend(), tmp_path
    )


def test_execute_runs_everything_and_selects_points(tmp_path):
    results = executed(tmp_path)
    assert results["vout_nominal"].value == pytest.approx(7.484)
    assert results["vout_low"].value == pytest.approx(7.45)
    assert results["vout_high"].value == pytest.approx(7.51)
    # at RL="500": FIRST point of the axis trace, even though "500" is
    # written LAST in the plan's values — selection follows the axis
    # the raw records, not the plan's ordering.
    assert results["vout_fullload"].value == pytest.approx(7.35)
    assert results["vout_noload"].value == pytest.approx(7.60)


def test_spice_suffix_parsing():
    assert analysis.parse_spice_number("500") == 500.0
    assert analysis.parse_spice_number("1.8k") == pytest.approx(1800.0)
    assert analysis.parse_spice_number("100k") == pytest.approx(1e5)
    assert analysis.parse_spice_number("1Meg") == pytest.approx(1e6)
    assert analysis.parse_spice_number("2m") == pytest.approx(2e-3)  # milli!
    assert analysis.parse_spice_number("470u") == pytest.approx(470e-6)
    with pytest.raises(analysis.AnalysisError):
        analysis.parse_spice_number("banana")


def test_execute_computes_derived_measurements(tmp_path):
    results = executed(tmp_path)
    expected = 100 * (7.51 - 7.45) / 7.45
    assert results["line_reg_pct"].value == pytest.approx(expected)


def test_provenance_simulated(tmp_path):
    m = executed(tmp_path)["vout_low"]
    assert m.source == "simulation"
    assert m.run == "linesweep"
    assert m.backend == "fake"
    assert m.formula is None
    assert m.at == {"V1": 12}
    assert m.reliable


def test_provenance_derived(tmp_path):
    m = executed(tmp_path)["load_reg_pct"]
    assert m.source == "derived"
    assert m.run is None
    assert m.backend == "fake"
    assert m.formula == "100 * (vout_noload - vout_fullload) / vout_fullload"
    assert m.reliable


def test_wrong_length_trace_is_an_error(tmp_path):
    backend = FakeBackend()
    backend.canned["linesweep"] = {"V(vout)": [7.45, 7.51]}  # 2 != 9
    with pytest.raises(analysis.AnalysisError, match="linesweep"):
        executed(tmp_path, backend)


# ------------------------------------------------------- regime assertions


def test_violated_regime_marks_measurements_unreliable_not_absent(tmp_path):
    # Zener starved at the last two load points: reverse current
    # collapses. The numbers must still be reported — flagged, with the
    # reason — because dropout is worth seeing, not hiding.
    backend = FakeBackend()
    # Ascending-RL order: the heavy loads (where the zener starves)
    # are the first points of the axis.
    backend.canned["loadsweep"]["I(D1)"] = [-1e-8, -5e-5, -0.0037,
                                            -0.0035, -0.0036]
    backend.canned["loadsweep"]["V(vb)"] = [5.8, 7.1, 8.29, 8.30, 8.30]
    results = executed(tmp_path, backend)

    for name in ("vout_noload", "vout_fullload"):
        assert results[name].value is not None
        assert not results[name].reliable
        assert any("D1" in w for w in results[name].warnings)
    # Derived from unreliable inputs -> unreliable, reason carried along.
    assert not results["load_reg_pct"].reliable
    assert any("D1" in w for w in results["load_reg_pct"].warnings)
    # Other runs are untouched.
    assert results["vout_nominal"].reliable
    assert results["line_reg_pct"].reliable


def test_healthy_regime_leaves_everything_reliable(tmp_path):
    results = executed(tmp_path)
    assert all(m.reliable for m in results.values())


# ------------------------------------------------------- report rendering


def test_report_shows_formula_and_operating_points(tmp_path):
    report = analysis.render_report(executed(tmp_path), q1_analysis())
    assert "load_reg_pct" in report
    assert "100 * (vout_noload - vout_fullload) / vout_fullload" in report
    # The formula again with the actual numbers substituted in.
    assert "7.6" in report and "7.35" in report
    # Simulated points carry where they were taken.
    assert "RL=100k" in report
    # The textbook-reconciliation line.
    assert "no-load to full-load, normalised to full-load" in report


def test_report_distinguishes_simulation_from_derivation(tmp_path):
    report = analysis.render_report(executed(tmp_path), q1_analysis())
    assert "simulation" in report
    assert "derived" in report
    assert "fake" in report  # backend named


def test_internal_backend_results_carry_the_weakness_explanation(tmp_path):
    # A backend that IS the evaluator (no external simulator checks it)
    # must say why that is weaker, not merely name itself. See
    # CLAUDE.md, "The evaluator asymmetry".
    backend = FakeBackend()
    backend.verification = "internal"
    report = analysis.render_report(executed(tmp_path, backend),
                                    q1_analysis()).lower()
    assert "no external simulator" in report
    assert "undetectable" in report


def test_external_backend_results_carry_no_such_warning(tmp_path):
    report = analysis.render_report(executed(tmp_path), q1_analysis()).lower()
    assert "no external simulator" not in report


def test_measurement_records_backend_verification(tmp_path):
    backend = FakeBackend()
    backend.verification = "internal"
    assert executed(tmp_path, backend)["vout_nominal"].verification == \
        "internal"
    assert executed(tmp_path)["vout_nominal"].verification == "external"


def test_report_names_device_choices(tmp_path):
    # Every reported number must be traceable to the DeviceChoice that
    # produced it, path (a/b/c) named. The regression that motivated
    # this: a whole experiment ran on an outlawed unanchored card and
    # nothing in the output showed which device the numbers came from.
    from ohmwork.parts import DeviceChoice

    devices = {"D1": DeviceChoice(
        part="DZ8V3", directive=".model DZ8V3 D(BV=8.3 IBV=5m)",
        policy="synthesized",
        report="using a synthesised model: Vz=8.3 V anchored at a 5m "
               "test current",
    )}
    report = analysis.render_report(
        executed(tmp_path), q1_analysis(), devices=devices
    )
    assert "D1" in report
    assert "synthesized" in report          # the policy path, named
    assert "anchored at a 5m" in report     # the anchoring, visible


def test_report_flags_unreliable_measurements(tmp_path):
    backend = FakeBackend()
    backend.canned["loadsweep"]["I(D1)"] = [-1e-8] * 5
    report = analysis.render_report(executed(tmp_path, backend), q1_analysis())
    assert "UNRELIABLE" in report
    assert "D1" in report


# ------------------------------------------------- LTspice (skips if absent)


def _ltspice_available():
    from ohmwork.simulate import locate_ltspice
    try:
        locate_ltspice()
        return True
    except FileNotFoundError:
        return False


needs_ltspice = pytest.mark.skipif(
    not _ltspice_available(), reason="LTspice not installed"
)


@needs_ltspice
def test_full_q1_experiment_against_ltspice(tmp_path):
    """The whole point: Q1's four asks, from plan to numbers.

    Every expected value is a Baseline from tests/baselines.py with its
    provenance attached. An earlier set (vout 7.9392 etc.) was measured
    on the outlawed unanchored card and is void; the vb_nominal
    assertion cross-checks against the user's ngspice measurement so
    that class of mix-up fails loudly instead of passing plausibly.
    """
    from ohmwork.simulate import LTspiceBackend

    from tests import baselines as B

    results = analysis.execute(
        reference_circuit(), q1_analysis(), LTspiceBackend(), tmp_path
    )
    assert results["vb_nominal"].value == pytest.approx(
        B.VB_ANCHORED_NGSPICE.value, abs=3e-3)
    assert results["vout_nominal"].value == pytest.approx(
        B.VOUT_ANCHORED.value, abs=1e-3)
    assert results["iz_nominal"].value == pytest.approx(
        B.IZ_ANCHORED.value, abs=1e-5)
    assert results["line_reg_pct"].value == pytest.approx(
        B.LINE_REG_ANCHORED.value, abs=1e-3)
    assert results["load_reg_pct"].value == pytest.approx(
        B.LOAD_REG_ANCHORED.value, abs=1e-3)
    assert all(m.reliable for m in results.values())


@needs_ltspice
def test_full_q3_experiment_against_ltspice(tmp_path):
    """The design-question acceptance: examples/q3.json (bridge +
    C-L-C + zener, tran run, waveform stats, designed values) from the
    input gate all the way to pinned numbers."""
    import json

    from ohmwork.question import load_question
    from ohmwork.simulate import LTspiceBackend

    from tests import baselines as B

    q = load_question(json.loads(
        (pathlib_root() / "examples" / "q3.json").read_text(encoding="utf-8")))
    results = analysis.execute(q.circuit, q.plan, LTspiceBackend(), tmp_path)
    assert results["v_in_wave"].stats["rms"] == pytest.approx(
        B.Q3_VIN_RMS.value, abs=1e-3)
    assert results["v_out_wave"].stats["mean"] == pytest.approx(
        B.Q3_VOUT_MEAN.value, abs=1e-3)
    assert results["v_out_wave"].stats["ripple_pp"] == pytest.approx(
        B.Q3_VOUT_RIPPLE_PP.value, abs=2e-4)
    assert results["v_filt_wave"].stats["mean"] == pytest.approx(
        B.Q3_VFILT_MEAN.value, abs=5e-3)
    assert all(m.reliable for m in results.values())


def pathlib_root():
    import pathlib
    return pathlib.Path(__file__).parent.parent


@needs_ltspice
def test_dropout_sweep_fires_the_regime_check(tmp_path):
    """Sweep deliberately into dropout: at RL=10 the base current the
    pass transistor needs exceeds what R1 can supply, the zener starves,
    and load regulation would be a ratio across two different circuits'
    behaviours. The regime check must catch it."""
    from ohmwork.simulate import LTspiceBackend

    plan = q1_analysis()
    plan["runs"][2]["values"] = ["2k", "10"]
    plan["measurements"][5]["at"] = {"RL": "2k"}   # vout_noload
    plan["measurements"][6]["at"] = {"RL": "10"}   # vout_fullload
    results = analysis.execute(
        reference_circuit(), plan, LTspiceBackend(), tmp_path
    )
    assert not results["vout_fullload"].reliable
    assert not results["load_reg_pct"].reliable
    assert any("D1" in w for w in results["load_reg_pct"].warnings)
