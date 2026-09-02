"""The web endpoint. Both halves, and it verifies before it answers.

THE RULE THIS ENDPOINT REWRITES, AND WHY THAT IS ALLOWED. The project's
standing rule said: no live-API hot path, because *"the server CANNOT
simulate -- so it could only ever produce results labelled UNVERIFIED"*. For
a DIGITAL question on a Linux host that premise is simply false. Logisim
Evolution is Java, it runs on the server, and it is the same external
evaluator the CLI uses. The rule's spirit -- never serve a number nobody
checked -- is not weakened here; it is enforced harder, because
`design.solve` RAISES rather than returning a circuit the evaluator did not
confirm.

So the operational test changes shape. It is no longer "does a request path
import llm.py" (it does now, deliberately). It is:

    **no response may carry a circuit or a table that Logisim did not
    confirm, and every response says which evaluator confirmed it.**

That is what this file protects, along with the two things a hosted tool with
somebody's API keys on it must not get wrong: letting a stranger in, and
letting a key out.

ANALOG (added 2026-08-30, for the desktop app whose backend this is): the
endpoint routes on `domain.classify` and DISCLOSES the routing before
anything runs. An analog answer arrives as its own "measured" event -- a
deliberately weaker claim than "verified", because numbers checked against
the question's own figures are not rows checked against an exhaustive table
-- and on a machine with no LTspice the question is REFUSED with the
download named, never answered unverified.
"""

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient           # noqa: E402

from ohmwork import server                          # noqa: E402


PASSWORD = "a-shared-passphrase"


class FakeSolution:
    """Shaped like design.Solution, with the fields the endpoint reads."""

    def __init__(self, *, failed=(), circ_text="<project/>"):
        self.attempts = len(failed) + 1
        self.failed_attempts = tuple(failed)
        self.provider, self.model = "cerebras", "llama-3.3-70b"
        self.spec = type("Spec", (), {
            "render": lambda self: "Y0 = EN & I0\nY1 = EN & I1",
            "inputs": ("EN", "I0", "I1"),
            "outputs": ("Y0", "Y1"),
        })()
        self.table = type("Table", (), {
            "inputs": ["EN", "I0", "I1"], "outputs": ["Y0", "Y1"],
            "rows": [[0, 0, 0, 0, 0], [1, 1, 0, 1, 0]],
            "backend": "logisim-evolution 4.1.0",
        })()
        self.comparison = type("Cmp", (), {
            "agrees": True, "summary": "32 of 32 rows agree"})()
        # The REAL basis object, not another stub: what a verified answer is
        # allowed to claim is decided in partcheck.py, and a fake that
        # invented its own wording here would let the two drift apart.
        from ohmwork.partcheck import spec_basis
        self.spec.expressions = {"Y0": "EN & I0", "Y1": "EN & I1"}
        self.basis = spec_basis(self.spec)
        self._circ_text = circ_text

    def write_to(self, workdir):
        path = workdir / "solution.circ"
        path.write_text(self._circ_text, encoding="utf-8")
        self.circ_path = path
        return path


