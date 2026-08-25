"""The provider seam: the only place ohmwork talks to a language model.

WHERE THE API KEY GOES
----------------------
A `.env` file in the repo root, or a real environment variable. Both work;
an already-set environment variable WINS, because setting one is a more
deliberate act than leaving a line in a file.

    GROQ_API_KEY            the Groq key
    CEREBRAS_API_KEY        the Cerebras key
    GEMINI_API_KEY          the Google AI Studio key
    OPENROUTER_API_KEY      the OpenRouter key
    MISTRAL_API_KEY         the Mistral key
    ANTHROPIC_API_KEY       the Anthropic key
    OHMWORK_LLM             which provider, or "pool"; default groq
    OHMWORK_LLM_POOL        pool membership, in the order tried
    OHMWORK_LLM_MODEL       which model, for a SINGLE provider
    OHMWORK_LLM_MODEL_<X>   which model, for pool member X

THE POOL, AND WHY IT IS NOT CHEATING
------------------------------------
A free Groq account serves 8000 tokens a minute, prompt plus max_tokens —
about one design call. The design loop makes three or four, so a solve spent
minutes in rate-limit pauses. Several free accounts at DIFFERENT vendors,
tried in turn, remove the pause without paying anyone.

Several accounts at the SAME vendor would be evasion of that vendor's terms
and is deliberately not what this does. One account each, at different
vendors, is ordinary use of each free tier.

`.env` is gitignored, and the ignore rule was added BEFORE the file existed —
a key committed once lives in the history forever, even after it is deleted
from the working tree. `.env.example` is the committed template and holds no
real values.

There is still deliberately no CLI flag that takes a key. Flags end up in
shell history, in screenshots, and in the process list.

WHAT THIS IS AND IS NOT ALLOWED TO BE
-------------------------------------
CLAUDE.md, "Deployment": *"Do NOT build a Groq or any other live-API hot
path. Extraction happens locally at generation time, where the human reviews
it."*

That rule is about WHERE the call happens, not which vendor makes it. This
module is only ever driven by the CLI, on a machine where the simulators
live, with a human at the dry-run gate. Nothing here may be reachable from a
served page — the site is a static viewer over the library and never calls a
model. If a request path ever imports this module, that rule has been broken.

MODEL IDS ARE NOT STABLE, SO THEY ARE NOT GUESSED
--------------------------------------------------
Hosted-model catalogues change faster than this file will. Rather than bake
in an ID that silently 404s a year from now, an unknown model raises with the
list of models the account can actually see. A guess that announces itself is
recoverable; a guess that looks like a default is not.
"""

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path

#: Provider default. Groq, because that is what this project uses; Anthropic
#: stays wired so the seam is demonstrably a seam and not a single vendor
#: with extra steps.
DEFAULT_PROVIDER = "groq"

#: Per-provider default model. VERIFY THESE — they are the one thing in this
#: module that rots without warning. `python -m ohmwork --list-models` prints
#: what the account can actually see, and an unknown model says the same.
DEFAULT_MODEL = {
    # Confirmed present 2026-08-25 via `--list-models` on a free Groq
    # account. Confirmed, not recalled: the previous default here
    # (llama-3.3-70b-versatile) did not exist on that account at all.
    "groq": "openai/gpt-oss-120b",
    "anthropic": "claude-opus-5",
}

#: Extraction reads a photographed lab-manual page, so the model must accept
#: images. A text-only model cannot solve Q1 at all — the component values
#: exist nowhere but the picture.
#
# MEASURED 2026-08-25: a free Groq account serves NO multimodal model. Its 13
# models are text chat, speech-to-text (whisper), text-to-speech (orpheus) and
# safety classifiers (prompt-guard). So image extraction cannot run on Groq
# today, and the entry below is a placeholder that will fail loudly with the
# real catalogue rather than silently doing something else.
DEFAULT_VISION_MODEL = {
    "groq": "meta-llama/llama-4-scout-17b-16e-instruct",
    "anthropic": "claude-opus-5",
}


class LLMError(Exception):
    """The model could not be reached, configured, or believed."""


# ------------------------------------------------------------ .env loading


