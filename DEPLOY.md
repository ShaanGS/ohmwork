# Deploying the digital service

The whole thing is one container: a Python API that serves a built React
page, with Logisim Evolution inside it doing the verifying.

**Where the keys go, in one sentence:** never in this repository, never in the
browser — only as secrets on the host, where `ohmwork/llm.py` reads them out
of the environment.

---

## What decides the host

Logisim Evolution is Java, and it has to actually run, because it is the thing
that checks the answer. That rules out Vercel, Netlify and every serverless
platform: no JVM, and a solve takes 10–60 seconds, past their function limits.
A platform that runs a **Docker container** is the requirement.

The analog half is not hosted anywhere and that is deliberate. LTspice is a
Windows GUI application, and ngspice is not a substitute because it cannot read
LTspice's device libraries. An analog answer from a Linux host could only ever
be unverified, which is exactly what this project exists to avoid.

## Recommended: Hugging Face Spaces (free, no card)

Free CPU tier, no payment method, enough RAM for a JVM, secrets built in, and
it serves on port 7860 — which is what the container listens on.

### 1. Create the Space

<https://huggingface.co/new-space> → **Docker** → **Blank** → public.
Public is fine and is the point of building in public: the password is what
keeps strangers out, not obscurity.

### 2. Push this repository to it

```bash
HF_TOKEN=hf_your_write_token deploy/push-space.sh <your-user>/ohmwork
```

The script ships `git archive HEAD` — only committed, tracked files. `.env` is
gitignored and therefore cannot ride along.

### 3. Set the secrets

Space → **Settings** → **Variables and secrets** → *New secret*:

| secret | required | what it is |
|---|---|---|
| `OHMWORK_PASSWORD` | **yes** | the shared passphrase your five people type |
| `GROQ_API_KEY` | at least one | free key, console.groq.com |
| `CEREBRAS_API_KEY` | | free key, cloud.cerebras.ai |
| `GEMINI_API_KEY` | | free key, aistudio.google.com/apikey |
| `OPENROUTER_API_KEY` | | free key, openrouter.ai/keys |
| `MISTRAL_API_KEY` | | free key, console.mistral.ai |

The image already sets `OHMWORK_LLM=pool`, so it uses every key you provide and
moves to the next vendor the moment one rate limits.

**The server refuses to start without `OHMWORK_PASSWORD`.** That is a
deliberate fail-closed: an open endpoint spends your API keys, and the symptom
of that mistake is everything appearing to work perfectly.

### 4. Check it

The Space log should end with `Uvicorn running on http://0.0.0.0:7860`.
Open the Space, type the passphrase, ask for a 2-to-4 decoder.

---

## Running it locally, the same way it runs hosted

```bash
pip install -e ".[web,llm]"
cd web && npm install && npm run build && cd ..
OHMWORK_PASSWORD=whatever python -m ohmwork.server
```

Then <http://127.0.0.1:7860>. For frontend work, `npm run dev` in `web/`
proxies `/api` to that server, so you get hot reload against the real backend.

## What the server does and does not promise

- **Every circuit it returns was evaluated by Logisim Evolution**, as a file,
  row by row against the specification. A design the evaluator disagrees with
  is thrown away and redone; if none survives, you get an error rather than a
  circuit.
- **It cannot check the reading.** The specification it worked from is printed
  above the answer, in amber, because that is the one failure the machinery
  cannot catch: a misread question produces a spec and a circuit that agree
  perfectly with each other.
- **The layout inside the file is mechanical**, not hand-drawn-looking. That is
  stated in the output rather than hoped past.

## Notes on the specific choices

- **The Logisim version is pinned** in the Dockerfile (`LOGISIM_VERSION=4.1.0`)
  — the same version every published number in this project was measured
  against. A floating `latest` would quietly change what "verified" means
  between deploys.
- **xvfb wraps the process.** Logisim is a desktop application being driven in
  `--tty` mode; giving it a virtual display costs a few megabytes and removes a
  whole class of "works on my machine" AWT failures.
- **Cookies are `Secure` in the image** (`OHMWORK_SECURE_COOKIES=1`), which
  requires HTTPS. Hugging Face terminates TLS for you. Running locally over
  plain HTTP, leave it unset.

## If you would rather not use Hugging Face

Anything that runs a Dockerfile works. Render's free tier does, with the
caveat that it sleeps after 15 minutes idle and a cold JVM start makes the
first request of the day slow. Fly.io and Railway are better but both want a
card. Nothing in the container is Hugging Face specific except the port, which
is an environment variable.
