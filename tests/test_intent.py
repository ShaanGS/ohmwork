"""The analog design intent: this file is its spec.

THE PROBLEM THIS LAYER HAS AND THE DIGITAL ONE DOES NOT. A digital question
has an oracle. Write the boolean expressions, enumerate 2**n rows, and an
outside tool either reproduces them or does not. Analog has nothing of the
kind: a circuit converges, produces numbers, and no exhaustive table exists to
compare them against.

So what IS checkable, and it is three things, none of which is as strong as a
truth table:

    1. IT SIMULATES        LTspice reads the emitted file and converges. Not
                           nothing -- a floating node or a source loop fails
                           here -- but far from correctness.
    2. THE REGIMES HOLD    convergence is not correctness. A load sweep into
                           dropout converges perfectly and reports a
                           confident, meaningless regulation figure. These are
                           DERIVED from the circuit, never asked for: every
                           zener gets zener_in_breakdown, every BJT gets
                           bjt_active, on every measured run, so a design
                           cannot quietly omit one.
    3. THE NUMBERS MATCH   each target the question states -- "9 V output",
       THE INTENT          "line regulation better than 1%" -- measured in
                           LTspice and checked against the number the question
                           gave, within a tolerance that is capped.

That third one is the analog counterpart of `compare_tables`, and its hole is
the same one: the intent is the model's READING of the question. If it reads
"9 V" as "5 V", intent and circuit agree, LTspice confirms both, and every
check passes. Hence `Intent.render()` is OUTPUT.

It also has a hole the digital side does not: **meeting a target is not being
a good design.** A regulator that hits 9.0 V with a 40% ripple, or with the
pass transistor dissipating 6 W, passes every check here. The basis says so
rather than implying otherwise.

A target with no number is an OBSERVATION -- "observe the output waveform" is
a real ask with nothing to check. Those are measured, reported, and counted
separately, because a run where nothing could have failed must not look like a
run where nothing did.
"""

import json

import pytest

from ohmwork.analysis import Measurement, Experiment, RegimeResult, parse_spice_number
from ohmwork.intent import (MAX_TOLERANCE_PCT, Intent, IntentError, Target,
                            build_analog_plan, compare_targets, parse_intent_reply,
                            spice_number)

REGULATOR = json.dumps({
    "topology": "series voltage regulator",
    "targets": [
        {"name": "vout_nominal", "kind": "dc_voltage", "net": "vout",
         "quantity": "regulated output voltage", "unit": "V",
         "value": 9.0, "tolerance_pct": 5},
        {"name": "line_reg_pct", "kind": "line_regulation", "net": "vout",
         "low": 12, "high": 20, "quantity": "line regulation", "unit": "%",
         "max": 1.0},
        {"name": "iz_nominal", "kind": "dc_current", "role": "zener",
         "quantity": "zener current", "unit": "A"},
    ],
    "stated_values": [
        {"what": "unregulated supply", "value": "15", "unit": "V"},
        {"what": "load resistance", "value": "1k", "unit": "ohm"},
    ],
    "notes": ["the line-regulation range 12 V to 20 V is the question's"],
})

REGULATOR_CIRCUIT = {
    "components": [
        {"ref": "V1", "type": "voltage", "value": "15"},
        {"ref": "R1", "type": "res", "value": "1.8k"},
        {"ref": "D1", "type": "zener", "device": {"vz": 9.7, "exact": True}},
        {"ref": "Q1", "type": "npn", "device": {"params": {"BF": 100}}},
        {"ref": "RL", "type": "res", "value": "1k"},
    ],
    "nets": {
        "vin": ["V1.+", "R1.a", "Q1.C"],
        "vb": ["R1.b", "D1.cathode", "Q1.B"],
        "vout": ["Q1.E", "RL.a"],
        "0": ["V1.-", "D1.anode", "RL.b"],
    },
}


# ------------------------------------------------------------ parsing

def test_an_intent_reply_is_parsed():
    intent = parse_intent_reply(REGULATOR)
    assert intent.topology == "series voltage regulator"
    assert [t.name for t in intent.targets] == [
        "vout_nominal", "line_reg_pct", "iz_nominal"]
    assert intent.targets[0].value == 9.0
    assert intent.targets[1].maximum == 1.0
    assert intent.notes


