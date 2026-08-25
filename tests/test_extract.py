"""The extraction layer: a photographed lab-manual page -> question JSON.

THIS IS THE MOST DANGEROUS COMPONENT IN THE PROJECT, and the tests are shaped
by that rather than by what is convenient to check.

Everywhere else, a mistake shows up: a bad netlist fails to converge, a wrong
pin fails the round trip, a wrong gate produces a wrong truth table. Here a
mistake produces a circuit that is WRONG BUT SELF-CONSISTENT. Read `1.8k` as
`1.8M` and the file emits, round-trips, converges, and reports a confident
number answering a question nobody asked. No downstream check can catch it,
because every downstream check verifies internal agreement and the extraction
is what internal agreement is measured against.

So the defences here are of a different kind:

1. **Transcribe, never summarise.** The verbatim question text is the one
   thing in this system that comes from outside it. CLAUDE.md incident 8: a
   paraphrase in examples/q3.json made ask coverage read 74% against the
   paraphrase and 59% against the real wording — the screen designed to
   reveal dropped work was quietly grading itself. A tidied question is not
   a question.
2. **The gate is the referee, not the model.** Output goes through
   `load_question` and its path-shaped errors, and a rejection is fed back
   for a bounded retry. The model does not get to decide whether its output
   is valid.
3. **The prompt's schema is GENERATED from the gate's own constants.** Hand
   writing the allowed keys into a prompt creates two sources of truth that
   drift apart silently, and the drift shows up as a model that is "wrong"
   about a schema that changed underneath it.
4. **Extract twice and diff.** Two independent passes disagreeing is the
   only signal available that the source was ambiguous. Agreement is not
   proof — but disagreement is proof of trouble, and it is free.

No test here touches the network.
"""

import json

import pytest

from ohmwork import extract
from ohmwork.llm import Reply

Q3_TEXT = (
    "Design and simulate a regulated 6.2 V DC power supply using LTspice. "
    "The circuit should consist of a bridge rectifier, a C-L-C smoothing "
    "filter with C = 470 uF and L = 1 mH."
)


def minimal_question(**over):
    """The smallest thing load_question accepts, as a model would emit it."""
    data = {
        "question": Q3_TEXT,
        "circuit": {
            "components": [
                {"ref": "V1", "type": "voltage", "value": "5"},
                {"ref": "R1", "type": "res", "value": "1k"},
            ],
            "nets": {"vin": ["V1.+", "R1.a"], "0": ["V1.-", "R1.b"]},
        },
    }
    data.update(over)
    return data


class FakeProvider:
    """Records prompts, returns canned replies in order."""

    name, model = "fake", "fake-model"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []
        self.images = []

    def complete(self, prompt, *, images=(), **kw):
        self.prompts.append(prompt)
        self.images.append(list(images))
        text = self.replies.pop(0) if self.replies else "{}"
        if not isinstance(text, str):
            text = json.dumps(text)
        return Reply(text=text, model=self.model, provider=self.name)


# ------------------------------------------------- the prompt's own schema


def test_the_prompt_lists_the_schema_the_gate_actually_enforces():
    """Two sources of truth for the schema is one too many.

    A prompt with the allowed keys typed into it drifts the moment the gate
    changes, and the symptom is a model that looks wrong about a schema that
    moved underneath it.
    """
    from ohmwork import question as gate

    schema = extract.schema_description("ltspice")
    for key in gate._TOP_KEYS:
        assert key in schema, f"{key} is accepted by the gate but absent from the prompt"
    for key in gate._COMPONENT_KEYS:
        assert key in schema


def test_the_prompt_lists_only_the_targets_own_component_types():
    """An LTspice extraction offered `and2` would produce a circuit the gate
    rejects, and the model would have had no way to know."""
    analog = extract.schema_description("ltspice")
    digital = extract.schema_description("logisim")
    assert "zener" in analog and "and2" not in analog
    assert "and2" in digital and "zener" not in digital


def test_the_prompt_forbids_summarising_in_so_many_words():
    """Incident 8 exists because a paraphrase looked entirely fine."""
    prompt = extract.build_prompt(Q3_TEXT, target="ltspice")
    lowered = prompt.lower()
    assert "verbatim" in lowered
    assert "do not summarise" in lowered or "do not summarize" in lowered
    assert Q3_TEXT in prompt


# ------------------------------------------------------ parsing the reply


def test_a_bare_json_object_parses():
    assert extract.parse_reply(json.dumps({"a": 1})) == {"a": 1}


def test_a_fenced_json_block_parses():
    """Models wrap JSON in fences no matter how firmly asked not to."""
    fenced = "Here you go:\n```json\n{\"a\": 1}\n```\nHope that helps."
    assert extract.parse_reply(fenced) == {"a": 1}


def test_unparseable_output_is_an_error_naming_what_came_back():
    with pytest.raises(extract.ExtractionError, match="not JSON"):
        extract.parse_reply("I'm afraid I can't do that.")


# ------------------------------------------------------- the retry loop


def test_a_valid_first_reply_needs_one_attempt():
    provider = FakeProvider(minimal_question())
    result = extract.extract(Q3_TEXT, provider=provider)
    result.data["question"] = Q3_TEXT
    assert result.attempts == 1
    assert len(provider.prompts) == 1


