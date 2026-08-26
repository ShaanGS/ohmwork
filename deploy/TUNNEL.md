# Serving it from this machine, over a tunnel

The shortest route to a working, private, HTTPS deployment. No container, no
card, no platform account.

**Why this and not a host.** Every hosted option refuses analog questions
forever, because LTspice is a Windows GUI application that no Linux box will
ever run. This machine has LTspice AND Logisim on it. It is the only place
where the whole product can eventually be served rather than half of it.

**What it costs, stated up front.** The machine has to be awake. For an
audience of five people around lab time that is fine; for a public site it is
not, and the answer there is a paid container (see `DEPLOY.md`, and note that
`Dockerfile` is proven green in CI, so that path is one command away whenever
you want it).

---

## 1. Build the page once

```bash
cd web && npm install && npm run build && cd ..
```

`web/dist` is gitignored, so this is per-machine. Skip it if `web/dist/index.html`
already exists.

## 2. Start the server

PowerShell:

```powershell
$env:OHMWORK_PASSWORD = "pick-something-real"
$env:OHMWORK_SECURE_COOKIES = "1"
$env:OHMWORK_LLM = "pool"
python -m ohmwork.server
```

`OHMWORK_SECURE_COOKIES=1` matters the moment there is a tunnel in front:
Cloudflare terminates TLS, so the session cookie should be marked `secure`.
Leave it unset when you are only using `http://localhost`.

The server **refuses to start without `OHMWORK_PASSWORD`**. That is deliberate:
an open endpoint spends your API keys, and the symptom of that mistake is
everything appearing to work.

Check it locally before going any further:

```bash
curl http://127.0.0.1:7860/api/health
```

## 3. Install cloudflared

```powershell
winget install --id Cloudflare.cloudflared
```

## 4. Prove it works — a quick tunnel, 30 seconds

```powershell
cloudflared tunnel --url http://localhost:7860
```

It prints a `https://something-random.trycloudflare.com` URL. Open it. You
should get the password screen, and a real solve should work through it.

Random URL, dies when you close the terminal. That is all it is for: proving
the path end to end before you spend any time on the permanent one.

## 5. Make it permanent — a named tunnel

Needs a free Cloudflare account and a domain on it.

```powershell
cloudflared tunnel login
cloudflared tunnel create ohmwork
cloudflared tunnel route dns ohmwork ohmwork.yourdomain.com
cloudflared tunnel run --url http://localhost:7860 ohmwork
```

Stable hostname, HTTPS, and nothing of yours exposed except port 7860 — the
tunnel dials out, so there is no port forwarding and no inbound firewall hole.

To have it survive a reboot, install it as a service:

```powershell
cloudflared service install
```

---

## What is and is not served

| | |
|---|---|
| digital questions | **fully served.** The design loop runs here, Logisim verifies the emitted file, and the browser gets the table and the download. |
| analog questions | **refused, with a message pointing at the CLI.** `server.py` is digital-only today. |

That second row is a code gap, not a hosting one, and this is the only host
where closing it is even possible: `domain.classify` and
`analog.solve_analog` both exist and are tested, but nothing wires them into a
request path yet. The interesting part of that work is not the routing — it is
deciding what a browser shows while LTspice runs for several seconds, and what
the page does with a result whose guarantee is deliberately weaker than a
digital one's.

## Before you share the URL

- Pick a real password. It is the only thing between the internet and your API
  keys.
- Confirm an anonymous solve is refused:
  `curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{"question":"x"}' <your-url>/api/solve`
  must print `401`.
- Expect the free model quota, not the server, to be what limits you. All five
  free providers can be exhausted at once; the page says so plainly when they
  are, and says when the earliest one wakes.
