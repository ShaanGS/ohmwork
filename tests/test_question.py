"""Tests for ohmwork.question: the JSON input gate (build step 5).

This layer is the last checkpoint where a human is still in the loop —
everything after it will be machine-generated intent. So it is a gate,
not a file loader:

  a. STRICT schema: unknown keys are rejected with a path-shaped error,
     never ignored. An LLM emitting "resistance" instead of "value"
     must fail loudly, not silently produce a defaulted component.
  b. Device resolution: semiconductors arrive as specs ("part" for a
     question-named device, "device": {...} for one needing the
     policy) and leave as resolved parts + anchored cards, with the
     DeviceChoice recorded for the report.
  c. Semantic warnings (warn, never fail — they exist to be shown to
     the human at the confirmation step): component values outside
     plausible magnitude windows, and runs measured without any regime
     assertion.
  d. Round trip: to_dict() rebuilds the input from parsed state and
     must equal the original — catches fields the loader parsed but
     forgot, i.e. schema drift, once the LLM starts producing these.

load_question() also runs the full non-simulation validation chain:
device policy, emit + geometric parse round trip, plan validation.
"""

import copy

import pytest

from ohmwork.question import QuestionError, dry_run_report, load_question

from tests.test_parts import mini_library


def q1_question():
    """The anchored Q1 as it will arrive from the LLM layer: device
    SPECS, not resolved parts, and regimes on every run."""
    regimes = [
        {"kind": "regime", "run": run, "assert": check, "device": dev,
         **extra}
        for run in ("nominal", "linesweep", "loadsweep")
        for check, dev, extra in (
            ("zener_in_breakdown", "D1", {"vz": 8.3}),
            ("bjt_active", "Q1", {}),
        )
    ]
    return {
        # The verbatim question text, exactly as supplied. This is the
        # only part of the input NOT written by the extractor, which
        # makes it the only thing coverage can honestly be checked
        # against: asks-vs-measurements compares model output with
        # model output.
        "question": (
            "Calculate the output voltage in both load and line "
            "regulation and Zener diode current in the regulator "
            "circuit shown below using LTspice."
        ),
        "circuit": {
            "components": [
                {"ref": "V1", "type": "voltage", "value": "15"},
                {"ref": "R1", "type": "res", "value": "1.8k"},
                {"ref": "D1", "type": "zener",
                 "device": {"vz": 8.3, "exact": True}},
                {"ref": "Q1", "type": "npn",
                 "device": {"params": {"BF": 100}}},
                {"ref": "RL", "type": "res", "value": "2k"},
            ],
            "nets": {
                "vin": ["V1.+", "R1.a", "Q1.C"],
                "vb": ["R1.b", "D1.cathode", "Q1.B"],
                "vout": ["Q1.E", "RL.a"],
                "0": ["V1.-", "D1.anode", "RL.b"],
            },
        },
        # Verbatim phrases from the question text, each mapped to the
        # measurement that answers it. Coverage is checked both ways:
        # an unmapped ask means the extractor dropped work, a
        # measurement answering no ask means we invented work.
        "asks": [
            {"text": "output voltage", "answered_by": "vout_nominal"},
            {"text": "line regulation", "answered_by": "line_reg_pct"},
            {"text": "load regulation", "answered_by": "load_reg_pct"},
            {"text": "Zener diode current", "answered_by": "iz_nominal"},
        ],
        "analysis": {
            "runs": [
                {"id": "nominal", "type": "op"},
                {"id": "linesweep", "type": "dc", "label": "line regulation",
                 "sweep": {"source": "V1", "start": 12, "stop": 20,
                           "step": 1}},
                {"id": "loadsweep", "type": "param_sweep",
                 "label": "load regulation", "component": "RL",
                 "values": ["100k", "5k", "2k", "1k", "500"]},
            ],
            "measurements": [
                {"name": "vout_nominal", "run": "nominal",
                 "expr": "V(vout)"},
                {"name": "vb_nominal", "run": "nominal", "expr": "V(vb)",
                 "guard": "cross-simulator tripwire: vb must sit near "
                          "8.292 V on any compliant backend"},
                {"name": "iz_nominal", "run": "nominal", "expr": "I(D1)"},
                {"name": "vout_low", "run": "linesweep", "expr": "V(vout)",
                 "at": {"V1": 12}},
                {"name": "vout_high", "run": "linesweep", "expr": "V(vout)",
                 "at": {"V1": 20}},
                {"name": "line_reg_pct", "kind": "derived",
                 "formula": "100 * (vout_high - vout_low) / vout_low",
                 "definition":
                     "12 V to 20 V input, normalised to the 12 V output"},
                {"name": "vout_noload", "run": "loadsweep",
                 "expr": "V(vout)", "at": {"RL": "100k"}},
                {"name": "vout_fullload", "run": "loadsweep",
                 "expr": "V(vout)", "at": {"RL": "500"}},
                {"name": "load_reg_pct", "kind": "derived",
                 "formula":
                     "100 * (vout_noload - vout_fullload) / vout_fullload",
                 "definition": "no-load to full-load, normalised to "
                               "full-load"},
            ] + regimes,
        },
    }


