"""The library manifest contract.

This file IS the spec for ohmwork/library.py, the way test_analysis.py's
docstring is the spec for the analysis layer.

The manifest is a PUBLISHED contract, not an internal dump: a static site
serves it to a student who cannot re-run anything. So the tests here are
mostly about what the format REFUSES to publish -- a number with no backend,
an unreliable number with no reason, an unverified file with no explanation,
a designed value with no rationale. Each of those would render on a page as
though it were as solid as an LTspice operating point, and none of them is.
"""

import json
from pathlib import Path

import pytest

from ohmwork.analysis import Measurement
from ohmwork.library import (
    INDEX_NAME,
    MANIFEST_NAME,
    MANIFEST_VERSION,
    Deliverable,
    ManifestError,
    build_index,
    build_manifest,
    validate_manifest,
    write_manifest,
)
from ohmwork.question import load_question

EXAMPLES = Path(__file__).parent.parent / "examples"


# --------------------------------------------------------------- helpers

def q3_question():
    """A real question with designed values, a tran run, and a .plt."""
    return load_question(json.loads((EXAMPLES / "q3.json").read_text(encoding="utf-8")))


def a_measurement(**over):
    base = dict(name="vout_nominal", value=7.484, run="op_nominal",
                backend="ltspice-26.0.2.1", source="simulation",
                verification="external")
    base.update(over)
    return Measurement(**base)


def build(tmp_path, question=None, results=None, deliverables=None,
          prose=None):
    """Build a manifest with real files on disk so hashing is real."""
    question = question or q3_question()
    if results is None:
        results = {"vout_nominal": a_measurement()}
    if deliverables is None:
        (tmp_path / "q3.asc").write_text("Version 4.1\n")
        deliverables = [Deliverable(
            "q3.asc", "ltspice-schematic", True,
            verified_by="geometric round trip; simulated from the same "
                        "circuit description")]
    return build_manifest(
        question, results, deliverables,
        question_id="exp03-regulated-supply", backend="ltspice-26.0.2.1",
        generated="2026-08-24", out_dir=tmp_path, prose_entries=prose,
    )


def put(library, manifest):
    """Write a manifest into its own question directory, as the layout requires."""
    directory = library / manifest["question_id"]
    directory.mkdir(parents=True, exist_ok=True)
    write_manifest(manifest, directory / MANIFEST_NAME)
    return directory


# ------------------------------------------------------- what it carries

def test_manifest_round_trips_through_json(tmp_path):
    manifest = build(tmp_path)
    path = tmp_path / "exp3.json"
    write_manifest(manifest, path)
    assert json.loads(path.read_text(encoding="utf-8")) == manifest


def test_regenerating_an_unchanged_question_is_byte_identical(tmp_path):
    """`generated` is passed in, not read from the clock.

    A manifest that changes on every run cannot be diffed, and a library
    whose files churn cannot be reviewed.
    """
    first, second = build(tmp_path), build(tmp_path)
    assert first == second
    write_manifest(first, tmp_path / "a.json")
    write_manifest(second, tmp_path / "b.json")
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_carries_the_verbatim_question_text(tmp_path):
    manifest = build(tmp_path)
    assert manifest["question"]["text"]
    assert "6.2" in manifest["question"]["text"]


def test_carries_device_choices_with_their_policy_path(tmp_path):
    # WHICH path chose a part is the point: named / synthesized / nearest.
    manifest = build(tmp_path)
    assert manifest["devices"]
    for ref, device in manifest["devices"].items():
        assert device["policy"] in {"named", "synthesized", "nearest"}
        assert device["report"], ref


def test_carries_designed_values_with_rationale_authorship(tmp_path):
    # q3's RS=220 is the tool's engineering judgement, with a generated
    # rationale. Publishing it as though the question stated it would submit
    # our judgement as the student's own.
    manifest = build(tmp_path)
    designed = {d["ref"]: d for d in manifest["designed_values"]}
    assert "RS" in designed
    assert designed["RS"]["origin"] == "designed"
    assert designed["RS"]["rationale"]
    assert designed["RS"]["rationale_origin"] == "generated"


def test_every_result_names_what_computed_it(tmp_path):
    manifest = build(tmp_path)
    for result in manifest["results"]:
        assert result["backend"]
        assert result["verification"] in {"external", "internal"}
        assert "reliable" in result