class FakeAnalogSolution:
    """Shaped like analog.AnalogSolution, with the fields the endpoint reads.

    The intent, the comparison and the basis are the REAL classes, not stubs:
    what an analog answer is allowed to claim is decided in intent.py, and a
    fake that invented its own wording here would let the two drift apart --
    the same reasoning as FakeSolution using the real spec_basis.
    """

    def __init__(self, *, checked=1, failed=(), asc_text="Version 4.1\n"):
        import types

        from ohmwork import intent as intent_mod

        target = intent_mod.Target(
            name="vout", kind="dc_value", quantity="output voltage",
            unit="V", value=9.0, tolerance_pct=2.0)
        observation = intent_mod.Target(
            name="i_zener", kind="dc_current", quantity="zener current",
            unit="A", role="zener")
        self.intent = intent_mod.Intent(
            topology="series voltage regulator",
            targets=(target, observation), stated_values=(), frequency=None,
            notes=("tolerance of 2% was chosen, not stated",))
        outcomes = (
            intent_mod.TargetOutcome("vout", target.wanted(), 8.87064,
                                     True, True),
            intent_mod.TargetOutcome("i_zener", observation.wanted(), 0.0037,
                                     True, False),
        )
        self.comparison = intent_mod.IntentComparison(
            agrees=True,
            summary="1 of 2 stated targets carry a number; vout measured "
                    "8.87064 V against 9 V +/- 2%",
            outcomes=outcomes, observations=1, checked=checked,
            regimes_held=2, regimes_failed=())
        self.basis = intent_mod.intent_basis(
            self.intent, backend=None, plan={"measurements": []})
        self.experiment = {"vout": types.SimpleNamespace(
            backend="LTspice 26.0.2.1", verification="external")}
        self.attempts = len(failed) + 1
        self.failed_attempts = tuple(failed)
        self.provider, self.model = "mistral", "mistral-large-latest"
        self.warnings = ()
        self._asc_text = asc_text

    def write_to(self, workdir):
        path = workdir / "solution.asc"
        # newline="" so the bytes asserted by the download test are the bytes
        # written, not Windows' \r\n translation of them.
        path.write_text(self._asc_text, encoding="utf-8", newline="")
        self.asc_path = path
        return path


ANALOG_QUESTION = (
    "Design a series voltage regulator in LTspice that delivers 9 V to a "
    "1 kOhm load from a 15 V unregulated supply.")


def make_client(solver=None, *, analog_solver=None, password=PASSWORD,
                **kwargs):
    def default_solver(question, *, workdir, progress=None):
        solution = FakeSolution()
        solution.write_to(workdir)
        return solution

    def uninjected_analog(question, *, workdir, progress=None):
        # A test that routes analog without saying what should happen there
        # has made a mistake, and this must never fall through to the REAL
        # analog solver -- that would spend model tokens and require LTspice.
        raise AssertionError(
            "routed to the analog solver, but the test injected none")

    app = server.create_app(solver=solver or default_solver,
                            analog_solver=analog_solver or uninjected_analog,
                            password=password, **kwargs)
    return TestClient(app)


def default_analog_solver(question, *, workdir, progress=None):
    solution = FakeAnalogSolution()
    solution.write_to(workdir)
    return solution


def login(client, password=PASSWORD):
    return client.post("/api/login", json={"password": password})


def events(response):
    """The SSE stream as a list of (event name, parsed data)."""
    out = []
    name = None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: "):
            out.append((name, json.loads(line[6:])))
    return out


def solve(client, question="design a 2-to-4 decoder"):
    return client.post("/api/solve", json={"question": question})


# ------------------------------------------------------- letting people in


def test_a_solve_without_logging_in_is_refused():
    client = make_client()
    assert solve(client).status_code == 401


def test_the_right_password_gets_a_session_and_the_wrong_one_does_not():
    client = make_client()
    assert login(client, "not-it").status_code == 401
    assert solve(client).status_code == 401

    assert login(client).status_code == 200
    assert solve(client).status_code == 200


def test_a_server_with_no_password_configured_REFUSES_TO_START():
    """Fail closed, not open.

    An empty password is the one configuration mistake whose symptom is
    everything working perfectly -- for everyone on the internet, spending
    the owner's API keys.
    """
    with pytest.raises(server.ConfigError, match="OHMWORK_PASSWORD"):
        server.create_app(solver=lambda *a, **k: None, password="")


def test_login_attempts_are_rate_limited():
    """Five friends know the password; a script guessing it must not get
    thousands of tries against a free-tier key it would then drain."""
    client = make_client(max_login_attempts=3)
    for _ in range(3):
        assert login(client, "wrong").status_code == 401
    assert login(client, "wrong").status_code == 429
    # And the correct password does not slip through the block either.
    assert login(client, PASSWORD).status_code == 429


# --------------------------------------------------------- letting keys out