def loaded(data=None):
    return load_question(data or q1_question(), library=mini_library())


# ----------------------------------------------------- device resolution


def test_devices_resolved_with_policy_paths():
    q = loaded()
    assert q.devices["D1"].policy == "synthesized"
    assert q.devices["Q1"].policy == "synthesized"
    d1 = next(c for c in q.circuit["components"] if c["ref"] == "D1")
    assert d1["part"] == q.devices["D1"].part
    assert q.devices["D1"].directive in q.circuit["directives"]
    assert "IBV" in q.devices["D1"].directive  # anchored, of course


def test_named_part_is_path_a():
    data = q1_question()
    data["circuit"]["components"][2] = {
        "ref": "D1", "type": "zener", "part": "UMZ8_2T"}
    q = loaded(data)
    assert q.devices["D1"].policy == "named"
    assert q.devices["D1"].part == "UMZ8_2T"


def test_vague_zener_is_path_c():
    data = q1_question()
    data["circuit"]["components"][2]["device"] = {"vz": 8.3, "exact": False}
    q = loaded(data)
    assert q.devices["D1"].policy == "nearest"
    assert q.devices["D1"].part == "BZX84C8V2L"


def test_semiconductor_with_neither_part_nor_device_rejected():
    data = q1_question()
    data["circuit"]["components"][2] = {"ref": "D1", "type": "zener"}
    with pytest.raises(QuestionError, match="D1"):
        loaded(data)


# ----------------------------------------------------------- strict schema


def test_unknown_component_key_rejected_with_path():
    data = q1_question()
    data["circuit"]["components"][1] = {
        "ref": "R1", "type": "res", "resistance": "1.8k"}
    with pytest.raises(QuestionError, match=r"components\[1\].*resistance"):
        loaded(data)


def test_unknown_top_level_key_rejected():
    data = q1_question()
    data["notes"] = "hello"
    with pytest.raises(QuestionError, match="notes"):
        loaded(data)


def test_unknown_measurement_key_rejected():
    data = q1_question()
    data["analysis"]["measurements"][0]["units"] = "V"
    with pytest.raises(QuestionError, match=r"measurements\[0\].*units"):
        loaded(data)


def test_unknown_device_spec_key_rejected():
    data = q1_question()
    data["circuit"]["components"][2]["device"] = {"voltage": 8.3}
    with pytest.raises(QuestionError, match="voltage"):
        loaded(data)


# ------------------------------------------------------- semantic warnings


def test_healthy_question_has_no_warnings():
    assert loaded().warnings == []


def test_implausible_resistor_value_warns_but_loads():
    data = q1_question()
    data["circuit"]["components"][1]["value"] = "1.8Meg"  # classic misread
    q = loaded(data)
    assert any("R1" in w for w in q.warnings)


def test_run_without_regime_assertions_warns():
    data = q1_question()
    ms = data["analysis"]["measurements"]
    data["analysis"]["measurements"] = [
        m for m in ms
        if not (m.get("kind") == "regime" and m["run"] == "linesweep")
    ]
    q = loaded(data)
    assert any("linesweep" in w for w in q.warnings)


# --------------------------------------------------------------- coverage


def test_ask_mapped_to_unknown_measurement_is_an_error():
    data = q1_question()
    data["asks"][0]["answered_by"] = "vout_typo"
    with pytest.raises(QuestionError, match="vout_typo"):
        loaded(data)


def test_unmapped_ask_warns_loudly():
    # The dominant vision failure: the question asked for something the
    # extractor never turned into a measurement.
    data = q1_question()
    data["asks"].append({"text": "ripple rejection"})
    q = loaded(data)
    assert any("ripple rejection" in w for w in q.warnings)


def test_measurement_answering_no_ask_warns():
    # Invented work: a measurement no ask requested.
    data = q1_question()
    data["analysis"]["measurements"].insert(3, {
        "name": "vin_check", "run": "nominal", "expr": "V(vin)"})
    q = loaded(data)
    assert any("vin_check" in w for w in q.warnings)


