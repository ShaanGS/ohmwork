"""The key pool: many free accounts standing in for one paid one.

WHY THIS EXISTS. A free Groq account serves 8000 tokens per MINUTE, prompt
plus max_tokens, which is roughly one design call a minute. The design loop
makes three or four calls, so a single solve spent minutes sitting in a
rate-limit pause. That is tolerable at a terminal and impossible behind a web
request.

WHAT IS BEING PROTECTED, and none of it is "does the API work" -- that can
only be learned by calling it:

1. **A rate limit costs the next member's latency, not a minute.** The whole
   point. A member that says "slow down" is set aside and the next one
   answers immediately.

2. **The wait is a LAST resort, not the first move.** When every member is
   cooling there is nothing left to do but wait, so the pool waits -- but it
   waits until the EARLIEST member wakes, not a fixed minute, and it refuses
   to wait longer than its budget rather than hanging.

3. **Provenance survives pooling.** Every other result in this project names
   the tool that produced it. A reply must carry the MEMBER that answered,
   never the pool -- "pool" is not a model id, and a manifest recording one
   would be a lie in the field built to prevent lies.

4. **A degraded pool announces itself.** A member dropped for a missing key
   or a dead endpoint is recorded with the reason. Answering from two of four
   members while looking exactly like answering from four is the project's
   standing failure mode: an unrun check that reads as a passed one.

5. **The global model id is NOT sprayed across members.** OHMWORK_LLM_MODEL
   names a model on ONE provider's catalogue. Applying a Groq id to Gemini
   would 404 every member but one, which is worse than not pooling at all.
"""

import pytest

from ohmwork import llm


# ---------------------------------------------------------------- fakes


class FakeMember:
    """Shaped like a provider: .name, .model, .complete(...)."""

    def __init__(self, name, *, model="m", replies=(), errors=()):
        self.name, self.model = name, model
        self.calls = []
        # errors[i] is raised on call i if not None; otherwise a reply.
        self._errors = list(errors)
        self._replies = list(replies) or ["ok"]

    def complete(self, prompt, *, images=(), max_tokens=4000,
                 temperature=0.2):
        index = len(self.calls)
        self.calls.append(prompt)
        if index < len(self._errors) and self._errors[index] is not None:
            raise self._errors[index]
        text = self._replies[min(index, len(self._replies) - 1)]
        return llm.Reply(text=text, model=self.model, provider=self.name)


class FakeClock:
    """A clock and a sleep that move together, and record every wait."""

    def __init__(self):
        self.now, self.waits = 0.0, []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def make_pool(*members, **kwargs):
    clock = kwargs.pop("clock", None) or FakeClock()
    pool = llm.Pool(list(members), clock=clock.time, sleep=clock.sleep,
                    **kwargs)
    return pool, clock


# --------------------------------------------------- failover, the point


def test_the_first_healthy_member_answers_and_the_rest_are_untouched():
    a, b = FakeMember("a"), FakeMember("b")
    pool, clock = make_pool(a, b)

    reply = pool.complete("hello")

    assert reply.text == "ok"
    assert len(a.calls) == 1 and b.calls == []
    assert clock.waits == []


def test_a_rate_limited_member_is_skipped_and_the_next_answers_immediately():
    a = FakeMember("a", errors=[llm.RateLimited("slow down", retry_after=60)])
    b = FakeMember("b", replies=["from b"])
    pool, clock = make_pool(a, b)

    reply = pool.complete("hello")

    assert reply.text == "from b"
    # The whole reason the pool exists: a rate limit costs the next member's
    # latency, not sixty seconds.
    assert clock.waits == []


def test_the_reply_names_the_member_that_answered_never_the_pool():
    a = FakeMember("a", errors=[llm.RateLimited("slow down")])
    b = FakeMember("b", model="llama-3.3-70b")
    pool, _ = make_pool(a, b)

    reply = pool.complete("hello")

    assert (reply.provider, reply.model) == ("b", "llama-3.3-70b")
    assert reply.provider != pool.name


def test_a_cooling_member_stays_cool_on_the_next_call():
    a = FakeMember("a", errors=[llm.RateLimited("slow down", retry_after=60)])
    b = FakeMember("b")
    pool, clock = make_pool(a, b)

    pool.complete("one")
    pool.complete("two")

    # `a` was tried once, learned it was limited, and was not tried again --
    # a pool that retried the limited member every call would spend its whole
    # budget rediscovering the same 429.
    assert len(a.calls) == 1 and len(b.calls) == 2

    clock.now += 61
    pool.complete("three")
    assert len(a.calls) == 2


