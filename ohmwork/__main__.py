"""CLI: python -m ohmwork question.json [--dry-run] [--out DIR] [--library DIR]

--dry-run loads and fully validates the question, resolves devices,
prints the plan for human confirmation, and exits without simulating.
This is the confirmation step CLAUDE.md requires: a misread component
value survives every downstream check, so the human must see the
extracted values before anything runs.

TARGET DISPATCH. Everything past the gate is the TARGET's: which backend
evaluates the plan, which emitter writes the deliverable, and what may
honestly be claimed about that file. The two differ in a way that matters
and is stated in the manifest rather than smoothed over:

  LTspice   the deliverable carries the whole experiment with one run active
            and the rest commented, so its exact bytes are NOT the bytes
            LTspice ran -- the per-run scratch files were. What it does have
            is the geometric round trip.
  Logisim   there is one run and no directives, so the deliverable IS the
            file Logisim evaluated. That is a stronger claim, so it is
            checked by comparing the two files rather than asserted.
"""

import argparse
import filecmp
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

from ohmwork import analysis, captions, prose
from ohmwork.emitter import CircuitError, write_asc
from ohmwork.library import (INDEX_NAME, MANIFEST_NAME, QUESTION_NAME,
                             Deliverable, build_index, build_manifest,
                             write_manifest)
from ohmwork.logisim_backend import DigitalEvaluationError
from ohmwork.question import QuestionError, dry_run_report, load_question
from ohmwork.simulate import SimulationError
from ohmwork.targets import get_target