def test_intermediates_of_covered_derived_are_covered():
    # vout_low/vout_high answer no ask directly but feed line_reg_pct,
    # which does; they must not be flagged as invented work.
    q = loaded()
    assert not any("vout_low" in w for w in q.warnings)


def test_guard_measurements_are_exempt_from_coverage():
    # vb_nominal is deliberate extra work with a declared reason.
    q = loaded()
    assert not any("vb_nominal" in w for w in q.warnings)


def test_coverage_section_in_dry_run():
    report = dry_run_report(loaded())
    assert "question asks (4" in report
    assert '"line regulation"' in report
    assert "line_reg_pct" in report
    assert "unmapped" in report
    assert "vb_nominal" in report and "tripwire" in report  # guards named
    assert "5 components, 4 nets, 4 asks" in report
    # The parameters line: what would catch a dropped beta.
    assert "Vz=8.3 (D1)" in report
    assert "BF=100 (Q1)" in report


# -------------------------------------------------------------- verbosity


def test_default_report_is_scannable_not_verbose():
    report = dry_run_report(loaded())
    # Values and specs one line per component, tag not paragraph.
    assert "[synth, anchored 5m]" in report
    assert "BF=100" in report
    # The rationale paragraph and model cards live behind --explain.
    assert "answer a slightly different question" not in report
    assert ".model DZ8V3" not in report


def test_explain_restores_rationale_and_cards():
    report = dry_run_report(loaded(), explain=True)
    assert "answer a slightly different question" in report
    assert ".model DZ8V3 D(BV=8.3 IBV=5m)" in report


def test_nearest_tag_names_the_substitute_part():
    data = q1_question()
    data["circuit"]["components"][2]["device"] = {"vz": 8.3, "exact": False}
    report = dry_run_report(loaded(data))
    # A substitution must be visible at a glance, not only in --explain.
    assert "[nearest: BZX84C8V2L" in report


# --------------------------------------------------- value origins
#
# Q1 hands you a circuit (analysis question); Q2-Q4 say "design..."
# (design questions). A designed value the tool chose must never be
# indistinguishable from a value the question stated: the student
# has to understand and defend the choices, so they get their own
# prominent section. Origins: stated | designed | default.


def test_designed_value_requires_rationale():
    data = q1_question()
    data["circuit"]["components"][1]["origin"] = "designed"
    with pytest.raises(QuestionError, match="rationale"):
        loaded(data)


def test_invalid_origin_rejected():
    data = q1_question()
    data["circuit"]["components"][1]["origin"] = "guessed"
    with pytest.raises(QuestionError, match="guessed"):
        loaded(data)


def test_designed_values_get_their_own_section():
    data = q1_question()
    data["circuit"]["components"][1]["origin"] = "designed"
    data["circuit"]["components"][1]["rationale"] = (
        "sets ~3.7 mA zener bias, above the 5 mA knee at low line")
    report = dry_run_report(loaded(data))
    section = report[report.index("designed values"):]
    assert "R1" in section and "1.8k" in section
    assert "3.7 mA zener bias" in section
    assert "choices, not given" in report
    assert "change them before use" in report


def test_all_stated_question_has_no_designed_section():
    report = dry_run_report(loaded())
    assert "designed values" not in report


def test_component_table_marks_non_stated_origins():
    data = q1_question()
    data["circuit"]["components"][1]["origin"] = "designed"
    data["circuit"]["components"][1]["rationale"] = "chosen for bias"
    report = dry_run_report(loaded(data))
    table = report[report.index("components"):report.index("nets")]
    assert "<- designed" in table


def test_design_notes_render_in_designed_section():
    data = q1_question()
    data["design_notes"] = [{
        "item": "topology",
        "choice": "series pass regulator with shunt zener reference",
        "rationale": "the standard topology for this spec",
    }]
    report = dry_run_report(loaded(data))
    section = report[report.index("designed values"):]
    assert "topology" in section
    assert "series pass regulator" in section
    assert loaded(data).to_dict() == data  # round-trips


# ------------------------------------------------- rationale authorship
#
# A rationale's trustworthiness does NOT come from a human having
# written it — the moment the LLM layer lands, most rationales are
# model-written (examples/q3.json's RS=220 rationale already is). What
# makes one trustworthy is that a human REVIEWED it at this gate. So
# authorship is recorded and rendered honestly, and the dry run counts
# how many rationales are awaiting that review.


def designed_r1(origin=None):
    data = q1_question()
    comp = data["circuit"]["components"][1]
    comp["origin"] = "designed"
    comp["rationale"] = "sets the zener bias current"
    if origin is not None:
        comp["rationale_origin"] = origin
    return data


