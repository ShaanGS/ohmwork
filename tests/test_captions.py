"""The caption generator: the only place a model writes into the output.

WHAT THIS MODULE IS AND IS NOT. Everything else in ohmwork is deterministic
Python over simulator output. This is the one seam where a language model
contributes text a student will read, so it is built to be as small and as
constrained as the job allows:

- **The generator sees ONLY the resolved evidence.** Not the circuit, not the
  netlist, not the full truth table — just the ask and the rows that were
  selected for it. That is what makes "check it against the rows above" a
  real instruction rather than a slogan: the model had nothing else to work
  from, so anything it says that the rows do not support is visible as such.
- **The call is behind a seam.** `generate_captions` takes a callable. The
  Anthropic client is one implementation; every test here uses a fake. No
  test in this repo touches the network, and none needs an API key.
- **Nothing is written anywhere automatically.** The caption comes back, gets
  fingerprinted against the rows it was written over, and goes into the
  question JSON only when a human is looking at it.

The refusal to let the generator near the circuit is the whole design. A
model that could see the netlist would write captions that are true of the
circuit rather than true of the printed rows, and the reader would have no
way to tell the difference.
"""

import pytest

from ohmwork import captions, prose
from ohmwork.analysis import Measurement

ASK = "Discuss how it behaves when the enable is low"


def table_measurement():
    return Measurement(
        name="truth_table", value=None, run="exhaustive",
        backend="logisim-evolution", source="simulation",
        verification="external",
        table={
            "columns": ["EN", "I1", "I0", "Y"],
            "rows": [[0, 0, 0, 0], [0, 1, 1, 0],
                     [1, 0, 1, 0], [1, 1, 0, 1], [1, 1, 1, 1]],
            "notes": [],
        },
    )


def asks():
    return [{"text": ASK, "kind": "prose", "prose": {
        "tier": "prose_from_results",
        "evidence": [{"label": "enable low", "measurement": "truth_table",
                      "select": {"equals": {"EN": 0}}}]}}]


def resolved():
    return prose.resolve_prose(
        asks(), {"truth_table": table_measurement()}, notes=[])


class Recorder:
    """A fake generator that records what it was shown."""

    def __init__(self, reply="Every disabled row reads Y = 0."):
        self.seen = []
        self.reply = reply

    def __call__(self, request):
        self.seen.append(request)
        return self.reply


# ------------------------------------------------- what the model may see


def test_the_generator_sees_the_ask_and_the_selected_rows():
    recorder = Recorder()
    captions.generate_captions(resolved(), recorder)
    request = recorder.seen[0]
    assert request.ask == ASK
    assert request.groups[0]["label"] == "enable low"
    assert request.groups[0]["rows"] == [[0, 0, 0, 0], [0, 1, 1, 0]]


def test_the_generator_is_shown_only_the_selected_rows_not_the_table():
    """Two of five rows were selected. If the generator could see all five it
    could write a caption true of the table and false of the evidence."""
    recorder = Recorder()
    captions.generate_captions(resolved(), recorder)
    assert len(recorder.seen[0].groups[0]["rows"]) == 2


def test_the_request_carries_no_circuit_and_no_netlist():
    """Stated as an assertion because it is the load-bearing constraint, and
    a future refactor that helpfully passes 'more context' would break the
    grounding claim without breaking anything else."""
    recorder = Recorder()
    captions.generate_captions(resolved(), recorder)
    fields = vars(recorder.seen[0])
    assert set(fields) == {"ask", "groups"}
    blob = repr(fields).lower()
    for leak in ("component", "net", "gate", "and2", "circuit"):
        assert leak not in blob


def test_the_generator_is_told_the_verification_standing_of_its_rows():
    """Prose over internally-computed rows should hedge differently from
    prose over externally-computed ones, so the model is told which it has."""
    recorder = Recorder()
    captions.generate_captions(resolved(), recorder)
    assert recorder.seen[0].groups[0]["verification"] == "external"
    assert recorder.seen[0].groups[0]["backend"] == "logisim-evolution"


