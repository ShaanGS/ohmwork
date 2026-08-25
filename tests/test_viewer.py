"""The static viewer: this file is its spec.

WHAT THE VIEWER IS. The library is the product; the site is a dumb window
onto it. LTspice is a Windows GUI application and a hosted ohmwork cannot
simulate, so there is no backend, no database, and no model in the request
path. `build_site` turns a `library/` directory into a folder of plain HTML
that can be served from any static host — or opened from `file://`.

THE ONE RULE EVERYTHING ELSE FOLLOWS FROM: **the viewer adds no facts.**
Every claim on a page comes from a manifest field. It does not compute a
number, does not infer a verification status from a backend name, does not
decide that a result looks fine. If the manifest does not say it, the page
does not say it. This is what makes the page auditable against the manifest
sitting next to it — which is why the manifest is copied into the site.

The corollaries, each a test below:

1. It REFUSES rather than degrades. An invalid manifest, a missing
   deliverable, a deliverable whose bytes no longer match the sha256 the
   manifest published, or a stale index — each stops the build with a
   path-shaped error. A site that quietly serves a file the manifest does
   not describe has broken the only promise the manifest makes.

2. An unrun check announces itself. `checks_skipped` renders with its reason.
   And the mirror: a question with NO skipped checks says so in words, and
   regime checks that HELD are rendered with what they examined. A quiet page
   must never be indistinguishable from an unexamined one — that is the
   project's oldest rule and the site is where it reaches an audience.

3. Second-class results carry their label where the number is, not in a
   footnote: `internal` verification, `reliable: false`, an unverified
   deliverable's reason, and prose freshness. STALE prose renders its warning
   BEFORE the sentence, because a reader who has already read the sentence
   has already been misled.

4. Deterministic. Building twice produces identical bytes. A site that churns
   cannot be reviewed, and neither can its diff.

5. Self-contained. No CDN, no external font, no fetch(). A page that needs
   the network to render is a page that can render differently tomorrow.
"""

import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest

from ohmwork.library import INDEX_NAME, MANIFEST_NAME, build_index
from ohmwork.viewer import ViewerError, build_site

LIBRARY = Path(__file__).parent.parent / "library"

pytestmark = pytest.mark.skipif(
    not LIBRARY.is_dir(), reason="no library has been generated yet")


@pytest.fixture
def library(tmp_path):
    """A writable copy of the real committed library.

    Real data on purpose: a viewer tested only against a hand-made minimal
    manifest is tested against the manifest we imagined, not the one we
    publish.
    """
    dest = tmp_path / "library"
    shutil.copytree(LIBRARY, dest)
    return dest


@pytest.fixture
def site(library, tmp_path):
    out = tmp_path / "site"
    build_site(library, out)
    return out


def page(site, slug):
    return (site / slug / "index.html").read_text(encoding="utf-8")


def manifest_of(library, slug):
    return json.loads(
        (library / slug / MANIFEST_NAME).read_text(encoding="utf-8"))