def test_invalid_rationale_origin_rejected():
    with pytest.raises(QuestionError, match="alleged"):
        loaded(designed_r1("alleged"))


def test_human_rationale_labeled_human():
    report = dry_run_report(loaded(designed_r1("human")))
    assert "[human-written]" in report


def test_generated_rationale_labeled_as_reviewed_not_authored():
    report = dry_run_report(loaded(designed_r1("generated")))
    assert "[generated, reviewed at input gate]" in report
    assert "[human-written]" not in report


def test_unrecorded_authorship_is_not_assumed_human():
    # Absent authorship must NOT default to "human": assuming it is the
    # same unfounded trust the whole feature exists to remove.
    report = dry_run_report(loaded(designed_r1()))
    assert "[authorship not recorded" in report
    assert "[human-written]" not in report


def test_review_count_near_the_top_of_the_dry_run():
    report = dry_run_report(loaded(designed_r1("generated")))
    assert "1 rationale requires your review" in report
    # Above the component table, so it is seen before the details.
    assert report.index("requires your review") < report.index("components")


def test_review_count_counts_generated_and_unrecorded_together():
    data = designed_r1("generated")
    data["design_notes"] = [
        {"item": "topology", "choice": "series pass",
         "rationale": "standard for this spec"},                 # unrecorded
        {"item": "load", "choice": "2k", "rationale": "as given",
         "rationale_origin": "human"},                           # exempt
    ]
    report = dry_run_report(loaded(data))
    assert "2 rationales require your review" in report


def test_no_review_line_when_everything_is_human_written():
    data = designed_r1("human")
    report = dry_run_report(loaded(data))
    assert "require your review" not in report
    assert "requires your review" not in report


def test_device_policy_reports_do_not_count_as_rationales():
    # A DeviceChoice report is deterministic output derived from the
    # verified library, not model prose; it carries its policy tag and
    # needs no authorship review.
    # A plain diode with no spec resolves through path (c); its rationale
    # is the policy report. (diode and zener share anode/cathode pins,
    # so the reference nets still apply.)
    data = q1_question()
    data["circuit"]["components"][2] = {
        "ref": "D1", "type": "diode", "device": {}}
    report = dry_run_report(loaded(data))
    assert "1N4148" in report          # the default was picked and shown
    assert "require your review" not in report


def test_design_note_invalid_rationale_origin_rejected():
    data = q1_question()
    data["design_notes"] = [{"item": "x", "choice": "y", "rationale": "z",
                             "rationale_origin": "vibes"}]
    with pytest.raises(QuestionError, match="vibes"):
        loaded(data)


def test_rationale_origin_round_trips():
    data = designed_r1("generated")
    data["design_notes"] = [{"item": "x", "choice": "y", "rationale": "z",
                             "rationale_origin": "human"}]
    assert loaded(data).to_dict() == data


def test_origin_round_trips():
    data = q1_question()
    data["circuit"]["components"][1]["origin"] = "designed"
    data["circuit"]["components"][1]["rationale"] = "chosen for bias"
    assert loaded(data).to_dict() == data


# ------------------------------------------------- structured AC source
#
# "SINE(0 16.97 50)" as an opaque string hides the 12 V RMS -> 16.97 V
# peak conversion — the single most dangerous place for a misread,
# because an RMS/peak confusion simulates beautifully and is wrong by
# 41%. So the schema takes {rms, freq} and the conversion happens in
# code, displayed in the dry run.


def with_ac_source(ac):
    data = q1_question()
    data["circuit"]["components"][0] = {
        "ref": "V1", "type": "voltage", "ac": ac}
    # V1 no longer sweeps meaningfully; drop the dc run bits that
    # reference specific DC values to keep the fixture valid.
    return data


def test_sine_rms_converted_to_peak_in_code():
    q = loaded(with_ac_source({"kind": "sine", "rms": 12, "freq": 50}))
    v1 = next(c for c in q.circuit["components"] if c["ref"] == "V1")
    assert v1["value"] == "SINE(0 16.9706 50)"


def test_sine_amplitude_taken_verbatim():
    q = loaded(with_ac_source({"kind": "sine", "amplitude": 17, "freq": 50}))
    v1 = next(c for c in q.circuit["components"] if c["ref"] == "V1")
    assert v1["value"] == "SINE(0 17 50)"


def test_sine_dry_run_shows_the_conversion():
    report = dry_run_report(
        loaded(with_ac_source({"kind": "sine", "rms": 12, "freq": 50})))
    assert "12 Vrms" in report
    assert "50 Hz" in report
    assert "16.97" in report  # the derived peak, visible for checking