def _make_stdout_unicode_safe() -> None:
    """The dry run prints the VERBATIM question text, and lab manuals are full
    of Greek: 470 uF, 1 kOhm, beta = 100.

    On Windows the console defaults to cp1252, which cannot encode an ohm
    sign, so printing a faithfully transcribed question raised
    UnicodeEncodeError and the confirmation gate died before showing anything.

    UTF-8 first, since any modern terminal handles it. backslashreplace as the
    fallback rather than "replace": a question rendered with U+03A9 visible is
    obviously an encoding artefact, whereas a row of question marks silently
    corrupts the one screen whose entire job is letting a human check the
    values against the image.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError):      # already redirected, or no tty
            pass


def _solve(args) -> int:
    """--solve: a digital question in, a verified circuit out.

    Prints the READING before the answer, deliberately. The loop can prove
    that the circuit computes the specification; nothing here can prove the
    specification is the right reading of the question. A human glancing at
    four lines of algebra can catch what no amount of simulation will.
    """
    from ohmwork.design import DesignError, solve
    from ohmwork.domain import DomainError
    from ohmwork.llm import LLMError, get_provider
    from ohmwork.logisim_backend import best_available_backend

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = best_available_backend()

    try:
        provider = get_provider()
    except LLMError as exc:
        print(f"no model provider: {exc}", file=sys.stderr)
        return 1

    print(f"question: {args.solve}")
    print(f"evaluator: {backend.name} [{backend.verification}]")
    # WHICH model answers is a fact about the result, so it is printed
    # before the result. A pool additionally says who is NOT in it: two live
    # members and four live members produce identical-looking output, and
    # the difference is the difference between a pause and no pause.
    print(f"model: {describe_provider(provider)}")
    if backend.verification != "external":
        print("!! Logisim was not found, so the circuit will be checked by "
              "ohmwork's OWN evaluator, which also computes anything it "
              "would be checked against. Install Logisim Evolution or set "
              "OHMWORK_LOGISIM.")
    print()

    try:
        solution = solve(args.solve, provider=provider, backend=backend,
                         workdir=out_dir)
    except DomainError as exc:
        # Refused, not failed. Printed to stdout rather than stderr for the
        # same reason it renders differently on the web: it is an ANSWER to
        # the question asked -- "not this tool" -- rather than a breakdown.
        print(f"refused: {exc}")
        return 2
    except (DesignError, LLMError) as exc:
        print(f"no verified circuit: {exc}", file=sys.stderr)
        return 1

    print("THE READING — this is what the circuit was verified AGAINST.")
    print("If this misreads the question, every check below still passes.")
    print()
    for line in solution.spec.render().splitlines():
        print(f"    {line}")
    print()

    for index, failure in solution.failed_attempts:
        first = failure.splitlines()[0]
        print(f"  attempt {index} was rejected: {first}")
    print(f"VERIFIED in {solution.attempts} design attempt(s) by "
          f"{solution.table.backend}.")
    print(f"  {solution.comparison.summary.splitlines()[0]}")
    print()

    names = solution.table.inputs + solution.table.outputs
    print("    " + "  ".join(f"{n:>4}" for n in names))
    for row in solution.table.rows:
        print("    " + "  ".join(f"{bit:>4}" for bit in row))
    print()
    print(f"circuit file: {solution.circ_path}")
    print(f"designed by: {solution.provider}/{solution.model}")
    print("The layout is generated mechanically: inputs in a left column, "
          "gates in columns by logic depth. Correct, not pretty.")
    _report_pool_incidents(provider)
    return 0


def describe_provider(provider) -> str:
    """One line naming who is answering. A pool lists its membership."""
    describe = getattr(provider, "describe", None)
    return describe() if describe else f"{provider.name}/{provider.model}"


def _report_pool_incidents(provider) -> None:
    """What the pool absorbed on the way to the answer.

    A rate limit that cost nothing is worth seeing anyway: it is the evidence
    that the pool did something, and a member failing every single call for a
    dead key looks exactly like a healthy pool from the outside.
    """
    incidents = getattr(provider, "incidents", None)
    if not incidents:
        return
    limited = sorted({i.member for i in incidents if i.rate_limited})
    broken = {i.member: i.reason for i in incidents if not i.rate_limited}
    if limited:
        print(f"pool: rate limited and skipped past — {', '.join(limited)}")
    for member, reason in broken.items():
        print(f"pool: {member} FAILED and was set aside — "
              f"{reason.splitlines()[0]}")


def _build_site(args, parser) -> int:
    """Render the library into static HTML.

    Separate from the generate path on purpose. Generating requires a
    simulator and a human at the dry-run gate; publishing the result requires
    neither, and keeping them apart is what makes it obvious that the site
    cannot produce a number of its own.
    """
    from .viewer import ViewerError, build_site

    if not args.library:
        parser.error("--build-site needs --library DIR to render from")
    try:
        written = build_site(args.library, args.build_site)
    except ViewerError as exc:
        print(f"cannot publish: {exc}", file=sys.stderr)
        return 1

    out = Path(args.build_site)
    print(f"wrote {len(written)} files to {out}")
    print(f"    open {out / 'index.html'}")
    print("The site is a VIEWER. It renders manifests that were produced and "
          "reviewed locally; it cannot simulate, and a question that is not "
          "in the library shows as not solved yet.")
    return 0


def main(argv=None) -> int:
    _make_stdout_unicode_safe()
    parser = argparse.ArgumentParser(prog="ohmwork")
    parser.add_argument("question", nargs="?", help="question JSON file")
    parser.add_argument("--list-models", action="store_true",
                        help="print the models the configured provider can "
                             "actually serve, and exit. Hosted catalogues "
                             "change; this is how you find a current id")
    parser.add_argument("--llm",
                        help="provider for generated text: groq (default), "
                             "cerebras, gemini, openrouter, mistral, "
                             "anthropic, or 'pool' to use every provider "
                             "whose key is set, moving to the next one when "
                             "a free tier rate limits. Also settable as "
                             "OHMWORK_LLM")
    parser.add_argument("--extract", nargs="+", metavar="SOURCE",
                        help="extract a question JSON from a lab-manual page: "
                             "one or more image files and/or a .txt of the "
                             "verbatim question. Writes with --write-question")
    parser.add_argument("--write-question", metavar="PATH",
                        help="where --extract writes the question JSON")
    parser.add_argument("--once", action="store_true",
                        help="with --extract, run a single pass instead of "
                             "two independent ones. Faster, and gives up the "
                             "only free signal that the source was ambiguous")
    parser.add_argument("--llm-model",
                        help="model id for the provider. Also settable as "
                             "OHMWORK_LLM_MODEL")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and print the plan; do not simulate")
    parser.add_argument("--explain", action="store_true",
                        help="include device rationale and model cards")
    parser.add_argument("--write-prose", action="store_true",
                        help="generate captions for prose asks grounded in "
                             "results, show them, and offer to store them in "
                             "the question file for review")
    parser.add_argument("--regenerate-prose", action="store_true",
                        help="with --write-prose, rewrite captions that are "
                             "already stored and fresh")
    parser.add_argument("--attest-prose", action="store_true",
                        help="record that the stored prose answers describe "
                             "the CURRENT evidence rows, after you have read "
                             "them against it. For answers written by hand, "
                             "which no generator fingerprinted")
    parser.add_argument("--yes", action="store_true",
                        help="with --write-prose, store generated captions "
                             "without the confirmation prompt")
    parser.add_argument("--out", default=".",
                        help="directory for the deliverable circuit file")
    parser.add_argument("--library",
                        help="also write a library manifest into this "
                             "directory and refresh its index")
    parser.add_argument("--id",
                        help="question id for the manifest (default: the "
                             "question file's stem)")
    parser.add_argument("--generated",
                        help="date recorded in the manifest (default: today). "
                             "Passed in so regenerating an unchanged question "
                             "produces an identical file")
    parser.add_argument("--solve", metavar="QUESTION",
                        help="a digital question in plain English. Designs a "
                             "circuit for it and does not return until "
                             "Logisim has confirmed the circuit computes what "
                             "the question asked for")
    parser.add_argument("--build-site", metavar="DIR",
                        help="render --library into a folder of static HTML "
                             "at DIR and exit. No question file is needed: "
                             "the site is a viewer over what is already "
                             "published, and it never simulates anything")
    args = parser.parse_args(argv)

    if args.list_models:
        return _list_models(args)
    if args.build_site:
        return _build_site(args, parser)
    if args.solve:
        return _solve(args)
    if args.extract:
        if args.llm:
            os.environ["OHMWORK_LLM"] = args.llm
        if args.llm_model:
            os.environ["OHMWORK_LLM_MODEL"] = args.llm_model
        return _extract(args)
    if not args.question:
        parser.error("a question file is required (or use --list-models)")

    # An explicit flag beats the environment; both beat the built-in default.
    if args.llm:
        os.environ["OHMWORK_LLM"] = args.llm
    if args.llm_model:
        os.environ["OHMWORK_LLM_MODEL"] = args.llm_model

    try:
        data = json.loads(Path(args.question).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"cannot read {args.question}: {e}", file=sys.stderr)
        return 2
    try:
        question = load_question(data)
    except (QuestionError, CircuitError) as e:
        print(f"rejected: {e}", file=sys.stderr)
        return 2

    print(dry_run_report(question, explain=args.explain))
    if args.dry_run:
        return 0

    if question.plan is None:
        print("no analysis plan in the question; nothing to simulate")
        return 0

    target = get_target(question.target_name)
    try:
        backend = target.backend()
    except FileNotFoundError as e:
        print(f"no evaluator available: {e}", file=sys.stderr)
        return 1
    if getattr(backend, "verification", "external") == "internal":
        print(f"!! evaluating with {backend.name}, ohmwork's own engine — "
              f"no outside tool checks these results")

    workdir = Path(tempfile.mkdtemp(prefix="ohmwork_"))
    try:
        results = analysis.execute(
            question.circuit, question.plan, backend, workdir
        )
    except (SimulationError, DigitalEvaluationError,
            analysis.AnalysisError, FileNotFoundError) as e:
        print(f"evaluation failed: {e}", file=sys.stderr)
        return 1

    print(analysis.render_report(results, question.plan,
                                 devices=question.devices))

    # Prose LAST, after every computed result, never interleaved: a sentence
    # and a measured number must not sit side by side looking equally solid.
    #
    # resolve_prose runs ONCE and everything downstream renders from its
    # output — the terminal section, the caption generator, and the published
    # manifest. Resolving twice would let the page a student reads and the
    # page the site serves disagree about which rows support which sentence,
    # with nothing to catch it.
    prose_entries = prose.resolve_prose(
        question.asks, results, question.design_notes, answers={})

    if args.write_prose:
        prose_entries = _write_prose(args, question, results, prose_entries)
    if args.attest_prose:
        prose_entries = _attest_prose(args, question, results, prose_entries)

    prose_text = prose.render_prose(prose_entries)
    if prose_text:
        print(prose_text)

    # With --library, the deliverables live INSIDE the question's directory,
    # so a manifest plus its folder is self-contained (CLAUDE.md, "Library
    # layout"). Without it, --out behaves as before.
    question_id = args.id or Path(args.question).stem
    if args.library:
        out_dir = Path(args.library) / question_id
    else:
        out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Published files are named after the SLUG, which is stable forever, not
    # after whatever the question JSON happens to be called locally. A student
    # downloading exp08-priority-encoder.circ can tell what it is; q2.circ
    # tells them nothing, and renaming the input file would rename the
    # download.
    stem = question_id if args.library else Path(args.question).stem
    deliverables = _write_deliverables(
        target, question, results, out_dir, stem, workdir)

    if args.library:
        _write_library_entry(args, question, results, deliverables,
                             out_dir, backend, prose_entries)
    return 0


def _extract(args) -> int:
    """--extract: a photographed page in, a question JSON out.

    Two independent passes by default. The dominant failure here is a DROP —
    a missed beta, an omitted component, an ask that never became a
    measurement — and it leaves every downstream screen looking perfect.
    Disagreement between passes is the only free signal that the source was
    ambiguous, so giving it up takes an explicit --once.
    """
    from ohmwork import extract as extractor
    from ohmwork.llm import Image, LLMError

    texts, images, source_names = [], [], []
    for source in args.extract:
        path = Path(source)
        if not path.is_file():
            print(f"no such file: {source}", file=sys.stderr)
            return 2
        source_names.append(path.name)
        if path.suffix.lower() in (".txt", ".md"):
            texts.append(path.read_text(encoding="utf-8").strip())
        else:
            try:
                images.append(Image.from_path(path))
            except LLMError as e:
                print(str(e), file=sys.stderr)
                return 2

    text = "\n\n".join(texts) if texts else None
    if text is None and not images:
        print("--extract needs a .txt of the question, an image, or both",
              file=sys.stderr)
        return 2
    if text is None:
        print("!! no verbatim text supplied, so the question wording will be")
        print("   the model's TRANSCRIPTION of the image. Supply a .txt")
        print("   alongside the image when you can: the verbatim text is the")
        print("   one defence in this system that does not come from the")
        print("   system itself.")

    run = extractor.extract if args.once else extractor.extract_twice
    try:
        result = run(text, images, attempts=3,
                     source_file=", ".join(source_names))
    except (extractor.ExtractionError, LLMError) as e:
        print(f"extraction failed: {e}", file=sys.stderr)
        return 1

    print(f"extracted by {result.provider}/{result.model} in "
          f"{result.attempts} model call(s)")
    for warning in result.warnings:
        print(f"  !! {warning}")
    for disagreement in result.disagreements:
        print(f"  DISAGREEMENT: {disagreement}")

    text_out = json.dumps(result.data, indent=2, ensure_ascii=False) + "\n"
    if args.write_question:
        Path(args.write_question).write_text(text_out, encoding="utf-8")
        print()
        print(f"wrote {args.write_question}")
        print("NOTHING has been simulated. Read the values back against the "
              "original before you trust any of it:")
        print(f"    python -m ohmwork {args.write_question} --dry-run")
    else:
        print()
        print(text_out)
    return 0


def _confirm(question: str) -> str:
    """Ask, unless there is nobody to ask.

    With stdin redirected — a pipe, CI, a scripted run — input() raises
    EOFError, and letting that propagate would throw away a whole
    experiment's measured results at the very last step because a prompt
    could not be shown. Treat "no human present" as "no", which is the safe
    answer: nothing is written, and everything already computed still prints.
    """
    print()
    try:
        if not sys.stdin or not sys.stdin.isatty():
            raise EOFError                       # redirected, piped, or CI
        print(f"{question} [y/N] ", end="", flush=True)
        return input().strip().lower()
    except EOFError:
        # isatty() is only a heuristic and lies in some shells — it reported
        # a terminal here while stdin was redirected, so the EOFError is
        # caught as well. Both paths mean the same thing: nobody to ask.
        print(f"{question} [nobody to ask — assuming no. Use --yes to save "
              f"without asking.]")
        return "n"


def _list_pool_models(pool) -> int:
    """Every member's catalogue, member by member.

    Not one merged list: an id is only meaningful against the account that
    serves it, and merging them would invite setting a Cerebras id on Gemini.
    Each member is checked against its OWN configured model, so a pool that
    is one stale id away from being useful says which member and which id.
    """
    from ohmwork import llm

    print(pool.describe())
    problems = 0
    for member in pool.members:
        provider = member.provider
        print()
        try:
            models = provider.available_models()
        except llm.LLMError as e:
            problems += 1
            print(f"{provider.name}: COULD NOT LIST — {e}")
            continue
        print(f"{provider.name} serves {len(models)} model(s); "
              f"configured: {provider.model}")
        for model in models:
            marker = " <- configured" if model == provider.model else ""
            print(f"  {model}{marker}")
        if provider.model not in models:
            problems += 1
            print(f"!! {provider.model} is NOT in that list — set "
                  f"OHMWORK_LLM_MODEL_{provider.name.upper()}")
    return 1 if problems else 0


def _list_models(args) -> int:
    """What the configured account can actually serve.

    Exists because this repo cannot know a hosted catalogue's contents, and a
    model id baked in today will be wrong eventually. Rather than let that
    arrive as a bare 404, both this command and the request path print the
    real list.
    """
    from ohmwork import llm

    if args.llm:
        os.environ["OHMWORK_LLM"] = args.llm
    try:
        provider = llm.get_provider()
    except llm.LLMError as e:
        print(f"cannot list models: {e}", file=sys.stderr)
        return 1

    if isinstance(provider, llm.Pool):
        return _list_pool_models(provider)

    try:
        models = provider.available_models()
    except llm.LLMError as e:
        print(f"cannot list models: {e}", file=sys.stderr)
        return 1
    print(f"{provider.name} serves {len(models)} model(s); "
          f"currently configured: {provider.model}")
    for model in models:
        marker = " <- configured" if model == provider.model else ""
        print(f"  {model}{marker}")
    if provider.model not in models:
        print()
        print(f"!! {provider.model} is NOT in that list — set "
              f"OHMWORK_LLM_MODEL or pass --llm-model")
    return 0


def _write_prose(args, question, results, entries):
    """Generate captions for grounded prose asks, then offer to store them.

    STORED, not regenerated each run — a caption written fresh every time
    would make the published manifest churn, and a library that churns cannot
    be reviewed. Stored means it can also go stale, which is what
    `answer_evidence` exists to catch.

    The confirmation is the same gate that makes a generated RATIONALE
    trustworthy everywhere else in this project: not that a human wrote it,
    but that a human read it. Prose gets no weaker a rule, so nothing is
    written to disk without the text on screen first.
    """
    try:
        generator = captions.ModelCaptioner()
    except captions.CaptionError as e:
        print(f"cannot generate prose: {e}", file=sys.stderr)
        return entries

    generated = captions.generate_captions(
        entries, generator, regenerate=args.regenerate_prose)
    if not generated:
        print("no captions to generate — every grounded prose ask already "
              "has a fresh answer (use --regenerate-prose to rewrite them)")
        return entries

    print()
    print("generated captions, NOT yet saved — read them against the "
          "evidence below:")
    for ask_text, caption in generated.items():
        print(f'\n  "{ask_text}"')
        for line in textwrap.wrap(caption, width=76,
                                  initial_indent="    ",
                                  subsequent_indent="    "):
            print(line)

    if not args.yes:
        reply = _confirm(f"store these in {args.question}?")
        if reply not in ("y", "yes"):
            print("not saved; rendering them for this run only")
            return prose.resolve_prose(question.asks, results,
                                       question.design_notes, generated)

    path = Path(args.question)
    data = json.loads(path.read_text(encoding="utf-8"))
    updated = captions.apply_captions(data, entries, generated)
    path.write_text(
        json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(f"stored in {path} — review them there before publishing")
    return prose.resolve_prose(updated["asks"], results,
                               question.design_notes)


def _attest_prose(args, question, results, entries):
    """Stamp stored answers with the evidence they were just read against.

    WHAT THE FINGERPRINT MEANS, stated here because it is easy to weaken by
    accident: not "a machine wrote this text from these rows" but "a human
    confirmed this text against these rows". That is the same thing that makes
    a generated RATIONALE trustworthy elsewhere in this project — review at
    the gate, not authorship — so the stamp is only ever applied after the
    text and its rows have been on screen together.

    Which is why this is a separate, explicit act rather than something the
    tool does helpfully on the way past. An automatic stamp would record a
    review that never happened.
    """
    from ohmwork import prose as _prose

    pending = [e for e in entries
               if e.get("answer") and e.get("answer_freshness") == "unknown"]
    if not pending:
        print("nothing to attest — every stored answer already records the "
              "evidence it was written over")
        return entries

    print()
    print("the evidence for these answers is printed in full below. Read "
          "each answer")
    print("against its rows before confirming.")
    for entry in pending:
        print(f'    "{entry["text"]}"')

    if not args.yes:
        reply = _confirm("do these answers describe the rows shown in "
                         "this run?")
        if reply not in ("y", "yes"):
            print("not attested; they stay marked as unrecorded")
            return entries

    path = Path(args.question)
    data = json.loads(path.read_text(encoding="utf-8"))
    by_text = {e["text"]: e for e in pending}
    stamped = 0
    for ask in data.get("asks", []):
        entry = by_text.get(ask.get("text"))
        if entry is None or ask.get("kind") != "prose":
            continue
        ask["prose"]["answer_evidence"] = _prose.evidence_fingerprint(
            entry["evidence"])
        stamped += 1
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(f"attested {stamped} answer(s) in {path}")
    return _prose.resolve_prose(data["asks"], results, question.design_notes)


def _write_deliverables(target, question, results, out_dir, stem, workdir):
    """The file(s) the student opens, plus what may honestly be said of each."""
    path = out_dir / (stem + target.extension)
    if target.name == "ltspice":
        write_asc(
            analysis.deliverable_circuit(question.circuit, question.plan),
            path,
        )
        print(f"deliverable: {path}")
        out = [Deliverable(
            path.name, target.deliverable_kind, True,
            verified_by="emitted from the same circuit description that was "
                        "simulated, and round-trips through the geometric "
                        "parser. The exact bytes were not the ones handed to "
                        "LTspice: the deliverable carries every run, with "
                        "one active and the rest commented",
        )]
        plot = _write_plot_settings(question, results, path)
        if plot is not None:
            out.append(plot)
        return out

    from ohmwork.logisim_emitter import write_circ

    write_circ(question.circuit, path)
    print(f"deliverable: {path}")
    print("   NOTE: the layout is mechanically generated — inputs in a left "
          "column,\n"
          "   gates in columns by logic depth, orthogonal routes crossing "
          "freely. It is\n"
          "   correct and it will not look hand-drawn.")

    # A stronger claim than the .asc one, so it is checked rather than made.
    # execute() wrote one .circ per run from the same circuit description; if
    # the deliverable is byte-identical to it, then the file being shipped is
    # literally the file Logisim evaluated.
    evaluated = next(
        (f for f in sorted(Path(workdir).glob("*.circ"))
         if filecmp.cmp(f, path, shallow=False)), None)
    if evaluated is not None:
        backends = sorted({m.backend for m in results.values()})
        verified_by = (
            f"byte-identical to {evaluated.name}, the file "
            f"{' and '.join(backends)} actually evaluated — compared, "
            f"not assumed"
        )
    else:
        verified_by = ("emitted from the same circuit description that was "
                       "evaluated, and passes validate_wiring")
        print("   note: the deliverable differs from the evaluated scratch "
              "file; claiming only same-description provenance")
    return [Deliverable(path.name, target.deliverable_kind, True,
                        verified_by=verified_by)]


def _write_library_entry(args, question, results, deliverables, out_dir,
                         backend, prose_entries) -> None:
    """The generator half of the deployment split (CLAUDE.md, "Deployment").

    The simulator cannot run on the host, so the site never simulates: it
    serves this manifest. Everything a reader needs to judge a number
    therefore has to be IN the manifest, and library.validate_manifest
    refuses to write one that leaves any of it out.
    """
    from datetime import date

    library = Path(args.library)
    question_id = args.id or Path(args.question).stem
    manifest = build_manifest(
        question, results, deliverables,
        question_id=question_id,
        backend=backend.name,
        generated=args.generated or date.today().isoformat(),
        out_dir=out_dir,
        prose_entries=prose_entries,
    )
    write_manifest(manifest, out_dir / MANIFEST_NAME)

    # the question JSON travels with its answer: a reader can see exactly what
    # was fed in, not only what came out
    rebuilt = json.dumps(question.to_dict(), indent=2, ensure_ascii=False)
    (out_dir / QUESTION_NAME).write_text(rebuilt + "\n", encoding="utf-8")
    write_manifest_index(library)
    print(f"library entry: {out_dir / MANIFEST_NAME}")


def write_manifest_index(library: Path) -> None:
    index = build_index(library)
    text = json.dumps(index, indent=2, sort_keys=True)
    (library / INDEX_NAME).write_text(text + "\n", encoding="utf-8")


def _write_plot_settings(question, results, deliverable: Path):
    """For "observe the waveform" asks: a .plt beside the .asc so the
    right traces are already plotted when the student runs it. Y ranges
    come from the measured stats. Pending visual verification — see
    ohmwork/plt.py."""
    from ohmwork.plt import write_plt

    runs = {r["id"]: r for r in question.plan["runs"]}
    tran_runs = {rid for rid, r in runs.items() if r["type"] == "tran"}
    panes = [
        {"expr": m["expr"],
         "ymin": results[m["name"]].stats["min"],
         "ymax": results[m["name"]].stats["max"]}
        for m in question.plan["measurements"]
        if m.get("kind") == "waveform_stats" and m["run"] in tran_runs
    ]
    if not panes:
        return None
    run = runs[next(iter(tran_runs))]
    plt_path = deliverable.with_suffix(".plt")
    write_plt(
        plt_path, "Transient Analysis", panes,
        x_start=analysis.parse_spice_number(run.get("settle", "0")),
        x_stop=analysis.parse_spice_number(run["stop"]),
    )
    unverified = ("batch mode does not read .plt, so no machine check "
                  "exists for plot files; transcribed from LTspice's own "
                  "examples, pending one visual confirmation in the GUI")
    print(f"plot settings: {plt_path} — UNVERIFIED: {unverified}")
    return Deliverable(plt_path.name, "ltspice-plot", False,
                       unverified_reason=unverified)


if __name__ == "__main__":
    sys.exit(main())
