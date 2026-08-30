"""The provider seam. No test here touches the network or needs a key.

WHAT IS BEING PROTECTED. Two things, and neither is "does the API work" —
that can only be learned by calling it:

1. **The seam is real.** Swapping Groq for Anthropic must be configuration,
   not a code change, or the vendor has quietly become load-bearing. Both
   providers are driven here through the same `complete(...)` call with the
   same arguments, against fakes.

2. **A stale model id says what to do about it.** Hosted catalogues change
   faster than this repo will, so an id that is right today will 404 some
   day. The failure has to arrive as "here is what your account can see",
   not as a bare 404 — a guess that announces itself is recoverable.

The one thing deliberately NOT faked is the request shape. A fake that
accepted anything would let the payload drift and still pass, so the fakes
here record what they were handed and the tests assert on it.
"""

import os

import pytest

from ohmwork import llm


# ------------------------------------------------------------- fake SDKs


class FakeGroq:
    """Shaped like groq.Groq: .chat.completions.create and .models.list."""

    def __init__(self, reply="a caption", raise_error=None, models=()):
        self.reply, self.raise_error = reply, raise_error
        self._models = models or ("llama-3.3-70b-versatile", "whisper-large-v3")
        self.calls = []
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if outer.raise_error:
                    raise outer.raise_error
                message = type("M", (), {"content": outer.reply})()
                choice = type("C", (), {"message": message})()
                return type("R", (), {"choices": [choice]})()

        class Chat:
            completions = Completions()

        class Models:
            def list(self):
                data = [type("M", (), {"id": m})() for m in outer._models]
                return type("L", (), {"data": data})()

        self.chat, self.models = Chat(), Models()


class FakeAnthropic:
    def __init__(self, reply="a caption", raise_error=None):
        self.reply, self.raise_error = reply, raise_error
        self.calls = []
        outer = self

        class Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if outer.raise_error:
                    raise outer.raise_error
                block = type("B", (), {"type": "text", "text": outer.reply})()
                return type("R", (), {"content": [block],
                                      "stop_reason": "end_turn"})()

        self.messages = Messages()


def groq(**kw):
    return llm.GroqProvider(model="test-model", client=FakeGroq(**kw))


def anthropic(**kw):
    return llm.AnthropicProvider(model="test-model", client=FakeAnthropic(**kw))


# ------------------------------------------------------- the seam is real


def test_both_providers_answer_the_same_call():
    """If this ever needs two different call sites, the seam has failed."""
    for provider in (groq(), anthropic()):
        reply = provider.complete("say something", max_tokens=100)
        assert reply.text == "a caption"
        assert reply.model == "test-model"
        assert reply.provider == provider.name


def test_the_reply_carries_its_provenance():
    """Every other result in this project records what produced it; model
    output is not exempt."""
    reply = groq().complete("hello")
    assert (reply.provider, reply.model) == ("groq", "test-model")


def test_groq_default_provider(monkeypatch):
    monkeypatch.delenv("OHMWORK_LLM", raising=False)
    assert llm.DEFAULT_PROVIDER == "groq"


def test_an_unknown_provider_lists_the_known_ones():
    with pytest.raises(llm.LLMError) as excinfo:
        llm.get_provider("cohere")
    assert "groq" in str(excinfo.value) and "anthropic" in str(excinfo.value)