def rewrite(library, slug, manifest):
    (library / slug / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")


# --------------------------------------------------------------- shape

def test_one_page_per_question_plus_an_index(site, library):
    assert (site / "index.html").is_file()
    for entry in build_index(library)["questions"]:
        assert (site / entry["question_id"] / "index.html").is_file()


def test_the_manifest_travels_with_its_page(site, library):
    """The page is a rendering; the manifest is the record. Serving the page
    without the record makes the rendering unauditable."""
    for entry in build_index(library)["questions"]:
        slug = entry["question_id"]
        assert (site / slug / MANIFEST_NAME).is_file()
        assert (site / slug / "question.json").is_file()
        assert manifest_of(site, slug) == manifest_of(library, slug)


def test_every_deliverable_is_served_and_still_hashes(site, library):
    for entry in build_index(library)["questions"]:
        slug = entry["question_id"]
        for item in manifest_of(library, slug)["deliverables"]:
            served = site / slug / item["path"]
            assert served.is_file(), f"{slug}: {item['path']} not served"
            digest = hashlib.sha256(served.read_bytes()).hexdigest()
            assert digest == item["sha256"], (
                f"{slug}: the served bytes are not the bytes the manifest "
                f"describes")


def test_links_are_relative_so_the_site_serves_from_any_prefix(site):
    """The folder is the unit of publication. A page that hard-codes a root
    path cannot be mirrored, and cannot be opened from file://."""
    html = (site / "index.html").read_text(encoding="utf-8")
    assert not re.search(r'href="/', html)
    assert not re.search(r'src="/', html)


def test_nothing_is_loaded_from_the_network(site, library):
    pages = [(site / "index.html").read_text(encoding="utf-8")]
    pages += [page(site, e["question_id"])
              for e in build_index(library)["questions"]]
    for html in pages:
        for pattern in ("http://", "https://", "//cdn", "fetch(", "XMLHttpRequest"):
            assert pattern not in html, f"page reaches outside itself: {pattern}"


def test_building_twice_is_byte_identical(library, tmp_path):
    """`generated` is passed in rather than read from the clock everywhere
    else in this project, for the same reason."""
    first, second = tmp_path / "a", tmp_path / "b"
    build_site(library, first)
    build_site(library, second)
    for path in sorted(first.rglob("*")):
        if path.is_file():
            twin = second / path.relative_to(first)
            assert twin.read_bytes() == path.read_bytes(), f"{path} churned"


# ------------------------------------------------- it adds no facts

def test_the_page_renders_the_manifest_not_a_remembered_number(site, library,
                                                               tmp_path):
    """Change a published value, rebuild, and the page must change with it.

    This is the cheap test that the page is a rendering of the manifest
    rather than of anything else. A viewer that pulled numbers from a second
    source would pass every other test here.
    """
    slug = "exp02-series-regulator"
    manifest = manifest_of(library, slug)
    scalar = next(r for r in manifest["results"] if r["value"] is not None)
    before = build_site(library, tmp_path / "before") and page(
        tmp_path / "before", slug)

    scalar["value"] = 1.2345678
    rewrite(library, slug, manifest)
    build_site(library, tmp_path / "after")
    after = page(tmp_path / "after", slug)

    assert "1.2345678" in after
    assert after != before


def test_verification_is_rendered_from_the_field_not_the_backend_name(
        library, tmp_path):
    """An internal result must be labelled internal even when the backend it
    names is one that CAN be external. Inferring the label from the backend
    would silently upgrade the weakest results in the project."""
    slug = "exp08-priority-encoder"
    manifest = manifest_of(library, slug)
    # The manifest contract will not publish an internal result without a
    # warning saying so, so a viewer can never be handed a silent one. Both
    # halves are required here: the contract refuses the silence, the viewer
    # renders the label.
    manifest["results"][0]["verification"] = "internal"
    manifest["results"][0]["warnings"] = ["Logisim was unavailable"]
    rewrite(library, slug, manifest)

    build_site(library, tmp_path / "site")
    html = page(tmp_path / "site", slug)
    assert "INTERNAL" in html
    assert "own evaluator" in html


def test_an_unreliable_result_says_so_where_the_number_is(library, tmp_path):
    slug = "exp02-series-regulator"
    manifest = manifest_of(library, slug)
    target = manifest["results"][0]
    target["reliable"] = False
    target["warnings"] = ["the zener left breakdown at the 500R load point"]
    rewrite(library, slug, manifest)

    build_site(library, tmp_path / "site")
    html = page(tmp_path / "site", slug)
    assert "UNRELIABLE" in html
    assert "the zener left breakdown at the 500R load point" in html


# ------------------------------------ checks: run, skipped, and held

def test_a_skipped_check_is_named_with_its_reason(site):
    """exp08 defers the geometric round trip. The page must say which check
    did not run and what stands in its place — a reader who saw only a clean
    page would conclude the opposite of the truth."""
    html = page(site, "exp08-priority-encoder")
    assert "geometric round trip" in html
    assert "SKIPPED" in html


def test_a_question_with_no_skipped_checks_says_so_in_words(site):
    """The mirror of the rule. Omitting the section leaves a clean page
    indistinguishable from a page where nothing was examined."""
    html = page(site, "exp02-series-regulator")
    assert "no checks were skipped" in html.lower()


def test_regime_checks_that_held_are_rendered_with_what_they_examined(site,
                                                                      library):
    """A check whose only output is a warning is invisible when it passes."""
    slug = "exp08-priority-encoder"
    html = page(site, slug)
    for check in manifest_of(library, slug)["regime_checks"]:
        assert check["assertion"] in html
        assert check["examined"] in html


def test_a_violated_regime_is_loud(library, tmp_path):
    slug = "exp08-priority-encoder"
    manifest = manifest_of(library, slug)
    manifest["regime_checks"][0]["held"] = False
    manifest["regime_checks"][0]["reasons"] = ["I0 is not driven"]
    rewrite(library, slug, manifest)

    build_site(library, tmp_path / "site")
    html = page(tmp_path / "site", slug)
    assert "VIOLATED" in html
    assert "I0 is not driven" in html


# ----------------------------------------------------- deliverables

def test_a_verified_deliverable_states_HOW_not_just_that(site, library):
    slug = "exp08-priority-encoder"
    html = page(site, slug)
    item = manifest_of(library, slug)["deliverables"][0]
    assert item["verified_by"] in html


def test_an_unverified_deliverable_carries_its_reason_at_the_link(site,
                                                                  library):
    """The .plt has no machine check of any kind. That must be visible
    wherever the file ships, and this is where it ships to a reader."""
    slug = "exp03-regulated-supply"
    manifest = manifest_of(library, slug)
    plot = next(d for d in manifest["deliverables"] if not d["verified"])
    html = page(site, slug)
    assert "UNVERIFIED" in html
    assert plot["unverified_reason"] in html


def test_the_sha256_is_published_beside_the_download(site, library):
    slug = "exp02-series-regulator"
    html = page(site, slug)
    for item in manifest_of(library, slug)["deliverables"]:
        assert item["sha256"] in html


# ------------------------------------------------------------ prose

def test_prose_renders_its_tier_and_its_authorship(site, library):
    slug = "exp08-priority-encoder"
    html = page(site, slug)
    for entry in manifest_of(library, slug)["prose"]:
        assert entry["text"] in html
        if entry.get("answer_authorship"):
            assert entry["answer_authorship"] in html


def test_evidence_rows_render_with_the_status_they_inherit(site, library):
    """Grounding is a chain and its weakest link must be visible. Prose over
    Logisim rows is weaker than prose over LTspice rows and must not look
    equally solid."""
    slug = "exp08-priority-encoder"
    html = page(site, slug)
    groups = [g for e in manifest_of(library, slug)["prose"]
              for g in e["evidence"]]
    assert groups, "this fixture is supposed to have grounded prose"
    for group in groups:
        assert group["label"] in html
        assert group["backend"] in html


def test_stale_prose_warns_BEFORE_the_sentence(library, tmp_path):
    """A reader who has already read the sentence has already been misled,
    so the warning cannot come after it."""
    slug = "exp08-priority-encoder"
    manifest = manifest_of(library, slug)
    entry = next(p for p in manifest["prose"] if p.get("answer"))
    entry["answer_freshness"] = "stale"
    rewrite(library, slug, manifest)

    build_site(library, tmp_path / "site")
    html = page(tmp_path / "site", slug)
    assert "STALE" in html
    assert html.index("STALE") < html.index(entry["answer"][:40])


def test_unknown_freshness_is_its_own_state_not_fresh(library, tmp_path):
    slug = "exp08-priority-encoder"
    manifest = manifest_of(library, slug)
    entry = next(p for p in manifest["prose"] if p.get("answer"))
    entry["answer_freshness"] = "unknown"
    rewrite(library, slug, manifest)

    build_site(library, tmp_path / "site")
    html = page(tmp_path / "site", slug)
    assert "could not be checked" in html.lower()


# ------------------------------------------------------- the index

def test_the_index_states_that_anything_absent_is_not_solved_yet(site):
    """A real answer, not a failure state — and the only honest thing a site
    that cannot simulate can say."""
    html = (site / "index.html").read_text(encoding="utf-8")
    assert "not solved yet" in html.lower()


def test_the_index_says_the_site_cannot_simulate(site):
    html = (site / "index.html").read_text(encoding="utf-8")
    assert "cannot simulate" in html.lower()


def test_the_index_flags_a_page_before_the_reader_opens_it(library, tmp_path):
    slug = "exp08-priority-encoder"
    manifest = manifest_of(library, slug)
    manifest["results"][0]["verification"] = "internal"
    manifest["results"][0]["warnings"] = ["Logisim was unavailable"]
    rewrite(library, slug, manifest)

    build_site(library, tmp_path / "site")
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    row = html[html.index(slug):]
    assert "internal" in row[:2000].lower()


# ---------------------------------------------------------- refusals

def test_it_refuses_an_invalid_manifest(library, tmp_path):
    slug = "exp02-series-regulator"
    manifest = manifest_of(library, slug)
    manifest["results"][0]["backend"] = None
    rewrite(library, slug, manifest)

    with pytest.raises(Exception) as excinfo:
        build_site(library, tmp_path / "site")
    assert slug in str(excinfo.value)


def test_it_refuses_a_deliverable_whose_bytes_no_longer_match(library,
                                                              tmp_path):
    """The sha256 is the whole promise: the file you download is the file
    that was evaluated. Serving a page whose download does not match it is
    worse than serving nothing."""
    slug = "exp02-series-regulator"
    target = next((library / slug).glob("*.asc"))
    target.write_bytes(target.read_bytes() + b"\n* tampered\n")

    with pytest.raises(ViewerError) as excinfo:
        build_site(library, tmp_path / "site")
    assert "sha256" in str(excinfo.value)
    assert target.name in str(excinfo.value)


def test_it_refuses_a_missing_deliverable(library, tmp_path):
    slug = "exp03-regulated-supply"
    next((library / slug).glob("*.plt")).unlink()

    with pytest.raises(ViewerError) as excinfo:
        build_site(library, tmp_path / "site")
    assert ".plt" in str(excinfo.value)


def test_it_refuses_a_stale_index(library, tmp_path):
    """A site built from a stale index lists questions that are not what it
    serves. build_index already refuses to WRITE one; the viewer refuses to
    PUBLISH one."""
    index = json.loads((library / INDEX_NAME).read_text(encoding="utf-8"))
    index["questions"] = index["questions"][:1]
    (library / INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(ViewerError) as excinfo:
        build_site(library, tmp_path / "site")
    assert "stale" in str(excinfo.value).lower()


def test_it_refuses_an_unknown_manifest_version(library, tmp_path):
    """MANIFEST_VERSION is bumped when an existing viewer could misread a
    manifest. This viewer is that existing viewer: it must stop rather than
    render a format it does not know."""
    slug = "exp02-series-regulator"
    manifest = manifest_of(library, slug)
    manifest["manifest_version"] = 99
    rewrite(library, slug, manifest)

    with pytest.raises(ViewerError) as excinfo:
        build_site(library, tmp_path / "site")
    assert "99" in str(excinfo.value)


# ---------------------------------------------------------- escaping

def test_question_text_is_escaped_not_interpreted(library, tmp_path):
    """Manifest text is data. It is human-transcribed lab-manual prose today,
    but the whole point of the gate is that model output eventually lands in
    these fields."""
    slug = "exp02-series-regulator"
    manifest = manifest_of(library, slug)
    manifest["question"]["text"] = "compute <script>alert(1)</script> & Vz"
    rewrite(library, slug, manifest)

    build_site(library, tmp_path / "site")
    html = page(tmp_path / "site", slug)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp; Vz" in html


# ------------------------------- a complete answer must not read as a gap

def test_a_design_tier_ask_is_not_reported_as_missing_an_answer(site):
    """`prose_from_design` is ZERO generation by design: quoting the choices
    and their rationales IS the answer, and prose.py says so in as many words.

    The first version of this page rendered "No answer has been written for
    this ask" over a complete one, in warning colour. That is the same failure
    as the prose ask that used to report as dropped work: a false alarm on the
    screen built to surface real gaps teaches the reader to skip the line, and
    then it does not fire on the gaps that are real.
    """
    html = page(site, "exp08-priority-encoder")
    design = html[html.index("Explain your design choices"):]
    design = design[:design.index("prose_from_results")]
    assert "No answer has been written" not in design
    assert "is the answer" in design.lower()


def test_a_results_tier_ask_with_no_answer_still_says_so(library, tmp_path):
    """The other half of the discrimination. Suppressing the notice for one
    tier must not suppress it where an answer really is missing — a check that
    cannot fire is worth nothing."""
    slug = "exp08-priority-encoder"
    manifest = manifest_of(library, slug)
    entry = next(p for p in manifest["prose"]
                 if p["tier"] == "prose_from_results")
    entry["answer"] = None
    entry["answer_authorship"] = None
    entry["answer_freshness"] = "unknown"
    rewrite(library, slug, manifest)

    build_site(library, tmp_path / "site")
    html = page(tmp_path / "site", slug)
    assert "No answer has been written" in html


def test_the_design_notes_are_not_printed_twice(site, library):
    """When a prose ask quotes the design notes, printing the same four
    rationales again higher up is noise on a page whose whole job is letting a
    reader find the one thing that needs their judgement."""
    slug = "exp08-priority-encoder"
    html = page(site, slug)
    notes = manifest_of(library, slug)["design_notes"]
    assert notes, "this fixture is supposed to have design notes"
    for note in notes:
        assert html.count(esc_like(note["rationale"])) == 1, (
            f"{note['item']}: rationale rendered more than once")


def esc_like(text: str) -> str:
    import html as _html
    return _html.escape(text, quote=False)


def test_a_waveform_value_names_which_statistic_it_equals(site):
    """A bare 8.139e-06 beside "v_in_wave" invites exactly the misreading
    that incident 5 was: it is the time-weighted MEAN of a symmetric AC
    waveform, which is near zero and says almost nothing, sitting where a
    reader expects the answer.

    The viewer may not import from analysis.py that the value of a
    waveform_stats measurement is its mean — that would be a fact from a
    second source, and the page would keep asserting it after analysis.py
    changed. What it MAY do is state a match it finds inside the manifest:
    this value is equal to the published stats.mean, in this manifest, and a
    reader can check that against the row itself.
    """
    html = page(site, "exp03-regulated-supply")
    assert "= stats.mean" in html


def test_no_statistic_is_named_when_the_manifest_shows_no_match(library,
                                                                tmp_path):
    """The other direction: the label is a comparison, not an assumption
    about what waveform values always are."""
    slug = "exp03-regulated-supply"
    manifest = manifest_of(library, slug)
    with_stats = [r for r in manifest["results"] if r.get("stats")]
    assert with_stats, "this fixture is supposed to have waveform statistics"
    for index, result in enumerate(with_stats):
        # a value matching no published statistic, and distinct per row
        result["value"] = -1.5 - index
    rewrite(library, slug, manifest)

    build_site(library, tmp_path / "site")
    html = page(tmp_path / "site", slug)
    assert "-1.5" in html, "the mutated values did reach the page"
    assert "= stats." not in html
