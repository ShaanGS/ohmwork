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

### The STATIC site is on Vercel; the app is not

`vercel.json` at the repo root builds the same thing the Pages workflow
builds -- the library viewer rendered into `site/library/` and the landing
page copied to the root -- and Vercel serves it at ohmwork.vercel.app. It
can do that because the site computes nothing. The warning below is about
the APP in `web/`, which Vercel would import as a bare React page with no
solver behind it.

### If Vercel offers to import `web/`, decline

Pushing this repository makes Vercel email *"1 new project available to
import — web · vite"*. It has detected the React frontend in `web/` and it is
right that it can build it. Importing it would deploy **the page with no
server behind it**: no Logisim, no `/api/solve`, no password gate. The login
box would render, and nothing behind it would work.

That failure is worse than no deployment, because it looks like one. The same
goes for Netlify, Cloudflare Pages, and every other static or serverless host.
The requirement is a container that runs a JVM for 10–60 seconds per request,
and it is not negotiable — it is the thing that does the verifying.

The analog half is not hosted anywhere and that is deliberate. LTspice is a
Windows GUI application, and ngspice is not a substitute because it cannot read
LTspice's device libraries. An analog answer from a Linux host could only ever
be unverified, which is exactly what this project exists to avoid.

## Hugging Face Spaces — Docker is now a PAID SDK

**Observed 2026-08-26 on the Create-a-Space page: the Docker SDK card is
greyed out and badged "Paid".** Static and Gradio are free; Docker is not.
That removes the free-and-no-card recommendation this file used to make, and
it is recorded rather than quietly edited because the reasoning behind that
recommendation was sound and only its premise changed.

Everything else about Hugging Face still fits — enough RAM for a JVM, secrets
built in, and it serves on port 7860, which is what the container listens on.
`deploy/push-space.sh` works unchanged the moment the SDK is available. So
this is a price question now, not an engineering one: check what the Docker
SDK costs and decide.

**Do not work around it with a free Gradio Space.** A Gradio Space is Python
with an apt list, so the temptation is to install a JRE there and shell out to
Logisim. Logisim Evolution 4.1.0 needs **Java 21**, the Space base image ships
an older default JRE, and the whole verification story rests on the evaluator
being the pinned version every published number was measured against. A JRE
that "probably works" is not a foundation for a tool whose entire claim is
that an outside tool checked the answer.

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

- **Every digital circuit it returns was evaluated by Logisim Evolution**, as
  a file, row by row against the specification; every analog one was run by
  LTspice, and is returned as `measured`, a deliberately weaker claim. A design the evaluator disagrees with
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

## The other hosts

Anything that runs a Dockerfile works; nothing in the container is Hugging
Face specific except the port, and that is an environment variable. Prices and
free tiers move, so treat all of this as "check before committing":

| host | the catch |
|---|---|
| Render | free tier runs Docker, sleeps after ~15 min idle, and a cold JVM start makes the first request of the day slow |
| Fly.io, Railway | better behaved, both want a card |
| Google Cloud Run | generous free allowance, needs a billing account |

### The genuinely different option: a tunnel from the machine that already has the simulators

Run the container — or just `python -m ohmwork.server` — on the Windows
machine where LTspice already lives, and expose it with a Cloudflare Tunnel
(free, and a named tunnel gives a stable hostname). For an audience of five
people this is not a compromise, and it is the only option with a property no
Linux host can ever have:

**it is the only place the ANALOG half could also be served.** Every hosted
option refuses analog questions permanently, because LTspice is a Windows GUI
application and ngspice cannot read its device libraries. On that machine both
loops can run against the real simulators.

**Step by step: `deploy/TUNNEL.md`.** It needs no container at all — the
server runs directly, the way it already runs on the machine it was developed
on.

Be precise about what that buys: `server.py` serves BOTH halves (since
2026-08-30, streaming the reading and each attempt while LTspice runs), but
the analog half only ever answers on a machine that has LTspice, which is a
Windows GUI application. A tunnel from a Windows PC is therefore the only
hosted shape in which analog questions are answered at all; every row above
refuses them with the download named.

The obvious cost is the obvious one: the machine has to be awake.