def test_deliverables_are_hashed(tmp_path):
    """So the viewer can prove the file it serves is the file that was run.

    Without the hash the manifest describes a run of something, and nobody
    can tell whether it is the download sitting beside it.
    """
    manifest = build(tmp_path)
    entry = manifest["deliverables"][0]
    assert len(entry["sha256"]) == 64
    import hashlib
    assert entry["sha256"] == hashlib.sha256(
        (tmp_path / "q3.asc").read_bytes()).hexdigest()


# ------------------------------------------------ what it refuses to publish

def test_unknown_key_is_rejected_with_a_path(tmp_path):
    manifest = build(tmp_path)
    manifest["confidence"] = 0.9
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "manifest: unknown key(s) ['confidence']" in str(excinfo.value)


def test_missing_verbatim_question_text_is_rejected(tmp_path):
    manifest = build(tmp_path)
    manifest["question"]["text"] = ""
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "VERBATIM" in str(excinfo.value)


def test_result_without_a_backend_is_rejected(tmp_path):
    manifest = build(tmp_path)
    manifest["results"][0]["backend"] = ""
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "must name what computed it" in str(excinfo.value)


def test_unreliable_result_without_a_reason_is_rejected(tmp_path):
    # A violated regime marks a measurement unreliable. Dropout data is
    # pedagogically interesting and should be SHOWN FLAGGED, never hidden --
    # but never shown flagged with no explanation either.
    manifest = build(tmp_path, results={
        "vout": a_measurement(reliable=False, warnings=("dropout",)),
    })
    manifest["results"][0]["warnings"] = []          # strip the reason
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "unreliable" in str(excinfo.value)


def test_unreliable_result_with_a_reason_is_fine(tmp_path):
    manifest = build(tmp_path, results={
        "vout": a_measurement(
            reliable=False,
            warnings=("zener out of breakdown at RL=500: I(D1)=0.1 mA",)),
    })
    validate_manifest(manifest)
    assert manifest["results"][0]["warnings"]


def test_unverified_deliverable_must_say_why(tmp_path):
    """The .plt rule, enforced in the format.

    Batch mode does not read plot files, so nothing can machine-check one.
    An artefact with no verification path must state that wherever it ships,
    and the manifest is where it ships to the site.
    """
    (tmp_path / "q3.asc").write_text("Version 4.1\n")
    (tmp_path / "q3.plt").write_text("[Transient Analysis]\n")
    with pytest.raises(ManifestError) as excinfo:
        build(tmp_path, deliverables=[
            Deliverable("q3.asc", "ltspice-schematic", True,
                        verified_by="geometric round trip"),
            Deliverable("q3.plt", "ltspice-plot", False),
        ])
    assert "deliverables[1]" in str(excinfo.value)
    assert "must state why" in str(excinfo.value)


def test_verified_deliverable_must_say_how(tmp_path):
    """"verified: true" with no explanation overstates what we did.

    The deliverable .asc carries the whole experiment with one run active;
    the bytes LTspice actually ran were the per-run scratch files. It has a
    real machine check -- the geometric round trip -- but that is a different
    claim, and the format makes you write which one you mean.
    """
    (tmp_path / "q3.asc").write_text("Version 4.1\n")
    with pytest.raises(ManifestError) as excinfo:
        build(tmp_path, deliverables=[
            Deliverable("q3.asc", "ltspice-schematic", True),
        ])
    assert "must say HOW" in str(excinfo.value)


def test_unverified_deliverable_with_a_reason_publishes(tmp_path):
    (tmp_path / "q3.asc").write_text("Version 4.1\n")
    (tmp_path / "q3.plt").write_text("[Transient Analysis]\n")
    manifest = build(tmp_path, deliverables=[
        Deliverable("q3.asc", "ltspice-schematic", True,
                    verified_by="geometric round trip"),
        Deliverable("q3.plt", "ltspice-plot", False,
                    unverified_reason=
                    "batch mode does not read .plt; transcribed from "
                    "LTspice's own examples, pending one visual confirmation"),
    ])
    plot = [d for d in manifest["deliverables"] if d["kind"] == "ltspice-plot"][0]
    assert plot["verified"] is False
    assert "batch mode" in plot["unverified_reason"]