def find_env_file(start=None) -> Path | None:
    """The nearest .env at or above `start`. None if there is none.

    Searched upwards so the CLI works from a subdirectory, which is where
    people actually run things from.
    """
    here = Path(start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def parse_env_file(text: str) -> dict:
    """KEY=value lines. Deliberately small and deliberately literal.

    Handles what people actually paste: comments, blank lines, a stray
    `export`, and quotes around the value. It does NOT strip inline comments
    — a `#` is a legal character in a key, and silently truncating a
    credential at one would produce an authentication failure with no
    plausible cause.
    """
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load_env_file(path=None) -> dict:
    """Merge a .env into os.environ WITHOUT overriding what is already set.

    An environment variable that is already set was set deliberately — by a
    CI secret, a shell export, or a deployment — and a file in the working
    tree must not silently outrank it. Returns what it actually applied.
    """
    path = Path(path) if path else find_env_file()
    if path is None or not Path(path).is_file():
        return {}
    applied = {}
    for key, value in parse_env_file(Path(path).read_text(encoding="utf-8")).items():
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


# Loaded once, at import. This is a CLI tool and the alternative is every
# entry point remembering to call it.
load_env_file()


@dataclass(frozen=True)
class Reply:
    """What came back, and from what. Provenance travels with the text.

    Every other result in this project records the tool and version that
    produced it; model output gets the same treatment, so a caption in a
    manifest can name the model that wrote it.
    """

    text: str
    model: str
    provider: str


@dataclass(frozen=True)
class Image:
    """One image for a vision request."""

    data: bytes
    media_type: str = "image/png"

    @classmethod
    def from_path(cls, path) -> "Image":
        path = Path(path)
        suffix = path.suffix.lower()
        media = {".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".webp": "image/webp",
                 ".gif": "image/gif"}.get(suffix)
        if media is None:
            raise LLMError(
                f"{path.name}: unsupported image type {suffix!r}. Lab-manual "
                f"screenshots are png or jpg."
            )
        return cls(path.read_bytes(), media)

    @property
    def b64(self) -> str:
        return base64.standard_b64encode(self.data).decode("ascii")

    @property
    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.b64}"


# ---------------------------------------------------------------- Groq


class GroqProvider:
    """Groq's hosted open models, via the official SDK.

    Chat-completions shaped (OpenAI-compatible), which is why the vision
    payload uses image_url with a data: URL rather than Anthropic's source
    block. That difference is the only reason this class and the Anthropic
    one are not the same code.
    """

    name = "groq"
    env_var = "GROQ_API_KEY"

    def __init__(self, model: str | None = None, client=None):
        self.model = model or os.environ.get("OHMWORK_LLM_MODEL") \
            or DEFAULT_MODEL["groq"]
        if client is not None:
            self.client = client
            return
        try:
            from groq import Groq
        except ImportError:
            raise LLMError(
                "the groq SDK is not installed. `pip install groq`, then set "
                "GROQ_API_KEY in your environment."
            ) from None
        if not os.environ.get(self.env_var):
            raise LLMError(
                f"{self.env_var} is not set. Get a key from console.groq.com "
                f"and set it in your environment — never in a file in this "
                f"repo."
            )
        self.client = Groq()

    def available_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list().data)
        except Exception as e:                          # noqa: BLE001
            raise LLMError(f"could not list Groq models: {e}") from None

    def complete(self, prompt: str, *, images=(), max_tokens: int = 4000,
                 temperature: float = 0.2) -> Reply:
        if images:
            content = [{"type": "text", "text": prompt}]
            content += [{"type": "image_url",
                         "image_url": {"url": image.data_url}}
                        for image in images]
        else:
            content = prompt

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_completion_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:                          # noqa: BLE001
            raise self._explain(e) from None

        text = (response.choices[0].message.content or "").strip()
        return Reply(text=text, model=self.model, provider=self.name)

    def _explain(self, error: Exception) -> LLMError:
        """Turn a stale model id into an actionable message.

        The catalogue moves; this file does not. So when a request fails in
        the way a wrong model id fails, say which models the account can
        actually see rather than leaving a bare 404.
        """
        text = str(error)
        # A rate limit is a different KIND of failure, not a worse one: the
        # pool moves to the next member on this and disables a member on
        # anything else. Reporting it as an ordinary error would have it
        # retire a perfectly healthy account.
        if getattr(error, "status_code", None) == 429 or any(
                phrase in text.lower()
                for phrase in ("rate limit", "too many requests")):
            return RateLimited(f"Groq is rate limiting: {text}",
                               retry_after=_retry_after({}, text))
        looks_like_bad_model = (
            getattr(error, "status_code", None) == 404
            or "model" in text.lower() and (
                "not found" in text.lower() or "does not exist" in text.lower()
                or "decommission" in text.lower())
        )
        if not looks_like_bad_model:
            return LLMError(f"Groq request failed: {type(error).__name__}: {error}")
        try:
            available = ", ".join(self.available_models())
        except LLMError:
            available = "(could not list them either)"
        return LLMError(
            f"Groq does not serve model {self.model!r}. Hosted catalogues "
            f"change, so this is expected to happen eventually — set "
            f"OHMWORK_LLM_MODEL to one of: {available}"
        )