def test_sine_rejects_rms_and_amplitude_together():
    with pytest.raises(QuestionError, match="rms"):
        loaded(with_ac_source(
            {"kind": "sine", "rms": 12, "amplitude": 17, "freq": 50}))


def test_sine_unknown_key_rejected():
    with pytest.raises(QuestionError, match="phase"):
        loaded(with_ac_source(
            {"kind": "sine", "rms": 12, "freq": 50, "phase": 90}))


def test_ac_source_round_trips():
    data = with_ac_source({"kind": "sine", "rms": 12, "freq": 50})
    assert loaded(data).to_dict() == data


# ------------------------------------------------- verbatim question text


def test_question_text_rendered_verbatim_at_top():
    report = dry_run_report(loaded())
    # The raw string, not the model's summary of it, and before
    # anything extraction-derived.
    assert '"Calculate the' in report
    assert report.index("Calculate the") < report.index("components")
    assert "% claimed by asks" in report


def test_unclaimed_question_words_are_visible():
    report = dry_run_report(loaded())
    # No ask claims "regulator circuit" or "LTspice"; they must be
    # listed as unclaimed rather than silently absorbed. Unclaimed is
    # display, not a warning — the human judges "fine" vs "dropped".
    assert "unclaimed:" in report
    assert "regulator circuit" in report[report.index("unclaimed:"):]


def test_dropping_ask_and_measurement_together_still_shows_in_text():
    # The self-audit hole: extractor drops "load regulation" from BOTH
    # asks and measurements. asks-vs-measurements coverage reads clean;
    # the words must surface as unclaimed question text instead.
    data = q1_question()
    data["asks"] = [a for a in data["asks"]
                    if a["text"] != "load regulation"]
    ms = data["analysis"]["measurements"]
    data["analysis"]["measurements"] = [
        m for m in ms if m.get("name") not in
        ("vout_noload", "vout_fullload", "load_reg_pct")
    ]
    report = dry_run_report(loaded(data))
    unclaimed = report[report.index("unclaimed:"):report.index("warnings")]
    assert "load" in unclaimed


def test_paraphrased_ask_warns():
    # An ask whose words do not appear in the question text was
    # paraphrased or invented by the extractor.
    data = q1_question()
    data["asks"].append(
        {"text": "ripple factor", "answered_by": "vout_nominal"})
    q = loaded(data)
    assert any("ripple factor" in w and "verbatim" in w
               for w in q.warnings)


def test_question_text_survives_round_trip():
    data = q1_question()
    assert loaded(data).to_dict()["question"] == data["question"]


# ------------------------------------------------------- source provenance


def test_source_block_accepted_and_rendered():
    data = q1_question()
    data["source"] = {
        "file": "q1.png",
        "resolution": "1080x2400",
        "question_chars": 214,
        "extractor": "claude-sonnet-4-6",
        "attempts": 2,
        "confidence": {"R1": "low"},
        "annotations_unused": ["'ambient 25C' near title block"],
    }
    q = loaded(data)
    report = dry_run_report(q)
    assert "q1.png (1080x2400)" in report
    assert "claude-sonnet-4-6" in report
    assert "low confidence on R1" in report
    assert "ambient 25C" in report  # seen-but-unused must be surfaced
    assert q.to_dict() == data     # survives the round trip


def test_source_block_unknown_key_rejected():
    data = q1_question()
    data["source"] = {"file": "q1.png", "dpi": 300}
    with pytest.raises(QuestionError, match="dpi"):
        loaded(data)


# ----------------------------------------------------------- round trip


def test_round_trip_preserves_the_input():
    data = q1_question()
    assert loaded(data).to_dict() == data


def test_round_trip_is_not_the_same_object():
    data = q1_question()
    q = loaded(data)
    data["circuit"]["components"][0]["value"] = "999"
    assert q.to_dict() != data  # to_dict rebuilt from parsed state


# ------------------------------------------------------------ dry run


def test_dry_run_report_shape():
    report = dry_run_report(loaded())
    # Devices with their policy tag.
    assert "D1" in report and "[synth" in report
    assert "anchored" in report
    # Nets, runs with labels, measurements with formulas.
    assert "vb" in report
    assert "line regulation" in report
    assert "100 * (vout_high - vout_low) / vout_low" in report
    assert "no-load to full-load" in report
    # Regime assertions visible.
    assert "zener_in_breakdown" in report
    # The gate's verdict, and the promise not to simulate.
    assert "OK" in report
    assert "warnings" in report


def test_dry_run_report_shows_warnings():
    data = q1_question()
    data["circuit"]["components"][1]["value"] = "1.8Meg"
    report = dry_run_report(loaded(data))
    assert "1.8Meg" in report or "R1" in report
