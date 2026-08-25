"""The one client that talks to every OpenAI-shaped free tier.

Cerebras, Gemini, OpenRouter and Mistral all expose the SAME chat-completions
endpoint, so they are ONE class with a base URL rather than four SDKs. That is
a claim about the wire format, and a claim about a wire format is exactly the
kind of thing that rots silently -- so the request this builds is asserted
field by field against a fake transport rather than trusted.

No test here touches the network or needs a key. What is being protected:

1. **The request shape.** A fake that accepted anything would let the payload
   drift and still pass.

2. **A 429 arrives as RateLimited, carrying HOW LONG to wait.** The pool's
   entire behaviour hangs off that distinction: a rate limit means try the
   next member, and anything else means this member is broken. Collapsing the
   two would either spin forever on a dead key or give up on a live one.

3. **A stale model id still says what to do about it**, as it does for Groq.
   The catalogues these ids come from move faster than this repo.
"""

import json

import pytest

from ohmwork import llm


class FakeTransport:
    """Records requests; replies from a queued list of (status, headers, body)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append({"method": method, "url": url,
                              "headers": headers,
                              "payload": json.loads(body) if body else None})
        if not self.responses:
            raise AssertionError(f"no fake response queued for {url}")
        return self.responses.pop(0)


def chat_reply(text="ok"):
    body = json.dumps({"choices": [{"message": {"content": text}}]})
    return (200, {}, body)


def make(name="cerebras", transport=None, model="a-model"):
    return llm.OpenAICompatibleProvider(
        name, model=model, api_key="key-123",
        transport=transport or FakeTransport(chat_reply()))


# ------------------------------------------------------- the request shape


def test_the_request_goes_to_the_providers_chat_completions_endpoint():
    transport = FakeTransport(chat_reply("hello"))
    provider = make("cerebras", transport)

    reply = provider.complete("a prompt", max_tokens=1500, temperature=0.1)

    request, = transport.requests
    assert request["url"] == llm.BASE_URL["cerebras"] + "/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer key-123"
    # Not decoration. Without it Groq's edge answered 403 "error code: 1010",
    # a Cloudflare bot challenge that looks like neither an auth failure nor
    # a model problem -- measured 2026-08-26, on the first live call.
    assert "ohmwork" in request["headers"]["User-Agent"]
    assert request["payload"] == {
        "model": "a-model",
        "messages": [{"role": "user", "content": "a prompt"}],
        "max_tokens": 1500,
        "temperature": 0.1,
    }
    assert reply.text == "hello"
    assert (reply.provider, reply.model) == ("cerebras", "a-model")


def test_an_image_is_sent_as_an_openai_image_url_data_block():
    """Every provider here takes images the OpenAI way, not Anthropic's.

    Gemini's compatibility endpoint is the reason this matters: it is the
    only free tier measured so far that accepts an image at all, which is
    what unblocks reading a photographed lab-manual page.
    """
    transport = FakeTransport(chat_reply())
    provider = make("gemini", transport)
    image = llm.Image(b"\x89PNG-ish", "image/png")

    provider.complete("read this", images=[image])

    content = transport.requests[0]["payload"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "read this"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


# ---------------------------------------------------------- rate limiting


def test_a_429_becomes_RateLimited_with_the_retry_after_header():
    transport = FakeTransport((429, {"retry-after": "17"}, "slow down"))
    provider = make(transport=transport)

    with pytest.raises(llm.RateLimited) as caught:
        provider.complete("hi")

    assert caught.value.retry_after == pytest.approx(17)


def test_a_429_with_no_header_reads_the_wait_out_of_the_message():
    """Groq and friends put the number in prose: "try again in 8.5s".

    Guessing a fixed minute instead would idle four times longer than the
    provider asked for, on the one path whose whole purpose is not waiting.
    """
    body = json.dumps({"error": {"message":
                                 "Rate limit reached. Please try again in 8.5s"}})
    transport = FakeTransport((429, {}, body))
    provider = make(transport=transport)

    with pytest.raises(llm.RateLimited) as caught:
        provider.complete("hi")

    assert caught.value.retry_after == pytest.approx(8.5)


def test_a_429_with_nothing_to_read_falls_back_to_a_stated_default():
    transport = FakeTransport((429, {}, "too many requests"))
    provider = make(transport=transport)

    with pytest.raises(llm.RateLimited) as caught:
        provider.complete("hi")

    assert caught.value.retry_after == llm.DEFAULT_COOLDOWN


def test_an_ordinary_failure_is_NOT_a_rate_limit():
    transport = FakeTransport((401, {}, "invalid api key"))
    provider = make(transport=transport)

    with pytest.raises(llm.LLMError) as caught:
        provider.complete("hi")

    assert not isinstance(caught.value, llm.RateLimited)
    assert "invalid api key" in str(caught.value)


def test_an_empty_completion_is_a_failure_here_not_three_layers_up():
    """A reasoning model can spend every token thinking and answer "".

    Measured on Groq's gpt-oss-120b at max_tokens=20: HTTP 200, empty
    content. Passed on, it arrives at the design loop as "the model produced
    invalid JSON" -- blaming the model for a budget this side chose.
    """
    transport = FakeTransport(chat_reply(""))
    provider = make(transport=transport)

    with pytest.raises(llm.LLMError, match="max_tokens"):
        provider.complete("hi", max_tokens=20)


# ------------------------------------------------------------ stale model


def test_an_unknown_model_reports_what_the_account_can_actually_serve():
    catalogue = json.dumps({"data": [{"id": "llama-3.3-70b"},
                                     {"id": "qwen-3-32b"}]})
    transport = FakeTransport(
        (404, {}, json.dumps({"error": {"message": "model not found"}})),
        (200, {}, catalogue))
    provider = make(transport=transport, model="llama-3.3-700b")

    with pytest.raises(llm.LLMError) as caught:
        provider.complete("hi")

    message = str(caught.value)
    assert "llama-3.3-700b" in message
    assert "llama-3.3-70b" in message and "qwen-3-32b" in message
    assert "OHMWORK_LLM_MODEL_CEREBRAS" in message


def test_available_models_reads_the_models_endpoint():
    catalogue = json.dumps({"data": [{"id": "b"}, {"id": "a"}]})
    transport = FakeTransport((200, {}, catalogue))
    provider = make(transport=transport)

    assert provider.available_models() == ["a", "b"]
    assert transport.requests[0]["url"] == llm.BASE_URL["cerebras"] + "/models"
    assert transport.requests[0]["method"] == "GET"


def test_a_provider_with_no_key_says_where_to_get_one(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    with pytest.raises(llm.LLMError) as caught:
        llm.OpenAICompatibleProvider("cerebras")
    assert "CEREBRAS_API_KEY" in str(caught.value)
    assert "cerebras.ai" in str(caught.value)


def test_every_openai_compatible_provider_has_a_base_url_and_a_signup_url():
    for name in llm.BASE_URL:
        assert llm.BASE_URL[name].startswith("https://"), name
        assert name in llm.SIGNUP_URL, name
