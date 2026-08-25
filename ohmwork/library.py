"""The library manifest: one JSON per solved question, and the index over them.

WHY THIS EXISTS. LTspice is a Windows GUI application. It does not run on
Vercel, on serverless, or in a normal Linux container, and ngspice-in-Docker
would put every number back on synthesised models -- the 0.45 V error this
project spent three rounds removing. So a hosted ohmwork cannot simulate.

The settled architecture (CLAUDE.md, "Deployment"):

    the CLI is the GENERATOR   runs locally where LTspice is, produces
                               verified results, reviewed at the dry-run gate
    the library is the PRODUCT this file's format: what was asked, what was
                               built, what was measured, and how each number
                               is warranted
    the site is a VIEWER       static, over the library. No backend, no
                               simulation, no LLM in the hot path, no
                               database. Not in the library -> "not solved yet"

So this manifest is a PUBLISHED CONTRACT, not an internal dump. Two
consequences run through the whole module:

1. Every number carries its provenance and its verification status, because
   the site serves numbers to a student who cannot re-run them. A value with
   no backend named is not publishable.
2. Validation is strict -- unknown keys are rejected with a path-shaped
   error, exactly like the input gate in question.py. A contract that
   silently accepts drift is not a contract.

MANIFEST_VERSION is bumped on any change that an existing viewer could
misread. Adding an optional key is not such a change; renaming, removing, or
changing the meaning of one is.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

MANIFEST_VERSION = 1

#: One directory per question, named by a slug that becomes the URL and is
#: therefore STABLE FOREVER once published. Renaming one breaks every link
#: anyone saved.
#:
#:     library/
#:       index.json
#:       exp02-series-regulator/
#:         manifest.json
#:         question.json
#:         series_regulator.asc
#:         series_regulator.plt
#:
#: Deliverable paths in a manifest are relative to that question directory, so
#: a manifest plus its folder is self-contained and can be moved or served
#: from anywhere without rewriting.
SLUG = re.compile(r"^exp\d{2}-[a-z0-9]+(-[a-z0-9]+)*$")
MANIFEST_NAME = "manifest.json"
QUESTION_NAME = "question.json"
INDEX_NAME = "index.json"


class ManifestError(Exception):
    """A manifest does not satisfy the published contract."""


@dataclass(frozen=True)
class Deliverable:
    """A file the student actually opens.

    `verified` is the honest bit, and it is deliberately not a bare boolean
    hiding a fudge. Exactly one of the two notes is required:

    - verified=True  needs `verified_by`, saying HOW. Note what this does and
      does not claim for a .asc: the deliverable carries the whole experiment
      with one run active and the rest commented, so the exact BYTES shipped
      were not the bytes handed to LTspice -- the per-run scratch files were.
      What the deliverable does have is a real machine check: it round-trips
      through the geometric parser, and it is emitted from the same circuit
      description that was simulated. Say that, rather than "verified".
    - verified=False needs `unverified_reason`. A .plt has NO machine check
      at all: batch mode does not read plot files. That must be visible
      wherever the file ships, and the manifest is where it ships to the site.
    """

    path: str
    kind: str                       # "ltspice-schematic" | "ltspice-plot" | ...
    verified: bool
    verified_by: str | None = None
    unverified_reason: str | None = None


# ------------------------------------------------------------- building

def _hash_file(path: Path) -> str:
    """sha256, so the viewer can prove the file it serves is the file that
    was simulated. Without it the manifest describes a run of something, and
    nobody can tell whether it is the download beside it."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _measurement_entry(m) -> dict:
    """One analysis.Measurement, flattened for publication.

    Nothing here is optional-by-omission: backend, source and verification
    are always written, because a reader must never have to assume them.
    """
    entry = {
        "name": m.name,
        "value": m.value,
        "run": m.run,
        "source": m.source,                 # "simulation" | "derived"
        "backend": m.backend,
        "verification": m.verification,     # "external" | "internal"
        "reliable": m.reliable,
        "warnings": list(m.warnings),
    }
    for optional in ("formula", "definition", "at", "stats", "table"):
        value = getattr(m, optional, None)
        if value is not None:
            entry[optional] = value
    return entry


def _device_entry(choice) -> dict:
    """A DeviceChoice, including WHICH POLICY PATH produced it.

    The path is the point. "named" means the question named the part;
    "synthesized" means we built a model anchored at the asked value;
    "nearest" means we substituted and the substitution must be visible.
    A part with no path recorded is indistinguishable from a silent pick.
    """
    return {
        "part": choice.part,
        "policy": choice.policy,
        "directive": choice.directive,
        "report": choice.report,
    }