def test_a_target_with_no_number_is_an_observation_and_says_so():
    """"Observe the output waveform" is a real ask with nothing to check.

    It must be measured and reported -- and counted apart from the targets
    that could have failed, because a run where nothing COULD fail must not
    look like a run where nothing did.
    """
    intent = parse_intent_reply(REGULATOR)
    zener_current = intent.targets[2]
    assert zener_current.is_observation
    assert not intent.targets[0].is_observation
    assert intent.checkable == 2


def test_a_tolerance_wide_enough_to_admit_anything_is_refused():
    """A check that cannot fail is worth nothing, and a 50% tolerance on an
    output voltage is exactly that. The cap is deliberate and documented."""
    reply = json.loads(REGULATOR)
    reply["targets"][0]["tolerance_pct"] = MAX_TOLERANCE_PCT + 1

    with pytest.raises(IntentError) as excinfo:
        parse_intent_reply(json.dumps(reply))
    assert f"{MAX_TOLERANCE_PCT:g}" in str(excinfo.value)


def test_a_value_with_no_tolerance_is_refused():
    """An exact float never matches a simulated one. A target with a value and
    no tolerance would fail every time, which reads as a broken design rather
    than as a malformed intent."""
    reply = json.loads(REGULATOR)
    del reply["targets"][0]["tolerance_pct"]
    with pytest.raises(IntentError):
        parse_intent_reply(json.dumps(reply))


def test_a_target_may_carry_only_one_kind_of_bound():
    reply = json.loads(REGULATOR)
    reply["targets"][0]["max"] = 10.0
    with pytest.raises(IntentError) as excinfo:
        parse_intent_reply(json.dumps(reply))
    assert "one" in str(excinfo.value)


def test_an_intent_with_no_targets_at_all_is_refused():
    """The analog shape of `check_spec_has_logic`: an intent that asks for
    nothing verifies perfectly and means nothing."""
    with pytest.raises(IntentError):
        parse_intent_reply(json.dumps({"topology": "x", "targets": []}))


def test_a_target_name_must_be_usable_as_a_measurement_name():
    reply = json.loads(REGULATOR)
    reply["targets"][0]["name"] = "V out!"
    with pytest.raises(IntentError):
        parse_intent_reply(json.dumps(reply))


def test_a_net_name_LTspice_would_mangle_is_refused_at_the_intent():
    """Same rule as the digital loop's SAFE_LABEL, one layer earlier: these
    names become FLAG labels in the emitted file and traces in the results."""
    reply = json.loads(REGULATOR)
    reply["targets"][0]["net"] = "v out"
    with pytest.raises(IntentError):
        parse_intent_reply(json.dumps(reply))


def test_an_unknown_target_kind_names_the_ones_that_exist():
    reply = json.loads(REGULATOR)
    reply["targets"][0]["kind"] = "efficiency"
    with pytest.raises(IntentError) as excinfo:
        parse_intent_reply(json.dumps(reply))
    assert "dc_voltage" in str(excinfo.value)


def test_a_transient_target_without_a_frequency_is_refused():
    """The transient window is DERIVED from the source frequency. Without one
    there is no principled stop time, and an invented one either misses the
    steady state or takes minutes to simulate."""
    reply = json.loads(REGULATOR)
    reply["targets"] = [{"name": "vout_ripple", "kind": "ripple_pp",
                         "net": "vout", "quantity": "output ripple",
                         "unit": "V", "max": 0.1}]
    with pytest.raises(IntentError) as excinfo:
        parse_intent_reply(json.dumps(reply))
    assert "frequency" in str(excinfo.value)


# ------------------------------------------------------- the derived plan

def test_the_plan_is_derived_from_the_intent_and_the_circuit():
    """Deterministic Python, for the same reason `design.build_plan` is: a
    model asked for the plan can only introduce error into something that
    follows from the intent with nothing left to choose."""
    plan = build_analog_plan(parse_intent_reply(REGULATOR), REGULATOR_CIRCUIT)

    runs = {run["id"]: run for run in plan["runs"]}
    assert set(runs) == {"nominal", "linesweep"}
    assert runs["nominal"]["type"] == "op"
    assert runs["linesweep"]["sweep"] == {
        "source": "V1", "start": 12, "stop": 20, "step": 1}

    named = {m.get("name"): m for m in plan["measurements"] if "name" in m}
    assert named["vout_nominal"]["expr"] == "V(vout)"
    assert named["iz_nominal"]["expr"] == "I(D1)"
    assert named["line_reg_pct"]["kind"] == "derived"
    assert "definition" in named["line_reg_pct"], (
        "load regulation alone has several textbook definitions; a bare "
        "percentage cannot be reconciled with a lab manual")


