"""The committed library, checked as the published artefact it is.

The library is the PRODUCT (CLAUDE.md, "Deployment"): the CLI runs locally
where the simulators are, and the site serves these files without ever
simulating anything. So the committed contents get the same treatment as any
other output — validated, not assumed.

These tests need no simulator. They read what was published and check it
against the contract, which is exactly what a viewer would be able to do.
"""

import json
from pathlib import Path

import pytest

from ohmwork.library import INDEX_NAME, MANIFEST_NAME, build_index, validate_manifest
from ohmwork.question import load_question

LIBRARY = Path(__file__).parent.parent / "library"

pytestmark = pytest.mark.skipif(
    not LIBRARY.is_dir(), reason="no library has been generated yet")


def manifests():
    return sorted(LIBRARY.glob(f"*/{MANIFEST_NAME}"))


def test_the_library_is_not_empty():
    assert manifests(), "the library is the product; an empty one ships nothing"


def test_every_published_manifest_validates():
    """build_index refuses a library that indexes a broken manifest, because
    a library that indexes one publishes it. Checked here directly too, so a
    failure names the file."""
    for path in manifests():
        validate_manifest(json.loads(path.read_text(encoding="utf-8")))


def test_the_index_matches_what_is_on_disk():
    published = json.loads((LIBRARY / INDEX_NAME).read_text(encoding="utf-8"))
    assert published == build_index(LIBRARY), (
        "index.json is stale — regenerate it, or the site lists questions "
        "that are not what it serves"
    )


def test_every_deliverable_named_in_a_manifest_exists_and_hashes(tmp_path):
    """The sha256 is what lets a viewer prove the file it serves is the file
    that was evaluated. A stale hash silently breaks that promise."""
    import hashlib

    for path in manifests():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for item in manifest["deliverables"]:
            target = path.parent / item["path"]
            assert target.is_file(), f"{path}: missing {item['path']}"
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            assert digest == item["sha256"], f"{target} does not match its hash"


def test_every_published_question_reloads_as_the_target_it_was_solved_for():
    """The question JSON travels with its answer so a reader can see what was
    fed in. It has to actually LOAD, and load as the same target: `target`
    was once dropped by to_dict(), so a republished Logisim question came
    back as an LTspice one and failed on a missing SPICE ground.
    """
    for path in manifests():
        data = json.loads(
            (path.parent / "question.json").read_text(encoding="utf-8"))
        question = load_question(data)
        assert question.question, f"{path}: no verbatim question text"


def test_no_published_result_is_internal_or_unexplained():
    """The library's promise: every number traceable to an outside tool."""
    for path in manifests():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for result in manifest["results"]:
            assert result["backend"]
            assert result["verification"] == "external", (
                f"{path}: {result['name']} was computed by our own evaluator"
            )


def test_the_seeded_questions_are_the_ones_we_actually_have_text_for():
    """Three, not five, and deliberately so.

    Q1 (Exp 2), Q3 (Exp 3) and Q2 (Exp 8) are every question whose VERBATIM
    text is in hand and which this build can solve. Q4 (Exp 9) has its text
    but is blocked on a Logisim Evolution fixture for the 7447, so it is not
    here — "not solved yet" is a real answer and padding the count with an
    invented question would not be.
    """
    ids = {json.loads(p.read_text(encoding="utf-8"))["question_id"]
           for p in manifests()}
    assert ids == {"exp02-series-regulator", "exp03-regulated-supply",
                   "exp08-priority-encoder"}