# ------------------------------------------------------------- Anthropic


class AnthropicProvider:
    """Claude, via the official SDK. Kept so the seam has two sides."""

    name = "anthropic"
    env_var = "ANTHROPIC_API_KEY"

    def __init__(self, model: str | None = None, client=None):
        self.model = model or os.environ.get("OHMWORK_LLM_MODEL") \
            or DEFAULT_MODEL["anthropic"]
        if client is not None:
            self.client = client
            return
        try:
            import anthropic
        except ImportError:
            raise LLMError(
                "the anthropic SDK is not installed. `pip install anthropic`, "
                "then set ANTHROPIC_API_KEY (or run `ant auth login`)."
            ) from None
        try:
            self.client = anthropic.Anthropic()
        except Exception as e:                          # noqa: BLE001
            raise LLMError(f"could not construct an Anthropic client: {e}") from None

    def available_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self.client.models.list())
        except Exception as e:                          # noqa: BLE001
            raise LLMError(f"could not list Anthropic models: {e}") from None

    def complete(self, prompt: str, *, images=(), max_tokens: int = 4000,
                 temperature: float = 0.2) -> Reply:
        content = [{"type": "image",
                    "source": {"type": "base64",
                               "media_type": image.media_type,
                               "data": image.b64}}
                   for image in images]
        content.append({"type": "text", "text": prompt})

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": content}],
            )
        except Exception as e:                          # noqa: BLE001
            raise LLMError(
                f"Anthropic request failed: {type(e).__name__}: {e}") from None

        if getattr(message, "stop_reason", None) == "refusal":
            raise LLMError("the model declined this request")
        text = "".join(block.text for block in message.content
                       if block.type == "text").strip()
        return Reply(text=text, model=self.model, provider=self.name)


# ------------------------------------------ one client, every free tier
#
# Cerebras, Gemini, OpenRouter, Mistral and Groq itself all expose the SAME
# chat-completions endpoint. So they are ONE class with a base URL, not five
# SDKs — and pooling them costs no new dependency, which matters because the
# pool is the thing that makes a free tier usable at all.
#
# THESE IDS AND URLS WILL ROT. That is not a reason to leave them out; it is
# the reason `--list-models` exists and the reason a wrong id fails with the
# real catalogue attached rather than as a bare 404.

BASE_URL = {
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
    "mistral": "https://api.mistral.ai/v1",
}

#: Where a human goes to get a free key. Printed in the "no key" error,
#: because "CEREBRAS_API_KEY is not set" is only half an instruction.
SIGNUP_URL = {
    "groq": "https://console.groq.com",
    "cerebras": "https://cloud.cerebras.ai",
    "gemini": "https://aistudio.google.com/apikey",
    "openrouter": "https://openrouter.ai/keys",
    "mistral": "https://console.mistral.ai",
}

#: Non-obvious key variable names go here; everything else is <NAME>_API_KEY.
ENV_VAR = {"gemini": "GEMINI_API_KEY"}