def test_the_regime_assertions_are_DERIVED_not_requested():
    """A design cannot quietly omit one. Every zener gets
    zener_in_breakdown and every BJT gets bjt_active, on every measured run,
    because convergence is not correctness and the model has nothing to add
    to a rule that follows from the parts list."""
    plan = build_analog_plan(parse_intent_reply(REGULATOR), REGULATOR_CIRCUIT)
    regimes = {(m["assert"], m["device"], m["run"])
               for m in plan["measurements"] if m.get("kind") == "regime"}

    for run in ("nominal", "linesweep"):
        assert ("zener_in_breakdown", "D1", run) in regimes
        assert ("bjt_active", "Q1", run) in regimes


def test_a_zener_whose_voltage_is_known_has_it_checked_too():
    plan = build_analog_plan(parse_intent_reply(REGULATOR), REGULATOR_CIRCUIT)
    zener = next(m for m in plan["measurements"]
                 if m.get("assert") == "zener_in_breakdown")
    assert zener["vz"] == 9.7


def test_a_zener_named_only_by_part_number_asserts_what_it_can():
    """`vz` is omitted rather than guessed. The reverse-current half of the
    assertion still runs, and a check that examined less must not claim to
    have examined more."""
    circuit = json.loads(json.dumps(REGULATOR_CIRCUIT))
    circuit["components"][2] = {"ref": "D1", "type": "zener",
                                "part": "BZX84C9V1L"}
    plan = build_analog_plan(parse_intent_reply(REGULATOR), circuit)
    zener = next(m for m in plan["measurements"]
                 if m.get("assert") == "zener_in_breakdown")
    assert "vz" not in zener


def test_the_derived_plan_is_one_the_gate_accepts():
    """The end-to-end check on this whole module: hand what it built to the
    real validator, against the real circuit."""
    from ohmwork.analysis import validate_plan

    validate_plan(REGULATOR_CIRCUIT,
                  build_analog_plan(parse_intent_reply(REGULATOR),
                                    REGULATOR_CIRCUIT))


def test_a_role_that_matches_no_part_is_refused_with_what_IS_there():
    circuit = json.loads(json.dumps(REGULATOR_CIRCUIT))
    circuit["components"] = [c for c in circuit["components"]
                             if c["ref"] != "D1"]
    with pytest.raises(IntentError) as excinfo:
        build_analog_plan(parse_intent_reply(REGULATOR), circuit)
    assert "zener" in str(excinfo.value)


def test_a_role_that_matches_two_parts_is_refused_rather_than_guessed():
    circuit = json.loads(json.dumps(REGULATOR_CIRCUIT))
    circuit["components"].append(
        {"ref": "D2", "type": "zener", "device": {"vz": 5.1}})
    circuit["nets"]["vb"].append("D2.cathode")
    circuit["nets"]["0"].append("D2.anode")
    with pytest.raises(IntentError) as excinfo:
        build_analog_plan(parse_intent_reply(REGULATOR), circuit)
    assert "D1" in str(excinfo.value) and "D2" in str(excinfo.value)


def test_the_reserved_refs_are_named_when_they_are_missing():
    """`V1` is the supply and `RL` is the load, by convention stated in the
    design prompt. Resistors are indistinguishable by type, so a role lookup
    cannot find a load; a reserved name can, and the failure says so."""
    circuit = json.loads(json.dumps(REGULATOR_CIRCUIT))
    circuit["components"][0]["ref"] = "VSUPPLY"
    circuit["nets"]["vin"][0] = "VSUPPLY.+"
    circuit["nets"]["0"][0] = "VSUPPLY.-"
    with pytest.raises(IntentError) as excinfo:
        build_analog_plan(parse_intent_reply(REGULATOR), circuit)
    assert "V1" in str(excinfo.value)


# ---------------------------------------------- the transient window