def test_when_everything_is_cooling_the_pool_waits_for_the_EARLIEST_wake():
    a = FakeMember("a", errors=[llm.RateLimited("wait", retry_after=90)])
    b = FakeMember("b", errors=[llm.RateLimited("wait", retry_after=20)])
    pool, clock = make_pool(a, b)

    reply = pool.complete("hello")

    # It waited 20 seconds for b, not 90 for a, and not a fixed minute.
    assert clock.waits == [pytest.approx(20)]
    assert reply.provider == "b"


def test_the_pool_refuses_to_wait_longer_than_its_budget():
    a = FakeMember("a", errors=[llm.RateLimited("wait", retry_after=600)])
    pool, clock = make_pool(a, max_wait=120)

    with pytest.raises(llm.LLMError) as caught:
        pool.complete("hello")

    assert clock.waits == []
    assert "600" in str(caught.value)


def test_a_hard_failure_fails_over_but_is_reported_not_swallowed():
    a = FakeMember("a", errors=[llm.LLMError("cerebras does not serve 'x'")])
    b = FakeMember("b")
    pool, _ = make_pool(a, b)

    reply = pool.complete("hello")

    assert reply.provider == "b"
    # The answer arrived, so nothing raised -- which is exactly when a broken
    # member disappears silently unless something records it.
    assert any(incident.member == "a" for incident in pool.incidents)
    assert any("does not serve" in incident.reason
               for incident in pool.incidents)


def test_every_member_failing_names_every_failure_not_just_the_last():
    a = FakeMember("a", errors=[llm.LLMError("key rejected")])
    b = FakeMember("b", errors=[llm.LLMError("endpoint unreachable")])
    pool, _ = make_pool(a, b)

    with pytest.raises(llm.LLMError) as caught:
        pool.complete("hello")

    message = str(caught.value)
    assert "key rejected" in message and "endpoint unreachable" in message
    assert "a" in message and "b" in message


def test_an_empty_pool_says_which_keys_to_set_rather_than_failing_obscurely():
    with pytest.raises(llm.LLMError) as caught:
        llm.Pool([])
    assert "no members" in str(caught.value).lower()


# ------------------------------------------------- membership from config


def test_membership_comes_from_the_env_and_skips_members_with_no_key(monkeypatch):
    monkeypatch.setenv("OHMWORK_LLM_POOL", "groq,cerebras,gemini")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("CEREBRAS_API_KEY", "y")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    names, skipped = llm.planned_pool()

    assert names == ["groq", "cerebras"]
    assert [name for name, _ in skipped] == ["gemini"]
    assert "GEMINI_API_KEY" in skipped[0][1]


def test_membership_defaults_to_every_provider_with_a_key(monkeypatch):
    monkeypatch.delenv("OHMWORK_LLM_POOL", raising=False)
    for name in llm.POOL_ORDER:
        monkeypatch.delenv(llm.env_var_for(name), raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")

    names, _ = llm.planned_pool()

    assert names == ["openrouter"]


def test_an_unknown_pool_member_is_named_and_the_known_ones_listed(monkeypatch):
    monkeypatch.setenv("OHMWORK_LLM_POOL", "groq,groqq")
    monkeypatch.setenv("GROQ_API_KEY", "x")

    with pytest.raises(llm.LLMError) as caught:
        llm.planned_pool()

    assert "groqq" in str(caught.value)
    assert "cerebras" in str(caught.value)


def test_the_global_model_id_is_not_applied_to_every_member(monkeypatch):
    """OHMWORK_LLM_MODEL names a model on ONE catalogue.

    Spraying a Groq id across Gemini and Cerebras would 404 every member but
    one -- a pool that is worse than no pool, and whose failure looks like
    four dead vendors rather than one misapplied setting.
    """
    monkeypatch.setenv("OHMWORK_LLM_MODEL", "openai/gpt-oss-120b")

    assert llm.model_for("cerebras") == llm.DEFAULT_MODEL["cerebras"]
    assert llm.model_for("gemini") == llm.DEFAULT_MODEL["gemini"]


def test_a_member_model_can_be_overridden_on_its_own(monkeypatch):
    monkeypatch.setenv("OHMWORK_LLM_MODEL_CEREBRAS", "qwen-3-32b")
    assert llm.model_for("cerebras") == "qwen-3-32b"
    assert llm.model_for("gemini") == llm.DEFAULT_MODEL["gemini"]


def test_every_pool_provider_has_a_default_model_and_a_key_variable():
    """A member with no default model cannot be constructed from config.

    Cheap, and it is the check that fires when someone adds a provider to one
    table and forgets the other two.
    """
    for name in llm.POOL_ORDER:
        assert name in llm.DEFAULT_MODEL, name
        assert llm.env_var_for(name).endswith("_API_KEY"), name
