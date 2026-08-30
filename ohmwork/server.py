"""The web endpoint: a question in, a VERIFIED circuit out.

WHAT THIS IS ALLOWED TO BE, AND THE RULE IT REWRITES
----------------------------------------------------
This project has a standing rule against a live-API hot path. Its stated
reason was: *"the server CANNOT simulate -- so it could only ever produce
results labelled UNVERIFIED"*.

For a DIGITAL question that premise is false, and the rule is rewritten
around the distinction rather than deleted. Logisim Evolution is Java, it
runs on an ordinary Linux host, and it is the same external evaluator the
CLI uses. A circuit served from here has been handed to Logisim as a FILE
and confirmed row by row against the specification -- `design.solve` raises
rather than returning one that was not.

So the operational test is no longer "does a request path import llm.py".
It does, deliberately. It is now:

    no response may carry a circuit or a table Logisim did not confirm,
    and every response names the evaluator that confirmed it.

ANALOG IS SERVED ONLY WHERE LTSPICE IS (added 2026-08-30 for the desktop
app, whose backend this is). The rule holds by construction: an analog
question is routed to `analog.solve_analog`, which measures the emitted
file with LTspice and raises rather than returning a circuit that missed
its intent -- and on a machine with no LTspice (every Linux host; LTspice
is a Windows GUI application, and ngspice cannot read its device
libraries) the question is REFUSED with the download named, never answered
unverified. An analog answer is also a WEAKER claim than a digital one --
numbers checked against the question's own figures, not rows checked
against an exhaustive table -- so it arrives as its own "measured" event,
never as "verified".

WHAT A HOSTED TOOL WITH SOMEBODY'S API KEYS MUST NOT GET WRONG
--------------------------------------------------------------
Two things, and both are enforced here rather than documented:

1. **Letting a stranger in.** The password is required, checked in constant
   time, and rate limited. A server with no password configured REFUSES TO
   START -- the one misconfiguration whose symptom is everything working
   perfectly, for everyone, on the owner's free-tier keys.

2. **Letting a key out.** Provider errors quote request context and the pool
   quotes several of them at once, so every byte leaving here is scrubbed of
   anything that looks like a configured secret.
"""

import asyncio
import functools
import hashlib
import hmac
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import Cookie, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from ohmwork.domain import DomainError, check_analog, check_digital, classify
from ohmwork.llm import PoolExhausted

#: Where to get the analog evaluator. Named in the refusal when it is absent,
#: because "install LTspice" without the where is an instruction to go
#: searching, and the first search result is not always Analog Devices.
LTSPICE_DOWNLOAD = ("https://www.analog.com/en/resources/design-tools-and-"
                    "calculators/ltspice-simulator.html")

#: How long a login lasts. Long enough that five people are not typing a
#: passphrase all day; short enough that a borrowed laptop is not forever.
SESSION_HOURS = 24 * 14

#: A question is a paragraph, not a document. The cap is a cost control:
#: every character is a prompt token on somebody's free tier.
MAX_QUESTION_CHARS = 2000

#: Concurrent solves. Each one spawns a JVM, and free hosting has one small
#: CPU -- three at once is slower for everybody than three in a row.
MAX_CONCURRENT_SOLVES = 2


class ConfigError(Exception):
    """The server is configured in a way that would be unsafe to run."""


# ------------------------------------------------------------- scrubbing


def secret_values() -> list[str]:
    """Every configured value that must never appear in a response.

    Read from the environment at call time rather than captured at import,
    because a test (and a redeploy) can change it underneath us.
    """
    out = []
    for name, value in os.environ.items():
        if not value or len(value) < 8:
            continue
        if name.endswith("_API_KEY") or name in ("OHMWORK_PASSWORD",
                                                 "SESSION_SECRET"):
            out.append(value)
    return out


def scrub(text: str) -> str:
    """Redact configured secrets from anything on its way to a browser."""
    for value in secret_values():
        text = text.replace(value, "REDACTED")
    return text


# --------------------------------------------------------------- sessions


def _session_secret(password: str) -> bytes:
    """Signing key for the session cookie.

    Derived from the password when SESSION_SECRET is unset, so that changing
    the password invalidates every existing session -- which is the whole
    reason a person changes a shared password.
    """
    configured = os.environ.get("SESSION_SECRET")
    if configured:
        return configured.encode("utf-8")
    return hashlib.sha256(b"ohmwork-session:" + password.encode("utf-8")).digest()