def test_the_transient_window_is_derived_from_the_source_frequency():
    """MEASURED against a hand-written plan, which is the point of the test.

    `examples/q3.json` was written by hand for a 50 Hz supply: stop 200m,
    settle 100m, max_step 100u. The rule below -- ten periods, discard five,
    two hundred steps a period -- reproduces all three exactly. A derivation
    that agrees with a plan a person wrote for a real circuit is worth more
    than one that merely looks reasonable.
    """
    reply = json.loads(REGULATOR)
    reply["frequency"] = 50
    reply["targets"] = [
        {"name": "v_out_wave", "kind": "waveform", "net": "vout",
         "quantity": "the regulated output waveform", "unit": "V"},
        {"name": "v_in_rms", "kind": "ac_rms", "net": "ac1", "net2": "ac2",
         "quantity": "input RMS voltage", "unit": "V",
         "value": 12.0, "tolerance_pct": 2},
    ]
    intent = parse_intent_reply(json.dumps(reply))
    plan = build_analog_plan(intent, REGULATOR_CIRCUIT)

    waveforms = next(r for r in plan["runs"] if r["type"] == "tran")
    assert waveforms["stop"] == "200m"
    assert waveforms["settle"] == "100m"
    assert waveforms["max_step"] == "100u"

    named = {m["name"]: m for m in plan["measurements"] if "name" in m}
    assert named["v_in_rms"]["expr"] == "V(ac1)-V(ac2)"
    assert named["v_in_rms"]["kind"] == "waveform_stats"


def test_spice_numbers_round_trip_through_the_parser_that_reads_them():
    """The formatter writes what `analysis.parse_spice_number` reads. Two
    independent conventions for one number is how a 470u becomes a 470p."""
    for value in (0.2, 0.1, 1e-4, 1500.0, 2.0, 4.7e-6, 1e6):
        assert parse_spice_number(spice_number(value)) == pytest.approx(value)


# --------------------------------------------------------- the comparison

def measured(name, value, reliable=True, stats=None, warnings=()):
    return Measurement(name=name, value=value, run="nominal",
                       backend="ltspice", source="simulation",
                       reliable=reliable, stats=stats, warnings=tuple(warnings))


def held(assertion="zener_in_breakdown", ok=True):
    return RegimeResult(assertion=assertion, run="nominal", device="D1",
                        held=ok, examined="1 operating point",
                        reasons=() if ok else ("out of breakdown",))


def experiment(results, regimes=(held(),)):
    return Experiment({m.name: m for m in results}, list(regimes))


def test_a_circuit_that_meets_every_target_agrees():
    intent = parse_intent_reply(REGULATOR)
    outcome = compare_targets(intent, experiment([
        measured("vout_nominal", 9.15),
        measured("line_reg_pct", 0.4),
        measured("iz_nominal", 0.0037),
    ]))
    assert outcome.agrees
    assert "3" in outcome.summary or "2" in outcome.summary


def test_a_number_outside_its_tolerance_fails_and_names_both_values():
    """The message is fed straight back to the model that designed it, so it
    has to say what was wanted and what came out."""
    intent = parse_intent_reply(REGULATOR)
    outcome = compare_targets(intent, experiment([
        measured("vout_nominal", 7.4),
        measured("line_reg_pct", 0.4),
        measured("iz_nominal", 0.0037),
    ]))
    assert not outcome.agrees
    assert "9" in outcome.summary and "7.4" in outcome.summary
    assert "vout_nominal" in outcome.summary


def test_a_bound_is_checked_as_a_bound_not_as_a_tolerance():
    intent = parse_intent_reply(REGULATOR)
    ok = compare_targets(intent, experiment([
        measured("vout_nominal", 9.0), measured("line_reg_pct", 0.9),
        measured("iz_nominal", 0.0037)]))
    bad = compare_targets(intent, experiment([
        measured("vout_nominal", 9.0), measured("line_reg_pct", 1.6),
        measured("iz_nominal", 0.0037)]))
    assert ok.agrees and not bad.agrees
    assert "at most" in bad.summary


def test_a_violated_regime_fails_the_whole_comparison():
    """Convergence is not correctness. A load sweep into dropout converges
    perfectly and reports a confident, meaningless regulation figure, so a
    regime that did not hold is a failure and not a footnote."""
    intent = parse_intent_reply(REGULATOR)
    outcome = compare_targets(intent, experiment(
        [measured("vout_nominal", 9.0), measured("line_reg_pct", 0.4),
         measured("iz_nominal", 0.0037)],
        regimes=(held(ok=False),)))
    assert not outcome.agrees
    assert "zener_in_breakdown" in outcome.summary