def test_a_missing_key_says_which_variable_to_set(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    pytest.importorskip("groq", reason="SDK absent: the import error fires first")
    with pytest.raises(llm.LLMError) as excinfo:
        llm.GroqProvider()
    assert "GROQ_API_KEY" in str(excinfo.value)
    assert "never in a file in this repo" in str(excinfo.value)


def test_a_missing_sdk_says_how_to_install_it(monkeypatch):
    if "groq" in os.sys.modules:                       # pragma: no cover
        pytest.skip("groq SDK is installed here")
    with pytest.raises(llm.LLMError) as excinfo:
        llm.GroqProvider()
    assert "pip install groq" in str(excinfo.value)


# -------------------------------------------------- request shape per vendor


def test_a_text_request_carries_no_image_payload():
    provider = groq()
    provider.complete("just text")
    assert provider.client.calls[0]["messages"][0]["content"] == "just text"


def test_groq_sends_images_as_openai_style_data_urls():
    provider = groq()
    provider.complete("read this", images=[llm.Image(b"\x89PNG", "image/png")])
    content = provider.client.calls[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "read this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_anthropic_sends_images_as_source_blocks():
    """The one place the two vendors genuinely differ, which is the whole
    reason there are two classes rather than one with a flag."""
    provider = anthropic()
    provider.complete("read this", images=[llm.Image(b"\x89PNG", "image/png")])
    content = provider.client.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[-1] == {"type": "text", "text": "read this"}


# --------------------------------------------- a stale model id self-corrects


class NotFound(Exception):
    status_code = 404


def test_an_unknown_model_names_the_models_the_account_can_see():
    provider = groq(raise_error=NotFound("model not found"))
    with pytest.raises(llm.LLMError) as excinfo:
        provider.complete("hello")
    message = str(excinfo.value)
    assert "test-model" in message
    assert "llama-3.3-70b-versatile" in message        # from models.list()
    assert "OHMWORK_LLM_MODEL" in message


def test_a_decommissioned_model_is_recognised_too():
    """Groq retires models rather than deleting them, and says so in prose
    rather than only in the status code."""
    provider = groq(raise_error=Exception("The model `x` has been decommissioned"))
    with pytest.raises(llm.LLMError, match="OHMWORK_LLM_MODEL"):
        provider.complete("hello")


def test_an_ordinary_failure_is_not_dressed_up_as_a_bad_model():
    """A rate limit reported as "pick another model" would send someone off
    to change configuration that was never wrong."""
    provider = groq(raise_error=Exception("rate limit exceeded"))
    with pytest.raises(llm.LLMError) as excinfo:
        provider.complete("hello")
    assert "OHMWORK_LLM_MODEL" not in str(excinfo.value)
    assert "rate limit" in str(excinfo.value)


def test_a_json_validation_refusal_arrives_as_MalformedReply():
    """The model ANSWERED and the answer was garbage. Not a transport
    failure, not a broken member, not a stale model id.

    MEASURED 2026-08-30, on the fourth attempt of a Q3 run: Groq refused
    the model's own output with `json_validate_failed` and an empty
    failed_generation, and the whole run died as "the model could not be
    reached" -- three attempts of real progress lost to one stochastic
    flub. The design loops treat MalformedReply as a spent attempt, so
    the classification is what keeps a run alive.
    """
    provider = groq(raise_error=Exception(
        "Error code: 400 - {'error': {'message': \"Failed to validate "
        "JSON. Please adjust your prompt.\", 'type': "
        "'invalid_request_error', 'code': 'json_validate_failed', "
        "'failed_generation': ''}}"))
    with pytest.raises(llm.MalformedReply) as excinfo:
        provider.complete("hello")
    message = str(excinfo.value)
    assert "request itself was fine" in message
    assert "OHMWORK_LLM_MODEL" not in message


def test_a_groq_rate_limit_arrives_as_RateLimited_so_a_pool_can_move_on():
    """The pool tells "slow down" from "this member is broken" by TYPE.

    Groq answering a 429 as a plain LLMError would have the pool disable a
    perfectly healthy account for the rest of the run.
    """
    provider = groq(raise_error=Exception(
        "Rate limit reached. Please try again in 8.5s"))
    with pytest.raises(llm.RateLimited) as excinfo:
        provider.complete("hello")
    assert excinfo.value.retry_after == pytest.approx(8.5)


# --------------------------------------------- Anthropic thinking headroom


def test_anthropic_requests_headroom_for_thinking():
    """Thinking bills as output and SHARES max_tokens on this API. Measured
    on the first paid Q3 run: three of six design calls returned EMPTY, the
    whole 5000-token budget spent thinking. The floor costs nothing -- only
    tokens actually produced are billed."""
    provider = anthropic()
    provider.complete("design something", max_tokens=5000)
    assert provider.client.calls[0]["max_tokens"] == 16000


def test_an_empty_anthropic_reply_is_a_MalformedReply():
    """A spent attempt, never a dead run -- and never passed upward as an
    empty string for a parser to blame on 'no JSON object'."""
    with pytest.raises(llm.MalformedReply, match="no text"):
        anthropic(reply="").complete("hello")


# ----------------------------------- Anthropic failures are classified too


def test_an_anthropic_rate_limit_arrives_as_RateLimited():
    """Written the day a PAID Anthropic key arrived. Every failure class
    that killed a free-tier run this week -- timeout, 429, overload -- would
    kill a paid run identically if Anthropic's SDK errors all wrap into one
    generic fatal LLMError. Same classification the other providers have."""
    error = type("RateLimitError", (Exception,), {"status_code": 429})(
        "rate limited")
    with pytest.raises(llm.RateLimited):
        anthropic(raise_error=error).complete("hello")


def test_an_anthropic_timeout_arrives_as_TransientNetworkError():
    error = type("APITimeoutError", (Exception,), {})("Request timed out")
    with pytest.raises(llm.TransientNetworkError):
        anthropic(raise_error=error).complete("hello")


def test_an_anthropic_overload_is_transient_not_fatal():
    """529 overloaded_error is Anthropic having a moment, not a dead key."""
    error = type("InternalServerError", (Exception,), {"status_code": 529})(
        "overloaded_error: Overloaded")
    with pytest.raises(llm.RateLimited):
        anthropic(raise_error=error).complete("hello")


def test_a_workspace_requirement_names_the_variable_to_set():
    """MEASURED on the FIRST paid call, 2026-08-31: an identity-linked key
    answers 400 'anthropic-workspace-id is required'. The raw message names
    a header; the fix a user can act on is an environment variable."""
    error = type("BadRequestError", (Exception,), {"status_code": 400})(
        "anthropic-workspace-id is required when authenticating with an "
        "identity-linked API key; send the id of the workspace this "
        "request acts in.")
    with pytest.raises(llm.LLMError) as caught:
        anthropic(raise_error=error).complete("hello")
    message = str(caught.value)
    assert "ANTHROPIC_WORKSPACE_ID" in message
    assert "console.anthropic.com" in message


def test_an_ordinary_anthropic_failure_stays_a_plain_LLMError():
    error = type("BadRequestError", (Exception,), {"status_code": 400})(
        "invalid request")
    with pytest.raises(llm.LLMError) as caught:
        anthropic(raise_error=error).complete("hello")
    assert not isinstance(caught.value,
                          (llm.RateLimited, llm.TransientNetworkError))


# ------------------------------------------------------------------ images


def test_an_unsupported_image_type_is_refused_by_name(tmp_path):
    bad = tmp_path / "page.bmp"
    bad.write_bytes(b"BM")
    with pytest.raises(llm.LLMError, match="page.bmp"):
        llm.Image.from_path(bad)


def test_a_png_loads_with_the_right_media_type(tmp_path):
    path = tmp_path / "page.png"
    path.write_bytes(b"\x89PNG\r\n")
    assert llm.Image.from_path(path).media_type == "image/png"


def test_vision_selects_the_multimodal_default(monkeypatch):
    """Extraction reads a photographed page. Picking a text-only model there
    fails at the worst moment — after the human has supplied the image."""
    monkeypatch.delenv("OHMWORK_LLM_MODEL", raising=False)
    assert llm.DEFAULT_VISION_MODEL["groq"] != llm.DEFAULT_MODEL["groq"]