# ---------------------------------------------------------- what comes back


def test_captions_are_keyed_by_ask_text():
    out = captions.generate_captions(resolved(), Recorder())
    assert out == {ASK: "Every disabled row reads Y = 0."}


def test_a_design_tier_ask_is_never_sent_to_the_generator():
    """Quoting the design notes IS the answer. Generating prose over them
    would replace a quotation with a paraphrase, which is strictly worse."""
    entries = [{"text": "Explain", "tier": "prose_from_design",
                "quoted_notes": [{"item": "a", "choice": "b",
                                  "rationale": "c", "authorship": "[human-written]"}],
                "evidence": []}]
    recorder = Recorder()
    assert captions.generate_captions(entries, recorder) == {}
    assert recorder.seen == []


def test_an_ask_that_already_has_a_fresh_answer_is_not_regenerated():
    """Regenerating would churn the library for no gain and would throw away
    text a human already reviewed."""
    entries = resolved()
    entries[0]["answer"] = "Already reviewed."
    entries[0]["answer_freshness"] = "fresh"
    recorder = Recorder()
    assert captions.generate_captions(entries, recorder) == {}
    assert recorder.seen == []


def test_a_stale_answer_IS_regenerated():
    entries = resolved()
    entries[0]["answer"] = "Written for other rows."
    entries[0]["answer_freshness"] = "stale"
    recorder = Recorder()
    assert captions.generate_captions(entries, recorder) == {ASK: recorder.reply}


def test_regenerate_all_overrides_the_fresh_check():
    entries = resolved()
    entries[0]["answer"] = "Already reviewed."
    entries[0]["answer_freshness"] = "fresh"
    out = captions.generate_captions(entries, Recorder(), regenerate=True)
    assert out == {ASK: "Every disabled row reads Y = 0."}


def test_an_empty_evidence_group_is_not_sent_to_the_generator():
    """No rows means nothing to caption. Asking anyway invites a confident
    sentence with nothing under it, which is the exact failure this design
    exists to prevent."""
    entries = resolved()
    entries[0]["evidence"][0]["rows"] = []
    recorder = Recorder()
    assert captions.generate_captions(entries, recorder) == {}
    assert recorder.seen == []


def test_a_generator_returning_nothing_is_not_recorded_as_an_answer():
    out = captions.generate_captions(resolved(), Recorder(reply="   "))
    assert out == {}


def test_a_generator_failure_names_the_ask_and_does_not_kill_the_run():
    """One caption failing must not lose a whole experiment's results."""
    def broken(request):
        raise RuntimeError("upstream said no")

    with pytest.warns(captions.CaptionWarning, match=ASK[:20]):
        out = captions.generate_captions(resolved(), broken)
    assert out == {}


# --------------------------------------------------------- the written form


def test_writing_an_answer_back_records_authorship_and_the_fingerprint():
    """The three facts that make a stored caption reviewable: the text, who
    wrote it, and which rows it was written over."""
    data = {"asks": asks()}
    entries = resolved()
    written = captions.apply_captions(
        data, entries, {ASK: "Every disabled row reads Y = 0."})
    spec = written["asks"][0]["prose"]
    assert spec["answer"] == "Every disabled row reads Y = 0."
    assert spec["answer_origin"] == "generated"
    assert spec["answer_evidence"] == prose.evidence_fingerprint(
        entries[0]["evidence"])


def test_applying_captions_does_not_mutate_the_input():
    data = {"asks": asks()}
    captions.apply_captions(data, resolved(), {ASK: "text"})
    assert "answer" not in data["asks"][0]["prose"]


def test_a_written_answer_reloads_as_fresh():
    """The round trip that matters: write it, load it back, and the staleness
    check must agree it belongs to these rows."""
    entries = resolved()
    written = captions.apply_captions(
        {"asks": asks()}, entries, {ASK: "Every disabled row reads Y = 0."})
    reloaded = prose.resolve_prose(
        written["asks"], {"truth_table": table_measurement()}, notes=[])
    assert reloaded[0]["answer_freshness"] == "fresh"