DEFAULT_MODEL.update({
    "cerebras": "llama-3.3-70b",
    "gemini": "gemini-2.5-flash",
    "openrouter": "meta-llama/llama-3.3-70b-instruct:free",
    "mistral": "mistral-large-latest",
})

# Vision is a SEPARATE default and a separate question. A free Groq account
# serves no multimodal model at all (measured 2026-08-25), and Cerebras and
# Mistral's free ids here are text-only — so those providers are skipped for
# an image request rather than handed a job they cannot do. Gemini is the
# first free tier in this project that can read a photographed page.
DEFAULT_VISION_MODEL.update({
    "gemini": "gemini-2.5-flash",
    "openrouter": "meta-llama/llama-3.2-11b-vision-instruct:free",
})

#: How long a member sits out when the provider did not say. A guess, and
#: named as one so nothing reads it as measured.
DEFAULT_COOLDOWN = 60.0

HTTP_TIMEOUT = 120


class RateLimited(LLMError):
    """The provider asked us to slow down, and said for how long.

    A subclass rather than a flag because the pool's whole behaviour hangs
    off this distinction: a rate limit means *try the next member*, and any
    other failure means *this member is broken*. Collapsing the two would
    either spin forever on a dead key or give up on a live one.
    """

    def __init__(self, message, retry_after: float = DEFAULT_COOLDOWN):
        super().__init__(message)
        self.retry_after = float(retry_after)


def env_var_for(name: str) -> str:
    """The environment variable holding this provider's key."""
    return ENV_VAR.get(name, f"{name.upper()}_API_KEY")


def model_for(name: str, *, vision: bool = False) -> str:
    """The model id for one pool member.

    Deliberately does NOT read OHMWORK_LLM_MODEL. That variable names a model
    on ONE provider's catalogue; applying a Groq id to Gemini would 404 every
    member but one, and the failure would look like four dead vendors rather
    than one misapplied setting. Per-member override is
    OHMWORK_LLM_MODEL_<NAME>.
    """
    override = os.environ.get(f"OHMWORK_LLM_MODEL_{name.upper()}")
    if override:
        return override
    table = DEFAULT_VISION_MODEL if vision else DEFAULT_MODEL
    model = table.get(name)
    if model is None:
        raise LLMError(
            f"{name} has no known multimodal model here, so it cannot read an "
            f"image. Set OHMWORK_LLM_MODEL_{name.upper()} if that has changed."
        )
    return model