def test_a_provider_failure_never_echoes_a_key_into_the_response(monkeypatch):
    """Provider errors quote request context, and the pool quotes several of
    them at once. A key in that text goes straight to the browser."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_a_real_looking_secret")

    def exploding_solver(question, *, workdir, progress=None):
        raise RuntimeError("groq rejected key gsk_a_real_looking_secret")

    client = make_client(exploding_solver)
    login(client)
    response = solve(client)

    assert "gsk_a_real_looking_secret" not in response.text
    assert "REDACTED" in response.text


def test_the_health_check_reports_nothing_about_configuration():
    client = make_client()
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert not any("key" in str(v).lower() for v in body.values())


# ------------------------------------------------- what a solve is allowed
#                                                    to say


def test_the_reading_is_streamed_BEFORE_any_answer():
    """The loop can prove the circuit computes the spec. Nothing can prove
    the spec is the right reading of the question -- so the reading is not a
    detail tucked under the result, it comes first."""
    client = make_client()
    login(client)
    stream = events(solve(client))
    names = [name for name, _ in stream]

    assert names.index("reading") < names.index("verified")
    # And it carries the expressions as DATA beside the rendered text, so a
    # page can wrap one expression per line instead of scrolling sideways.
    reading = dict(stream)["reading"]
    assert set(reading["expressions"]) == set(reading["outputs"])


def test_every_rejected_attempt_is_reported_not_hidden():
    """A run that took three tries and reports only "verified" hides the two
    designs that were wrong, and how they were wrong is the most interesting
    thing about the run."""
    def solver(question, *, workdir, progress=None):
        solution = FakeSolution(failed=[(1, "4 rows disagree"),
                                        (2, "G3.in2 connects to no net")])
        solution.write_to(workdir)
        return solution

    client = make_client(solver)
    login(client)
    stream = events(solve(client))

    attempts = [data for name, data in stream if name == "attempt"]
    assert [a["index"] for a in attempts] == [1, 2]
    assert "4 rows disagree" in attempts[0]["failure"]


def test_the_verified_event_names_the_evaluator_that_confirmed_it():
    """A result that does not say who checked it is a result nobody can
    re-check. Same contract the manifest enforces for the library."""
    client = make_client()
    login(client)
    verified = dict(events(solve(client)))["verified"]

    assert verified["evaluator"] == "logisim-evolution 4.1.0"
    assert verified["verification"] == "external"
    assert verified["rows"] and verified["columns"]


def test_the_verified_event_also_says_WHAT_it_was_checked_against():
    """Who checked it and what it was checked against are two questions.

    A gate-level answer is checked against a specification read from the
    question's words; a question naming a 7447 is checked against the chip's
    own measured behaviour. Rendering both as a green "verified" and nothing
    else makes the stronger claim by default, so the basis travels with the
    payload -- including the limit it does not establish.
    """
    client = make_client()
    login(client)
    verified = dict(events(solve(client)))["verified"]

    assert verified["basis"]["kind"] == "spec"
    assert verified["basis"]["headline"]
    assert "reading of the question" in verified["basis"]["limit"]


def test_the_model_that_designed_it_is_reported_as_the_MEMBER_not_the_pool():
    client = make_client()
    login(client)
    verified = dict(events(solve(client)))["verified"]
    assert verified["designed_by"] == "cerebras/llama-3.3-70b"


def test_a_failed_solve_yields_an_error_and_NO_download():
    """The failure this whole project exists to prevent is handing someone a
    circuit nobody checked. `design.solve` raises rather than returning one;
    the endpoint must not invent a download out of the wreckage."""
    from ohmwork.design import DesignError

    def solver(question, *, workdir, progress=None):
        raise DesignError("no circuit matching the specification")

    client = make_client(solver)
    login(client)
    stream = dict(events(solve(client)))

    assert "verified" not in stream
    assert "no circuit matching" in stream["error"]["message"]
    assert stream["error"].get("download") is None


def test_an_analog_question_NEVER_reaches_the_digital_loop():
    """The incident, at the endpoint -- updated for routing.

    An LTspice question once reached the digital loop, which invented boolean
    signals for 12 V RMS waveforms and served the result as VERIFIED. The
    question is now ROUTED: it goes to the analog loop and the digital solver
    is never called, so that green badge remains impossible.
    """
    calls = []

    def digital_solver(question, *, workdir, progress=None):
        calls.append(question)
        raise AssertionError("an analog question reached the design loop")

    client = make_client(digital_solver, analog_solver=default_analog_solver)
    login(client)
    stream = dict(events(solve(
        client,
        "Design a regulated 6.2 V supply in LTspice with a bridge rectifier, "
        "a 470 uF filter and a Zener regulator on a 1 kOhm load.")))

    assert calls == []
    # The answer is MEASURED, never "verified": numbers checked against the
    # question's own figures are a weaker claim than rows checked against an
    # exhaustive table, and the two must not share an event name.
    assert "verified" not in stream
    assert "measured" in stream


# ------------------------------------------------------- the analog half


def test_the_routing_is_disclosed_before_anything_else():
    """Which half answers is a guess made from the question's words, so it is
    the FIRST event on the stream -- disclosure, exactly as the CLI prints
    it, not a silent decision."""
    client = make_client(analog_solver=default_analog_solver)
    login(client)

    stream = events(solve(client))
    assert stream[0][0] == "routing"
    assert stream[0][1]["domain"] == "digital"
    assert stream[0][1]["reason"]

    stream = events(solve(client, ANALOG_QUESTION))
    assert stream[0][0] == "routing"
    assert stream[0][1]["domain"] == "analog"
    assert "ltspice" in stream[0][1]["reason"].lower()


def test_an_analog_question_with_no_ltspice_is_refused_naming_the_download():
    """The product contract: without LTspice, an analog question is REFUSED
    with a message naming the download -- never answered unverified."""
    def no_ltspice(question, *, workdir, progress=None):
        raise FileNotFoundError("LTspice not found. Looked in: F:\\...")

    client = make_client(analog_solver=no_ltspice)
    login(client)
    stream = dict(events(solve(client, ANALOG_QUESTION)))

    assert "measured" not in stream and "verified" not in stream
    assert "error" not in stream
    refused = stream["refused"]
    assert refused["download"] is None
    assert "LTspice" in refused["message"]
    assert "analog.com" in refused["message"]


def test_a_digital_file_error_is_NOT_blamed_on_ltspice():
    """The FileNotFoundError-means-install-LTspice story belongs to the
    analog path only; a digital solver losing a file is a plain error."""
    def solver(question, *, workdir, progress=None):
        raise FileNotFoundError("logisim went missing mid-run")

    client = make_client(solver)
    login(client)
    stream = dict(events(solve(client)))

    assert "refused" not in stream
    assert "logisim went missing" in stream["error"]["message"]


def test_the_measured_event_carries_the_weaker_claim_and_its_evaluator():
    """What an analog answer is allowed to say, and must.

    The evaluator comes from the MEASUREMENTS. The basis is the intent basis,
    with its limit -- which has no digital counterpart: meeting a target is
    not being a good design. And the file note says plainly that the .asc's
    exact bytes are not what LTspice ran.
    """
    client = make_client(analog_solver=default_analog_solver)
    login(client)
    measured = dict(events(solve(client, ANALOG_QUESTION)))["measured"]

    assert measured["evaluator"] == "LTspice 26.0.2.1"
    assert measured["verification"] == "external"
    assert measured["basis"]["kind"] == "intent"
    assert "weaker" in measured["basis"]["headline"]
    assert "meets the intent" in measured["headline"]
    assert measured["designed_by"] == "mistral/mistral-large-latest"
    assert "NOT what LTspice ran" in measured["file_note"]

    outcomes = {o["name"]: o for o in measured["outcomes"]}
    assert outcomes["vout"]["measured"] == 8.87064
    assert outcomes["vout"]["ok"] and outcomes["vout"]["checked"]
    # An observation is measured and reported, never counted as a pass.
    assert outcomes["i_zener"]["checked"] is False
    assert measured["checked"] == 1 and measured["observations"] == 1


def test_zero_checked_targets_reads_as_nothing_checked_not_as_a_pass():
    """The Q3 lesson: "0 of 5 targets carry a number, and LTspice met every
    one" reads as a pass over nothing at all. The headline must say that
    nothing numeric could fail or pass."""
    def solver(question, *, workdir, progress=None):
        solution = FakeAnalogSolution(checked=0)
        solution.write_to(workdir)
        return solution

    client = make_client(analog_solver=solver)
    login(client)
    measured = dict(events(solve(client, ANALOG_QUESTION)))["measured"]

    assert "NOTHING NUMERIC WAS CHECKED" in measured["headline"]
    assert "meets the intent" not in measured["headline"]


def test_an_analog_reading_and_attempts_are_backfilled_too():
    """The same two non-negotiables as the digital path: the reading, because
    nothing downstream can prove the intent is the right reading of the
    question, and the rejected attempts, because a run reporting only its
    success hides the designs that were wrong."""
    def solver(question, *, workdir, progress=None):
        solution = FakeAnalogSolution(
            failed=[(1, "vout measured 7.1 V against 9 V +/- 2%")])
        solution.write_to(workdir)
        return solution

    client = make_client(analog_solver=solver)
    login(client)
    stream = events(solve(client, ANALOG_QUESTION))
    names = [name for name, _ in stream]

    assert names.index("reading") < names.index("measured")
    reading = dict(stream)["reading"]
    assert "series voltage regulator" in reading["intent"]
    # The same reading as data, for a page that lays it out rather than
    # printing the monospace block: every target names WHAT it is measured
    # on and whether a number could fail.
    assert reading["topology"]
    assert {"name", "quantity", "where", "wanted", "checked"} <= set(
        reading["targets"][0])
    attempts = [data for name, data in stream if name == "attempt"]
    assert attempts and "7.1 V" in attempts[0]["failure"]


def test_the_asc_download_is_the_file_named_as_an_asc():
    client = make_client(analog_solver=default_analog_solver)
    login(client)
    measured = dict(events(solve(client, ANALOG_QUESTION)))["measured"]

    downloaded = client.get(f"/api/circuit/{measured['download']}")
    assert downloaded.status_code == 200
    assert downloaded.text == "Version 4.1\n"
    assert ".asc" in downloaded.headers["content-disposition"]


def test_every_provider_being_out_of_capacity_is_NOT_reported_as_a_bad_design():
    """Three outcomes, not two.

    "no verified circuit" in red says YOUR CIRCUIT FAILED. When every free
    tier is spent, no circuit was ever designed and nothing about the
    question was wrong -- there was nobody to ask. Collapsing the two sends
    someone off to rewrite a question that was fine.
    """
    from ohmwork.llm import PoolExhausted

    def solver(question, *, workdir, progress=None):
        raise PoolExhausted(
            "none of the 2 model provider(s) could answer right now.",
            members=[("groq", "busy or rate limited right now"),
                     ("gemini", "free quota for today is spent; it resets "
                                "tomorrow")])

    client = make_client(solver)
    login(client)
    stream = dict(events(solve(client)))

    assert "error" not in stream
    assert "verified" not in stream
    assert stream["unavailable"]["download"] is None
    members = dict(stream["unavailable"]["members"])
    assert "resets tomorrow" in members["gemini"]


def test_an_empty_question_is_refused_before_a_single_model_call():
    calls = []

    def solver(question, *, workdir, progress=None):
        calls.append(question)
        raise AssertionError("should not have been reached")

    client = make_client(solver)
    login(client)
    assert client.post("/api/solve", json={"question": "   "}).status_code == 400
    assert calls == []


def test_a_question_longer_than_the_limit_is_refused():
    client = make_client(max_question_chars=100)
    login(client)
    response = client.post("/api/solve", json={"question": "x" * 101})
    assert response.status_code == 400


# -------------------------------------------------------- first-run status
#
# PRD gap 3: no LTspice, or no key, otherwise surfaces as a confusing
# failure at solve time. /api/status says so plainly, before the first
# question -- names only, never values, never paths.


def _clear_provider_keys(monkeypatch):
    from ohmwork.llm import POOL_ORDER, env_var_for

    for name in POOL_ORDER:
        monkeypatch.delenv(env_var_for(name), raising=False)


def test_the_status_route_requires_a_session():
    """It names the owner's configured providers; an anonymous probe gets
    the health route, which says nothing."""
    client = make_client()
    assert client.get("/api/status").status_code == 401


def test_status_names_configured_providers_and_NEVER_their_values(monkeypatch):
    _clear_provider_keys(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_a_real_looking_secret")

    client = make_client()
    login(client)
    response = client.get("/api/status")
    body = response.json()

    assert body["providers"] == ["groq"]
    assert "gsk_a_real_looking_secret" not in response.text


def test_status_with_no_key_says_so_and_names_where_to_get_one(monkeypatch):
    _clear_provider_keys(monkeypatch)

    client = make_client()
    login(client)
    body = client.get("/api/status").json()

    assert body["providers"] == []
    # The page needs somewhere to send a person, not just a diagnosis.
    assert body["signup"]["groq"].startswith("https://")


def test_status_reports_missing_ltspice_with_the_download_named(monkeypatch):
    import ohmwork.simulate as simulate

    def not_found():
        raise FileNotFoundError("LTspice not found")

    monkeypatch.setattr(simulate, "locate_ltspice", not_found)
    client = make_client()
    login(client)
    body = client.get("/api/status").json()

    assert body["analog"]["available"] is False
    assert "analog.com" in body["analog"]["detail"]
    assert "refused" in body["analog"]["detail"]


def test_status_reports_an_internal_only_digital_evaluator(monkeypatch):
    """Both directions of the probe, so the check reads as a discrimination
    rather than one that happens never to fire on this machine."""
    import ohmwork.logisim_backend as logisim_backend

    def not_found():
        raise FileNotFoundError("no logisim")

    monkeypatch.setattr(logisim_backend, "locate_logisim", not_found)
    client = make_client()
    login(client)
    body = client.get("/api/status").json()

    # No fallback evaluator exists (InternalLogicBackend raises on first
    # use), so the honest status is "not available", never "internal".
    assert body["digital"]["available"] is False
    assert body["digital"]["verification"] is None
    assert "refused" in body["digital"]["detail"]
    assert "NOT found" in body["digital"]["detail"]


def test_status_reports_present_evaluators_without_leaking_paths(monkeypatch):
    import ohmwork.logisim_backend as logisim_backend
    import ohmwork.simulate as simulate
    from pathlib import Path

    monkeypatch.setattr(simulate, "locate_ltspice",
                        lambda: Path("F:/secret-drive/LTspice.exe"))
    monkeypatch.setattr(logisim_backend, "locate_logisim",
                        lambda: Path("C:/somewhere/logisim.exe"))
    client = make_client()
    login(client)
    response = client.get("/api/status")
    body = response.json()

    assert body["analog"]["available"] is True
    assert body["digital"]["verification"] == "external"
    # Found/not-found is the fact a student needs; a server path in a
    # browser is nobody's business.
    assert "secret-drive" not in response.text
    assert "somewhere" not in response.text


# ------------------------------------------------------------- the download


def test_the_verified_circuit_can_be_downloaded_and_is_the_file_that_ran():
    client = make_client()
    login(client)
    verified = dict(events(solve(client)))["verified"]

    downloaded = client.get(f"/api/circuit/{verified['download']}")
    assert downloaded.status_code == 200
    # Byte-identical to what the solver wrote and Logisim evaluated. A .circ
    # has one run and no directives, so unlike the LTspice deliverable the
    # bytes shipped really are the bytes that were checked.
    assert downloaded.text == "<project/>"
    assert ".circ" in downloaded.headers["content-disposition"]


def test_a_download_needs_a_session_too():
    client = make_client()
    login(client)
    token = dict(events(solve(client)))["verified"]["download"]

    client.cookies.clear()
    assert client.get(f"/api/circuit/{token}").status_code == 401


def test_an_unknown_download_token_is_a_404_not_a_traceback():
    client = make_client()
    login(client)
    assert client.get("/api/circuit/nope").status_code == 404
    # A token is a dict key, never a path fragment, so traversal has nothing
    # to traverse. Written with the escape kept intact, because an unescaped
    # one is normalised away by the HTTP client and the test would then be
    # checking the client rather than the server.
    assert client.get("/api/circuit/..%2f..%2fetc%2fpasswd").status_code == 404


def test_an_unknown_api_route_is_a_404_not_the_single_page_app(tmp_path):
    """A SPA fallback that answers every URL with index.html turns a renamed
    endpoint into a 200 full of HTML, and the caller reports "unexpected
    token < in JSON" instead of "no such route"."""
    (tmp_path / "index.html").write_text("<html>the app</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    client = make_client(static_dir=tmp_path)

    assert client.get("/api/nonsense").status_code == 404
    assert client.get("/some/deep/link").text == "<html>the app</html>"


def test_the_built_page_is_found_by_an_INSTALLED_package_too(tmp_path,
                                                             monkeypatch):
    """Found by the first real Docker build, and it could not have been found
    anywhere else.

    The page was looked for at `__file__/../../web/dist`, which is the source
    tree only for an EDITABLE install -- how this repo is developed. In an
    image the package is installed properly, that path lands inside
    site-packages, and the API kept working while the page went blank. So the
    lookup takes an explicit `OHMWORK_STATIC`, and the environment wins.
    """
    built = tmp_path / "dist"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    monkeypatch.setenv("OHMWORK_STATIC", str(built))

    client = TestClient(server.create_app(solver=lambda *a, **k: None,
                                          password=PASSWORD))
    page = client.get("/")
    assert page.status_code == 200
    assert 'id="root"' in page.text
    # ...and an unknown /api route is still a 404, never the page.
    assert client.get("/api/nope").status_code == 404


def test_desktop_can_bind_the_backend_to_loopback(monkeypatch):
    """The desktop backend is a private helper, never a LAN service.

    A public hosted service needs 0.0.0.0 for its reverse proxy, which remains
    the default. The desktop shell explicitly selects loopback and this test
    pins that distinction at the one place it can accidentally disappear.
    """
    import sys
    import types

    called = {}
    monkeypatch.setenv("OHMWORK_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "47123")
    monkeypatch.setattr(server, "build", lambda: object())
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(
        run=lambda app, *, host, port: called.update(app=app, host=host, port=port)
    ))

    assert server.main() == 0
    assert called["host"] == "127.0.0.1"
    assert called["port"] == 47123


# ---------------------------------------------- login hardening (2026-09-02)
#
# Found in the pre-launch review: the one unauthenticated route that reads a
# body read it unbounded and 500'd on anything that was not a JSON object,
# and the failed-login bucket never expired -- behind a reverse proxy every
# client is one host, so ten wrong guesses from one person locked everyone
# out until the process restarted.

def test_a_login_body_that_is_not_a_json_object_is_a_400_not_a_traceback():
    client = make_client()
    assert client.post("/api/login", content=b"not json",
                       headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/login", content=b"[1, 2, 3]",
                       headers={"Content-Type": "application/json"}).status_code == 400


def test_an_oversized_login_body_is_refused_before_it_is_read():
    from ohmwork.server import MAX_LOGIN_BODY
    client = make_client()
    huge = b'{"password": "' + b"x" * (MAX_LOGIN_BODY * 4) + b'"}'
    assert client.post("/api/login", content=huge,
                       headers={"Content-Type": "application/json"}).status_code == 413


def test_the_failed_login_bucket_expires(monkeypatch):
    import time as _time
    from ohmwork import server as server_module
    client = make_client(max_login_attempts=2)
    now = [1_000_000.0]
    monkeypatch.setattr(server_module.time, "time", lambda: now[0])
    assert login(client, "wrong").status_code == 401
    assert login(client, "wrong").status_code == 401
    assert login(client, PASSWORD).status_code == 429     # locked, in window
    now[0] += server_module.LOGIN_WINDOW_SECONDS + 1
    assert login(client, PASSWORD).status_code == 200     # window passed