def test_an_unreliable_measurement_fails_even_when_its_number_looks_right():
    """A measurement marked unreliable carries the reason its run was
    invalidated. Accepting the number because it happens to land inside the
    tolerance is accepting a number nobody should read."""
    intent = parse_intent_reply(REGULATOR)
    outcome = compare_targets(intent, experiment([
        measured("vout_nominal", 9.0, reliable=False,
                 warnings=("Q1 left the active region",)),
        measured("line_reg_pct", 0.4),
        measured("iz_nominal", 0.0037)]))
    assert not outcome.agrees
    assert "active region" in outcome.summary


def test_an_observation_is_reported_and_never_fails():
    """It has no number to fail against. What it must NOT do is look like a
    check that passed."""
    intent = parse_intent_reply(REGULATOR)
    outcome = compare_targets(intent, experiment([
        measured("vout_nominal", 9.0),
        measured("line_reg_pct", 0.4),
        measured("iz_nominal", 1e9)]))       # absurd, and unchecked
    assert outcome.agrees
    assert outcome.observations == 1


def test_a_target_with_no_measurement_at_all_is_a_failure():
    """Agreement over the subset that happens to be present is not agreement,
    and the plan is derived from the intent, so an absence is a real fault."""
    intent = parse_intent_reply(REGULATOR)
    outcome = compare_targets(intent, experiment([
        measured("vout_nominal", 9.0), measured("line_reg_pct", 0.4)]))
    assert not outcome.agrees
    assert "iz_nominal" in outcome.summary


def test_a_waveform_target_is_read_from_the_right_STATISTIC():
    """A waveform measurement's `value` is its time-weighted MEAN. Reading a
    ripple target off that would compare a peak-to-peak requirement against
    an average -- incident 5's number in a new place."""
    reply = json.loads(REGULATOR)
    reply["frequency"] = 50
    reply["targets"] = [{"name": "vout_ripple", "kind": "ripple_pp",
                         "net": "vout", "quantity": "output ripple",
                         "unit": "V", "max": 0.05}]
    intent = parse_intent_reply(json.dumps(reply))

    ripply = measured("vout_ripple", 9.0,
                      stats={"mean": 9.0, "ripple_pp": 0.9, "rms": 9.0})
    assert not compare_targets(intent, experiment([ripply])).agrees
    smooth = measured("vout_ripple", 9.0,
                      stats={"mean": 9.0, "ripple_pp": 0.01, "rms": 9.0})
    assert compare_targets(intent, experiment([smooth])).agrees


# ------------------------------------------------------------- the reading

def test_the_reading_shows_every_number_a_misread_would_corrupt():
    """The one failure nothing downstream can catch is a misreading, and the
    only defence is a person seeing what was understood. `1.8k` read as
    `1.8M` simulates perfectly well."""
    reading = parse_intent_reply(REGULATOR).render()

    assert "series voltage regulator" in reading
    assert "9" in reading and "5%" in reading
    assert "at most 1" in reading
    assert "1k" in reading, "a stated component value must be visible"
    assert "not checked" in reading, "the observation must say it is one"


def test_the_first_line_of_a_failure_stands_on_its_own():
    """MEASURED on the first live analog run. Both the CLI and the web UI
    render a rejected attempt as ONE line, and a bare header with every fact
    underneath it reads as an attempt that failed for no stated reason."""
    intent = parse_intent_reply(REGULATOR)
    outcome = compare_targets(intent, experiment(
        [measured("vout_nominal", 7.4), measured("line_reg_pct", 3.0),
         measured("iz_nominal", 0.0037)]))

    first = outcome.summary.splitlines()[0]
    assert "vout_nominal" in first and "7.4" in first
    assert "1 more" in first, "and it must say how much it is not showing"


def test_a_choice_made_here_never_renders_as_something_the_question_said():
    """Also from the live run: a tolerance the model CHOSE rendered directly
    under "stated in the question:", at the same indent."""
    reply = json.loads(REGULATOR)
    reply["notes"] = ["a 2% tolerance was assumed; the question gives none"]
    reading = parse_intent_reply(json.dumps(reply)).render()

    stated = reading.index("stated in the question:")
    chosen = reading.index("chosen here, because the question left it open:")
    assert stated < chosen
    assert "2% tolerance was assumed" in reading.split(
        "chosen here, because the question left it open:")[1]