def _urllib_transport(method, url, headers, body):
    """The real transport. Returns (status, headers, text); never raises HTTP.

    An HTTP error is DATA here, not an exception, because the caller has to
    tell 429 from 404 and doing that through exception attributes is how the
    distinction gets lost.
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, data=body, headers=headers,
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return (response.status,
                    {k.lower(): v for k, v in response.headers.items()},
                    response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return (e.code,
                {k.lower(): v for k, v in (e.headers or {}).items()},
                e.read().decode("utf-8", "replace"))
    except Exception as e:                              # noqa: BLE001
        raise LLMError(f"{url}: {type(e).__name__}: {e}") from None


def _retry_after(headers: dict, text: str) -> float:
    """How long the provider asked us to wait.

    Header first, then the number providers write in prose ("try again in
    8.5s"), then the stated default. Guessing a flat minute when the provider
    asked for eight seconds idles seven times longer than necessary, on the
    one code path whose entire purpose is not waiting.
    """
    import re

    raw = headers.get("retry-after")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    match = re.search(r"try again in ([0-9.]+)\s*s", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return DEFAULT_COOLDOWN


class OpenAICompatibleProvider:
    """Any hosted model behind an OpenAI chat-completions endpoint."""

    def __init__(self, name: str, *, model: str | None = None,
                 api_key: str | None = None, base_url: str | None = None,
                 transport=None):
        if name not in BASE_URL and base_url is None:
            raise LLMError(
                f"no base URL known for {name!r}. Known: "
                f"{', '.join(sorted(BASE_URL))}")
        self.name = name
        self.base_url = (base_url or BASE_URL[name]).rstrip("/")
        self.model = model or model_for(name)
        self.transport = transport or _urllib_transport
        self.api_key = api_key or os.environ.get(env_var_for(name))
        if not self.api_key:
            raise LLMError(
                f"{env_var_for(name)} is not set. A free key comes from "
                f"{SIGNUP_URL.get(name, 'the provider')} — put it in .env, "
                f"which is gitignored, never in a file that is committed."
            )

    # -- requests

    #: MEASURED 2026-08-26, not guessed: without a User-Agent, Groq's edge
    #: answered "HTTP 403: error code: 1010" -- a Cloudflare bot challenge,
    #: which looks nothing like an auth or model problem and would have sent
    #: someone hunting for a bad key. urllib's default agent is the tell.
    USER_AGENT = "ohmwork/0.1 (+https://github.com/ShaanGS/ohmwork)"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": self.USER_AGENT}

    def available_models(self) -> list[str]:
        status, _, text = self.transport(
            "GET", f"{self.base_url}/models", self._headers(), None)
        if status != 200:
            raise LLMError(f"could not list {self.name} models: "
                           f"HTTP {status}: {text[:200]}")
        try:
            import json
            return sorted(entry["id"] for entry in json.loads(text)["data"])
        except Exception as e:                          # noqa: BLE001
            raise LLMError(f"could not read {self.name}'s model list: "
                           f"{type(e).__name__}: {e}") from None

    def complete(self, prompt: str, *, images=(), max_tokens: int = 4000,
                 temperature: float = 0.2) -> Reply:
        import json

        if images:
            content = [{"type": "text", "text": prompt}]
            content += [{"type": "image_url",
                         "image_url": {"url": image.data_url}}
                        for image in images]
        else:
            content = prompt

        payload = {"model": self.model,
                   "messages": [{"role": "user", "content": content}],
                   "max_tokens": max_tokens,
                   "temperature": temperature}
        status, headers, text = self.transport(
            "POST", f"{self.base_url}/chat/completions", self._headers(),
            json.dumps(payload).encode("utf-8"))

        if status != 200:
            raise self._explain(status, headers, text)

        try:
            body = json.loads(text)
            reply = (body["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:                          # noqa: BLE001
            raise LLMError(
                f"{self.name} returned something that is not a chat "
                f"completion: {type(e).__name__}: {e}: {text[:200]}") from None

        # MEASURED 2026-08-26 against Groq's gpt-oss-120b: a reasoning model
        # asked for 20 tokens spends all of them thinking and returns an
        # EMPTY content string with a perfectly successful 200. Passing that
        # on would surface three layers up as "the model produced invalid
        # JSON", blaming the model for a budget this side set.
        if not reply:
            raise LLMError(
                f"{self.name}/{self.model} returned an empty completion. On a "
                f"reasoning model this usually means max_tokens ({max_tokens}) "
                f"was spent before any answer was written.")
        return Reply(text=reply, model=self.model, provider=self.name)

    def _explain(self, status: int, headers: dict, text: str) -> LLMError:
        """Turn an HTTP status into the thing to do about it."""
        if status == 429:
            return RateLimited(f"{self.name} is rate limiting: {text[:200]}",
                               retry_after=_retry_after(headers, text))

        lowered = text.lower()
        stale_model = status in (400, 404) and "model" in lowered and any(
            phrase in lowered for phrase in
            ("not found", "does not exist", "decommission", "invalid model",
             "unknown model"))
        if not stale_model:
            return LLMError(f"{self.name} request failed: HTTP {status}: "
                            f"{text[:300]}")
        try:
            available = ", ".join(self.available_models())
        except LLMError:
            available = "(could not list them either)"
        return LLMError(
            f"{self.name} does not serve model {self.model!r}. Hosted "
            f"catalogues change, so this is expected to happen eventually — "
            f"set OHMWORK_LLM_MODEL_{self.name.upper()} to one of: "
            f"{available}")


# ------------------------------------------------------------- the pool
#
# WHY. A free Groq account serves 8000 tokens per MINUTE, prompt plus
# max_tokens — about one design call a minute. The design loop makes three or
# four, so a solve spent minutes sitting in a pause. Several free accounts at
# different vendors, tried in turn, remove the pause without paying anyone.
#
# Several accounts at ONE vendor would be evasion of that vendor's terms, and
# is deliberately not what this does: members are different providers.

#: Tried in this order, and this is the default membership when every
#: provider with a key present is used.
POOL_ORDER = ["groq", "cerebras", "gemini", "openrouter", "mistral"]

#: Nameable in OHMWORK_LLM_POOL, but never a default member: Anthropic is not
#: a free tier and joining a pool silently is exactly how a bill appears.
POOL_KNOWN = POOL_ORDER + ["anthropic"]


@dataclass(frozen=True)
class Incident:
    """A member that failed, and why. Kept so a degraded pool can say so."""

    member: str
    reason: str
    rate_limited: bool = False


@dataclass
class _Member:
    provider: object
    #: When this member may be tried again.
    until: float = 0.0
    #: Whether waiting for `until` is worth doing. True for a rate limit —
    #: the provider told us when it would serve us again. False for a broken
    #: key or a dead endpoint, where waiting sixty seconds only produces the
    #: same failure more slowly.
    waitable: bool = False

    @property
    def name(self) -> str:
        return self.provider.name


class Pool:
    """Several providers, tried in turn, sharing one interface with them.

    A rate-limited member is set aside and the NEXT one answers, which is the
    entire point: a 429 should cost the next member's latency, not a minute.
    Waiting happens only when every member is rate limited and there is
    nothing else left to do.
    """

    name = "pool"

    def __init__(self, providers, *, clock=None, sleep=None,
                 max_wait: float = 180.0,
                 default_cooldown: float = DEFAULT_COOLDOWN, skipped=()):
        import time as _time

        if not providers:
            raise LLMError(
                "the pool has no members. Set at least one key — "
                + ", ".join(f"{env_var_for(n)} ({SIGNUP_URL[n]})"
                            for n in POOL_ORDER))
        self.members = [_Member(p) for p in providers]
        self.clock = clock or _time.monotonic
        self.sleep = sleep or _time.sleep
        self.max_wait = max_wait
        self.default_cooldown = default_cooldown
        #: Members that could not be built at all — no key, no vision model.
        #: Recorded rather than dropped: a pool answering from two of four
        #: members looks exactly like one answering from four.
        self.skipped = list(skipped)
        self.incidents: list[Incident] = []

    @property
    def model(self) -> str:
        """A description, NOT a model id.

        Nothing may record this as provenance — each Reply names the member
        that actually answered, and that is the truthful field.
        """
        return "+".join(f"{m.name}:{m.provider.model}" for m in self.members)

    def describe(self) -> str:
        lines = [f"pool of {len(self.members)}: "
                 + ", ".join(f"{m.name} ({m.provider.model})"
                             for m in self.members)]
        for name, reason in self.skipped:
            lines.append(f"  NOT in the pool: {name} — {reason}")
        return "\n".join(lines)

    def available_models(self) -> list[str]:
        out = []
        for member in self.members:
            try:
                out += [f"{member.name}/{m}"
                        for m in member.provider.available_models()]
            except LLMError as e:
                out.append(f"{member.name}/(could not list: {e})")
        return out

    def complete(self, prompt: str, *, images=(), max_tokens: int = 4000,
                 temperature: float = 0.2) -> Reply:
        failures: list[tuple[str, str]] = []
        waited = 0.0

        while True:
            for member in self.members:
                if member.until > self.clock():
                    continue
                try:
                    return member.provider.complete(
                        prompt, images=images, max_tokens=max_tokens,
                        temperature=temperature)
                except RateLimited as e:
                    member.until = self.clock() + e.retry_after
                    member.waitable = True
                    self._record(member.name, str(e), rate_limited=True)
                    failures.append((member.name,
                                     f"rate limited for {e.retry_after:g}s"))
                except LLMError as e:
                    member.until = self.clock() + self.default_cooldown
                    member.waitable = False
                    self._record(member.name, str(e))
                    failures.append((member.name, str(e)))

            # Nothing answered. Waiting is only worth doing for a member that
            # told us when it would serve us again.
            wakeable = [m.until for m in self.members if m.waitable]
            if not wakeable:
                raise self._exhausted(failures)
            pause = max(0.0, min(wakeable) - self.clock())
            if waited + pause > self.max_wait:
                raise self._exhausted(
                    failures,
                    extra=f"The earliest member wakes in {pause:g}s, which is "
                          f"past the {self.max_wait:g}s wait budget.")
            self.sleep(pause)
            waited += pause

    def _record(self, name, reason, *, rate_limited=False) -> None:
        self.incidents.append(Incident(name, reason, rate_limited))
        del self.incidents[:-50]

    def _exhausted(self, failures, extra: str = "") -> LLMError:
        """Every member's failure, verbatim.

        Reporting only the last one would blame whichever member happened to
        be tried last for a problem that might belong to all of them — an
        expired key and a stale model id look identical from there.
        """
        detail = "\n".join(f"  {name}: {reason}" for name, reason in failures)
        return LLMError(
            f"no pool member could answer ({len(self.members)} member(s) "
            f"tried).\n{detail}" + (f"\n{extra}" if extra else ""))


def planned_pool(names=None):
    """(members with a key, [(member, why it was left out)]).

    Configuration only — nothing is constructed and no network is touched, so
    this is also what `--list-models` prints to explain a pool before using
    it.
    """
    raw = names if names is not None else os.environ.get("OHMWORK_LLM_POOL")
    if isinstance(raw, str):
        wanted = [part.strip() for part in raw.split(",") if part.strip()]
    elif raw:
        wanted = list(raw)
    else:
        wanted = list(POOL_ORDER)

    unknown = [name for name in wanted if name not in POOL_KNOWN]
    if unknown:
        raise LLMError(
            f"unknown pool member(s) {', '.join(unknown)}. "
            f"OHMWORK_LLM_POOL takes: {', '.join(POOL_KNOWN)}")

    present, skipped = [], []
    for name in wanted:
        if os.environ.get(env_var_for(name)):
            present.append(name)
        else:
            skipped.append((name, f"{env_var_for(name)} is not set "
                                  f"({SIGNUP_URL.get(name, 'no free tier')})"))
    return present, skipped


def build_member(name: str, *, vision: bool = False):
    """One pool member, by name."""
    if name == "anthropic":
        return AnthropicProvider(model=model_for("anthropic", vision=vision))
    return OpenAICompatibleProvider(name, model=model_for(name, vision=vision))


def build_pool(names=None, *, vision: bool = False, **kwargs) -> Pool:
    present, skipped = planned_pool(names)
    members = []
    for name in present:
        try:
            members.append(build_member(name, vision=vision))
        except LLMError as e:
            skipped.append((name, str(e)))
    if not members:
        raise LLMError(
            "no usable pool member.\n"
            + "\n".join(f"  {name}: {why}" for name, why in skipped))
    return Pool(members, skipped=skipped, **kwargs)


PROVIDERS = {"groq": GroqProvider, "anthropic": AnthropicProvider}


def get_provider(name: str | None = None, *, model: str | None = None,
                 vision: bool = False):
    """Construct the configured provider. Raises LLMError with what to do.

    `vision=True` picks the multimodal default for the provider — extraction
    reads a photographed page, and a text-only model cannot do that job at
    all rather than doing it badly.

    `OHMWORK_LLM=pool` builds a pool across every provider whose key is set.
    Pooling is opt-in rather than automatic: which vendor answers is a fact
    about a result, and this project does not change that silently.
    """
    key = (name or os.environ.get("OHMWORK_LLM") or DEFAULT_PROVIDER).lower()
    if key == "pool":
        return build_pool(vision=vision)
    if key in PROVIDERS:
        if model is None and vision:
            model = os.environ.get("OHMWORK_LLM_MODEL") \
                or DEFAULT_VISION_MODEL[key]
        return PROVIDERS[key](model=model)
    if key in BASE_URL:
        return OpenAICompatibleProvider(
            key, model=model or os.environ.get("OHMWORK_LLM_MODEL")
            or model_for(key, vision=vision))
    raise LLMError(
        f"unknown provider {key!r}. Set OHMWORK_LLM to one of: "
        f"{', '.join(sorted(set(PROVIDERS) | set(BASE_URL)))}, or 'pool' to "
        f"use every provider whose key is set")