def _issue(secret: bytes) -> str:
    issued = str(int(time.time()))
    mac = hmac.new(secret, issued.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{issued}.{mac}"


def _valid(token: str | None, secret: bytes) -> bool:
    if not token or "." not in token:
        return False
    issued, _, mac = token.partition(".")
    expected = hmac.new(secret, issued.encode("ascii"),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return False
    try:
        age = time.time() - int(issued)
    except ValueError:
        return False
    return 0 <= age <= SESSION_HOURS * 3600


# ------------------------------------------------------------------- SSE


def sse(name: str, data) -> str:
    """One server-sent event. Scrubbed, always, on the way out."""
    import json
    return f"event: {name}\ndata: {scrub(json.dumps(data))}\n\n"


def _default_solver(question, *, workdir, progress=None):
    """The real thing. Imported lazily so tests never touch the model layer."""
    from ohmwork.design import solve
    from ohmwork.logisim_backend import best_available_backend

    return solve(question, backend=best_available_backend(), workdir=workdir,
                 progress=progress)


def _default_analog_solver(question, *, workdir, progress=None):
    """The real analog loop. LTspiceBackend() raises FileNotFoundError when
    LTspice is absent, and the endpoint turns that into a REFUSAL naming the
    download -- an analog question must never be answered without it."""
    from ohmwork.analog import solve_analog
    from ohmwork.simulate import LTspiceBackend

    return solve_analog(question, backend=LTspiceBackend(), workdir=workdir,
                        progress=progress)


def create_app(*, solver=None, analog_solver=None,
               password: str | None = None,
               max_login_attempts: int = 10,
               max_question_chars: int = MAX_QUESTION_CHARS,
               max_concurrent: int = MAX_CONCURRENT_SOLVES,
               secure_cookies: bool | None = None,
               static_dir=None) -> FastAPI:
    password = os.environ.get("OHMWORK_PASSWORD", "") if password is None \
        else password
    if not password.strip():
        raise ConfigError(
            "OHMWORK_PASSWORD is not set. This server will not start without "
            "one: an open endpoint spends the owner's API keys, and the "
            "symptom of that mistake is everything appearing to work.")
    if secure_cookies is None:
        secure_cookies = os.environ.get("OHMWORK_SECURE_COOKIES") == "1"

    solver = solver or _default_solver
    analog_solver = analog_solver or _default_analog_solver
    secret = _session_secret(password)
    app = FastAPI(title="ohmwork", docs_url=None, redoc_url=None)

    # Per-process state. Five users on one small container: a dict is the
    # right size of machinery, and nothing here is worth a database.
    failures: dict[str, int] = {}
    downloads: dict[str, Path] = {}
    gate = asyncio.Semaphore(max_concurrent)

    def authorised(token) -> bool:
        return _valid(token, secret)

    @app.post("/api/login")
    async def login(request: Request):
        client = request.client.host if request.client else "?"
        if failures.get(client, 0) >= max_login_attempts:
            raise HTTPException(429, "too many attempts; restart the server "
                                     "or wait it out")
        body = await request.json()
        given = str(body.get("password", ""))
        # Constant time, so the failure tells an attacker nothing about how
        # much of the password was right.
        if not hmac.compare_digest(given, password):
            failures[client] = failures.get(client, 0) + 1
            raise HTTPException(401, "wrong password")
        failures.pop(client, None)
        response = JSONResponse({"ok": True})
        response.set_cookie("ohmwork_session", _issue(secret),
                            max_age=SESSION_HOURS * 3600, httponly=True,
                            samesite="lax", secure=secure_cookies)
        return response

    @app.post("/api/logout")
    async def logout():
        response = JSONResponse({"ok": True})
        response.delete_cookie("ohmwork_session")
        return response

    @app.get("/api/session")
    async def session(ohmwork_session: str | None = Cookie(default=None)):
        return {"authorised": authorised(ohmwork_session)}

    @app.get("/api/health")
    async def health():
        """For the host's health check. Deliberately says nothing about
        configuration: it is the one route reachable without a password."""
        return {"status": "ok"}

    @app.post("/api/solve")
    async def solve_endpoint(request: Request,
                             ohmwork_session: str | None = Cookie(default=None)):
        if not authorised(ohmwork_session):
            raise HTTPException(401, "log in first")

        body = await request.json()
        question = str(body.get("question", "")).strip()
        if not question:
            raise HTTPException(400, "no question")
        if len(question) > max_question_chars:
            raise HTTPException(
                400, f"question is {len(question)} characters; the limit is "
                     f"{max_question_chars}")

        return StreamingResponse(
            _run(question), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache",
                     "X-Accel-Buffering": "no"})

    async def _run(question: str):
        """Stream the design loop's own steps as they happen.

        Not decoration: a solve makes several model calls and several
        simulator runs, and the steps ARE the honest account of what happened
        -- the reading it worked from, and every design it had to throw away.
        """
        # WHICH HALF answers is a guess made from the question's words, so it
        # is DISCLOSED before anything runs, exactly as the CLI prints it. A
        # misroute is safe: each loop runs its own domain check and refuses
        # with the reason, which is a better failure than a confident answer
        # from the wrong half of the tool.
        routing = classify(question)
        yield sse("routing", {"domain": routing.domain,
                              "reason": routing.reason})

        # The domain screen belongs to the ENDPOINT as well as to the loop.
        # It is cheap, it spends nothing, and putting it only inside the
        # solve functions would make the guarantee depend on which solver was
        # injected -- the same reasoning as _backfill below.
        try:
            if routing.domain == "analog":
                check_analog(question)
            else:
                check_digital(question)
        except DomainError as exc:
            yield sse("refused", {"message": f"{exc}", "download": None})
            return

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        workdir = Path(tempfile.mkdtemp(prefix="ohmwork-solve-"))

        seen: set[str] = set()

        def progress(name, data):
            seen.add(name)
            loop.call_soon_threadsafe(queue.put_nowait, (name, data))

        async def worker():
            try:
                async with gate:
                    chosen = analog_solver if routing.domain == "analog" \
                        else solver
                    solution = await asyncio.to_thread(functools.partial(
                        chosen, question, workdir=workdir, progress=progress))
                # The contract belongs to the ENDPOINT, not to whichever
                # solver it was handed: the reading and the rejected attempts
                # are reported whether or not the loop streamed them live. A
                # guarantee that holds only for a chatty implementation is
                # not a guarantee.
                if routing.domain == "analog":
                    _backfill_analog(solution, seen, progress)
                    progress("measured", _measured_payload(solution, downloads))
                else:
                    _backfill(solution, seen, progress)
                    progress("verified", _verified_payload(solution, downloads))
            except FileNotFoundError as exc:
                # The analog evaluator is not on this machine. A REFUSAL that
                # names the download, per the product contract: an analog
                # question without LTspice is never answered, and "install
                # this, from here" is the whole useful content of the answer.
                # Digital-path FileNotFoundErrors are NOT this story and fall
                # through to the plain error outcome.
                if routing.domain != "analog":
                    progress("error", {"message": f"{exc}", "download": None})
                    shutil.rmtree(workdir, ignore_errors=True)
                    return
                progress("refused", {
                    "message": (
                        f"This question needs LTspice, which was not found on "
                        f"this machine, so it was refused rather than "
                        f"answered unverified.\n\n{exc}\n\nInstall LTspice "
                        f"(free, from {LTSPICE_DOWNLOAD}) and ask again."),
                    "download": None})
                shutil.rmtree(workdir, ignore_errors=True)
            except PoolExhausted as exc:
                # Not a failure of the design and not a refusal of the
                # question: there was nobody to ask. A third outcome, and it
                # gets its own event so the page can say so.
                progress("unavailable", {"message": f"{exc}",
                                         "members": exc.members,
                                         "download": None})
                shutil.rmtree(workdir, ignore_errors=True)
            except DomainError as exc:
                # A REFUSAL, not a failure, and rendered as a different thing.
                # "the loop tried and could not" and "the loop should never
                # have tried" are different facts about a question, and
                # collapsing them tells someone to rephrase when the real
                # answer is "use the other tool".
                progress("refused", {"message": f"{exc}", "download": None})
                shutil.rmtree(workdir, ignore_errors=True)
            except Exception as exc:                        # noqa: BLE001
                # Deliberately broad. Everything from here reaches a browser,
                # so the alternative to catching it is a stack trace in the
                # response body.
                progress("error", {"message": f"{exc}", "download": None})
                shutil.rmtree(workdir, ignore_errors=True)
            finally:
                progress("__done__", None)

        task = asyncio.create_task(worker())
        try:
            while True:
                name, data = await queue.get()
                if name == "__done__":
                    break
                yield sse(name, data)
        finally:
            await task

    @app.get("/api/circuit/{token}")
    async def circuit(token: str,
                      ohmwork_session: str | None = Cookie(default=None)):
        if not authorised(ohmwork_session):
            raise HTTPException(401, "log in first")
        path = downloads.get(token)
        if path is None or not path.is_file():
            raise HTTPException(404, "no such circuit")
        # For a .circ: the FILE Logisim evaluated, byte for byte -- one run,
        # no directives. For an .asc the claim is weaker (the whole experiment
        # with runs commented; the per-run scratch files are what LTspice
        # ran), and the measured payload's file_note says so.
        suffix = path.suffix.lower() or ".circ"
        media = "application/xml" if suffix == ".circ" else "text/plain"
        return FileResponse(path, media_type=media,
                            filename=f"{token[:8]}{suffix}")

    _mount_static(app, static_dir)
    return app


def _backfill(solution, seen: set, progress) -> None:
    """Emit whatever the solver did not stream, from the finished solution.

    Both of these are things this project refuses to leave out: the reading,
    because nothing downstream can prove the specification is the right
    reading of the question, and the rejected attempts, because a run
    reporting only "verified" hides the designs that were wrong.
    """
    if "reading" not in seen:
        spec = solution.spec
        progress("reading", {"spec": solution.basis.reading,
                             "basis": solution.basis.kind,
                             "inputs": list(spec.inputs),
                             "outputs": list(spec.outputs),
                             "notes": list(getattr(spec, "notes", ()) or ())})
    if "attempt" not in seen:
        for index, failure in solution.failed_attempts:
            progress("attempt", {"index": index, "status": "rejected",
                                 "failure": failure})


def _backfill_analog(solution, seen: set, progress) -> None:
    """The analog mirror of _backfill, for the same two non-negotiables."""
    if "reading" not in seen:
        intent = solution.intent
        progress("reading", {"intent": intent.render(),
                             "topology": intent.topology,
                             "checkable": intent.checkable,
                             "observations": (len(intent.targets)
                                              - intent.checkable),
                             "notes": list(intent.notes)})
    if "attempt" not in seen:
        for index, failure in solution.failed_attempts:
            progress("attempt", {"index": index, "status": "rejected",
                                 "failure": failure})


def _measured_payload(solution, downloads: dict) -> dict:
    """What an analog answer is allowed to claim -- and it is DELIBERATELY a
    different event from "verified", because it is a weaker claim.

    A digital answer is checked row by row against an exhaustive table. This
    one is checked against the numbers the question named -- and when the
    question named none ("observe the waveforms" is a whole class of real
    question), NOTHING NUMERIC WAS CHECKED and the headline says so instead
    of reading as a pass over nothing. `checked` and `observations` travel
    with the payload so the page can keep that split visible.
    """
    token = secrets.token_urlsafe(16)
    downloads[token] = Path(solution.asc_path)
    cmp = solution.comparison

    # The evaluator comes from the MEASUREMENTS, not from a config field: the
    # experiment mapping is what actually ran, and each measurement records
    # who computed it.
    backend = verification = "unknown"
    for measurement in solution.experiment.values():
        backend = getattr(measurement, "backend", backend) or backend
        verification = getattr(measurement, "verification",
                               verification) or verification
        break

    units = {target.name: {"quantity": target.quantity, "unit": target.unit}
             for target in solution.intent.targets}
    if cmp.checked:
        headline = (f"meets the intent after {solution.attempts} design "
                    f"attempt(s)")
    else:
        headline = (f"ran and stayed in regime after {solution.attempts} "
                    f"design attempt(s) — NOTHING NUMERIC WAS CHECKED: the "
                    f"question stated no figure to hit")
    return {
        "download": token,
        "evaluator": backend,
        "verification": verification,
        "headline": headline,
        "checked": cmp.checked,
        "observations": cmp.observations,
        "summary": cmp.summary,
        "outcomes": [
            {"name": outcome.name,
             "quantity": units.get(outcome.name, {}).get("quantity", ""),
             "unit": units.get(outcome.name, {}).get("unit", ""),
             "wanted": outcome.wanted,
             "measured": outcome.measured,
             "ok": outcome.ok,
             "checked": outcome.checked,
             "reason": outcome.reason}
            for outcome in cmp.outcomes],
        "regimes_held": cmp.regimes_held,
        "regimes_failed": list(cmp.regimes_failed),
        "warnings": list(cmp.warnings) + list(solution.warnings),
        "basis": solution.basis.to_dict(),
        "attempts": solution.attempts,
        "designed_by": f"{solution.provider}/{solution.model}",
        # The claim the FILE can honestly make, and it is NOT the .circ's:
        # the .asc carries the whole experiment with one run active and the
        # rest commented, so its exact bytes were never handed to LTspice --
        # the per-run scratch files were. Shipped in the payload so the page
        # cannot render the stronger claim by forgetting which half answered.
        "file_note": (
            "This .asc carries the whole experiment: the first run active, "
            "the others commented out to uncomment. Those exact bytes are "
            "therefore NOT what LTspice ran — the per-run files were. What "
            "this file has is the emit/parse geometric round trip. The "
            "layout is generated mechanically: correct, not pretty."),
    }


def _verified_payload(solution, downloads: dict) -> dict:
    """What a verified answer is allowed to claim, and must.

    `evaluator` and `verification` are not optional decoration. A result that
    does not say who checked it is a result nobody can re-check, and the
    offline fallback evaluator computes the answer AND anything it would be
    checked against -- a reader has to be able to tell those apart.

    `basis` is here for the same reason one layer up: WHO checked it and WHAT
    it was checked against are separate questions. A gate-level answer is
    checked against a specification read from the question; an IC answer is
    checked against the part's own measured behaviour. Rendering both as a
    green "verified" and nothing else would make the stronger claim by
    default.
    """
    token = secrets.token_urlsafe(16)
    downloads[token] = Path(solution.circ_path)
    table = solution.table
    backend = getattr(table, "backend", "unknown")
    return {
        "download": token,
        "evaluator": backend,
        "verification": "internal" if "internal" in str(backend).lower()
                        else "external",
        "summary": solution.comparison.summary,
        "basis": solution.basis.to_dict(),
        "attempts": solution.attempts,
        "designed_by": f"{solution.provider}/{solution.model}",
        "columns": list(table.inputs) + list(table.outputs),
        "input_count": len(table.inputs),
        "rows": [list(row) for row in table.rows],
    }


def _mount_static(app: FastAPI, static_dir) -> None:
    """Serve the built frontend, if there is one.

    Optional on purpose: the API is the product and must be runnable and
    testable without a node toolchain anywhere near it.

    THREE PLACES ARE TRIED, and the honest account of why is worth more than
    the tidy one. `__file__/../..` was the only lookup, and it is the source
    tree only for an EDITABLE install -- which is how this repo is developed.
    That looked like a bug waiting for an image to expose it, so the image was
    changed and this was widened at the same time. The image then proved the
    prediction WRONG: `/app` is on `sys.path` ahead of site-packages there, so
    `__file__` still resolved into `/app` and the original lookup would have
    worked.

    It is kept, and the reason is not the bug that did not happen. The single
    lookup was correct only by way of a coincidence nobody chose -- the
    working directory and the install layout happening to line up. An explicit
    `OHMWORK_STATIC`, which the Dockerfile sets, does not depend on that. CI
    now fetches "/" and requires the real page, so the next arrangement that
    breaks it is caught rather than reasoned about.
    """
    from fastapi.staticfiles import StaticFiles

    candidates = [static_dir, os.environ.get("OHMWORK_STATIC"),
                  Path(__file__).resolve().parent.parent / "web" / "dist",
                  Path.cwd() / "web" / "dist"]
    for candidate in candidates:
        if candidate and (Path(candidate) / "index.html").is_file():
            directory = Path(candidate)
            break
    else:
        return

    app.mount("/assets", StaticFiles(directory=directory / "assets"),
              name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        # An unknown /api path is a 404, never the page. A single-page
        # fallback that answers every URL with index.html turns a typo'd or
        # renamed endpoint into a 200 full of HTML, and the caller reports
        # "unexpected token < in JSON" instead of "no such route".
        if full_path.startswith("api/"):
            raise HTTPException(404, "no such route")
        candidate = (directory / full_path).resolve()
        if full_path and candidate.is_file()                 and candidate.is_relative_to(directory.resolve()):
            return FileResponse(candidate)
        return FileResponse(directory / "index.html")


def build() -> FastAPI:
    """Entry point for `uvicorn ohmwork.server:build --factory`."""
    return create_app()


def main(argv=None) -> int:
    """`python -m ohmwork.server` — run it locally the way it runs hosted."""
    import uvicorn

    port = int(os.environ.get("PORT", "7860"))
    # Hosted deployments need to be reachable from their reverse proxy, so
    # their default remains 0.0.0.0. The desktop shell sets this to 127.0.0.1:
    # its backend is a private helper process, not a LAN service.
    host = os.environ.get("OHMWORK_BIND_HOST", "0.0.0.0")
    uvicorn.run(build(), host=host, port=port)
    return 0


if __name__ == "__main__":                              # pragma: no cover
    raise SystemExit(main())