def test_bad_device_policy_is_rejected(tmp_path):
    manifest = build(tmp_path)
    ref = next(iter(manifest["devices"]))
    manifest["devices"][ref]["policy"] = "probably fine"
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "never silently pick" in str(excinfo.value)


def test_designed_value_without_a_rationale_is_rejected(tmp_path):
    manifest = build(tmp_path)
    designed = next(d for d in manifest["designed_values"]
                    if d["origin"] == "designed")
    designed["rationale"] = None
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "requires a rationale" in str(excinfo.value)


def test_version_mismatch_is_rejected(tmp_path):
    # The published contract. A viewer reading a version it does not know
    # must be told, not left to guess which keys moved.
    manifest = build(tmp_path)
    manifest["manifest_version"] = MANIFEST_VERSION + 1
    with pytest.raises(ManifestError):
        validate_manifest(manifest)


# ---------------------------------------------------------------- index

def test_index_lists_solved_questions_and_flags_weak_results(tmp_path):
    """The viewer loads this first. Anything not in it is "not solved yet".

    It carries the two flags a reader needs before trusting a page: whether
    any result came from our own evaluator rather than an outside simulator,
    and whether any is marked unreliable.
    """
    library = tmp_path / "library"
    (tmp_path / "q3.asc").write_text("Version 4.1\n")

    put(library, build(tmp_path))

    internal = build(tmp_path, results={
        "truth_table": a_measurement(
            name="truth_table", backend="ohmwork-logic", verification="internal",
            warnings=("evaluated by ohmwork's own logic engine; no outside "
                      "tool checked this",)),
    })
    internal["question_id"] = "exp08-priority-encoder"
    put(library, internal)

    index = build_index(library)
    assert index["manifest_version"] == MANIFEST_VERSION
    by_id = {q["question_id"]: q for q in index["questions"]}
    assert set(by_id) == {"exp03-regulated-supply", "exp08-priority-encoder"}
    assert by_id["exp03-regulated-supply"]["has_internal_results"] is False
    assert by_id["exp08-priority-encoder"]["has_internal_results"] is True
    assert by_id["exp08-priority-encoder"]["path"] == (
        "exp08-priority-encoder/manifest.json")


def test_index_validates_every_manifest_it_lists(tmp_path):
    # A library that indexes a broken manifest publishes it.
    library = tmp_path / "library"
    (library / "exp99-broken").mkdir(parents=True)
    (library / "exp99-broken" / MANIFEST_NAME).write_text('{"manifest_version": 1}')
    with pytest.raises(ManifestError):
        build_index(library)


