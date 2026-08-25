""".env loading, and the two ways a config file goes wrong in silence.

Both failures here produce an authentication error with no plausible cause,
which is the worst kind of bug to hand someone setting a project up for the
first time:

1. **A file quietly outranking a real environment variable.** An env var that
   is already set was set deliberately — by a CI secret, a deployment, a
   shell export — and a stray .env in a parent directory must never win
   against it.
2. **A credential truncated at a `#`.** Stripping inline comments looks
   helpful until a key contains a hash, at which point the key is silently
   wrong and nothing says so.

There is also a test that the ignore rule exists, because the cost of it not
existing is unbounded and the cost of checking is one assert.
"""

from pathlib import Path

from ohmwork import llm

ROOT = Path(__file__).parent.parent


# ------------------------------------------------------------- the parser

def test_plain_assignments():
    assert llm.parse_env_file("A=1\nB=two") == {"A": "1", "B": "two"}


def test_comments_and_blank_lines_are_ignored():
    text = "# a comment\n\nA=1\n   # indented comment\nB=2\n"
    assert llm.parse_env_file(text) == {"A": "1", "B": "2"}


def test_quotes_are_stripped():
    assert llm.parse_env_file('A="quoted"\nB=\'single\'') == {
        "A": "quoted", "B": "single"}


def test_a_stray_export_is_tolerated():
    """People paste from shell instructions. Refusing that is pedantry."""
    assert llm.parse_env_file("export A=1") == {"A": "1"}


def test_a_hash_inside_a_value_is_NOT_treated_as_a_comment():
    """The failure this avoids: a key silently truncated at a '#', producing
    an auth error with no visible cause."""
    assert llm.parse_env_file("KEY=abc#def") == {"KEY": "abc#def"}


def test_an_equals_sign_inside_a_value_survives():
    assert llm.parse_env_file("KEY=a=b=c") == {"KEY": "a=b=c"}


# ------------------------------------------------------------- the loader

def test_a_real_environment_variable_wins_over_the_file(tmp_path, monkeypatch):
    """Setting an env var is a more deliberate act than leaving a line in a
    file, and a .env in some parent directory must not override a CI secret."""
    env = tmp_path / ".env"
    env.write_text("OHMWORK_TEST_KEY=from_file\n")
    monkeypatch.setenv("OHMWORK_TEST_KEY", "from_environment")

    applied = llm.load_env_file(env)
    assert applied == {}
    assert llm.os.environ["OHMWORK_TEST_KEY"] == "from_environment"


def test_the_file_fills_in_what_is_not_already_set(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("OHMWORK_TEST_KEY=from_file\n")
    monkeypatch.delenv("OHMWORK_TEST_KEY", raising=False)

    assert llm.load_env_file(env) == {"OHMWORK_TEST_KEY": "from_file"}
    assert llm.os.environ["OHMWORK_TEST_KEY"] == "from_file"
    monkeypatch.delenv("OHMWORK_TEST_KEY", raising=False)


def test_a_missing_file_is_not_an_error():
    """Everything except the model layer works without one."""
    assert llm.load_env_file(ROOT / "no-such.env") == {}


def test_the_file_is_found_from_a_subdirectory(tmp_path):
    """People run the CLI from wherever they happen to be."""
    (tmp_path / ".env").write_text("A=1\n")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert llm.find_env_file(deep) == tmp_path / ".env"


# ------------------------------------------------- the rule that matters

def test_dotenv_is_gitignored():
    """A key committed once lives in the history forever, even after it is
    deleted from the working tree. This assert costs nothing; not having the
    rule costs an unbounded amount."""
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "\n.env\n" in ignored


def test_the_committed_template_holds_no_real_key():
    """.env.example ships in the repo. If a real key ever lands in it, it
    lands in the history."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    # Every key line, named or not: the template grew from one vendor to
    # five, and a check that knows only the vendors present when it was
    # written stops protecting the ones added afterwards.
    checked = 0
    for raw in text.splitlines():
        line = raw.strip().removeprefix("#").strip()
        name, _, value = line.partition("=")
        if not value or not name.endswith("_API_KEY"):
            continue
        checked += 1
        assert "replace_me" in value, f"a real key is in the template: {name}"
    assert checked >= 2, "the template stopped showing how to set a key"