def _designed_values(components: list) -> list[dict]:
    """Values that are the tool's engineering judgement, not the question's.

    A designed value indistinguishable from a stated one submits our
    judgement as the student's own, so these are published separately with
    their rationale AND its authorship. Absent authorship is never assumed
    human -- that assumption is exactly the unfounded trust being removed.
    """
    out = []
    for comp in components:
        origin = comp.get("origin", "stated")
        if origin == "stated":
            continue
        out.append({
            "ref": comp["ref"],
            "value": comp.get("value") or comp.get("part"),
            "origin": origin,                       # "designed" | "default"
            "rationale": comp.get("rationale"),
            "rationale_origin": comp.get("rationale_origin"),
        })
    return out


def build_manifest(question, results, deliverables, *, question_id, backend,
                   generated, out_dir=".", prose_entries=None) -> dict:
    """Assemble one question's published record.

    `generated` is passed in rather than read from the clock so the manifest
    is reproducible and diffable: regenerating an unchanged question must
    produce an identical file.
    """
    out_dir = Path(out_dir)
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "question_id": question_id,
        "generated": generated,
        "backend": backend,
        "question": {
            "text": question.question,
            "source": question.source,
            "asks": question.asks or [],
        },
        "circuit": question.circuit,
        "plan": question.plan,
        "devices": {ref: _device_entry(c) for ref, c in question.devices.items()},
        # from the INPUT circuit via to_dict(), not question.circuit: the
        # resolved emit-ready circuit has origin and rationale stripped out,
        # and those are exactly what has to be published
        "designed_values": _designed_values(
            question.to_dict()["circuit"].get("components", [])),
        "design_notes": question.design_notes or [],
        # Prose asks, RESOLVED: the rows actually selected, with the backend
        # and verification status they inherit. The site is a dumb viewer, so
        # the selection happens here where the evidence is, not in the page.
        "prose": list(prose_entries or []),
        "results": [_measurement_entry(m) for m in results.values()],
        "deliverables": [
            {
                "path": d.path,
                "kind": d.kind,
                "verified": d.verified,
                "verified_by": d.verified_by,
                "unverified_reason": d.unverified_reason,
                "sha256": _hash_file(out_dir / d.path),
            }
            for d in deliverables
        ],
        # Regime assertions that RAN, held or not. Published for the same
        # reason checks_skipped is: a page with no warnings because every
        # regime held must be distinguishable from one with no warnings
        # because nobody evaluated any.
        "regime_checks": [
            {"assertion": r.assertion, "run": r.run, "device": r.device,
             "held": r.held, "examined": r.examined,
             "reasons": list(r.reasons)}
            for r in getattr(results, "regimes", ())
        ],
        "warnings": list(question.warnings),
        # Checks that did not run, published rather than held internally.
        # A page showing no warnings because nothing was examined must not
        # look like a page showing no warnings because everything passed.
        "checks_skipped": [{"name": c.name, "reason": c.reason}
                           for c in getattr(question, "skipped", [])],
    }
    validate_manifest(manifest)
    return manifest


# ----------------------------------------------------------- validation

_TOP = {
    "manifest_version", "question_id", "generated", "backend", "question",
    "circuit", "plan", "devices", "designed_values", "design_notes",
    "results", "deliverables", "warnings", "checks_skipped", "regime_checks",
    "prose",
}
_PROSE = {"text", "tier", "quoted_notes", "evidence", "answer",
          "answer_authorship", "answer_freshness"}
#: A stored caption describes specific rows. "unknown" means no fingerprint
#: was recorded, so the check could not run -- published as its own state,
#: never quietly folded into "fresh".
_FRESHNESS = {"fresh", "stale", "unknown"}
_PROSE_EVIDENCE = {"label", "measurement", "columns", "rows", "total_rows",
                   "backend", "verification"}
_PROSE_NOTE = {"item", "choice", "rationale", "authorship"}
_TIERS = {"prose_from_design", "prose_from_results", "prose_free"}
_REGIME_CHECK = {"assertion", "run", "device", "held", "examined", "reasons"}
_QUESTION = {"text", "source", "asks"}
_DEVICE = {"part", "policy", "directive", "report"}
_DESIGNED = {"ref", "value", "origin", "rationale", "rationale_origin"}
_DELIVERABLE = {"path", "kind", "verified", "verified_by",
                "unverified_reason", "sha256"}
_RESULT_REQUIRED = {"name", "value", "run", "source", "backend",
                    "verification", "reliable", "warnings"}
_RESULT_OPTIONAL = {"formula", "definition", "at", "stats", "table"}

_POLICIES = {"named", "synthesized", "nearest"}
_VERIFICATIONS = {"external", "internal"}
_ORIGINS = {"designed", "default"}