def test_index_ignores_its_own_file(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    (tmp_path / "q3.asc").write_text("Version 4.1\n")
    put(library, build(tmp_path))
    (library / INDEX_NAME).write_text(json.dumps(build_index(library)))
    assert len(build_index(library)["questions"]) == 1


def test_directory_name_must_match_the_question_id(tmp_path):
    """The directory name IS the published slug.

    A mismatch means saved links resolve to the wrong question, which is the
    kind of breakage nobody notices until someone follows an old URL.
    """
    library = tmp_path / "library"
    (tmp_path / "q3.asc").write_text("Version 4.1\n")
    wrong = library / "exp07-somewhere-else"
    wrong.mkdir(parents=True)
    write_manifest(build(tmp_path), wrong / MANIFEST_NAME)
    with pytest.raises(ManifestError) as excinfo:
        build_index(library)
    assert "does not match question_id" in str(excinfo.value)


@pytest.mark.parametrize("bad", ["q3", "exp3-x", "Exp03-X", "exp03_regulated",
                                 "exp03-", "regulated-supply", ""])
def test_bad_slugs_are_rejected(tmp_path, bad):
    # The slug becomes the URL and is stable forever once published, so it is
    # validated at write time rather than discovered after someone links to it.
    manifest = build(tmp_path)
    manifest["question_id"] = bad
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "not a valid slug" in str(excinfo.value)


def test_good_slugs_are_accepted(tmp_path):
    manifest = build(tmp_path)
    for good in ["exp02-series-regulator", "exp03-regulated-supply",
                 "exp08-priority-encoder", "exp09-bcd-seven-segment"]:
        manifest["question_id"] = good
        validate_manifest(manifest)


def test_deliverable_paths_are_relative_to_the_question_directory(tmp_path):
    """So a manifest plus its folder is self-contained.

    It can be moved, mirrored, or served from any prefix without rewriting
    paths -- which matters because the site is static and the folder IS the
    unit of publication.
    """
    manifest = build(tmp_path)
    for item in manifest["deliverables"]:
        assert "/" not in item["path"] and "\\" not in item["path"]
        assert not Path(item["path"]).is_absolute()


# ------------------------------- digital results must be externally verified

def test_internal_result_cannot_be_published_silently(tmp_path):
    """Logisim gives digital results external standing, so internal is second class.

    Before the Logisim CLI spike, an internal result was the ONLY option for
    digital and the asymmetry had to be accepted. It is now a fallback, and
    publishing one as though nothing were different would hide exactly the
    distinction the spike bought us.
    """
    manifest = build(tmp_path, results={
        "truth_table": a_measurement(name="truth_table",
                                     backend="ohmwork-logic",
                                     verification="internal",
                                     warnings=("no Logisim available",)),
    })
    manifest["results"][0]["warnings"] = []          # strip the declaration
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "our own evaluator" in str(excinfo.value)
    assert "regenerate with it installed" in str(excinfo.value)


def test_internal_result_publishes_when_declared(tmp_path):
    manifest = build(tmp_path, results={
        "truth_table": a_measurement(
            name="truth_table", backend="ohmwork-logic", verification="internal",
            warnings=("evaluated by ohmwork's own logic engine because Logisim "
                      "was not available; no outside tool checked this",)),
    })
    validate_manifest(manifest)
    assert manifest["results"][0]["verification"] == "internal"


def test_an_externally_verified_digital_result_needs_no_warning(tmp_path):
    # The normal case once Logisim is installed.
    manifest = build(tmp_path, results={
        "truth_table": a_measurement(name="truth_table",
                                     backend="logisim-evolution",
                                     verification="external"),
    })
    validate_manifest(manifest)
    assert manifest["results"][0]["warnings"] == []


# ------------------------------------------ an unrun check announces itself

def test_skipped_checks_are_published_not_just_held(tmp_path):
    """A quiet page is not necessarily a clean one.

    The rule (CLAUDE.md, "An unrun check must announce itself"): a check that
    can be skipped must say so IN THE OUTPUT. The manifest is the output the
    site serves, so it carries them.
    """
    data = json.loads((EXAMPLES / "q3.json").read_text(encoding="utf-8"))
    stripped = {k: v for k, v in data.items() if k not in ("asks", "analysis")}
    question = load_question(stripped)
    assert question.skipped, "removing asks and analysis must skip checks"

    (tmp_path / "q3.asc").write_text("Version 4.1\n")
    manifest = build(tmp_path, question=question)
    names = {c["name"] for c in manifest["checks_skipped"]}
    assert "ask coverage" in names
    assert "analysis plan validation" in names
    assert all(c["reason"] for c in manifest["checks_skipped"])


def test_a_skipped_check_with_no_reason_is_rejected(tmp_path):
    manifest = build(tmp_path)
    manifest["checks_skipped"] = [{"name": "ask coverage", "reason": ""}]
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "must say WHY" in str(excinfo.value)


def test_index_flags_entries_with_skipped_checks(tmp_path):
    library = tmp_path / "library"
    (tmp_path / "q3.asc").write_text("Version 4.1\n")

    clean = build(tmp_path)
    assert clean["checks_skipped"] == []
    put(library, clean)
    assert build_index(library)["questions"][0]["has_skipped_checks"] is False

    data = json.loads((EXAMPLES / "q3.json").read_text(encoding="utf-8"))
    quiet = build(tmp_path, question=load_question(
        {k: v for k, v in data.items() if k != "asks"}))
    quiet["question_id"] = "exp07-unexamined"
    put(library, quiet)
    by_id = {q["question_id"]: q for q in build_index(library)["questions"]}
    assert by_id["exp07-unexamined"]["has_skipped_checks"] is True


# ------------------------------------------------- tables, regimes, prose

def a_table(verification="external"):
    return Measurement(
        name="truth_table", value=None, run="exhaustive",
        backend="logisim-evolution", source="simulation",
        verification=verification,
        table={"columns": ["EN", "Y"], "rows": [[0, 0], [1, 1]], "notes": []},
    )


def test_a_table_result_publishes_its_rows(tmp_path):
    manifest = build(tmp_path, results={"truth_table": a_table()})
    entry = manifest["results"][0]
    assert entry["value"] is None
    assert entry["table"]["rows"] == [[0, 0], [1, 1]]
    assert entry["verification"] == "external"


def test_a_result_with_neither_value_nor_table_is_refused(tmp_path):
    manifest = build(tmp_path, results={"truth_table": a_table()})
    manifest["results"][0].pop("table")
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "no value and no table" in str(excinfo.value)


def test_a_regime_check_must_say_what_it_examined(tmp_path):
    """A passing check that says nothing is indistinguishable from one
    nobody ran -- the same rule as checks_skipped, one level down."""
    manifest = build(tmp_path)
    manifest["regime_checks"] = [{
        "assertion": "no_floating_inputs", "run": "exhaustive",
        "device": None, "held": True, "examined": "", "reasons": []}]
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "must say WHAT it examined" in str(excinfo.value)


def test_a_violated_regime_must_give_a_reason(tmp_path):
    manifest = build(tmp_path)
    manifest["regime_checks"] = [{
        "assertion": "no_floating_inputs", "run": "exhaustive",
        "device": None, "held": False, "examined": "20 input ports",
        "reasons": []}]
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "no reason" in str(excinfo.value)


def a_prose_entry(**over):
    base = {
        "text": "Discuss how it behaves", "tier": "prose_from_results",
        "quoted_notes": [],
        "evidence": [{
            "label": "enable off", "measurement": "truth_table",
            "columns": ["EN", "Y"], "rows": [[0, 0]], "total_rows": 2,
            "backend": "logisim-evolution", "verification": "external"}],
        "answer": None, "answer_authorship": None,
        "answer_freshness": None,
    }
    base.update(over)
    return base


def test_prose_evidence_publishes_the_rows_and_their_standing(tmp_path):
    """The site is a dumb viewer: the selection happens in the generator,
    where the evidence is, so the page cannot select different rows than
    the ones the caption was written over."""
    manifest = build(tmp_path, prose=[a_prose_entry()])
    group = manifest["prose"][0]["evidence"][0]
    assert group["rows"] == [[0, 0]]
    assert group["verification"] == "external"
    assert group["backend"] == "logisim-evolution"


def test_prose_evidence_with_no_backend_is_refused(tmp_path):
    manifest = build(tmp_path, prose=[a_prose_entry()])
    manifest["prose"][0]["evidence"][0]["backend"] = ""
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "weakest link" in str(excinfo.value)


def test_a_prose_answer_with_no_authorship_is_refused(tmp_path):
    manifest = build(tmp_path, prose=[
        a_prose_entry(answer="It behaves well.",
                      answer_authorship="[human-written]",
                      answer_freshness="fresh")])
    manifest["prose"][0]["answer_authorship"] = None
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "never assumed human" in str(excinfo.value)


def test_index_counts_prose_and_flags_ungrounded(tmp_path):
    library = tmp_path / "library"
    (tmp_path / "q3.asc").write_text("Version 4.1\n")
    manifest = build(tmp_path, prose=[
        a_prose_entry(),
        a_prose_entry(text="Speculate", tier="prose_free", evidence=[]),
    ])
    put(library, manifest)
    entry = build_index(library)["questions"][0]
    assert entry["prose_ask_count"] == 2
    assert entry["has_ungrounded_prose"] is True


def test_a_published_caption_must_say_whether_it_still_fits_its_rows(tmp_path):
    """Silence on freshness reads as "fresh" and is indistinguishable from a
    check that never ran — the same failure as a silent skipped check, one
    level down."""
    manifest = build(tmp_path, prose=[
        a_prose_entry(answer="It behaves well.",
                      answer_authorship="[generated]",
                      answer_freshness="fresh")])
    manifest["prose"][0]["answer_freshness"] = None
    with pytest.raises(ManifestError) as excinfo:
        validate_manifest(manifest)
    assert "still describes the rows" in str(excinfo.value)


def test_index_flags_a_stale_or_unrecorded_caption(tmp_path):
    library = tmp_path / "library"
    (tmp_path / "q3.asc").write_text("Version 4.1\n")
    put(library, build(tmp_path, prose=[
        a_prose_entry(answer="Written for other rows.",
                      answer_authorship="[generated]",
                      answer_freshness="stale")]))
    assert build_index(library)["questions"][0]["has_stale_prose"] is True
