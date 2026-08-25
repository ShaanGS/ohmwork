"""The provider seam: the only place ohmwork talks to a language model.

WHERE THE API KEY GOES
----------------------
A `.env` file in the repo root, or a real environment variable. Both work;
an already-set environment variable WINS, because setting one is a more
deliberate act than leaving a line in a file.

    GROQ_API_KEY        the Groq key
    ANTHROPIC_API_KEY   the Anthropic key
    OHMWORK_LLM         which provider  ("groq" | "anthropic"), default groq
    OHMWORK_LLM_MODEL   which model     (see DEFAULT_MODEL below)

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


PROVIDERS = {"groq": GroqProvider, "anthropic": AnthropicProvider}


def get_provider(name: str | None = None, *, model: str | None = None,
                 vision: bool = False):
    """Construct the configured provider. Raises LLMError with what to do.

    `vision=True` picks the multimodal default for the provider — extraction
    reads a photographed page, and a text-only model cannot do that job at
    all rather than doing it badly.
    """
    key = (name or os.environ.get("OHMWORK_LLM") or DEFAULT_PROVIDER).lower()
    if key not in PROVIDERS:
        raise LLMError(
            f"unknown provider {key!r}. Set OHMWORK_LLM to one of: "
            f"{', '.join(sorted(PROVIDERS))}"
        )
    if model is None and vision:
        model = os.environ.get("OHMWORK_LLM_MODEL") or DEFAULT_VISION_MODEL[key]
    return PROVIDERS[key](model=model)