def _exact(obj, allowed, where, required=None):
    if not isinstance(obj, dict):
        raise ManifestError(f"{where}: expected an object, got {type(obj).__name__}")
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise ManifestError(f"{where}: unknown key(s) {unknown}")
    missing = sorted((required if required is not None else allowed) - set(obj))
    if missing:
        raise ManifestError(f"{where}: missing key(s) {missing}")


def validate_manifest(manifest: dict) -> None:
    """Reject anything a published viewer could misread. Raises ManifestError."""
    _exact(manifest, _TOP, "manifest")

    if manifest["manifest_version"] != MANIFEST_VERSION:
        raise ManifestError(
            f"manifest.manifest_version: {manifest['manifest_version']!r} is not "
            f"the version this build writes ({MANIFEST_VERSION})"
        )
    question_id = manifest["question_id"]
    if not SLUG.match(question_id or ""):
        raise ManifestError(
            f"manifest.question_id: {question_id!r} is not a valid slug. Use "
            f"exp<NN>-<short-name>, lowercase, e.g. 'exp02-series-regulator'. "
            f"The slug becomes the published URL and is stable forever, so it "
            f"is validated at write time rather than discovered later."
        )

    _exact(manifest["question"], _QUESTION, "manifest.question")
    if not manifest["question"]["text"]:
        raise ManifestError(
            "manifest.question.text: the VERBATIM question text is required. It "
            "is the one defence that sits outside the system -- every other "
            "screen compares model output against model output."
        )

    for ref, device in manifest["devices"].items():
        _exact(device, _DEVICE, f"manifest.devices[{ref!r}]", required={"part", "policy", "report"})
        if device["policy"] not in _POLICIES:
            raise ManifestError(
                f"manifest.devices[{ref!r}].policy: {device['policy']!r} is not "
                f"one of {sorted(_POLICIES)}. Every device records which policy "
                f"path chose it; never silently pick."
            )

    for i, value in enumerate(manifest["designed_values"]):
        where = f"manifest.designed_values[{i}]"
        _exact(value, _DESIGNED, where)
        if value["origin"] not in _ORIGINS:
            raise ManifestError(f"{where}.origin: {value['origin']!r} not in {sorted(_ORIGINS)}")
        if value["origin"] == "designed" and not value["rationale"]:
            raise ManifestError(
                f"{where}: a designed value requires a rationale. Publishing one "
                f"without it submits our engineering judgement as the student's."
            )

    for i, result in enumerate(manifest["results"]):
        where = f"manifest.results[{i}]"
        _exact(result, _RESULT_REQUIRED | _RESULT_OPTIONAL, where,
               required=_RESULT_REQUIRED)
        if result["verification"] not in _VERIFICATIONS:
            raise ManifestError(
                f"{where}.verification: {result['verification']!r} not in "
                f"{sorted(_VERIFICATIONS)}"
            )
        if result["value"] is None and "table" not in result:
            raise ManifestError(
                f"{where}: no value and no table. A published result must BE "
                f"something a reader can look at; a null with nothing beside "
                f"it is a row on a page that says nothing."
            )
        if not result["backend"]:
            raise ManifestError(
                f"{where}.backend: a published number must name what computed it"
            )
        if not result["reliable"] and not result["warnings"]:
            raise ManifestError(
                f"{where}: marked unreliable with no reason. An unreliable number "
                f"is shown flagged, never hidden and never unexplained."
            )
        if result["verification"] == "internal" and not result["warnings"]:
            # Now that Logisim gives digital results EXTERNAL verification,
            # an internally-evaluated one is a second-class result and must
            # never be published as though it were not. Same rule shape as
            # unreliable: allowed, but only stated, never silent.
            raise ManifestError(
                f"{where}: computed by our own evaluator (verification "
                f"'internal') with no warning saying so. The library's promise "
                f"is that every number is traceable to an outside tool. If "
                f"Logisim was unavailable, regenerate with it installed; if "
                f"you mean to publish an internal result anyway, say so in "
                f"warnings so the index and the page can flag it."
            )

    for i, entry in enumerate(manifest["prose"]):
        where = f"manifest.prose[{i}]"
        _exact(entry, _PROSE, where,
               required={"text", "tier", "quoted_notes", "evidence"})
        if entry["tier"] not in _TIERS:
            raise ManifestError(
                f"{where}.tier: {entry['tier']!r} not in {sorted(_TIERS)}")
        for j, note in enumerate(entry["quoted_notes"]):
            _exact(note, _PROSE_NOTE, f"{where}.quoted_notes[{j}]")
        for j, group in enumerate(entry["evidence"]):
            _exact(group, _PROSE_EVIDENCE, f"{where}.evidence[{j}]")
            if group["verification"] not in _VERIFICATIONS:
                raise ManifestError(
                    f"{where}.evidence[{j}].verification: "
                    f"{group['verification']!r} not in {sorted(_VERIFICATIONS)}"
                )
            if not group["backend"]:
                raise ManifestError(
                    f"{where}.evidence[{j}]: prose grounded in rows must name "
                    f"what computed them. Grounding is a chain and its weakest "
                    f"link has to be visible."
                )
        if entry.get("answer") and not entry.get("answer_authorship"):
            raise ManifestError(
                f"{where}: an answer with no recorded authorship. Absent "
                f"authorship is never assumed human — that assumption is "
                f"exactly the unfounded trust this field removes."
            )
        if entry.get("answer"):
            freshness = entry.get("answer_freshness")
            if freshness not in _FRESHNESS:
                raise ManifestError(
                    f"{where}.answer_freshness: {freshness!r} not in "
                    f"{sorted(_FRESHNESS)}. A published caption must say "
                    f"whether it still describes the rows beside it; silence "
                    f"there reads as 'fresh' and cannot be distinguished "
                    f"from a check that never ran."
                )

    for i, entry in enumerate(manifest["regime_checks"]):
        where = f"manifest.regime_checks[{i}]"
        _exact(entry, _REGIME_CHECK, where)
        if not entry["examined"]:
            raise ManifestError(
                f"{where}: a regime check must say WHAT it examined. "
                f"'held: true' on its own is indistinguishable from a check "
                f"that looked at nothing."
            )
        if not entry["held"] and not entry["reasons"]:
            raise ManifestError(
                f"{where}: marked violated with no reason given."
            )

    for i, entry in enumerate(manifest["checks_skipped"]):
        where = f"manifest.checks_skipped[{i}]"
        _exact(entry, {"name", "reason"}, where)
        if not entry["reason"]:
            raise ManifestError(
                f"{where}: a skipped check must say WHY it was skipped. "
                f"Recording the name alone tells a reader something is "
                f"missing without telling them what they are not protected by."
            )

    for i, item in enumerate(manifest["deliverables"]):
        where = f"manifest.deliverables[{i}]"
        _exact(item, _DELIVERABLE, where)
        if item["verified"] and not item["verified_by"]:
            raise ManifestError(
                f"{where}: a verified deliverable must say HOW it was verified. "
                f"'verified' alone overstates a .asc, whose exact bytes were not "
                f"the ones handed to the simulator."
            )
        if not item["verified"] and not item["unverified_reason"]:
            raise ManifestError(
                f"{where}: an unverified deliverable must state why. An artefact "
                f"with no machine verification path must say so wherever it ships."
            )
        if len(item["sha256"]) != 64:
            raise ManifestError(f"{where}.sha256: expected a sha256 hex digest")