def test_a_rejected_reply_is_retried_WITH_the_gate_s_own_error():
    """The gate's path-shaped errors are the most useful thing the model
    could be told, so they are what it is told."""
    bad = minimal_question()
    bad["circuit"]["components"][0]["resistance"] = "oops"
    provider = FakeProvider(bad, minimal_question())

    result = extract.extract(Q3_TEXT, provider=provider)
    assert result.attempts == 2
    assert "resistance" in provider.prompts[1]
    assert "unknown key" in provider.prompts[1]


def test_retries_are_bounded_and_the_failure_names_the_last_error():
    bad = minimal_question()
    bad["circuit"]["components"][0]["resistance"] = "oops"
    provider = FakeProvider(bad, bad, bad, bad)
    with pytest.raises(extract.ExtractionError) as excinfo:
        extract.extract(Q3_TEXT, provider=provider, attempts=3)
    assert "3 attempts" in str(excinfo.value)
    assert "resistance" in str(excinfo.value)


def test_the_result_carries_the_model_that_produced_it():
    """Provenance, same as every other result in this project."""
    result = extract.extract(Q3_TEXT, provider=FakeProvider(minimal_question()))
    assert result.model == "fake-model"
    assert result.provider == "fake"


# --------------------------------------------- transcription is checked


def test_a_summarised_question_is_CORRECTED_and_the_fact_recorded():
    """Restoration, not rejection — and the difference matters.

    Rejecting would cost another call and could not do better than the
    authoritative text we are already holding. So the supplied wording simply
    wins, deterministically, and the fact that the model rewrote it is
    recorded as a warning: it says nothing about this run's output, but it
    says something about the model, and a reader deciding whether to trust an
    image-only extraction later should know it.
    """
    tidied = minimal_question(question="Design a regulated 6.2 V supply.")
    result = extract.extract(Q3_TEXT, provider=FakeProvider(tidied))
    assert result.data["question"] == Q3_TEXT
    assert result.attempts == 1                       # no wasted call
    assert any("altered the question wording" in w for w in result.warnings)


def test_supplied_text_is_restored_verbatim_rather_than_trusted():
    """Belt and braces: when the caller supplied the text, the extractor
    does not need the model's copy of it at all, and overwriting removes a
    whole class of silent corruption (a smart quote, a dropped unit)."""
    off_by_a_character = minimal_question(question=Q3_TEXT.replace("uF", "µF"))
    provider = FakeProvider(off_by_a_character)
    result = extract.extract(Q3_TEXT, provider=provider)
    assert result.data["question"] == Q3_TEXT


def test_an_image_only_extraction_keeps_the_models_transcription():
    """With no supplied text there is nothing to restore FROM, so the
    transcription is the model's — and that is exactly when a human has to
    read it against the picture. It is flagged for that."""
    from ohmwork.llm import Image

    provider = FakeProvider(minimal_question(question="Read from the image."))
    result = extract.extract(None, images=[Image(b"\x89PNG")], provider=provider)
    assert result.data["question"] == "Read from the image."
    assert any("transcription" in w.lower() for w in result.warnings)


# ------------------------------------------------------ the double pass


def test_two_agreeing_passes_report_no_disagreement():
    provider = FakeProvider(minimal_question(), minimal_question())
    result = extract.extract_twice(Q3_TEXT, provider=provider)
    assert result.disagreements == []
    assert result.attempts == 2


def test_a_dropped_ask_between_passes_is_reported():
    """The dominant vision failure is a DROP, and it leaves every
    extraction-driven screen looking perfect. Two passes disagreeing is the
    only free signal that the source was ambiguous."""
    with_asks = minimal_question(asks=[{"text": "Calculate the output voltage"}])
    without = minimal_question(asks=[])
    provider = FakeProvider(with_asks, without)

    result = extract.extract_twice(Q3_TEXT, provider=provider)
    assert result.disagreements
    assert any("Calculate the output voltage" in d for d in result.disagreements)


def test_a_differing_component_value_between_passes_is_reported():
    """1.8k vs 1.8M is the failure this whole layer is afraid of."""
    a = minimal_question()
    b = minimal_question()
    b["circuit"]["components"][1]["value"] = "1Meg"
    provider = FakeProvider(a, b)

    result = extract.extract_twice(Q3_TEXT, provider=provider)
    assert any("R1" in d and "1k" in d and "1Meg" in d
               for d in result.disagreements)


def test_disagreement_does_not_throw_away_the_extraction():
    """Two passes disagreeing means a human must look, not that there is
    nothing to look at. Hiding the result would remove the thing they need
    in order to judge."""
    b = minimal_question()
    b["circuit"]["components"][1]["value"] = "1Meg"
    provider = FakeProvider(minimal_question(), b)
    result = extract.extract_twice(Q3_TEXT, provider=provider)
    assert result.data["circuit"]["components"]


# --------------------------------------------------------- the source block


def test_the_source_block_records_how_the_extraction_happened():
    provider = FakeProvider(minimal_question())
    result = extract.extract(Q3_TEXT, provider=provider, source_file="exp3.png")
    source = result.data["source"]
    assert source["file"] == "exp3.png"
    assert source["extractor"] == "fake/fake-model"
    assert source["attempts"] == 1
    assert source["question_chars"] == len(Q3_TEXT)


def test_the_source_block_survives_the_gate():
    """It is schema-checked like everything else; a key the gate does not
    know would be rejected at load time rather than here."""
    from ohmwork.question import load_question

    provider = FakeProvider(minimal_question())
    result = extract.extract(Q3_TEXT, provider=provider, source_file="exp3.png")
    question = load_question(result.data)
    assert question.source["file"] == "exp3.png"
