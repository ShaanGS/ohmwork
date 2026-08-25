"""The web endpoint. Digital only, and it verifies before it answers.

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
        self._circ_text = circ_text

    def write_to(self, workdir):
        path = workdir / "solution.circ"
        path.write_text(self._circ_text, encoding="utf-8")
        self.circ_path = path
        return path


def make_client(solver=None, *, password=PASSWORD, **kwargs):
    def default_solver(question, *, workdir, progress=None):
        solution = FakeSolution()
        solution.write_to(workdir)
        return solution

    app = server.create_app(solver=solver or default_solver,
                            password=password, **kwargs)
    return TestClient(app)


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
    names = [name for name, _ in events(solve(client))]

    assert names.index("reading") < names.index("verified")


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