# ---------------------------------------------------------------- index

def write_manifest(manifest: dict, path) -> None:
    validate_manifest(manifest)
    Path(path).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def build_index(manifest_dir) -> dict:
    """The list the static viewer loads first.

    Deliberately thin: enough to render a list and route to a manifest,
    nothing a viewer could mistake for a result. Anything not listed here is
    "not solved yet", which is a real and honest answer.
    """
    entries = []
    for path in sorted(Path(manifest_dir).glob(f"*/{MANIFEST_NAME}")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        validate_manifest(manifest)
        if path.parent.name != manifest["question_id"]:
            raise ManifestError(
                f"{path}: directory {path.parent.name!r} does not match "
                f"question_id {manifest['question_id']!r}. The directory name IS "
                f"the published slug; a mismatch means links resolve to the "
                f"wrong question."
            )
        entries.append({
            "question_id": manifest["question_id"],
            "path": f"{path.parent.name}/{MANIFEST_NAME}",
            "generated": manifest["generated"],
            "backend": manifest["backend"],
            "result_count": len(manifest["results"]),
            # The two flags a reader needs before trusting a page. "internal"
            # means our own evaluator produced it and nothing outside checked
            # it -- the same standing warning as an unreliable number.
            "has_internal_results": any(
                r["verification"] == "internal" for r in manifest["results"]
            ),
            "has_unreliable_results": any(
                not r["reliable"] for r in manifest["results"]
            ),
            # A quiet page is not necessarily a clean one.
            "has_skipped_checks": bool(manifest["checks_skipped"]),
            "has_violated_regimes": any(
                not r["held"] for r in manifest["regime_checks"]
            ),
            # How much of the page is text nothing can check.
            "prose_ask_count": len(manifest["prose"]),
            "has_ungrounded_prose": any(
                p["tier"] == "prose_free" for p in manifest["prose"]
            ),
            # A caption over rows it was not written for is a false statement
            # sitting on top of computed evidence. Flagged before the reader
            # opens the page, like every other second-class result.
            "has_stale_prose": any(
                p.get("answer_freshness") in ("stale", "unknown")
                for p in manifest["prose"]
            ),
        })
    return {"manifest_version": MANIFEST_VERSION, "questions": entries}
