# Open Ended Question Solver

## What this is

A tool that takes an electronics lab "open ended question" in plain English and
produces two things:

1. A circuit file the student can open in the real tool (LTspice `.asc` first,
   Logisim `.circ` later).
2. The analysis the question actually asks for (numeric answers, truth tables,
   waveforms), computed by simulation rather than guessed by a language model.

Context: these are ungraded self-study questions from an ECE lab at SRM. One per
experiment. Nothing is submitted. The point is understanding, so the explanation
matters as much as the circuit.

## The core design principle

**The simulation must come FROM the generated file, not alongside it.**

An LLM can emit a netlist and an `.asc` separately and have them silently
disagree. That already happened once during prototyping: the netlist had the
zener correctly oriented as a shunt, the schematic had it forward-biased. Both
looked fine.

So the pipeline is always:

```
question text
  -> LLM produces a JSON circuit description (components + nets, NO coordinates)
  -> emitter places components and writes the .asc
  -> parser reads that .asc back and rebuilds connectivity from geometry alone
  -> simulate the recovered netlist with ngspice
  -> compare against what the question expects
  -> on mismatch, feed the failure back to the LLM and regenerate
```

The LLM never produces coordinates and never produces the netlist that gets
simulated. It only produces logical intent. Everything downstream is
deterministic Python.

## Verified format facts

These were derived empirically from real `.asc` files, not from documentation.
Do not change them without re-deriving from a real file.

### LTspice `.asc`

Structure:
```
Version 4.1
SHEET 1 880 680
SYMBOL <symname> <x> <y> <rotation>
SYMATTR InstName R1
SYMATTR Value 1.8k
WIRE <x1> <y1> <x2> <y2>
FLAG <x> <y> <netname>
TEXT <x> <y> Left 2 !<spice directive>
TEXT <x> <y> Left 2 ;<comment>
```

Pin offsets relative to the SYMBOL anchor, at rotation R0:

| symbol  | pins |
|---------|------|
| res     | (16,16), (16,96) |
| cap     | (16,0), (16,64) |
| ind     | (16,16), (16,96) |
| voltage | (0,16)=+, (0,96)=- |
| diode   | (16,0)=anode, (16,64)=cathode |
| zener   | (16,0)=anode, (16,64)=cathode |
| npn     | (64,0)=C, (0,48)=B, (64,96)=E |
| pnp     | (64,0)=C, (0,48)=B, (64,96)=E |

Note the height inconsistency: res is 96 tall, diode is 64. This kind of thing
is why offsets must be derived, never assumed.

Rotation transforms the offset about the anchor:

| rotation | (x,y) becomes |
|----------|---------------|
| R0       | (x, y)   |
| R90      | (-y, x)  |
| R180     | (-x, -y) |
| R270     | (y, -x)  |

Verified: an npn at (144,208) R270 gives C=(144,144), B=(192,208), E=(240,144),
matching a real hand-drawn file exactly. A zener at (208,432) R180 gives
(192,432) and (192,368), also matching.

Confirmed again 2026-08-24 against four MORE hand-drawn files, by three
different students, now in `tests/fixtures/ltspice/` with the measurements in
`tests/test_symbols_handdrawn.py`: every pin of every symbol in those files
(res, cap, voltage, diode at R0/R90/R270) lands exactly on a wire endpoint or
flag the student drew. This is the check the round trip structurally cannot
make — emitter and parser share symbols.py and would agree on the same wrong
coordinate — so it is worth extending every time real files arrive.

**Encoding: a real `.asc` is not ASCII or UTF-8.** LTspice writes a micro
sign as the single byte 0xB5 (cp1252), so `100µ` in a real file is invalid
UTF-8 and raises on an ascii read. FIXED 2026-08-24: `parser.ASC_ENCODING`
is cp1252, with the regression pinned against the real file that exposed it
(`tests/fixtures/ltspice/handdrawn_voltage_multiplier.asc`). Fixed
immediately rather than deferred to check-mine mode, because the trigger was
known and already sitting in the repo — Q3's own filter is 470µF.

The OUTPUT side has the same problem and was found the same day, by putting
the real question text back: the dry run prints the verbatim question, lab
manuals are full of `470 uF` / `1 kOhm` / `beta = 100`, and the Windows console
is cp1252. Printing a faithfully transcribed question raised
UnicodeEncodeError and the confirmation gate died before displaying anything —
on precisely the questions whose values most need checking by eye. Fixed in
`__main__._make_stdout_unicode_safe`: UTF-8, falling back to
`backslashreplace` rather than `replace`, because a visible `Ω` reads as
an encoding artefact while a row of question marks silently corrupts the one
screen whose whole job is letting a human compare values against the image.

The reasoning behind the choice: the parser's job is to reject a file on
GEOMETRY it cannot account for, with a message naming the pin, never to die
on an encoding technicality while reading a perfectly good schematic. That
file now fails with "pin V1.+ at (112, 96) is connected to no net label",
which is true and useful — it routes with wires instead of flag-and-stub.
The emitter still writes pure ascii (`100u`, never `µ`), and a test holds
that line.

### Connectivity: use net labels, never routed wires

Logisim and LTspice both determine connectivity geometrically: a wire connects
to a pin only if an endpoint lands exactly on it. Auto-routing that is a real
pathfinding problem and gets silently wrong.

Avoid it entirely. Each pin gets a 16-unit stub with a `FLAG` (LTspice) or
Tunnel (Logisim) net label on the free end. Same label means same net. The
emitter places components on a grid and never routes anything.

Net `0` is ground in LTspice and renders as a ground symbol automatically.

Stub direction must point away from the symbol body, otherwise the label lands
on top of the symbol and is unreadable.

### ngspice gotchas

- Batch mode (`-b`) does not implement `.step`. Loop externally for sweeps.
- `.print` needs a `.control` / `.endc` block to actually emit values in batch.
- LTspice itself handles both natively, so the `.asc` can use `.step` even
  though the verification path cannot.

## Known-good reference case

Series voltage regulator (Exp 2 open ended question):

```
V1 vin 0 15
R1 vin vb 1.8k
D1 0 vb DZ          ; anode to ground, cathode to vb: shunt zener
Q1 vin vb vout QN
RL vout 0 2k
.model DZ D(BV=8.3 N=1.2)
.model QN NPN(BF=100)
```

Expected results, confirmed in ngspice (NOT LTspice — see the divergence
note under "Simulate layer decisions"; LTspice with the same model cards
gives vout=7.939 V, vb=8.749 V, measured 2026-08-24 on 26.0.2.1):

- vout at 15V in, 2k load: 7.532 V
- vb: 8.340 V
- line regulation, 12V to 20V in: 36 mV, about 0.48%
- load regulation, 100k to 500R: 7.633 V to 7.495 V, about 1.84%
- zener current at nominal: about 3.66 mA

Any change that breaks these numbers is a regression.

## STATE AT 2026-08-26 — read this first

548 tests, 2 skipped (ngspice absent; one prose case). On a machine with
NEITHER simulator the same suite is 25 skipped and green, which is what CI
runs.

**v1.0 IS FEATURE-COMPLETE.** Every item in the frozen scope table is done
except the seeding count, which is short for the stated reason (no more
verbatim question text exists — see the seeding note). Nothing in v1.0 is
outstanding that code can fix.

**Landed 2026-08-26 (this session):**

- **The repo is under version control at last**, and the key never entered
  history: `git init`, `core.autocrlf false` (so the working bytes are the
  committed bytes; `.gitattributes -text` still protects fixtures from a
  cloner whose config differs), `.env` and `AGENTS.md` and `circ files/`
  excluded from the FIRST commit rather than removed from a later one.
- `ARCHITECTURE.md`, carrying what AGENTS.md holds that a public reader
  wants: the layer map, the derived format facts and the derivation method,
  the 16-row incident table, the verification limits, the deferred list with
  what unblocks each, and the anchored-model story. **AGENTS.md is now
  gitignored**, as decided.
- MIT `LICENSE`, and `.github/workflows/ci.yml` — Linux, no simulators,
  `-rs` so the log SAYS which checks did not run rather than reporting a
  quiet pass over ~25 skips.
- **`ohmwork/viewer.py`, the static viewer** — the last v1.0 feature.
  `python -m ohmwork --library DIR --build-site OUT`. `tests/test_viewer.py`
  is the spec (34 cases, run against the REAL committed library).
- `.github/workflows/pages.yml`, which builds and deploys it with no
  simulator installed — the architecture visible in a CI file.

### What the first rendered page caught, by being looked at

Both were found by opening the HTML, not by a test, which is the honest
record and the reason the browser pass happened at all:

1. **A `prose_from_design` ask rendered "No answer has been written for this
   ask" in warning colour over a COMPLETE answer.** That tier is zero
   generation by design — quoting the choices and their rationales IS the
   answer, and `prose.py` line 334 says exactly that. So the site
   contradicted the terminal renderer, and it did so as a false alarm on the
   one part of the page built to surface real gaps. Same species as gap 8,
   where a prose ask reported as dropped work. Pinned in BOTH directions: a
   results-tier ask with no answer must still say so, or the fix is just a
   check that cannot fire.
2. **A waveform row headlined `8.139164439895255e-06`** with nothing saying
   it was the time-weighted mean of a symmetric AC source — incident 5's
   number, sitting in the column a reader reads as the answer. The fix had to
   respect the viewer's one rule: it may NOT import from `analysis.py` that a
   waveform value is its mean (a page must not keep asserting a convention
   after the code that set it moves on). It states a match found inside the
   manifest itself — `= stats.mean` — which the reader can check against the
   row beside it. If the value matches no published statistic, it says
   nothing.

The proper fix for (2) lives one layer down: a `waveform_stats` result should
publish WHICH statistic its `value` is, instead of leaving it to be matched.
That is an optional manifest key, so not a version bump — but it needs every
LTspice manifest regenerated, so it was not done at the finish line. Note it
as the first v1.1 item that is a real gap rather than deferred scope.

### The viewer's one rule, because it will be tempting to break

**The viewer adds no facts.** Every claim on a page comes from a manifest
field. It does not compute a number, does not round one, does not infer a
verification status from a backend name. The manifest and `question.json` are
copied in beside each page so the rendering is auditable against the record
without running our code. Operational test: if the viewer ever needs to
import from `analysis.py`, `parts.py` or `prose.py` to render something, the
rule has been broken — it currently imports from `library.py` only.

`site/` is gitignored: committing generated HTML would put a second copy of
every published number in the repo, free to drift from the manifests, and
would bury real library diffs under regenerated markup.

### What is left, and it is not code

- **DONE 2026-08-26: pushed, public, and live.**
  `github.com/ShaanGS/ohmwork`, site at `shaangs.github.io/ohmwork`. Pages
  source set to GitHub Actions via the API. Both workflows green. Verified
  end to end from the public internet rather than assumed: the `.circ` served
  from the live site hashes to exactly the sha256 its manifest publishes, so
  the file a stranger downloads is the file Logisim Evolution evaluated.
  Pages over Vercel deliberately — the site must never be able to compute
  anything, and a host that cannot run code makes that structural rather than
  a rule someone has to keep. Vercel over the same `site/` output is a
  drop-in switch if previews or a custom domain are ever wanted.
- **Two more real questions, IF their verbatim text can be obtained.** The
  cheapest candidates remain the experiments behind the hand-drawn `.asc`
  files already here (PN junction VI characteristics, the voltage
  multiplier): the LTspice target already supports diodes and sources, so
  each is a transcription away. **What is needed is question TEXT, not
  code.**
- The column glossary for captions (below) is NO LONGER the next feature.
  See the pivot immediately below; it is still worth doing, but it is not
  what the product needs next.

## THE PRODUCT PIVOT — 2026-08-26, READ THIS BEFORE PLANNING ANYTHING

**The owner's actual product vision was never the static library.** Stated in
their words: *"if someone enters their question they should get the file and
answer, as simple as that"* — a chat-style front door, any question, not a
catalogue of solved ones. They also believed the three seeded questions were
"training data for now". They are not: nothing was ever trained, and the
library is a record of runs, not a lookup table the model learned from.

Two misunderstandings were corrected in both directions, and the correction
went the owner's way on the substance:

1. Mine: I built exactly what the settled architecture specified (a viewer
   over a library) without ever checking that against what they pictured.
   The doc was followed faithfully; the doc was not what they wanted.
2. Theirs: "it already works for any question" was false, and the reason was
   NOT the website. It was that **nobody had built the step that designs a
   circuit from a question.** Every circuit this project had ever solved,
   including the priority encoder, was designed by a HUMAN writing the JSON
   by hand. Everything downstream of a circuit description genuinely does
   work for anything.

### Decisions taken with the owner

- **Digital first.** Logisim Evolution is Java and runs on ordinary Linux
  hosting, so the digital half can be a genuinely live, genuinely verified
  web service. LTspice is a Windows GUI application and cannot be, so analog
  stays local until a Windows host is chosen (cloud VM ~$30-60/mo, or their
  own PC behind a tunnel). **This reopens the hosting constraint: the old
  reasoning assumed the server could never simulate. For digital it can.**
- **Confirmation only for images.** A typed question goes straight through;
  the READING is still printed before the answer as disclosure, not as a
  blocking prompt.
- **The "no live-API hot path" rule NEEDS REWRITING, not quietly breaking.**
  Its stated rationale was "the server CANNOT simulate — so it could only
  ever produce results labelled UNVERIFIED". For a digital question on a
  Linux host that premise is false: the server simulates with Logisim and
  the result is externally verified. The rule's SPIRIT (never serve a number
  nobody checked) is intact and is in fact enforced harder by the design
  loop, which raises rather than return an unverified circuit. Rewrite the
  rule around that distinction before building the endpoint; do not just
  delete it.

### LANDED 2026-08-26: the design loop (ohmwork/spec.py, ohmwork/design.py)

    1. SPEC     the model writes one boolean expression per output from the
                question's WORDS. No gates. Its signal names become
                authoritative downstream.
    2. PLAN     built in PYTHON. A truth-table plan follows from the spec
                with nothing to choose, so asking a model adds a way to be
                wrong and no way to be right.
    3. DESIGN   the model writes ONLY components and nets.
    4. GATE     load_question; a rejection is fed back verbatim.
    5. VERIFY   emit the .circ, hand THAT FILE to Logisim, compare its table
                against the spec's. Mismatch -> the differing rows go back
                and the design is retried.

`python -m ohmwork --solve "<question>"`. 591 tests green.

**MEASURED, not assumed:**

- The oracle was graded against `baselines.Q2_TRUTH_TABLE`, which Logisim
  computed from a STUDENT's hand-drawn encoder. The model's spec was correct
  on **all 32 rows**. Its only disagreement was a NAME — it called the valid
  flag `VALID` where the reference calls it `V` — and the comparison rightly
  refused to guess they were the same. Fixed at the source (spec names are
  handed to the designer), so the class cannot recur. Keep this test: it is
  the only place the oracle is graded against something we did not write.
- End to end on a 2-to-4 decoder this repo had never solved: **verified by
  Logisim in 3 design attempts.** The first two were wrong. The loop earned
  its place on its first real run, which is why `Solution.failed_attempts`
  exists — a run reporting only "verified" hides the designs that were not.

**The limit, and it must never be overclaimed.** The loop proves the circuit
computes THE SPEC. It cannot prove the spec is the right reading of THE
QUESTION. A misread (enable active-low when the question meant active-high)
has spec and circuit agreeing and Logisim confirming both. Same class as
reading 1.8k as 1.8M. That is why `spec.render()` is OUTPUT, printed before
the answer.

### THE BINDING CONSTRAINT IS NOW THE RATE LIMIT

Free Groq is **8000 tokens/minute TOTAL (prompt + max_tokens)**, which is
about ONE call per minute. The decoder run needed ~3 minutes of pauses.
Fine for the CLI, impossible for a website. The owner said they can add
"like 3 keys max" — build key rotation across DIFFERENT PROVIDERS (multiple
accounts on one vendor is evasion of their terms, and is not the way).
`ohmwork/llm.py` is the seam; nothing else should learn about pooling.

Also measured: `max_tokens` counts toward the per-minute budget, so the
library's `max_tokens=8000` in extract.py cannot fit under the cap at all.

### LANDED 2026-08-26: the provider pool (llm.py)

`OHMWORK_LLM=pool` uses every provider whose key is set — groq, cerebras,
gemini, openrouter, mistral — and moves to the NEXT member the moment one
rate limits. It waits only when every member is cooling, and then only until
the EARLIEST wakes, bounded by `max_wait`. Members are different VENDORS by
design: several accounts at one vendor is evasion of that vendor's terms.

Four things worth keeping:

- **One HTTP client, not five SDKs.** All of them are OpenAI
  chat-completions shaped, so `OpenAICompatibleProvider` covers the lot on
  stdlib urllib — pooling costs no new dependency. `GroqProvider` (SDK) stays
  for the single-provider path.
- **Provenance comes from the REPLY, never the provider object.** A pool's
  `.name` is "pool" and its `.model` is a description of membership; both
  design.py and extract.py used to record `provider.model` and would have
  written that into a manifest. Pinned by
  `test_provenance_names_the_model_that_answered_not_the_pool`.
- **OHMWORK_LLM_MODEL is NOT sprayed across members** — it names a model on
  one catalogue. Per-member override is `OHMWORK_LLM_MODEL_<NAME>`.
- **A rate limit is a TYPE, not a message.** `RateLimited` carries
  `retry_after`; the pool moves on for that and disables a member for
  anything else. Groq's SDK errors are classified into it too.

Two incidents, both from the first live call, both now rows 18 and 19 of the
table in ARCHITECTURE.md: urllib's default User-Agent gets a Cloudflare 403
"error code: 1010" from Groq's edge, and a reasoning model asked for 20
tokens returns `200 OK` with an empty string.

**UNMEASURED, and it is the next thing to measure:** the pool has only been
run live against groq. Cerebras, Gemini, OpenRouter and Mistral ids in
`DEFAULT_MODEL` are unverified against a real account — `--list-models` is
what fixes each one, and it now prints member by member.

### LANDED 2026-08-26: the web endpoint and the deployment

`ohmwork/server.py` (FastAPI) + `web/` (React 19 + Vite + Tailwind v4) +
`Dockerfile` + `DEPLOY.md`. `tests/test_server.py` is the spec, 17 cases.

**PROVEN END TO END IN A BROWSER**, not just in tests: logged in, asked
"outputs 1 when exactly two of its three inputs are high", and got the
correct 8-row table verified by Logisim Evolution in ONE attempt, with the
reading rendered above it. That run used the real design loop, the real Groq
key and the real evaluator on this machine.

**THE HOT-PATH RULE IS REWRITTEN, NOT BROKEN.** Its premise -- "the server
CANNOT simulate" -- is true for analog forever and FALSE for digital, because
Logisim is Java and runs on Linux. New wording, in ARCHITECTURE.md and in
server.py's docstring: *no response may carry a circuit or a table the
evaluator did not confirm, and every response names the evaluator*. server.py
imports llm.py deliberately. The spirit is enforced harder: a failed solve
returns an error and NO download, because design.solve raises.

Decisions and why:

- **Hugging Face Spaces, Docker SDK.** The owner's constraint was free with
  no card. HF is free, needs no payment method, has enough RAM for a JVM, and
  serves on 7860. Render's free tier sleeps and cold-starts a JVM; Fly and
  Railway both want a card. Vercel cannot host this AT ALL -- no JVM, and a
  solve outlives a serverless function.
- **A shared password, chosen by the owner over Cloudflare Access.** Fails
  CLOSED: no `OHMWORK_PASSWORD`, no server. Constant-time compare, attempts
  rate limited, HMAC session cookie derived from the password so changing it
  invalidates every session.
- **Everything leaving the server is scrubbed** of configured secret values.
  Pool errors quote several providers' messages at once, and that text goes
  straight to a browser.
- **The Logisim version is PINNED in the Dockerfile** (4.1.0, the version
  every published number here was measured against). A floating `latest`
  would silently change what "verified" means between deploys.
- **`.github/workflows/docker.yml` builds the image on every push** and, in
  it, runs Logisim under xvfb and asserts an anonymous solve gets 401 and a
  passwordless container refuses to boot. Written because **there is no
  Docker on this machine**: the Dockerfile is the one artefact here with no
  local verification path, and its first real run must not be a live deploy.

Two things the first rendered page caught, both by looking at it rather than
by a test -- the same pattern as the viewer:

1. every spec note printed TWICE (once inside `spec.render()`, once as a
   list beneath it) on the one card a person is asked to read carefully;
2. the winning design left showing "design attempt 1" with nothing under it,
   reading as a step that never finished.

**UNMEASURED, and it is what to do next:** the image has never been built,
and the pool has only run live against Groq.

### THE WORST OUTPUT THIS PROJECT HAS EVER PRODUCED (2026-08-26)

Found by the owner, minutes after the endpoint went up, by typing the REAL
Q3 into it -- bridge rectifier, C-L-C filter, zener, 12 V RMS, "using
LTspice". The digital loop answered it. The spec was
`RECT_OUT = AC, FILTER_OUT = AC, REG_OUT = AC, LOAD_CURR = LOAD`; the design
was wires; Logisim confirmed the wires computed the wires; the page said
**VERIFIED** in green with a download button.

Nothing lied. The circuit did match the spec, and an outside tool did confirm
it. **"Verified" never meant more than "the circuit matches the spec", and
nothing downstream of the spec can tell that the spec was a category error.**
The reading was rendered right there in amber and still did not save it,
because a reader who does not already know the loop is digital-only sees
four plausible-looking lines.

Fixed in `ohmwork/domain.py`, THREE layers, because each has a different
blind spot:

1. `check_digital` -- deterministic screen, before any model call, quoting
   the evidence it found ("LTspice, rectifier, 470 uF, 1 mH, Zener"). Needs
   a named simulator OR two independent signals: a false refusal costs the
   product, so the threshold is deliberate and the real digital questions
   are pinned as a set in `tests/test_domain.py`.
2. A refusal channel in `SPEC_PROMPT` -- `{"unsupported": "..."}` -- for
   analog written in words the screen does not know. **A refusal is never
   retried**: asking the same model four more times is how a "no" becomes a
   confident "yes".
3. `check_spec_has_logic` -- refuses a spec where every output is a bare
   copy or a constant. That is the SHAPE the incident produced, so it also
   catches domains nobody has thought of yet.

`DomainError` is a REFUSAL, not a failure, and renders as its own thing in
both the CLI (exit 2, stdout) and the UI (amber card, not the red one). "The
loop tried and could not" and "the loop should never have tried" are
different facts about a question, and collapsing them tells someone to
rephrase when the answer is "use the other tool".

Verified live afterwards: the real Q3 is refused in milliseconds with zero
model calls, and "Design a 2-to-4 decoder with an active-high enable" still
verifies (in 2 attempts -- attempt 1 was rejected by `validate_wiring` for a
mid-span short, which is the router check earning its place again).

Incident 20 in ARCHITECTURE.md.

## STATE AT END OF 2026-08-26 SESSION 2 — READ THIS FIRST

682 tests, 2 skipped. Ten commits since the design loop landed. Everything
below is committed; the working tree is clean.

### What now WORKS, proven by running it

- **A question answered end to end from plain English.** "Design a 2-to-4
  decoder with an active-high enable" -> verified by Logisim in 3 attempts,
  through the real pool, on this machine. That is the product claim, working.
- **Q4 is SOLVED** and is no longer blocked on anything. `examples/q4.json`
  emits a 7447 + seven-segment circuit that Logisim evaluates: all ten valid
  BCD digits match the datasheet, blanking and lamp test behave. The
  measurement came from PUBLIC .circ files on GitHub -- no fixture had to be
  drawn, which is worth remembering the next time something is "blocked on a
  file".
- **The web endpoint and UI** (`ohmwork/server.py`, `web/` on stock
  shadcn/ui). Four outcomes, each its own card: verified, refused (wrong
  domain), unavailable (no provider could answer), failed.
- **The provider pool**, live against five vendors.

### The keys, as measured 2026-08-26 (they will drift)

| provider | state |
|---|---|
| groq | works; 8000 tokens/MINUTE counting prompt + max_tokens. The binding wall. |
| mistral | works. `mistral-large-latest`. The one that made the decoder solve possible. |
| gemini | works on `gemini-flash-latest`. `gemini-3.6-flash` allows TWENTY requests A DAY. |
| cerebras | HTTP 402: that account needs billing. Not free. |
| openrouter | free models throttle every ~5s and return empty completions. |

`--list-models` found two of the five default ids wrong on first contact.
Run it before believing anything in `DEFAULT_MODEL`.

### SETTLED 2026-08-26: the IC-verification decision (ohmwork/partcheck.py)

The open decision above was taken as recommended -- **the chip is its own
reference** -- and it is landed, tested and RUN LIVE. 712 tests green.

The problem it fixes: for a question naming a part, the spec is the model's
MEMORY of a datasheet. For BCD 0000 that memory said every segment off; a
real 7447 shows a nought. The chip is right, so a correct answer failed --
and had the recollection been wrong the other way, a wrong answer would have
passed. Verifying an IC against a recollection is backwards in both
directions.

How it works now, for a question naming a part with output pins:

    1. PROBE     a bare 7447 with one Pin on every port, built as a circuit
                 DESCRIPTION and routed by the real emitter (a probe only
                 partcheck could write would be a second emitter), handed to
                 the same evaluator that will judge the answer. Measured, not
                 recalled. Verified live: all ten valid BCD digits come back
                 exactly as the datasheet says.
    2. WIRING    read out of the design's own nets.
    3. PREDICT   push the probe's table through that wiring.
    4. COMPARE   `spec.compare_tables`, the SAME comparison the gate-level
                 path uses. The two bases differ in what the reference IS and
                 in nothing else.

`Solution.basis` carries which basis ran, its headline, the reading a human
must check, and what it does NOT prove. The CLI prints all three, the web
payload carries `basis`, and `build_question_data` publishes it as two design
notes -- so a part-verified answer and a spec-verified one cannot look
identical anywhere.

**MEASURED LIVE, 2026-08-26.** The real Q4 wording, real pool, real Logisim:
**VERIFIED in 2 design attempts**, 16 rows, spot-checked against the
datasheet by hand. The model chose active-high outputs (an inverter per
segment) and tied RBI LOW -- which blanks the digit 0. That choice is
debatable and the map SHOWS it; that is the map earning its place.

### What the live runs changed, and neither was foreseen

Both were found by running the loop, not by reasoning about it:

1. **Gates in the path are EVALUATED, not refused.** The first live run
   failed four times over because the model put inverters on the segment
   outputs -- a fair reading of "the 7447 has active-low outputs" -- and the
   first cut of partcheck rejected any gate on the grounds that it had no
   logic engine. Refusing a sound design because the checker is thin is the
   checker's problem. `GATE_LOGIC` is now written in partcheck.py and
   imported from NOWHERE: Logisim has its own implementation, and two of them
   disagreeing is a finding. A test pins `GATE_LOGIC | PASSIVE_TYPES | parts`
   against `TYPE_MAP`, so a type added to the emitter without being taught
   here fails immediately instead of becoming silently unpredictable.
2. **An unknown component type is named in the TARGET's words.** A model
   wrote a nonsense type and got back `'type' is not a part this build can
   place: 'type'` -- a KeyError wearing a sentence, naming nothing it could
   act on, and it cost an attempt.

### The limit, and it must never be overclaimed

The part basis proves the emitted FILE routes the question's signals through
a real 7447 exactly as the design says, and that the part in it decodes as a
bare one does. It does NOT prove the wiring is the right reading of the
question: the prediction and the evaluation both read the same nets, so a
swap agrees with itself. Same shape of hole the spec basis has, same answer
-- the wiring map is OUTPUT.

One slice of it IS closed mechanically, and it is the one the old note
worried about: `name_conflicts` refuses a design that wires the question's
signal `A` to the part's pin `D`, because the part has a pin named `A`. That
constraint comes from the NAMES, which sit outside the wiring, so it is not
self-confirming. It fires only on a name that IS one of the part's pins, so a
question calling its inputs D3..D0 is left entirely alone.

Still refused, with a reason: a SECOND part that drives a net. That would
need its own probe and a way to predict two chips together, which nobody has
done. `solve` refuses such a question before spending a token.

### Facts about Logisim learned the hard way this session

Each cost at least one failed design attempt, and each is now enforced:

- **Labels are unique CASE-INSENSITIVELY.** Inputs A,B,C,D with outputs
  a,b,c,d came back as columns A,B,C,D,x,y,z,u -- the clashes renamed to
  letters nobody chose, with nothing in the file saying so.
- **A port on two nets is one net written twice**, and the router collides on
  it. Refused at emission now, as a NET error rather than a geometry one.
- **A Constant holds a level without becoming an input pin.** An input pin
  doubles the truth table; the 7447's three control pins would have turned 16
  rows into 128. Measured, and the value is always written out because
  Logisim's own default is 1.
- **`loc` is not always a port.** True for small gates, false for DIP
  packages. The seven-segment display is NOT an exception -- its loc is
  segment g -- so "Evolution parts are different" was the wrong
  generalisation.

## THE ANALOG DESIGN LOOP — LANDED 2026-08-26 (session 3)

`ohmwork/intent.py` + `ohmwork/analog.py`, with `tests/test_intent.py` and
`tests/test_analog.py` as their specs. 759 tests green. `--solve` now ROUTES:
`domain.classify` picks the analog or the digital loop from the question's
words, prints which way it went and why, and `--domain` overrides it.

**PROVEN LIVE**, real pool, real LTspice, on this machine:

    "Design a series voltage regulator in LTspice that delivers 9 V to a
     1 kOhm load from a 15 V unregulated supply. Report the output voltage
     and the zener current."

    -> MEETS THE INTENT after 4 design attempts. vout = 8.87064 V against a
       9 V +/- 2% target; both regimes held; zener current reported and
       explicitly NOT checked, because the question gave no number for it.

Three of the four attempts were rejected for real reasons, and they are the
evidence that the loop does something: a missed target, a designed value with
no rationale (the gate), and a net referencing a pin that does not exist.

### WHY ANALOG NEEDED A NEW SPEC LAYER, and it is the whole difficulty

A digital question has an ORACLE. Write the boolean expressions, enumerate
2**n rows, and an outside tool either reproduces every one or does not.
**Analog has nothing of the kind.** A circuit converges, produces numbers,
and there is no exhaustive table to check them against.

So `intent.py` is not `spec.py`. It records what the question DEMANDS of the
finished circuit -- one target per quantity, each with the question's own
number and a tolerance -- and verification is the three things that exist:

1. it simulates (a floating node or a source loop dies here);
2. the regime assertions hold (convergence is not correctness);
3. the numbers match the intent.

**THE GAP WITH NO DIGITAL COUNTERPART, and it must never be blurred: meeting
a target is not being a good design.** A regulator that hits 9.00 V while
dissipating six watts in the pass transistor, or with ripple nobody asked
about, passes everything here. Correct truth-table rows really are the whole
answer; correct measurements are not. `INTENT_LIMIT` says exactly that, and
the CLI prints it under "NOT established by any check here".

### Three things that stop the analog check becoming decorative

- **A tolerance is CAPPED** at `MAX_TOLERANCE_PCT` (20). Wider than that and
  any plausible circuit satisfies it, so the check cannot fail.
- **An intent with no targets is REFUSED** -- the analog shape of
  `check_spec_has_logic`.
- **The regime assertions are DERIVED from the parts list**, never requested.
  Every zener gets `zener_in_breakdown`, every BJT `bjt_active`, on every
  measured run, so a design cannot omit the one that would have failed.

A target the question gave no number for is an **observation**: measured,
reported, and counted SEPARATELY. A run in which nothing COULD fail must not
read like one in which nothing did.

### The transient window is derived, and it was CHECKED against a human

Ten periods simulated, the first five discarded, two hundred steps a period.
For the 50 Hz supply in `examples/q3.json` that reproduces the stop (200m),
settle (100m) and max_step (100u) a person wrote BY HAND for that circuit, to
the digit. Pinned in `test_the_transient_window_is_derived_from_the_source_frequency`.
A derivation that agrees with a plan someone wrote for a real circuit is
worth more than one that merely looks reasonable.

### Roles, and the two reserved refs

An intent is written before any circuit exists, so it cannot name a ref. It
names a ROLE. Three resolve by component TYPE (zener, transistor, diode) and
two cannot: a supply is a `voltage` and a load is a `res` like every other
one, so nothing in the parts list distinguishes them. **`V1` is the supply
and `RL` is the load**, stated in the design prompt and named in the error
when one is missing.

### What the live runs changed

- **`LTspiceBackend` and `NgspiceBackend` now DECLARE `verification`.** They
  were relying on a `getattr(..., "external")` default, which is the
  "silence is indistinguishable from a pass" pattern in the one place the
  project is loudest about.
- **The CLI streams the run.** Four attempts and several minutes used to
  print nothing until the end, and a FAILED run printed only the last error
  -- not the reading, which is the one thing that tells a person whether the
  loop misunderstood the question. `_LiveRun` prints it as it arrives and
  remembers it, so the summary does not print it twice (the defect the
  viewer and the web UI each shipped once).
- **An unknown pin now names the pins the component DOES have.** A model
  wrote `Q1.base` for a transistor whose pins are C, B, E and was told only
  that the pin did not exist -- a whole retry spent guessing at a vocabulary
  the message was already holding.
- **The first line of an intent failure is self-contained.** Both the CLI and
  the web UI render a rejected attempt as ONE line, and a bare header reading
  "the circuit does not meet the design intent:" reads as an attempt that
  failed for no stated reason.
- **A note about a CHOICE gets its own heading.** A tolerance the model chose
  rendered directly under "stated in the question:", at the same indent, and
  read as though the question had stated it.

### WHAT POINTING IT AT THE REAL Q3 FOUND, and it is the best evidence yet

Q3 -- bridge rectifier, C-L-C filter (470 uF / 1 mH), zener, 12 V RMS at
50 Hz, five waveforms -- is the hardest question in this repo and the one
with a hand-written answer to compare against. The loop has not solved it
yet. It found two real defects on the way, both of which are now fixed and
both of which are incidents 22 and 23 in ARCHITECTURE.md.

**The READING came back almost perfect**, which is worth recording on its
own: the topology, the frequency, all six stated values including `470 µF`
and `1 mH` and `1 kΩ`, five waveform observations correctly marked "not
checked (reported only)", one checkable target at 6.2 V +/- 5%, and two
notes recording the choices it made. That is the artefact the whole design
rests on, and on the hardest question available it was right.

1. **A current measured as a voltage, silently.** One of those observations
   was "load current waveform". The only waveform kind measured `V(net)`, so
   the model gave it the load's NODE -- and the report would have shown a
   voltage under a name that says current, with nothing to catch it, because
   an observation carries no number to fail against. Fixed with a
   `current_waveform` kind that takes a role and emits `I(RL)`, and by
   printing WHAT each target is measured on in the reading:

       i_load_waveform  load current waveform  the load's current  not checked

   That second half is the general defence. A reading that shows only names
   cannot be checked; one that shows the expression can.

2. **A 335 MB raw file, and a twenty-minute hang with no error.** The design
   that followed simulated FINE and well inside its 120 s timeout. What did
   not finish was parsing a third of a gigabyte of ASCII: the undamped LC
   rang, the solver took ever smaller steps, and the subprocess timeout never
   fired because the subprocess had already exited. `simulate.check_raw_size`
   now refuses a result too large to be about a circuit, in words a design
   loop can act on ("this means the circuit is RINGING"), and the design
   prompt says an LC filter with no series resistance rings. Note the shape:
   a timeout that guarded the wrong step, so the guarded thing was fast and
   the unguarded thing was unbounded.

3. **An intent with ZERO checkable targets, reported as a pass.** Q3 asks to
   OBSERVE five waveforms and states no figure to hit, so a perfectly legal
   intent came back with five observations and nothing numeric. The success
   summary then read *"0 of 5 stated targets carry a number, and LTspice met
   every one"* -- a sentence that reads like a pass over nothing at all. The
   result IS real (it converged, its regimes held); it is simply not a
   numeric one. `IntentComparison.checked` exists now, the summary says "NO
   target carried a number, so nothing numeric could fail or pass" and names
   what WAS checked, and the CLI prints **RAN AND STAYED IN REGIME** instead
   of **MEETS THE INTENT**. Note the species: the same one as `has_skipped_checks`
   and the unrun-check rule, in a new place.

4. **Nothing printed for the first ten minutes of a run.** `_LiveRun` streams
   the reading and each rejected attempt, but redirected to a file or a pipe
   -- which is how anyone watches a run that takes minutes -- Python
   block-buffers stdout. Fixed by line-buffering in
   `_make_stdout_unicode_safe`. A progress report that appears only once the
   work is finished is not a progress report.

Also: a missing `unit` is DERIVED from the target's kind rather than
demanded. A volt target is measured in volts whatever anyone writes, and
rejecting an otherwise sound intent over a cosmetic field spends a retry for
nothing.

### What is NOT done

- **The web endpoint is still digital-only.** `server.py` calls
  `design.solve`; nothing routes there yet. That is deliberate for now: the
  hosting constraint is unchanged -- LTspice is a Windows GUI application and
  cannot run on the Linux host -- so an analog question typed into the site
  must be refused or queued, not answered. Wiring `classify` into the server
  means deciding WHICH, and that is the hosting decision that is still open.
- **Nothing publishes an analog solve into `library/` yet.** Same item as
  before (next-session list item 4).
- **Q3 is not solved yet, and it is BLOCKED ON RATE LIMIT, not on code.**
  The second run got much further than the first: the reading was excellent
  (see above), attempt 1 was rejected for a real reason (`pin L1.b appears in
  more than one net`), and attempt 4 emitted a proper bridge + C-L-C + zener
  + NPN pass transistor that LTspice ran cleanly, producing a healthy 686 KB
  result rather than the 335 MB one. Then every free provider ran out at once:

      no circuit meeting the intent: none of the 5 model provider(s) could
      answer right now.
        cerebras: that account needs billing enabled; it is not free
        gemini:   free quota for today is spent; it resets tomorrow
        openrouter: busy or rate limited right now
        mistral:  TimeoutError: The read operation timed out
      The earliest member wakes in 2428.23s

  Which is the pool doing exactly its job: "nobody could be asked" is
  reported as its own outcome, not as "your circuit was wrong". **Re-run it
  when the quotas reset.** It is the natural acceptance case because a
  hand-written answer for it already sits in `examples/q3.json`.

## THE IMAGE IS PROVEN, 2026-08-26 — five red builds to get there

The `docker` workflow had never run before today. Its first run was the
Dockerfile's first execution ANYWHERE, and it failed four times. Every one of
those would otherwise have been discovered on a live Space, which is exactly
what that workflow exists to prevent. It is now **fully green**: the image
builds, Logisim runs inside it under a real display, the server says something
when it starts, it serves, an anonymous solve gets 401, the built page is
served, and a container with no `OHMWORK_PASSWORD` refuses to boot.

What the five runs found, in order, and every one is a species this project
already knows:

1. **`xvfb-run: error: xauth command not found`** — Debian's xvfb only
   RECOMMENDS xauth and the image installs with `--no-install-recommends`.
   The container died in 150 ms.
2. **The step that should have caught (1) had `|| true` on the half that
   mattered.** It ran `xvfb-run ... || true; java -version`, so only
   `java -version` had to succeed. A check written so its interesting half
   cannot fail is not a check.
3. **`docker logs` sat AFTER the failing command**, so the first failure
   reported eighty seconds of "connection refused" and never said why. A
   check that catches a broken image but cannot say what broke is half a
   check. The logs print unconditionally now, and the wait stops the moment
   the container is gone.
4. **A container that was UP, bound to nothing, and completely silent** —
   because `xvfb-run` was PID 1 with the server as a grandchild. Replaced by
   `deploy/entrypoint.sh`: start Xvfb, wait for its socket, `exec` the
   server. The server is PID 1, its stdout IS the container log, and
   `docker stop` reaches it.
5. **The fail-closed check could never have passed.** Written as
   `if docker run ... | tee out; then fail; fi` — a pipeline's status is its
   LAST command, and `tee` always succeeds. It reported "it started without a
   password" over a container that had refused to start with exactly the
   right error.

**And one overclaim of mine, corrected by the image.** I said the built page
was looked up in a place that only works for an editable install and would
serve blank from a container. It would not have: `/app` is on `sys.path`
ahead of site-packages there. The widened lookup and `OHMWORK_STATIC` stay --
the single lookup was right only by a coincidence between the working
directory and the install layout -- but the comment claimed a measurement it
did not have. Pinning a plausible story is the thing this project spends most
of its machinery avoiding, and it is just as easy to do in a comment.

### DECLINE VERCEL'S IMPORT OFFER

Pushing makes Vercel email *"1 new project available to import — web · vite"*.
It has found the React frontend and it really can build it. Importing it
deploys **the page with no server behind it**: no Logisim, no `/api/solve`, no
password gate. The login box renders and nothing behind it works — a failure
worse than no deployment, because it looks like one. Written into DEPLOY.md.

### THE HOST IS NOT SETTLED: HF's DOCKER SDK IS PAID NOW

**Observed 2026-08-26 on the Create-a-Space page: the Docker card is greyed
and badged "Paid".** Static and Gradio are free; Docker is not. DEPLOY.md
recommended Hugging Face *because* it was free and needed no card, so that
recommendation is gone. `deploy/push-space.sh` is unchanged and works the
moment the SDK is available — this became a price question, not an
engineering one.

**Do not "solve" it with a free Gradio Space.** It is Python with an apt list,
so the temptation is to install a JRE and shell out to Logisim. Evolution
4.1.0 needs Java 21, the Space base ships an older default JRE, and every
published number in this repo was measured against the PINNED evaluator. A
JRE that probably works is not a foundation for a tool whose whole claim is
that an outside tool checked the answer.

**The option worth taking seriously is a tunnel from THIS machine.**
Cloudflare Tunnel, free, stable hostname, audience of five. It is the only
place the ANALOG half could ever be served, because LTspice is a Windows GUI
application and no Linux host will ever run it. Be precise about the cost:
`server.py` is digital-only, and wiring `domain.classify` +
`analog.solve_analog` into a request path is real work — it means deciding
what a browser sees while LTspice runs for several seconds. The tunnel makes
analog POSSIBLE, which no hosted option can say; it does not make it done.

### THE BINDING CONSTRAINT ON A DEPLOY IS QUOTA, NOT CODE

Measured today, on the Q3 run: all five free providers exhausted at once
(cerebras needs billing, gemini's daily quota spent, openrouter throttling,
mistral timing out, groq's per-minute wall). Groq's 8000 tokens/minute counts
prompt + max_tokens, which is about ONE call a minute, and a solve is two to
five calls. So a deployed site on free tiers serves a handful of solves a day
and then tells everyone to come back in forty minutes -- honestly, because
`PoolExhausted` is its own outcome, but it is not a product anyone enjoys.
**The fix is one paid provider tier, and it is an env var, not a rewrite:
`llm.py` is already the seam.** Check current pricing before committing.

### NEXT SESSION, in order

1. DONE 2026-08-26: the IC-verification decision is settled and the 7447
   question now solves live. See the partcheck section above.
2. **Solve the real Q3 with the analog loop.** It is the natural acceptance
   case: eleven components, a transient run, five waveforms, and a
   hand-written answer already in the repo to compare against. The first
   live attempt died on `Q1.base` (the pin message is fixed now).
3. **Decide what the WEB endpoint does with an analog question.** It is
   digital-only today, deliberately: LTspice is a Windows GUI application
   and cannot run on the Linux host, so an analog question there must be
   refused or QUEUED, never answered. Wiring `domain.classify` into
   server.py means choosing which, and that is the hosting decision that is
   still open -- not a code one.
4. **Deploy — but the HOST is an open decision, not a task.** The image is
   PROVEN (see above): the docker workflow is fully green after five runs.
   What is not decided is where it goes, because HF's Docker SDK went paid.
   Read "THE HOST IS NOT SETTLED" above and DEPLOY.md's table. If the answer
   is the tunnel, the next piece of code is an analog path in `server.py`.
5. Publish solved questions into `library/` automatically, so the viewer and
   the live service stop being two products.

**Do not start the analog half's HOSTING** until a Windows host is chosen. It
is a hosting decision, not a code one; analog runs locally where LTspice is.

### Housekeeping for whoever picks this up

- A local server may still be running on port 7860 (`OHMWORK_PASSWORD=password`).
  `netstat -ano | grep :7860` then `taskkill //PID <pid> //F`.
- `web/dist` is gitignored; run `cd web && npm install && npm run build`
  before `python -m ohmwork.server` or the page will 404.
- Scratchpad holds the derivation scripts for the 7447 geometry
  (`scratchpad/ttl/derive*.py`) and the downloaded source files. They are NOT
  in the repo: the ones that carry the 7447 geometry are unlicensed and are
  cited in `tests/fixtures/README.md` rather than copied.

### What extract.py's probing found, and it is still unfixed

Real-model probes on 2026-08-26 (recorded because they cost rate limit to
rediscover):

- **the prompt is transcription-only.** Asked to "design a 2-to-4 decoder" it
  produced ZERO components, correctly following its own instructions. There
  is no design path in `extract.py`; `design.py` is now that path for digital.
- **extraction never produces the analysis plan.** Every probe had
  `plan=False`, so nothing could be simulated. `design.build_plan` sidesteps
  this for digital by deriving it; the analog path still needs it.
- **the `asks` sub-schema defeats the model** — three different ask-shaped
  rejections in three attempts.
- **the retry loop cannot see it is stuck**: attempts 2-5 returned the
  IDENTICAL rejection while it kept paying. `design.solve` now stops on a
  repeated identical failure; **`extract.py` still does not.**
- topology reading DOES work: components went 4 -> 9 -> 10 across retries on
  Q3, whose real circuit has 11.

### Model layer: MEASURED, not assumed

Groq is the provider (`OHMWORK_LLM=groq`), key in a gitignored `.env`,
template in the committed `.env.example`. `pip install -e ".[llm]"`.

**A free Groq account served exactly 13 models on 2026-08-25 and NONE of them
accepts images.** They are text chat, whisper (speech-to-text), orpheus
(text-to-speech) and prompt-guard (safety classifiers). Consequences:

- text extraction works; IMAGE extraction cannot run on Groq today
- `openai/gpt-oss-120b` is confirmed working and is now the default
- the previous default, `llama-3.3-70b-versatile`, did not exist on that
  account at all — which is exactly why `--list-models` and the
  self-correcting model error were built before any key existed

This blocks Q1-style extraction, where the component values exist nowhere but
the picture. Options, none chosen yet: keep transcribing question text by hand
(what has happened for all four questions so far), use Anthropic for the
vision step only (the seam already supports mixing), or find a tier that
serves a multimodal model.

### What the first real caption proved

Generated from the evidence rows alone, every number correct, and it said
"V = 1 ... indicating that the circuit produces a HIGH OUTPUT VOLTAGE". V is
the valid flag. The model guessed a column's meaning because rows are all it
can see — the review gate caught it, which is the best demonstration of the
gate that exists.

The prompt now forbids expanding column names, and the caption got safer AND
worse: "indicating a consistent value for V when EN = 1" is nearly vacuous.
**NEXT TASK, and the reason it is next: a COLUMN GLOSSARY supplied by the
question author.** The human knows V is the valid flag; the model should not
have to guess it or be blind to it. Small schema addition to the table
measurement, and it buys back the meaning without loosening the constraint
that the model only ever sees rows.

The stored Q2 caption is still the hand-written one. Do not replace it with a
generated one until the glossary lands.

### Decisions taken this session

- **Groq does not violate the "no live-API hot path" rule.** That rule is
  about WHERE the call happens. Operational test: if anything in a request
  path imports `ohmwork/llm.py`, the rule has been broken.
- **AGENTS.md goes PRIVATE** when the repo is published (gitignore it), with
  a public `ARCHITECTURE.md` carrying the incident table, the verification
  limits, and the derived format facts. Those are engineering findings, not
  workflow, and they are what makes the repo worth linking. **DONE
  2026-08-26**, and AGENTS.md is gitignored.
- **Repo hygiene: DONE 2026-08-26.** `git init`, MIT LICENSE, both GitHub
  Actions workflows, and ARCHITECTURE.md all landed, and the key never
  entered history — `.env` was ignored BEFORE the first commit, which is the
  only ordering that works. The reasoning is kept because it applies to the
  next repo: a key committed once is leaked forever even after it is deleted
  from the working tree, so `git init` early is insurance, not hygiene. What
  remains is pushing to a remote, which is the owner's call.

### Open product question, unresolved

Does the hosted site RUN extraction, or only serve solved questions?

- serve only -> static, no backend, no auth needed, nothing metered to protect
- extract live -> backend, SERVER-SIDE auth (a credential checked in the
  browser protects nothing: the endpoint is callable directly), the owner's
  key on a server, and the server CANNOT simulate — so it could only ever
  produce results labelled UNVERIFIED, in a mode kept clearly separate from
  solved questions, per "an upload is a REQUEST, not a solve".

The extractor is identical either way, so this blocks only the hosting phase.

## v1.0 SCOPE — FROZEN 2026-08-24

The project keeps finding real work. That is good engineering and bad project
management, so the line is drawn here.

**IN v1.0**

| item | state |
|---|---|
| LTspice target complete | DONE |
| Logisim target: Q2 end to end, externally verified | DONE 2026-08-25 |
| Prose layer (three tiers) | DONE 2026-08-25 |
| Library format, and real questions seeded | format DONE; **3 of 5 seeded** — see below |
| Static viewer over the library | DONE 2026-08-26, `ohmwork/viewer.py` |
| README | DONE 2026-08-25, numbers pinned by tests/test_readme.py |

**SEEDING IS SHORT AND THAT IS THE HONEST OUTCOME.** The freeze said "at
least 5 real questions". Three are seeded (exp02, exp03, exp08) because three
is how many questions we have VERBATIM TEXT for and can currently solve. Q4
has its text but is blocked on a Logisim Evolution fixture. There is no fifth
question in this repo. Filling the count would mean inventing one, and a
fabricated library entry is worse than a short library — the whole product
claim is that every entry traces to a real question and a real simulator run.
**What is needed is question TEXT, not code.** The cheapest candidates are the
experiments behind the hand-drawn `.asc` files already here (PN junction VI
characteristics forward/reverse, and the voltage multiplier): the LTspice
target already supports diodes and sources, so each is a transcription away.

**OUT — deferred to v1.1.** Do not start these. If one genuinely blocks
something in v1.0, say so rather than quietly building it:

- the vision / extraction layer (question text + image -> question JSON)
- Q4 and Evolution-native geometry (blocked on a fixture regardless)
- check-mine mode (parsing a student's own hand-drawn file)
- **the `.circ` geometric parser** (moved here 2026-08-25). External
  verification is strictly stronger than a round trip through our own code:
  Logisim evaluating the emitted file catches a geometry bug that emitter and
  parser sharing a pin table would agree on. The parser earns its place for
  check-mine mode and for reading foreign files, both v1.1.
- a layout engine beyond "correct but ugly"
- mirrored placements (M0/M90/M180/M270)

**What the freeze does NOT do.** It does not lower a verification standard,
and it does not license shipping something unlabelled. Every rule in
"Verification limits", "The evaluator asymmetry: RESOLVED", and the manifest
contract still holds for everything that ships. Cutting scope means fewer
things, not weaker things.

## Build order and current state (updated 2026-08-24)

1. DONE `emitter.py` - JSON circuit description to `.asc`. No AI.
2. DONE `parser.py` - `.asc` back to netlist, purely from SYMBOL + FLAG
   geometry. Hard-fails on any pin with no flag at its coordinate.
3. DONE `simulate.py` - Backend protocol; LTspiceBackend (authoritative,
   `-b -ascii` + spicelib) and NgspiceBackend (CI liveness only).
4. DONE tests covering the reference case, all baselines with provenance
   in `tests/baselines.py`.
5. DONE `analysis.py` (experiment plans: op/dc/param_sweep/tran runs,
   scalar/derived/waveform_stats/regime measurements, deliverable vs
   scratch files) and `question.py` (the input gate: strict schema,
   device policy, origins, coverage, `--dry-run`). `plt.py` is FROZEN.
   CLI: `python -m ohmwork <question.json> [--dry-run] [--explain]`.
   Working examples: `examples/q1_anchored.json` (analysis question),
   `examples/q3.json` (design question, tran + waveform stats).
6. IN PROGRESS: Logisim target. Format DERIVED from three real
   hand-drawn 2.7.1 files; geometry pinned in
   `tests/test_logisim_geometry.py`; table in `ohmwork/logisim_symbols.py`
   with hard failure on any unmeasured shape. Logisim Evolution is
   installed and `--tty table` gives EXTERNAL verification
   (`ohmwork/logisim_backend.py`). Targets are now first-class
   (`ohmwork/targets.py`) and `load_question` runs only the declared
   target's chain.
   Q2 IS DONE, end to end, 2026-08-25. The steps as they landed:
     a. DONE `truth_table` run type + `table` measurement kind.
     b. DONE digital regime asserts: no_floating_inputs,
        all_outputs_driven, no_combinational_loops. They name no device —
        each is a property of the whole circuit — and they require a
        truth_table run. **Now EVALUATED, not merely declared**
        (`analysis.check_regimes`), against the circuit description rather
        than against evaluator output: that is where a floating gate input,
        an undriven output pin and a combinational loop actually live, and
        it means a failure names the pin. Logisim can only report the same
        failures as an error marker in a cell, which parse_tty_table
        refuses as non-binary — true, but it names nothing.
     c. DONE the `.circ` emitter (`ohmwork/logisim_emitter.py`). Inputs in a left column, outputs in a
        right column, gates in columns by logic depth. Route orthogonally.
        CROSS FREELY — the crossing rule makes that safe — and NEVER split
        a wire at a crossing point. **Do not build a placer that tries to
        look good**: generated layouts are mechanical and the output must
        SAY so, because a student opening one will otherwise expect it to
        resemble a hand-drawn schematic. Layout quality is explicitly v1.1.
     d. MOVED TO v1.1: the geometric `.circ` parser and the Logisim round
        trip. External verification is strictly stronger than a round trip
        through our own code, so the parser earns its place for check-mine
        mode and for reading foreign files, not for checking our output.
        `LogisimTarget.round_trip` therefore still returns `ran=False`, and
        its reason now names what stands in its place — a reader who saw
        only "did not run" would conclude the opposite of the truth.
     e. DONE prose asks: the three tiers (`ohmwork/prose.py`).
   `examples/q2.json` (promoted out of `drafts/`) is the acceptance case and
   is green: 32 rows from Logisim, three regimes evaluated, both prose asks
   answered, published as `library/exp08-priority-encoder/`.
7. LATER (v1.1) LLM layer: question text + image to question JSON. Note what
   is already waiting for it: `prose.render_prose_section(... answers=...)`
   takes generated captions keyed by ask text and is called with `{}` today,
   so every prose_from_results ask prints its evidence rows and then says
   "no answer generated" unless the question carries a hand-written one.
8. LATER retry loop on verification failure.
9. Hosting: SETTLED, see "Deployment: the library is the product".
   Manifest, per-question layout, and three seeded questions landed.
   Remaining v1.0 work is the static viewer, plus two more real questions
   IF their verbatim text can be obtained (see the seeding note above).

Do not skip ahead. Environment: LTspice 26.0.2.1 at `F:\LTspice.exe`,
`OHMWORK_LTSPICE` set user-level; Logisim Evolution 4.1.0 at
`C:\Program Files\logisim-evolution\logisim-evolution.exe` (installed via
winget, bundles its own Java 21; `OHMWORK_LOGISIM` overrides); ngspice not
installed (its tests skip); `python -m pytest` from the repo root.

## Input is usually an image, not text

The questions live in lab manual PDFs. Most of them reference a circuit diagram
that is only present as a picture: "the regulator circuit shown below". The
component values, topology, and annotations (beta, zener voltage, resistor
values) exist only in that image.

So the input contract is: **question text + optional circuit image**. The LLM
layer must be vision-capable. A text-only pipeline cannot solve Q1 at all.

The extraction step should output a structured intermediate the human can check
before anything is generated:

```json
{
  "topology": "series voltage regulator",
  "components": [
    {"ref": "V1", "type": "voltage", "value": "15", "note": "unregulated"},
    {"ref": "R1", "type": "res", "value": "1.8k"},
    {"ref": "D1", "type": "zener", "value": "8.3", "note": "Vz"},
    {"ref": "Q1", "type": "npn", "params": {"BF": 100}},
    {"ref": "RL", "type": "res", "value": "2k"}
  ],
  "nets": {"vin": ["V1.+","R1.a","Q1.C"], "vb": ["R1.b","D1.cathode","Q1.B"],
           "vout": ["Q1.E","RL.a"], "0": ["V1.-","D1.anode","RL.b"]},
  "asks": ["output voltage", "line regulation", "load regulation", "zener current"]
}
```

Misreading a value from an image is the most likely failure in the whole system
and simulation cannot catch it, because a wrong-but-consistent circuit
simulates fine. So always print the extracted values back for the user to
confirm. Do not silently trust the vision step.

## Question taxonomy

Four real examples from the lab manuals, covering the range:

1. **Analog, numeric** (Exp 2, LTspice). Given a circuit image, compute output
   voltage, line regulation, load regulation, zener current. Needs `.dc` sweeps
   and an external loop over load values.
2. **Digital, gate-level** (Exp 8, Logisim). Design a 4-to-2 priority encoder
   with enable and valid-output. Wants truth table, all input combinations
   tested, and prose explaining behaviour when multiple inputs are active and
   when enable is off.
3. **Analog, waveforms** (Exp 3, LTspice). Bridge rectifier, C-L-C filter
   (C=470u, L=1mH), zener regulator, 12V RMS 50Hz source, 1k load. Wants four
   transient waveforms: input AC, rectifier output, filter output, regulated
   output, plus load current.
4. **Digital, IC-level** (Exp 9, Logisim Evolution). BCD to seven-segment using
   a 7447, all 16 input codes, identify valid vs invalid BCD. Note the 7447 has
   active-low segment outputs.

So the tool needs: DC operating point, DC sweep, transient analysis, truth
table generation, and IC-level component support. Not just one analysis type.

### What has actually been supplied (audited 2026-08-24)

Three of the four arrived as screenshots. Track this table, because "we have
the question" and "we have a paraphrase someone typed" are different states
and only one of them is evidence.

| # | exp | verbatim text? | status |
|---|---|---|---|
| Q1 | 2.14 | YES, screenshot, in `examples/q1_anchored.json` | SOLVED, baselines pinned |
| Q2 | 8.2 | YES, screenshot, in `examples/q2.json` | SOLVED 2026-08-25, table pinned, published as exp08-priority-encoder |
| Q3 | 3 | YES, screenshot, in `examples/q3.json` | SOLVED, baselines pinned |
| Q4 | 9.7 | YES, screenshot, recorded below | blocked on a Logisim Evolution fixture |

Both solved examples carry a `source` block recording that a human
transcribed them from a screenshot. That is the provenance which makes the
text re-checkable against the original.

**Q2 arrived 2026-08-24 and closed an interesting loop.** Its text had been
sitting in `examples/drafts/q2.json` as a hand-written schema probe (it now
lives at `examples/q2.json`, promoted out of drafts once it was solved), and when
the screenshot finally came the probe turned out to be the real question — off
by exactly two characters, the quotes around `'valid output'`. That is a
warning, not a reassurance: it was right by luck, we had no way to know, and a
paraphrase that happens to be accurate is indistinguishable from one that is
not until the source arrives. Treating it as unverified was correct procedure
even though it was, in the end, correct text.

**Q4's verbatim text** lives in `examples/drafts/q4_question.md` (it cannot
yet live in a valid question JSON — the schema would need Logisim components
whose geometry is underived). That file is the single source of truth for it
and records two transcription doubts: en dashes in `(0–9)`/`(10–15)`, and a
missing final period after "segment patterns". It is deliberately NOT copied
here — a verbatim text duplicated in two places is a verbatim text that will
eventually disagree with itself.

So Q4 is blocked on FORMAT EVIDENCE, not on the question. Note the question
names Logisim Evolution explicitly, so the earlier target decision was not
arbitrary — it just cannot be honoured without a file to derive from.

### The paraphrase regression (found 2026-08-24)

`examples/q3.json` shipped a PARAPHRASE in its `question` field for some time:
shorter, tidied, "using LTspice" dropped, "470 uF" written "470uF", sentences
restructured. It looked entirely fine.

The damage is measurable, and it points the flattering way. Against the
paraphrase the asks claimed **74%** of the question's words. Against the real
text, **59%**. A summary written alongside the asks agrees with the asks by
construction, so the one screen designed to reveal dropped work was quietly
grading itself — the exact circularity "Verification limits" warns about,
reintroduced through the one field that was supposed to be immune.

Guarded now by `test_question_text_is_the_verbatim_wording`, which pins
distinctive fragments of each supplied question INCLUDING its Greek
characters, and by `test_transcribed_questions_record_where_they_came_from`.

**Consequence for the vision layer:** it must transcribe, never summarise. A
tidied question is not a question. If the model cannot read a word, that is a
low-confidence marker for the human to resolve, not licence to smooth it.

### Traps in these specific questions

- **Q2**: Logisim has a built-in priority encoder in the Plexers library.
  "Solving" it with that one component is technically correct and pedagogically
  useless. The question wants gate-level design. The generator must be told
  explicitly whether primitives-only is required.
- **Q4**: needs the 7447 and a seven-segment display, which live in Logisim
  Evolution's TTL and I/O libraries. Plain Logisim 2.7.1 does not have them.
- **Q3**: transient analysis with 470uF and a bridge needs sensible `.tran`
  settings and an initial-condition strategy, or it will fail to converge or
  show only startup transient.

## Logisim target: DERIVED for 2.7.1, UNVERIFIED for Evolution

**Build target: Logisim 2.7.1** (revised 2026-08-24, later the same day).
Evolution was chosen earlier because Q4 requires it — the 7447 and
seven-segment live in Evolution's TTL and I/O libraries — but no Evolution
file is coming, so Q4 is parked and 2.7.1 is what we have real evidence for.
See "Q4 is BLOCKED ON FIXTURE" at the end of this section.

### THE derivation method for this format

**A port is a coordinate where exactly one wire terminates. Nothing else is
evidence.** In particular, proximity to a component is not evidence: a human
routes wires around and into component bodies, so "the endpoint nearest the
gate" picks up corners and stray stubs just as readily as pins.

The XOR gates in `adder_subtractor.circ` are the worked example. Degree =
number of wires touching a coordinate:

```
(-50,-20)   degree 1, 1, 1, 1     port
(-50,+20)   degree 2, 1, 1, 0     port
(-60,-20)   degree 2, 2, 2, 2     NOT a port -- a bend
(-60,+20)   degree 2, 2, 2, 2     NOT a port -- a bend
```

Degree 2 with both wires ending there is a corner: a horizontal and a
vertical segment meeting. Proximity filtering lands on (-60,+-20) and is
wrong. Deriving (-60,+-20) was in fact tried and produced exactly that.

Two cautions the same example carries:

- Degree 1 PROVES a port; degree != 1 does NOT disprove one. (-50,+20) is
  degree 2 on one XOR (the human routed a corner onto the pin) and degree 0
  on another (that input is simply unwired).
- So dead ends give CANDIDATES. Confirmation is the hypothesis holding across
  every instance, plus a whole file explained with nothing left over.

This generalises: it is the same reasoning the LTspice table came from, where
wire endpoints were matched against symbol anchors. State the rule in port
terms, never in distance terms.

### The files

**Format derived 2026-08-24 from three real hand-drawn lab files. All three
are Logisim 2.7.1, so none of it is verified for the target.** The files are
fixtures in `tests/fixtures/logisim/`, byte-for-byte apart from a redaction
(students' names and registration numbers were on the canvas as Text
elements; see `tests/fixtures/README.md` — no geometry was touched, and a
test guards against personal data reappearing). Every measurement below is
pinned in `tests/test_logisim_geometry.py`, which imports no ohmwork code and
is therefore pure evidence about the format. `ohmwork/logisim_symbols.py`
carries the same table for the code to use, and a test asserts the two agree:

- `exp8_gates.circ` (was "EXP 8.circ") — gate-level 4-to-2 priority encoder
  with enable and valid, primitives only. This is Q2, answered by hand.
- `priority_plexers.circ` (was "4_to_2_priority.circ") — the same question
  solved with the built-in Plexers Priority Encoder. The Q2 trap, in the wild.
- `adder_subtractor.circ` (was "open ended logisim.circ") — 4-bit
  adder/subtractor. Contains real drawing errors, and is kept for that reason.

### Verified format facts (Logisim 2.7.1)

```xml
<project source="2.7.1" version="1.0">
<lib desc="#Gates" name="1"/>
<wire from="(420,150)" to="(420,220)"/>
<comp lib="1" loc="(620,280)" name="AND Gate">
  <a name="inputs" val="2"/>
</comp>
<comp lib="1" loc="(410,480)" name="NOT Gate"/>
```

- `<comp>` is `lib` + `loc` + `name`, with `<a name= val=/>` children, and is
  self-closing when every attribute is default.
- `comp/@lib` is an index into THIS FILE'S `<lib>` block, not a global id.
  Always resolve it through the block; never match on the literal `lib="2"`.
- Wires are plain coordinate segments. No net names, no direction.
- Components and wires sit on a 10-unit grid. `Text` does NOT — the text tool
  places freely (measured at (376,274) and (463,115)).
- An output Pin carries `output=true` (drawn with `facing=west`,
  `labelloc=east`). Absence of `output` means INPUT: the default is not
  neutral, and a file can silently declare an output as an input pin.
- Line endings are NOT an invariant. Two of the three files are CRLF and one
  is bare LF, and all three are real files Logisim wrote. The earlier
  "files use CRLF" note came from a single file and is withdrawn.

### Pin table (measured, at DEFAULT size, facing east)

Offsets relative to `loc`, which for every component measured is also its
OUTPUT pin.

| component            | offsets from `loc`                             | instances |
|----------------------|------------------------------------------------|-----------|
| Pin (in or out)      | (0,0)                                          | 30 |
| NOT Gate             | in (-30,0); out (0,0)                          | 1  |
| AND/OR/XOR, 2 inputs | in (-50,-20), (-50,20); out (0,0)              | 10 |
| OR Gate, 4 inputs    | in (-50,-20), (-50,-10), (-50,10), (-50,20)    | 1  |
| Adder (width 1)      | A (-40,-10), B (-40,10), Cin (-20,-20), Cout (-20,20), S (0,0) | 4 |
| Priority Encoder     | in (-40,-10..+20 by 10), EN (-20,30), out (0,0), GS (0,10) | 1 |

Note the input spacing is NOT a single linear rule: two inputs sit 40 apart,
four sit at -20,-10,+10,+20 — 10 apart but straddling the axis with no input
on it. Derive, never extrapolate. **This is enforced, not merely advised:**
`logisim_symbols.ports_of` raises `UnmeasuredGeometryError` for any
(component, input count) pair with no measured entry, naming the pair and
the counts that HAVE been measured, and saying a real file is required. Same
for an unknown component, and for a gate carrying a `size` or `facing` we
have never seen — every gate measured is default size facing east. No
interpolation, no nearest match.

There is an output AT `loc` for every component measured, but do not assume
it is the only one: Adder and Priority Encoder each carry a second output
beside it (`cout`, `gs`). "The last port is the output" is false, and was a
wrong convention briefly baked into the table before the Adder caught it.

Port names for gates and Pin are safe. Adder and Priority Encoder port names
and directions are INFERRED from how the students wired them — the carry
chain between adder stages, the pin labels at each end — not measured. Their
geometry is measured; their semantics are not derivable from geometry, and
priority order in particular is not. Do not rely on those names for
behaviour.

Strongest single piece of evidence: `exp8_gates.circ` is COMPLETELY explained
by this table. It has 33 distinct port coordinates and 33 dead-end wire
endpoints, and the two sets are identical — every port is wired exactly once
as an endpoint, and no wire ends anywhere that is not a port. A wrong offset
would leave either an unexplained dead end or an unwired port.

### The connection rule (settled 2026-08-24, with evidence)

```
CONNECT:      two wires sharing an endpoint
CONNECT:      an endpoint lying on another wire's span   (a T)
DO NOT:       two spans intersecting where NEITHER ends  (an X)
```

This is not a detail and not a preference. It decides what the circuit IS,
and the two readings are indistinguishable on screen.

**The evidence, verified independently against the fixtures.**
`exp8_gates.circ` contains 20 true crossings — a horizontal and a vertical
wire meeting at a point strictly interior to both. 19 of the 20 join wires
that are on genuinely different nets. So the question is not academic; it
decides the file.

Take the two models and look at the drivers:

| model | nets | nets with >1 driver |
|---|---|---|
| crossings do NOT connect | 13 | 0 |
| crossings DO connect | 5 | 2, with 5 drivers each |

Under "crossings connect" all four data inputs D0–D3 short together with the
NOT gate output, and the enable pin shorts to three gate outputs. That is
electrically impossible. `exp8_gates.circ` is a human's working priority
encoder, so **crossings do not connect.** Under the correct rule it gives 13
nets covering all 33 ports, nothing floating, exactly one driver per net —
the Logisim equivalent of the `.asc` round trip, on a file we did not create.

`adder_subtractor.circ` says the same with 4 crossings, all 4 merging: the
crossing model adds a fresh short of A3, B3 and an XOR output together.

The 20th crossing merges nothing, because those two wires are already
connected by another path. Worth knowing: a crossing that *looks* connected
sometimes is, for an unrelated reason. Appearance is not evidence either way.

Pinned in `test_logisim_geometry.py`, in both directions and at both scales:
the fixture counts, the driver collapse, plus minimal fixture-free cases for
a T that must connect and an X that must not.

**Consequence for the emitter — routing gets EASIER.** The router may cross
wires freely. No crossing avoidance, no vias, no channel routing, no
planarity concerns. Place gates in columns by logic depth, run orthogonal
3-segment paths, let them cross wherever. Ugly is fine, and correct.

**Consequence for the parser — one hazard to hold.** The whole distinction
turns on whether an endpoint exists at a coordinate, so splitting one wire
into two segments that meet at a crossing silently converts an X into a
junction and changes the circuit. Identical drawing, different netlist.
Therefore: **an emitter must never split a wire at a crossing point**, and
`test_splitting_a_wire_at_the_crossing_changes_the_circuit` pins exactly
that failure so it cannot be reintroduced quietly.

Read back as logic, that file is `OUT1 = E.(D3+D2)`, `OUT2 = E.(D3+D1.~D2)`,
`V = E.(D3+D2+D1+D0)`, which is a correct 4-to-2 priority encoder.

**Be precise about what that buys, because it is easy to overclaim and this
is the project's weakest guarantee.** `exp8_gates.circ` is an independent
IMPLEMENTATION. It is NOT an independent EVALUATOR. Running it through our
logic engine and comparing against our own generated Q2 answer cross-checks
two implementations THROUGH ONE EVALUATOR: it catches a bug in either
implementation, and catches nothing at all in the evaluator, because a broken
evaluator breaks both sides identically and they go on agreeing. Use it
exactly that way — as an implementation cross-check, labelled as one. The
only outside check on the evaluator remains a truth table computed by hand.

### Still UNVERIFIED — do not guess

- **Tunnels. Zero occurrences across all three files.** The load-bearing
  question — is a tunnel a net or a point-to-point link — is still open, so
  THE FALLBACK IS NOW THE PLAN: the Logisim emitter uses real wires and
  routes on the 10-unit grid. The LTspice no-routing strategy does not
  transfer. Accepted, and it blocks nothing; the netlist rebuild above shows
  routed geometry parses back cleanly.
- **Logisim Evolution's dialect.** Every fixture is 2.7.1, and nothing here
  may be assumed to carry over. This is no longer blocking, because 2.7.1 is
  now the build target; it is the thing to re-derive if Evolution ever
  becomes relevant again.
- Gate `size` attribute — every gate measured is default size; narrow/wide
  gates will have different input offsets.
- Gate `facing` — everything measured faces east.
- NAND/NOR gates, and XOR with more than 2 inputs.
- Splitter port geometry. One instance, and its ports are ambiguous against a
  neighbouring pin, so deliberately not measured.
- Whether the `<options>`/`<toolbar>`/`<mappings>` boilerplate is required.
  Until that is known, transcribe it verbatim from a real file rather than
  inventing a minimal one — same discipline as plt.py.

### primitives_only is now mechanically enforceable

`priority_plexers.circ` gives the exact signature to reject:

```xml
<comp lib="2" loc="(530,370)" name="Priority Encoder">
  <a name="select" val="2"/>
</comp>
```

Enforcement resolves `comp/@lib` through the `<lib>` block and rejects any
component from outside `PRIMITIVE_LIBS` (`#Wiring`, `#Gates`, `#Base`). It
must NOT match the string `lib="2"`, which is only this file's index — the
indices are per-file, and a file with a different library set numbers
`#Plexers` differently.

Enforced by fixture, not by intent: `tests/fixtures/logisim/shuffled_libs.circ`
is `priority_plexers.circ` with every `<lib>` index permuted, so `#Plexers`
sits at 1 and index 2 is `#Memory`. A check written against `lib="2"` passes
on the original and fails on the permuted copy; `logisim_symbols.resolve_lib_indices`
passes on both. A further test asserts the two fixtures differ ONLY in
library numbering, so the stressor cannot silently rot into a different
circuit. That fixture is DERIVED, not hand-drawn, is named for what it is,
and has never been opened in Logisim.

### A hand-drawn file is not necessarily a valid file

`adder_subtractor.circ` declares all fourteen of its pins as INPUT pins,
including S0-S3 and Cout which are driven by adder outputs. One XOR input at
(360,270) is left unwired, with the wire meant to feed it dead-ending 10 units
short. It also carries 26 dead-end stubs drawn INSIDE component bodies, where
they are invisible on canvas. The parser must survive this and report it, not
assume real input is correct.

### Logisim IS machine-verifiable here (revised 2026-08-24)

This section previously said no Logisim and no Java were installed, so
nothing Logisim could ever be machine-checked. Both halves are now obsolete:
Logisim Evolution 4.1.0 is installed (winget), it ships its own Java 21
runtime, and `--tty table` gives an external answer the way `-b` does for
LTspice. See "The evaluator asymmetry: RESOLVED".

What that does and does not cover:

- COVERED: does Evolution open our file, what does it evaluate the circuit
  to, and how many components does it think are there. All three are now
  machine checks in `tests/test_logisim_backend.py`.
- NOT COVERED: the 7447. Having Evolution installed does not supply the
  measured geometry of components we have never seen in a real file, so Q4
  stays blocked on a fixture.

### Q4 is BLOCKED ON FIXTURE. Do not design around it.

**Decided 2026-08-24: assume no Logisim Evolution file is coming.** Producing
one would mean doing lab work purely to generate a fixture, which is not
worth it. So:

- Q4 (BCD to seven-segment, 7447) is blocked-on-FIXTURE and parked. Note what
  is and is not missing: the verbatim question text IS in hand (screenshot,
  recorded under "Question taxonomy"), and it names Logisim Evolution
  explicitly. What is missing is format evidence — Evolution's TTL and I/O
  libraries are absent from 2.7.1, and nothing about the 7447 can be derived,
  guessed, or tested here.
- **Q2 proceeds on 2.7.1**, which the derived table fully supports:
  input/output Pin, NOT, AND, OR, XOR, wires, and `primitives_only`
  enforcement are all measured or enforced.
- Do NOT build an Evolution abstraction layer, a dialect switch, or a
  version-negotiating emitter in anticipation. Target 2.7.1 directly.

If an Evolution file does turn up later this is a small unblocking job, not a
redesign: re-derive the pin table against it with the dead-end method (the
method is the durable part, not the numbers), add the TTL/IO components, and
decide then whether to emit one dialect or two. Anything built now to
smooth that path is speculative work against an unknown format.

## Output format for the student

The circuit file alone is not the deliverable. Each answer should include:

1. The generated circuit file.
2. The numeric or tabular results, taken from simulation output.
3. A short explanation of *why*, referencing the simulated numbers.

Example of the difference that matters: in the reference regulator, `vb` moves
1.3 mV across the full load range while `vout` drops 138 mV. So essentially all
load regulation comes from Vbe rising with emitter current, not from the zener
sagging. That observation comes out of the sweep data, and it is the actual
learning content of the question. Explanations should surface that kind of
thing rather than restating the formula.

## Verification limits

The round-trip check cannot catch a wrong pin offset. emitter.py and
parser.py both import symbols.py, so a bad offset makes the emitter place a
flag at the wrong coordinate and the parser look for it at the same wrong
coordinate. It will pass. The round-trip proves self-consistency, not
correctness.

The only real ground truth is (a) the real-file measurements in
test_symbols.py and (b) LTspice successfully simulating the emitted file.
Therefore every symbol added to the pin table MUST have a corresponding
real-file measurement test. No symbol may be added from a datasheet, from
documentation, or from inference.

The same limit exists one level up: the asks-vs-measurements coverage
check compares model output against model output. An extractor that drops
"load regulation" from BOTH the asks and the measurements produces a
clean coverage screen. Both the geometric round trip and the coverage
check prove internal agreement only. The defences that sit OUTSIDE the
system are: the verbatim question text (rendered in full at the top of
the dry run, with word-level span coverage so unclaimed phrases are
visible rather than invisible, and a warning when an ask's words do not
appear in the text at all), and the real-file measurements. Deferred,
for the vision layer: run extraction twice independently and diff the
two asks arrays — disagreement flags ambiguity in the image.

### An unrun check must announce itself (project-wide rule)

**A check that can be skipped MUST report that it was skipped, in the output,
not merely decline to add a warning.** Silence is indistinguishable from a
pass. A reader looking at a clean screen cannot tell whether everything was
examined and found good, or whether nothing was examined at all — and the
second is the more dangerous of the two, because it feels like the first.

This generalises `LogisimTarget.round_trip` returning
`RoundTrip(ran=False, reason=...)`. It applies everywhere: the dry run, the
report, and the published manifest.

Implemented as `question.SkippedCheck(name, reason)`, collected in
`Question.skipped`, rendered by the dry run under `checks [target: ...]` as
`ran` / `SKIPPED` lines, and published in the manifest as `checks_skipped`.
`build_index` flags `has_skipped_checks` beside `has_internal_results` and
`has_unreliable_results`, because a quiet page is not necessarily a clean one.
The manifest refuses a skipped check with no reason: naming one without
saying why tells a reader something is missing without telling them what they
are not protected by.

**What the sweep found (2026-08-24).** Looking for other places this bites
turned up more than expected, including in code written the same day:

- `load_question` computed `trip = target.round_trip(circuit)` and **never
  used the result**. The honest `ran=False` report went nowhere.
- The dry run ended with a flat `structural validation: OK (emits, geometric
  parse round-trips, plan validates)` for EVERY question. For a Logisim
  question that was simply false — the round trip does not exist yet. The
  unrun-check failure, printed as a reassurance, on the one screen a human
  reads to decide whether to trust everything above it.
- `_coverage_warnings` and `_ask_text_warnings` returned `[]` when `asks` was
  absent. A question with no asks got a perfectly clean coverage screen while
  the entire dropped-ask defence sat unexecuted.
- Word coverage needs BOTH asks and verbatim text; reporting only the
  ask-coverage skip left word coverage silently marked as run. (Caught while
  fixing the above — the rule bites its own implementation.)
- The device policy simply does not run for a digital target. True and
  harmless, but it should be visible rather than assumed.

**Done right already, as the pattern to copy:** the plan warns "run X is
measured but has no regime assertions: convergence is not correctness", and
the CLI prints "UNVERIFIED" beside every `.plt`. Both announce an absence
rather than staying quiet about it.

### The MIRROR of the rule: a check that PASSED must say what it examined

Found 2026-08-25, and it had been true for as long as regimes have existed.
A regime assertion that held left no trace anywhere — it only ever surfaced
as warnings attached to whatever it invalidated. So a report with no regime
warnings was indistinguishable from a report where no regime was evaluated,
which is the same failure in a mirror.

`analysis.RegimeResult` therefore carries `examined`: what was actually looked
at, in words, with the real point count so "1 operating point" and "1115 sweep
points" are visibly different amounts of evidence. `execute()` returns an
`Experiment` (a Mapping of measurements, so every existing caller is
unchanged, plus `.regimes`), the report prints every check held or not, and
the manifest publishes `regime_checks` and refuses one with an empty
`examined`.

Generalise it: **any check whose only output is a warning is invisible when it
passes.** Look for others.

Incidental find during the sweep: `load_question` used `target` as a loop
variable for `answered_by` while a `Target` object of the same name was live.
Harmless until something later in the function touched it. Renamed.

### The evaluator asymmetry: RESOLVED 2026-08-24

**This section previously said the asymmetry was permanent and had to be
accepted. That was wrong, and the error was a factual one: we believed
Logisim had no usable batch simulator. It has one.**

```
logisim-evolution --tty table <file.circ>
```

loads a circuit, enumerates EVERY input combination itself, and prints the
truth table. No test-vector file, no circuit name, no GUI. So an outside tool
computes digital results, exactly as LTspice does for analog ones, and
`LogisimBackend` declares `verification = "external"`.

**The spike that established it** (2026-08-24, Logisim Evolution 4.1.0). It
ran `tests/fixtures/logisim/exp8_gates.circ` — a file drawn by a student,
which we did not create — and compared against the logic recovered from that
file's coordinates by our geometric parser:

    OUT 1 = E . (D3 + D2)
    OUT 2 = E . (D3 + D1 . ~D2)
    V     = E . (D3 + D2 + D1 + D0)

All 32 rows x 3 outputs agreed, on the committed fixture and on a
label-renamed copy. `--tty stats` independently counted 8 Pin, 1 NOT, 4 AND,
3 OR, matching our parse exactly. Pinned in `tests/test_logisim_backend.py`.

Be precise about what that buys. It is not "our answer looks right". Nothing
in the comparison shares an implementation with anything else in it: our
parser reads geometry, Logisim evaluates a circuit, and the two are checked
against each other. Breaking it silently would require our parser to recover
the WRONG netlist and Logisim to independently compute matching wrong values.
Compare with the old situation, where one broken evaluator produced both
sides and they agreed by construction.

**What is still true, and still matters:**

1. The asymmetry returns the moment Logisim is absent. `InternalLogicBackend`
   is the offline fallback and declares `verification = "internal"`, meaning
   it computes the result AND anything the result would be checked against.
   `best_available_backend()` prefers Logisim, and **every report must say
   which one ran** — a report that hides the difference is the exact failure
   this distinction exists to prevent.
2. A hand-computed truth table is no longer the ONLY outside reference, but
   it remains the reference of last resort for the internal engine, which by
   definition cannot audit itself.
3. `exp8_gates.circ` remains an independent IMPLEMENTATION, and that is a
   separate and lesser thing from an independent EVALUATOR. Comparing our
   generated Q2 answer against it through one evaluator catches bugs on
   either side and none in the evaluator. Now that Logisim IS the evaluator,
   that comparison finally has teeth — but label it for what it is.
4. Structural checks the evaluator cannot fake are still worth having: no
   Plexers component when `primitives_only` is declared, and Logisim's own
   reaction to a malformed file.

### Logisim Evolution: empirical facts (4.1.0, measured not documented)

Installed 2026-08-24 via `winget install --id
logisim-evolution.logisim-evolution` to
`C:\Program Files\logisim-evolution\logisim-evolution.exe`.
`OHMWORK_LOGISIM` overrides the path, same pattern as `OHMWORK_LTSPICE`.

- The jpackage launcher DOES take CLI arguments and DOES write to stdout —
  unlike `LTspice.exe`, which is GUI-subsystem and prints nothing.
- Its bundled runtime is a jlink image with **no `java.exe`**, so `java -jar`
  is not an option. Drive the `.exe`.
- `--tty table` is the verification path. `--test-vector <circuit> <vector>
  <file>` HUNG for 90 s with no output and was abandoned; do not reach for it
  again without new evidence.
- `--tty stats` prints a component census — a cheap independent cross-check
  of our own parse.
- `--new-file-format <in> <out>` also hung (GUI init). Unresolved, and not
  needed.
- **The exit code came back empty on a successful run. Do not trust it.** A
  run counts only if a table parses with the expected columns and 2**n rows —
  the same doctrine as "a raw file existing proves nothing, traces in it do".
- **Labels are rewritten to VHDL-safe names.** A pin labelled `E IN` returns
  as `E_IN_ef467da7`: spaces to underscores plus a hash suffix we cannot
  reproduce. Consequence for the emitter: **emit labels matching
  `[A-Za-z][A-Za-z0-9_]*`** so no rewriting happens. Column matching falls
  back to unambiguous prefix matching only so hand-drawn fixtures still work.

### CONFIRMED: emit 2.7.1, verify with Evolution

Evolution opens original-Logisim 2.7.1 files, warns on stderr ("Old file
format -- compatibility mode"), and evaluates them correctly — the 32-row
agreement above was obtained on a 2.7.1 file. The warning is expected and is
surfaced as a note, never treated as failure.

**So the target decision stands unchanged: emit 2.7.1, verify with
Evolution.** The derived pin table stays valid, Q2 proceeds on 2.7.1, and no
dialect abstraction is needed. Recorded explicitly so it does not drift.

Q4 is still blocked on FIXTURE: the 7447 and seven-segment live in
Evolution's TTL and I/O libraries, and having Evolution installed does not
give us their measured geometry. What it does give is a way to derive it if a
file containing them ever arrives.

## Simulate layer decisions (settled)

- Option 2 chosen for the divergence problem: headless LTspice is the
  default backend and the only one whose numbers are shown to the user.
- Do not parse binary .raw files: run LTspice with `-ascii` and use the
  `spicelib` package (RawRead / LTSpiceLogReader). The novel code in this
  project is the emitter and the geometric parser; raw-file plumbing is
  solved elsewhere.
- Executable name varies by version: scad3.exe (IV), XVIIx64.exe (XVII),
  LTspice.exe (24.x+). `locate_ltspice()` searches known install paths and
  fails with a clear message naming what it looked for. On this machine it
  is F:\LTspice.exe (26.0.2.1). Note the exe is a GUI-subsystem app:
  `-version` prints nothing to a console, so confirm identity via file
  version metadata, not stdout.
- Spike result (2026-08-24, LTspice 26.0.2.1): headless `-b -ascii` on an
  .op-only .asc DOES produce a .raw. The reported "no .raw for .op" bug does
  not affect this version; no .dc fallback needed. spicelib's RawRead parses
  the ascii output directly. Also verified: LTspice's own netlister read the
  emitter's flag-and-stub geometry into a netlist matching the reference
  case line for line, including the shunt zener orientation.
- Spike also confirmed the divergence is real even with identical .model
  cards: for the reference regulator, LTspice gives vout=7.939 V, vb=8.749 V,
  I(D1)=3.43 mA where ngspice gave 7.532/8.340/3.66m. Never compare
  LTspice numbers against ngspice expectations; each backend gets its own
  regression values.
- Root cause of BOTH sets of wrong-looking numbers (2026-08-24): in SPICE,
  BV is the voltage at which reverse current equals IBV, and IBV defaults
  to 1 mA, while a datasheet Vz is quoted at a test current (usually 5 mA).
  So `D(BV=8.3)` does not describe an 8.3 V zener. A synthesised card is an
  invented device; the two simulators disagreeing about it was a symptom,
  not the problem. The textbook answer (Vz - Vbe = ~7.5-7.6 V) came from
  neither backend with the hand-rolled card.

## Why anchored models matter

The "simulator divergence problem" this project spent two rounds on was
never a simulator problem. With the unanchored card D(BV=8.3 N=1.2), LTspice
and ngspice disagreed by ~0.4 V (vb 8.749 vs 8.340) — because BV is defined
at IBV (default 1 mA) and each simulator fills in the unspecified knee its
own way. With the anchored card D(BV=8.3 IBV=5m):

    LTspice 26.0.2.1:  vb = 8.292139
    ngspice:           vb = 8.292262

123 uV apart, on two independently implemented simulators. Provenance of
the LTspice number was explicitly verified (2026-08-24): command
`F:\LTspice.exe -b -ascii <file>`, raw header `Command: Linear Technology
Corporation LTspice`, log `LTspice 26.0.2 for Windows`.

Moral: when two simulators disagree, suspect an under-specified device
before suspecting the simulators. A fully specified model is portable;
defaults are not.

### The regression that proved the point (2026-08-24, same day)

Hours after the policy was implemented and tested, the first full Q1
experiment — deliverable .asc, report, and four pinned baselines — was
built on the outlawed unanchored card, because an old test fixture was
reused as the experiment's input. The file emitted cleanly, round-tripped,
converged, passed every structural check, and reported vout=7.939 V with
confidence. The only reason it was caught is that a human recognised the
number from three rounds earlier. A policy that is implemented, tested,
and not applied to the actual inputs is worth nothing, and the symptom is
invisible by construction: correct file, clean convergence, plausible
number.

Machinery now catches it instead of memory, in three layers:
1. emit() hard-fails on any diode .model card with BV but no IBV
   (parts.unanchored_diode_card), so no .asc — deliverable, scratch run,
   or example — can carry one. tests/test_examples.py additionally sweeps
   every example JSON in the repo.
2. Reports name each device's DeviceChoice and policy path (a/b/c) above
   the numbers, so the device behind every result is visible in output.
3. The Q1 plan measures vb_nominal and pins it to ngspice's 8.292262
   within a few mV: an un-anchored card (vb 8.749) fails loudly instead
   of passing plausibly.

## Device models: real parts, not synthesised cards

- Semiconductors are specified by `part`; scalar components keep `value`.
  The JSON schema enforces exactly one of value/part per component type
  (VALUE_TYPES / PART_TYPES in symbols.py). A `part` is either a device
  from LTspice's bundled libraries or the name of a synthesised .model
  card carried in `directives` — which one is a policy decision:
- Device policy (parts.py choose_zener, three paths, priority order).
  The original sin was an UNANCHORED BV, not synthesis itself: with IBV
  anchored at the test current, D(BV=8.3 IBV=5m) genuinely is an 8.3 V
  zener and ngspice/LTspice agree on vb to the millivolt (8.292).
    a. Question names a part ("use a 1N4148") -> that real library part,
       no substitution.
    b. Question specifies a parameter value with no part ("Vz = 8.3 V",
       the common lab-manual case) -> synthesise a model anchored at
       exactly that value. A nearby real part (8.2 V) would silently
       answer a slightly different question than the one asked.
    c. Question is vague ("a zener") -> nearest real part, substitution
       reported ("using BZX84C8V2L, Vz=8.2 V (question asked for 8.3 V)").
  In all cases the choice and the reason are reported in the output.
  Never silently pick.
- Empirical library facts (LTspice 26.0.2.1, derived from the real files):
  libraries live in %LOCALAPPDATA%/LTspice/lib/cmp/; standard.dio is plain
  ASCII but standard.bjt is UTF-16LE with NO BOM; entries continue on lines
  starting '+'; zeners are marked `type=Zener` and carry `Vpk=<nominal Vz>`
  (the matching key — BV is NOT nominal Vz, and 1N4148 carries Vpk=75 with
  type=silicon, so filter on type, never on Vpk presence). Inventory: 264
  zeners, 165 NPN, 131 PNP; 2N3904/2N3906/2N2222/2N2907 all present, and
  the emitted netlist resolves parts because LTspice auto-includes
  .lib standard.dio / standard.bjt.
- Path (b) baseline, the reference question's actual answer (LTspice
  26.0.2.1, DZ8V3 = D(BV=8.3 IBV=5m) + QN = NPN(BF=100), both values from
  the question): vout=7.484 V, vb=8.2921 V, I(D1)=3.69 mA. vb matches
  ngspice's 8.292 for the same card — anchored models are
  simulator-independent in a way unanchored ones are not.
- Path (c) baseline, real-parts variant (LTspice 26.0.2.1, BZX84B8V2LY +
  2N3904): vout=7.5059 V, vb=8.1943 V, I(D1)=3.77 mA. Both baselines sit
  in the textbook Vz - Vbe band, which is the acceptance criterion that
  matters for a study aid.
- Consequence for ngspice: it cannot read LTspice's libraries, so the two
  backends model different devices and their numbers will not match BY
  DESIGN. ngspice exists to prove the pipeline runs on Linux and in CI,
  using synthesised cards; it is never the source of numbers shown to the
  user, and its regression values are its own baseline. Do not try to
  reconcile them.
- locate_ltspice() checks the OHMWORK_LTSPICE env var before any path
  search (this machine's install is F:\LTspice.exe, which no search would
  guess; the var is set user-level here). OHMWORK_LTSPICE_LIB similarly
  overrides the component-library directory, and OHMWORK_NGSPICE the
  ngspice executable.
- Verified empirically: on a hard failure (e.g. a voltage-source loop)
  LTspice STILL writes a .raw file, just with zero traces. A raw file
  existing proves nothing; traces in it do. The backend treats an empty
  raw as SimulationError and surfaces the log tail.
- Architecture: a `Backend` protocol with `run(asc_path) -> Results`.
  LTspiceBackend is the default. NgspiceBackend exists only so the pipeline
  stays testable on Linux/CI; tests asserting specific voltages run against
  ngspice with explicit .model cards, tests asserting the deliverable is
  correct require LTspice and skip cleanly when absent.

## Analysis layer (ohmwork/analysis.py)

The schema and every contract live in tests/test_analysis.py's module
docstring; that file is the spec. Facts and decisions recorded here so they
survive a rewrite:

- Deliverable vs scratch: the student receives ONE .asc containing the
  whole experiment — first run's directive active, every other run present
  as a comment line to uncomment, swept components on a {param} with an
  active .param default. The runner's per-run files (<run id>.asc in a
  scratch dir) are an implementation detail, never the deliverable.
- Verified empirically (26.0.2.1): .step OVERRIDES an active .param, so
  uncommenting the .step line just works. But TWO active analysis
  directives (.op + .dc) HANG batch mode until killed (and can orphan the
  process) — so the deliverable's comments say "comment out .op first",
  generated files never carry two active analyses, and LTspiceBackend
  converts a timeout into a SimulationError naming this cause.
- Verified empirically: LTspice runs .step LIST values in ASCENDING
  NUMERIC order no matter how the list is written, and records the axis as
  a trace ("rlstep" for .step param RLstep; the source name, "V1", for
  .dc). Plan-order indexing silently flips sweeps. `at` selection therefore
  parses the requested value (SPICE suffixes: k/Meg/m/u/...) and locates it
  in the recorded axis trace — the file's own account of what ran.
- Regime assertions (zener_in_breakdown, bjt_active) guard against
  convergence-without-correctness: a load sweep into dropout converges
  fine and reports a confident, meaningless regulation figure. A violated
  regime marks every measurement touching that run (transitively through
  derived formulas) UNRELIABLE with the reason attached, rather than
  failing the run: dropout data is pedagogically interesting and should be
  shown flagged, not hidden, and other runs' results stay valid.
- Derived measurements render with their formula, the numbers substituted
  in, and a "definition" line (load regulation alone has several textbook
  definitions; a bare percentage is unreconcilable with a manual).
- Q1 experiment baselines (2026-08-24, LTspice 26.0.2.1, ANCHORED
  path-(b) reference circuit DZ8V3=D(BV=8.3 IBV=5m) + QN=NPN(BF=100)):
  vout_nominal=7.4840, vb_nominal=8.29214, iz=3.690 mA,
  line_reg (12->20 V, /V12) = 0.3992%, load_reg (100k->500, /full) =
  1.8476%. Pinned in test_full_q1_experiment_against_ltspice. An earlier
  set (vout 7.9392, line 0.4069%, load 1.7434%) was measured on the
  outlawed unanchored card and is VOID — see the regression note under
  "Why anchored models matter". Note load_reg 1.85% vs the original
  ngspice 1.84%: anchored devices agree across simulators on the derived
  quantities too.

## The input gate (ohmwork/question.py, build step 5)

The question JSON is the boundary where a human stops writing input and a
model starts; everything after it is machine-generated intent. So the
loader is a gate, not a file reader:

- Strict schema: unknown keys anywhere are rejected with a path-shaped
  error ("circuit.components[1]: unknown key(s) ['resistance']"). An LLM
  drifting from the schema must fail loudly, never silently default.
- Semiconductors arrive as specs, not resolved parts: "part" means the
  question named the device (policy path a); "device": {"vz": 8.3,
  "exact": true} resolves through the policy (b/c); BJTs take
  "device": {"params": {"BF": 100}}. The loader records every
  DeviceChoice for the report and appends generated (anchored) cards.
- load_question runs the entire non-simulation chain: schema, device
  policy, emit + geometric-parse round trip, plan validation.
- Semantic heuristics WARN, never fail (they exist for the human
  confirmation step): values outside plausibility windows, resistor
  spreads over 500x in one circuit (the k-vs-Meg misread signature),
  and any measured run lacking regime assertions.
- Question.to_dict() rebuilds the input from parsed state; the
  round-trip test catches fields the loader parsed but forgot, i.e.
  schema drift once the LLM layer starts producing these files.
- Coverage ("show what is NOT there"): the dry run can only display
  what was extracted, and the dominant vision failure is a drop — a
  missed beta, an omitted component, an ask that never became a
  measurement — which leaves every extraction-driven screen looking
  perfect. Defence: an `asks` array of verbatim question phrases, each
  mapped via `answered_by` to the measurement that answers it. Coverage
  is checked both ways: an unmapped ask warns loudly (dropped work); a
  measurement covering no ask warns too (invented work). Intermediates
  feeding a covered derived measurement are covered transitively;
  measurements carrying a `guard` reason (e.g. the vb cross-simulator
  tripwire) are declared deliberate and exempt. A completeness block
  prints component/net/ask counts, every numeric parameter the
  extractor claims to have read ("Vz=8.3 (D1), BF=100 (Q1)"), and —
  when a `source` block exists — annotations seen but unused, which is
  the line that catches a read-but-dropped value.
- Optional `source` block (the vision layer's target; schema exists,
  data lands with that layer): file, resolution, question_chars,
  extractor, attempts, per-ref confidence, annotations_unused. Low
  confidence entries render on the extraction line.
- CLI: `python -m ohmwork question.json --dry-run` prints one aligned
  line per component (values are what a photo most likely corrupts, so
  they are the most prominent thing on screen) with a short device
  policy tag ("[synth, anchored 5m]", "[nearest: BZX84C8V2L Vz=8.2]"),
  nets, runs, measurements with formulas, regime assertions, the
  coverage section, and warnings, then exits without simulating. The
  full device rationale and model cards live behind --explain: the
  rationale matters once, the values matter every run. Without
  --dry-run it continues into simulation and writes the deliverable.

## Design questions and value origins

Q1 is an ANALYSIS question (circuit given, measure it). Q2-Q4 are DESIGN
questions ("design a..."): topology and several values are the tool's
engineering judgement, and a designed value indistinguishable from a
stated one would submit that judgement as the student's own. So every
component value carries an origin — `stated` (question text/image),
`designed` (tool's choice; a `rationale` is REQUIRED, enforced), or
`default` (library/policy fallback, inferred for empty device specs).
Non-component choices (topology) go in a top-level `design_notes` list.

Rationales additionally carry `rationale_origin`: `human` or `generated`.
A rationale's trustworthiness does NOT come from a human having written it
— once the LLM layer lands most rationales are model-written, and this
repo's own examples/q3.json RS=220 rationale already was — it comes from a
human REVIEWING it at the input gate. So: rendered honestly as
`[human-written]` vs `[generated, reviewed at input gate]`; absent
authorship is NEVER assumed human (`[authorship not recorded — review]`),
because that assumption is exactly the unfounded trust being removed; and
since the gate is now the only thing making a generated rationale
trustworthy, the dry run prints a count near the top ("!! 2 rationales
require your review") so the designed-values section is actually read.
Deterministic DeviceChoice reports are exempt — they are library-derived
policy output, not model prose, and already carry their policy tag.

Backends declare `verification`: `external` (an outside simulator computed
it, so our bugs show up as disagreement) or `internal` (we computed it and
also compute anything it would be checked against). Measurements inherit
it, and any report containing internal results leads with why that is
weaker. Implemented and wired now, so the logic engine only has to declare
itself when it lands.
The dry run gives designed/default values their own prominent section
("these are choices, not given ... change them before use") and marks
them in the component table. Same species as the device policy: never
silently pick, always report which path was taken.

## Q3 extensions (designed against two questions, all landed 2026-08-24)

The four gaps found by hand-writing Q3 against the old schema, and their
resolutions (examples/q3.json is the acceptance case, green end-to-end):

1. `tran` run type: {stop, settle, max_step}. .tran Tstart = settle, so
   LTspice discards startup and the saved data IS the post-settle window.
   A regime assertion on a tran run without `settle` is a validation
   error — the zener is out of breakdown during startup BY DESIGN, and
   asserting over startup would flag physics working correctly.
2. `waveform_stats` measurement kind: time-weighted (trapezoidal) mean /
   rms / min / max / ripple_pp — variable-step simulators cluster points,
   so naive point averages are biased. Scalar measurements on tran runs
   are rejected. expr may be a raw trace or a difference "V(a)-V(b)"
   (needed for the input across a floating bridge source). Validation of
   the whole chain: measured V(ac1)-V(ac2) rms = 12.000141 V against the
   question's stated 12 V RMS.
3. Plain diodes: choose_diode, path (a) named or path (c) default
   (1N4007 preferred, 1N4148 fallback, zeners excluded), reported and
   listed under designed values with origin `default`.
4. Structured AC source: {"kind": "sine", "rms"|"amplitude", "freq",
   "offset"} — the RMS->peak conversion happens in code and the dry run
   shows both ("SINE 12 Vrms 50 Hz -> 16.97 V peak"). An opaque
   SINE() string hid exactly the conversion a misread corrupts (41%).

"Observe the waveform" asks: the deliverable ships a .plt beside the
.asc so the right traces are already plotted when the student runs it —
they do the observing, which is the pedagogically correct outcome.
Format derived from REAL files shipped with LTspice 26.0.2.1
(examples/Educational/160.plt: a 5-pane transient, exactly the Q3
shape; pane colour ids copied verbatim, never invented). LIMIT: batch
mode does not read .plt, so generated files cannot be verified headless
— status is "derived from real files, PENDING one visual confirmation
in the GUI". Y ranges come from the measured stats so panes open framed
on the signal, not startup junk.

FROZEN: the .plt writer is the first artefact in this project with NO
machine verification path — no round trip, no simulation, no test can
check what LTspice renders. Its only check is a human looking at it
once. Therefore it stays strictly a transcription of the vendor file's
shape (panes, traces, ranges) and must not grow: the moment it encodes
plot styling opinions it becomes unverifiable surface area that
silently rots. Do not add features to plt.py.

General principle (Logisim will raise it again): an artefact with no
machine verification path must stay minimal, and its unverifiability
must be stated in the output whenever it ships — the CLI prints
"unverified" alongside every .plt it writes.

Other Q3 facts, verified empirically: the full bridge + 470u + 1m with
anchored devices converges in 0.7 s under .tran 0 200m 100m 100u (the
feared non-convergence did not materialise); Q3 LTspice baselines are in
tests/baselines.py (vout 6.249993 V mean / 3.99 mV ripple, vfilt
15.2289 V, vin rms 12.000141). Layout remains the known ugliness: 11
components in a single 2496-wide row simulates fine but a bridge drawn
as a row is topologically unreadable. Deliberately NOT building a placer
until Q2/Q4 (Logisim) show what a layout engine must generalise over.

## Targets (ohmwork/targets.py, landed 2026-08-24)

Gap 1 said the gate had no target concept. The fix was NOT a flag: probing
proved the checks were LTspice-SEMANTIC, not merely LTspice-flavoured. A
Logisim circuit was rejected for having no SPICE ground net, and told its
components were "not in the verified pin table" — LTspice's table.

A `Target` owns its component vocabulary, its structural rules, its emitter
and geometric parser (hence its own round trip), its device policy or lack of
one, its backend, and its error vocabulary. `load_question` selects the target
FIRST and then runs only that chain.

| | LTspiceTarget | LogisimTarget |
|---|---|---|
| requires_ground | yes | no |
| uses_device_policy | yes | no |
| extra component keys | none | `label` |
| round trip | emit + parse + netlist compare | **does not run — deferred to v1.1, and says so, naming what replaces it** |
| backend | LTspiceBackend (external) | Logisim (external) / internal fallback |

Two details worth keeping:

- **An unrun check must never look like a passed one.** `LogisimTarget.round_trip`
  returns `RoundTrip(ran=False, reason=...)` naming what was checked
  (structure, types, pins, labels) and what was not (geometry). Silence there
  would be indistinguishable from success, which is the worst failure
  available in this project. **Sharpened 2026-08-25:** the reason must also
  name what STANDS IN ITS PLACE. Logisim evaluating the emitted file is a
  stronger check than a round trip through our own parser, so a reader seeing
  only "did not run" would conclude the opposite of the truth — an honest
  statement that misleads is still misleading.
- **Plan validation now runs AFTER the circuit checks.** An analysis is an
  analysis OF a circuit, so a plan error is only meaningful once the
  components and pins are known good — and an unknown-run-type error was
  masking the circuit errors underneath it.

`target` defaults to `ltspice`, because every question written before targets
existed is an LTspice one.

### The label rule is enforced, not advised

Logisim rewrites pin labels to VHDL-safe names and appends a hash we cannot
reproduce: `E IN` comes back as `E_IN_ef467da7`. Reading a foreign file we
prefix-match around that. **Emitting one, we must never produce it** — a label
we emit that triggers the rewrite becomes unmatchable in our own results. So
`logisim_symbols.SAFE_LABEL` (`[A-Za-z][A-Za-z0-9_]*`) is a validation error
at the gate, not a convention.

### Two gate fixes that landed with it

- **A prose ask is no longer reported as dropped work.** It renders as prose
  and is counted separately. The false alarm was the worst kind of bug on
  that screen: a reader who learns to skip the one line designed to catch
  real drops stops seeing the true positives too. A prose ask may not carry
  `answered_by` — that is what makes it prose.
- **`origin` is rejected on a component with neither value nor part.** For a
  gate every property is our choice by definition, so the axis has no
  referent and only inflates the "rationales require your review" count where
  it should be signal. Logic and topology choices go in `design_notes`, which
  already carries authorship.

## Q2 gap list (re-probed 2026-08-24 against the VERBATIM question)

`examples/q2.json` (then in `drafts/`) is a hand-written gate-level 4-to-2 priority
encoder carrying the real Exp 8 wording, written in the shape the schema
SHOULD accept rather than the shape it does. Probing it means feeding that to
the gate we have, recording the rejection, removing the feature, and repeating
until nothing is left. The ordered rejections are the gap list.

Probes live in the scratchpad: `probe_q2.py` (strip to exhaustion),
`probe_q2_deep.py` and `probe_q2_accept.py` (inject FAKE geometry for the
digital types to reach the layers underneath). **That fake geometry is a probe
artefact and must never be committed** — AGENTS.md forbids any pin offset not
measured from a real file, and the whole point is that the real table now
exists in `ohmwork/logisim_symbols.py`.

Result: the gate only ACCEPTS Q2 after degrading it into something that
answers none of the question — no truth table, no prose, no digital regimes,
one meaningless scalar. Gaps, most structural first.

**1. NO TARGET CONCEPT — CLOSED 2026-08-24.** See "Targets" above.
`target` and `constraints` are accepted, and the LTspice-semantic checks
(ground, device policy, `.asc` round trip, pin-table vocabulary) now belong
to `LTspiceTarget` rather than to the gate.

**2. THE EVALUATOR INVERSION — RESOLVED, no longer a gap.** The premise was
that Logisim had no usable batch simulator. It has one: `--tty table`. Digital
results now get external verification with the same standing as LTspice
results, and `LogisimBackend` declares it. See "The evaluator asymmetry:
RESOLVED". What remains is a reporting duty rather than a gap: the offline
fallback is still `internal`, so the plan must record which backend produced a
table.

**3. The SPICE ground check** — NOT in the previous gap list, found by probing
past the component-type error. `emitter._validate` requires a net named `0`
and rejects a circuit without one. Logisim circuits have no ground net. This
fires even once component types exist, and it is a concrete instance of gap 1:
the checks are not merely LTspice-flavoured, they are LTspice-semantic.

**4. Digital component types — CLOSED.** `LogisimTarget.TYPE_MAP` maps
`and2`/`or4`/... onto Logisim names plus attributes, routed through
`logisim_symbols.ports_of` so an unmeasured input count hard-fails.
Originally recorded as: `input_pin`, `output_pin`,
`not`, `and2`, `or2`, `or4` are rejected with "not in the verified pin table".
That was correct when written, because no `.circ` geometry had been derived.
It now has been. What is missing is the wiring, in two parts:
  - **Type naming.** The question JSON uses logical names (`and2`, `or4`);
    Logisim uses `AND Gate` with `inputs=2`. A mapping layer is needed, and it
    MUST route through `logisim_symbols.ports_of` so an unmeasured input count
    hard-fails instead of being interpolated.
  - **Port naming.** The draft writes `I3.pin`, `G5.in3`, `G1.out`. Those match
    the measured table exactly, so this half already lines up.

**5. CLOSED with gap 1: error vocabulary.** Each target now describes an
unknown type in its own words and lists its own known types.
Originally recorded as: Unknown component types do NOT
"pass the gate and die deep in the emitter" — `load_question` catches
`CircuitError` and re-raises as `QuestionError`, so the error type is already
unified and nothing escapes. What is actually wrong is the message and the
layering: a Logisim question is told its component is "not in the verified pin
table", meaning LTspice's table, and the only reason the type got checked at
all is that `emit()` happened to run. The gate should validate types against
the TARGET's known set, up front, with the target's vocabulary.

**6. CLOSED: `truth_table` run type.** Landed with the run type, and executed
end to end 2026-08-25 (`analysis._execute_digital` writes a `.circ` per run
and hands it to the backend). Originally recorded as: `runs[].inputs` is rejected. Accepted run
types are `dc`, `op`, `param_sweep`, `tran`. Needs `{"type": "truth_table",
"inputs": [ordered refs]}` — for Q2 that is 5 inputs (4 data + enable), so 32
rows.

**7. CLOSED: `table` measurement kind.** A `Measurement` now carries
`table = {columns, rows, notes}` and `value = None` — a table has no single
number and nothing downstream may substitute a zero for one; the manifest
refuses a result with neither. The pinned copy (`baselines.Q2_TRUTH_TABLE`)
was computed by Logisim, an outside tool, and is additionally checked against
a SPEC ORACLE written from the question's own wording
(`test_digital_execution.spec_oracle`) — four lines, no gates, no netlist.
That oracle is the only check in the set that could catch our gate network
implementing the wrong FUNCTION, since Logisim would evaluate a wrong encoder
just as happily as a right one. Originally recorded as: Accepted kinds are `derived`, `regime`,
`simulated`, `waveform_stats`. Needs `{"kind": "table", "outputs": [refs]}`.
Tables are pinnable baselines — but see gap 2 for who is allowed to compute
the pinned copy, which is a human, by hand.

**8. CLOSED: prose asks.** `ohmwork/prose.py` landed 2026-08-25 with all
three tiers; both of Q2's prose asks are answered and published. What remains
is not a gap but the v1.1 LLM layer: `answers=` is passed `{}` today, so a
prose_from_results ask prints its evidence rows and then says "no answer
generated" unless the question carries a hand-written one. Q2's does, marked
`answer_origin: generated` — written by a model, made trustworthy (or not) by
a human reading it at the dry-run gate, and labelled accordingly. Originally recorded as: The false alarm is fixed (see "Two gate
fixes" above): a `kind: "prose"` ask no longer reports as dropped work. What
remains is generating the prose itself, per the three tiers below.
Originally recorded as: Both
prose asks currently produce: *"has no measurement answering it — the
extractor may have dropped it"*. Nothing was dropped. They are prose by
nature, and a permanent false alarm on a screen designed to surface real drops
is worse than no alarm, because it trains the reader to skip it. Needs ask
kind `prose` plus the three tiers in "Prose asks". Q2 uses all the interesting
ones: "Explain your design choices" is `prose_from_design` (zero generation —
listing the four design notes and their rationales IS the answer), and
"Discuss how your circuit behaves..." is `prose_from_results` with TWO
evidence groups (multiple-inputs-active rows, and enable-disabled rows).

**9. CLOSED: digital regime family.** Declared, validated, and now EVALUATED
(`analysis.check_regimes`, no simulator required — they are properties of the
circuit description). Originally recorded as: The gate correctly warns "run 'exhaustive' is
measured but has no regime assertions: convergence is not correctness", but
there is nothing to satisfy it with: `zener_in_breakdown` and `bjt_active` are
meaningless here. The equivalents are `no_floating_inputs`,
`all_outputs_driven`, `no_combinational_loops`.

**10. CLOSED: `primitives_only`.** `LogisimTarget.check_constraints` runs at
the gate and rejects any component whose Logisim library is outside
`PRIMITIVE_LIBS`, resolved by library NAME through `logisim_symbols.LIB_OF`
(which lists the non-primitives too, deliberately: a check with nothing
rejectable in its vocabulary can never fail and is worth nothing). Today it
cannot fire, because `TYPE_MAP` holds only Pin and gates — which is exactly
why it is written down rather than assumed: the day a Plexers type is added,
this is what stops it slipping past a question that declared the constraint.
Originally recorded as: `constraints.primitives_only` is now
accepted at the gate. What remains is applying it during emission.
Originally recorded as: The ENFORCEMENT machinery now exists —
`logisim_symbols.PRIMITIVE_LIBS`, `resolve_lib_indices`, and the
`shuffled_libs.circ` fixture proving it does not depend on one file's
numbering — it just cannot be requested from the question. Note the question
does NOT ask for primitives only; that is our constraint, and the draft
records it in `design_notes` with a rationale marked `generated`, never as if
the question required it.

**11. Gates take neither value nor part** (unchanged). The value/part/device
schema axis does not apply and the device policy is a no-op for them.

**12. CLOSED: `origin`/`rationale` on a gate.** Rejected at the gate now.
Originally recorded as: The
draft put `origin: designed` on one input pin and the gate took it, pushing
the dry run to "!! 5 rationales require your review". For gates there is no
value to have an origin — the whole circuit is designed by definition. Letting
components carry origins here inflates the review count and dilutes the signal
in exactly the cases where it matters. Digital components should reject the
origin axis, and design intent should live in `design_notes`.

Also observed, minor: ask coverage sits at 79% of the question text, with
`"'valid output'"` among the unclaimed spans — no ask quotes that phrase even
though the question requires the signal.

## Prose asks (LANDED 2026-08-25 — ohmwork/prose.py, spec in tests/test_prose.py)

Q2's "Explain your design choices" and "Discuss how your circuit behaves
when..." can never map to a measurement. Adding kind "prose" and printing
model text is the wrong fix: prose is the one place the output is entirely
unverifiable, so it gets the strongest available framing instead.

Central idea: **grounding does not make prose verified, it makes it locally
falsifiable.** "Discuss how your circuit behaves when multiple inputs are
active" is answerable FROM the truth table — select the rows where 2+ inputs
are high, print them, and the prose becomes a caption over a computed
selection the reader can check without leaving the page. So the design
maximises how often that applies. Three tiers, descending trust:

- `prose_from_design` — quotes design_notes rationales. ZERO generation:
  listing the choices and their rationales IS the explanation. Two of Q2's
  four asks become non-generated this way. What makes it trustworthy is
  NOT human authorship (see rationale_origin below) but human REVIEW at
  the gate, so each rationale renders with its authorship label.
- `prose_from_results` — computed evidence rows plus a generated caption
  sitting directly beneath them. Rows are pinnable baselines; only the
  connecting sentences are generated. GROUNDING IS A CHAIN AND ITS WEAKEST
  LINK MUST BE VISIBLE: "locally falsifiable" means the reader can check
  the caption against the rows, not that the rows are trustworthy. Q2's
  evidence comes from the truth table, i.e. from our own evaluator with no
  outside checker, so every evidence group renders the verification status
  it inherits from the backend that produced the measurement. Prose
  grounded in Logisim evidence is weaker than prose grounded in LTspice
  evidence and must never look equally solid.
- `prose_free` — nothing supports it. Allowed, labeled hardest, and the dry
  run COUNTS these so the human sees how much unverifiable text is coming.

Row selection uses a closed filter vocabulary (equals, min_high,
value_range reading columns as an MSB-first number) — enough for every ask
in all four sample questions. Deliberately not an expression language: the
arithmetic evaluator's whitelist is a feature, and a second looser evaluator
would undo it. Empty selections and missing answers are stated in the
output, never papered over.

Architecture consequence: prose text CANNOT live in the question JSON,
because extraction happens before simulation and cannot cite results that do
not exist yet. The JSON declares only the grounding contract (which
measurement, which rows, which notes); prose is generated afterwards from
the evidence, which also constrains the generator to the rows it is shown.
An optional hand-written `answer` renders by its RECORDED AUTHORSHIP so
questions stay completable by hand — and note the correction the
implementation forced: `answer_origin` mirrors `rationale_origin`, and an
answer with no origin renders `[authorship not recorded — review]`, never
`[human-written]`. "Hand-written" and "written by a person" are not the same
claim once a model is doing the writing, and the design spec had conflated
them. Rendering: separate section, after all computed results, never
interleaved.

### AS BUILT: two shape changes, both forced by the real question

Recorded rather than quietly amended, because a spec that gets silently
edited to match its implementation is not a spec.

1. **One filter per evidence group could not express Q2's own first group.**
   The design said `filter: {"kind": "min_high", ...}` — exactly one
   predicate. But "multiple inputs active" in Q2 means multiple active AND
   ENABLE ON, and the ask's entire point is the contrast with enable off. A
   bare `min_high` over all 32 rows selects 22, twelve of which are disabled
   rows belonging to the OTHER half of the same sentence: the evidence would
   have contradicted the caption sitting on top of it. So `select` is a
   CONJUNCTION — a dict of filter-kind -> params, all ANDed. The vocabulary
   is still closed (equals / min_high / value_range), which was the actual
   point; it is deliberately not an expression language.
2. **`tables` + `sources` collapsed into `results`.** Passing rows and their
   provenance as two arguments is two homes for one fact and one chance for
   them to disagree about which backend produced which rows. A `Measurement`
   already carries table, backend and verification together.

Also as built: `resolve_prose()` runs ONCE and both the terminal renderer and
the published manifest render from its output. Building the evidence twice
would let the page a student reads and the page the site serves disagree
about which rows support which sentence, with nothing to catch it.

### The provider seam (ohmwork/llm.py, LANDED 2026-08-25)

One module talks to a model; everything else takes a provider. `GroqProvider`
(default) and `AnthropicProvider` differ in exactly one thing that matters —
Groq is chat-completions shaped and takes images as OpenAI-style `image_url`
data URLs, Anthropic takes `source` blocks — and that difference is the only
reason there are two classes rather than one with a flag.

Configuration, never a code change: `OHMWORK_LLM` (provider), `OHMWORK_LLM_MODEL`
(model), `GROQ_API_KEY` / `ANTHROPIC_API_KEY` (credential). **Keys live in the
environment only.** There is deliberately no config-file path and no CLI flag
that takes a key: this repo is heading for public, and a key committed once is
a key leaked forever.

**MODEL IDS ARE NOT GUESSED.** Hosted catalogues change faster than a
checked-in default will, so the default here WILL be wrong eventually. Rather
than let that land as a bare 404, `GroqProvider._explain` recognises the
shapes a stale id fails in (404, "not found", "decommissioned") and re-raises
with the models the account can actually serve; `--list-models` prints the
same list on demand. A guess that announces itself is recoverable; a guess
that looks like a default is not. Note the discrimination is tested in both
directions — a rate limit must NOT be reported as "pick another model", or it
sends someone off to change configuration that was never wrong.

Vision is a separate default (`DEFAULT_VISION_MODEL`), because extraction
reads a photographed lab-manual page and a text-only model cannot do that job
at all — the component values exist nowhere but the picture.

### The caption generator (ohmwork/captions.py, LANDED 2026-08-25)

The prose generator that "Architecture consequence" above says must run AFTER
simulation. It is the ONLY place in this repo where a model writes text a
student reads, so three constraints hold it in place:

1. **It sees only the resolved evidence** — the ask, and the rows selected
   for it. Not the circuit, not the netlist, not the rest of the table.
   `test_the_request_carries_no_circuit_and_no_netlist` asserts the
   CaptionRequest field set is exactly `{ask, groups}`, because a later
   refactor helpfully threading "a bit more context" through would break the
   grounding claim without breaking anything else. A generator that could see
   the netlist would write captions true of the CIRCUIT rather than true of
   the printed rows, and a reader could not tell those apart.
2. **The call is behind a seam.** `generate_captions(entries, generator)`
   takes a callable; `AnthropicCaptioner` is one and every test passes a
   fake. No test touches the network and none needs a credential. With no SDK
   or key the CLI says so and continues — measured results are unaffected.
3. **Nothing is written without a human reading it.** `--write-prose` shows
   the text and asks. Same gate that makes a generated rationale trustworthy.

The prompt forbids evaluative claims ("the circuit works correctly") for the
same reason the tool will not grade: a sentence asserting correctness is
exactly what a reader trusts without checking. Describe the rows.

### STORED prose, and therefore STALE prose

Generating captions fresh each run would break "regenerating an unchanged
question produces a byte-identical file", and a library that churns cannot be
reviewed. So a caption is STORED in the question JSON via the existing
`answer` / `answer_origin` fields.

Which creates a failure that did not exist before, and is worse than the one
it solves: **a stored caption outlives the rows it describes.** Change a gate,
re-run, and the same confident sentence sits over different evidence while
still looking grounded. Nothing in the rendering would give it away.

`prose.evidence_fingerprint(groups)` hashes label + columns + rows, and
`answer_evidence` records it. Three states, each naming itself: `fresh`,
`STALE` (loud, printed BEFORE the sentence, and the "check it against the
rows above" invitation is withdrawn), and `unknown` — no fingerprint recorded,
so the check could not run. The unrun-check rule one level down; `unknown` is
never quietly folded into `fresh`.

Two properties worth keeping, pinned as a pair so the check reads as a
discrimination rather than as one that happens never to fire:

- the fingerprint is scoped to the EVIDENCE, not the table. A row the filter
  never selected changing does NOT invalidate the caption — a staleness check
  that fires on unrelated edits gets ignored, and then it does not fire on
  the edits that matter.
- backend and verification are deliberately NOT in the fingerprint. A caption
  describes rows; if identical rows arrive from a different evaluator the
  sentence is still true of them, and their standing is rendered live on every
  run so it cannot itself go stale.

**What the fingerprint MEANS is "a human confirmed this text against these
rows", not "a machine wrote this text from these rows".** That is why
`--attest-prose` exists as a separate explicit act for hand-written answers:
an automatic stamp would record a review that never happened. Q2's own caption
was written by hand and had to be attested before it stopped rendering
"[evidence not recorded]".

Published as `answer_freshness` in the manifest (refused if absent) and
flagged per question by `has_stale_prose` in the index.

**Q2's prose is the first in this project grounded in EXTERNALLY computed
evidence** (Logisim, not our own engine), and the rendering says so rather
than merely omitting the internal warning: a reader cannot distinguish a
missing warning from a missing check. Both cases name themselves —
`[EXTERNAL: an outside tool produced them ...]` / `[INTERNAL: ohmwork's own
evaluator ...]`.

## Deferred: mirrored placements (M0/M90/M180/M270)

The emitter never emits mirrors and human-drawn input is check-mine mode,
out of v1 scope. The parser refuses M placements rather than guessing,
which is correct. When needed, the derivation is a 20-minute job, same
method as the rotation table:

1. In LTspice, place four npn transistors: Ctrl+E once before dropping
   (M0), then Ctrl+E plus one/two/three Ctrl+R for M90/M180/M270.
2. Add one res and one zener at M0 (two-terminal symbols of different
   heights, to check they transform identically).
3. Wire a short stub (F3) off every pin and label it (F4) with instance
   and pin identity, e.g. Q1C/Q1B/Q1E, judged visually: collector is the
   top-line terminal, base the flat left bar.
4. Save; derive the M-transform from anchors vs labeled stub endpoints;
   pin the measurements in test_symbols.py before the parser accepts M.

## Deployment: the library is the product (settled 2026-08-24)

**The constraint.** LTspice is a Windows GUI application. It does not run on
Vercel, on serverless, or in a normal Linux container. ngspice-in-Docker is
not a substitute: it cannot read LTspice's libraries, so it would put every
number back on synthesised models — the 0.45 V disagreement this project
spent three rounds removing. **A hosted ohmwork cannot simulate.**

So the split is:

| piece | role |
|---|---|
| the CLI | the GENERATOR. Runs locally where LTspice is. Produces verified results, reviewed by a human at the dry-run gate. |
| the library | the PRODUCT. Per question: the JSON, the circuit file, every result with full provenance, and the explanation. Committed. |
| the site | a VIEWER. Static, over the library. No backend, no simulation, no LLM in the hot path, no database. |

A question not in the library shows **"not solved yet"**, which is a real and
honest answer rather than a failure state.

Enforce now, so nothing gets built wrong:

- Do NOT build a server-side simulation path.
- Do NOT build a Groq or any other live-API hot path. Extraction happens
  locally at generation time, where the human reviews it.
- The manifest is a PUBLISHED CONTRACT, versioned, and strict about drift.

**Groq IS the model provider as of 2026-08-25, and that does not violate the
rule above.** Read it precisely: it forbids a live-API HOT PATH — the served
page calling a model when a student uploads a question. It says nothing about
which vendor runs locally at generation time, and its second sentence says
exactly where the call belongs. `ohmwork/llm.py` is driven only by the CLI,
on a machine with the simulators, with a human at the dry-run gate. **The
operational test of the rule: if anything in a request path ever imports
`ohmwork/llm.py`, the rule has been broken.**

The payoff is that every number the site serves is traceable to a real
LTspice run a human reviewed. The runtime was never the point.

### The manifest format

`ohmwork/library.py`; `tests/test_library.py` is the spec. `MANIFEST_VERSION`
is bumped on any change an existing viewer could misread. Written by
`python -m ohmwork <question>.json --library <dir>`, which also refreshes
`index.json`.

Carries: question id, verbatim text, source/extraction block, asks; the
circuit and analysis JSON; every device choice WITH its policy path; designed
values with rationale and rationale authorship; every Measurement with run,
backend, source, verification status, reliability and warnings; deliverable
paths with sha256; and the loader's warnings.

What it REFUSES to publish, each enforced with a path-shaped error:

- a result with no backend named — the site serves numbers to someone who
  cannot re-run them
- a result marked unreliable with no reason — dropout data is shown flagged,
  never hidden and never unexplained
- a deliverable marked `verified: true` with no `verified_by` saying HOW. This
  one is subtle and worth keeping: the deliverable `.asc` carries the whole
  experiment with one run active and the rest commented, so **the exact bytes
  shipped are not the bytes LTspice ran** — the per-run scratch files were.
  What it does have is a real machine check, the geometric round trip. The
  format makes you write which claim you are making.
- a deliverable marked `verified: false` with no reason — the `.plt` case, and
  the general rule that an artefact with no machine verification path must say
  so wherever it ships
- a designed value with no rationale
- any unknown key, anywhere

`generated` is passed in rather than read from the clock, so regenerating an
unchanged question produces a byte-identical file. A library that churns
cannot be reviewed. `index.json` validates every manifest it lists — a library
that indexes a broken manifest publishes it — and flags per question whether
any result is `internal` (our own evaluator) or unreliable, so a reader knows
before opening the page.

### SETTLED: an upload is a REQUEST, not a solve

The product idea is an agent you hand a question to, which hands back the file
to solve it with. For a question NOT already in the library that cannot mean
"solve it now", because both ways of doing so are unacceptable: generating
without simulating is precisely the failure this project exists to prevent
(see "The core design principle"), and simulating on the server is impossible
per the constraint above.

**Decided 2026-08-24. Two paths, and only two:**

1. **In the library** — instant. Upload, match, download. This is the whole
   experience for anyone whose question is already answered, which for a fixed
   set of lab experiments is most people most of the time.
2. **Not in the library** — accepted, queued, shown as PENDING. It is generated
   on the next local run, reviewed at the dry-run gate, and appears in the
   library. Minutes to hours, not seconds, and every number still traceable to
   a reviewed LTspice run.

**Do not build a third path.** The dangerous one is anything that looks like
(1) but is secretly unverified generation.

If instant-for-arbitrary is ever genuinely needed, there is exactly one
acceptable form: a **clearly separate mode, labelled UNVERIFIED**, using the
same machinery that already labels `.plt` files and `internal` results. It
never masquerades as a solved question, never appears in the library index
beside verified entries, and carries its label in every view that shows it.
Anything short of that is the failure mode with extra steps.

### Library layout (decided 2026-08-24)

One directory per question:

```
library/
  index.json
  exp02-series-regulator/
    manifest.json
    question.json
    series_regulator.asc
    series_regulator.plt
  exp03-regulated-supply/
    ...
```

- Slug is `exp<NN>-<short-name>`, lowercase. **Stable forever once published**,
  because it is the URL. Validated at write time by `library.SLUG` rather than
  discovered after someone links to it, and `build_index` additionally refuses
  a directory whose name does not match the manifest's `question_id` — a
  mismatch silently resolves old links to the wrong question.
- Deliverable paths in a manifest are **relative to the question directory**,
  so a manifest plus its folder is self-contained and can be moved, mirrored,
  or served from any prefix without rewriting. The folder is the unit of
  publication.
- `question.json` travels with its answer, so a reader can see exactly what was
  fed in, not only what came out. It is written from `Question.to_dict()`, the
  same rebuild-from-parsed-state the round-trip test uses.
- `index.json` lists `question_id`, `path`, `generated`, `backend`,
  `result_count`, and the two flags a reader needs before trusting a page:
  `has_internal_results` and `has_unreliable_results`.

Written by `python -m ohmwork <question>.json --library <dir> --id <slug>`,
which puts the deliverables in the question directory and refreshes the index.

### SEEDED 2026-08-25 — what is actually in `library/`

| slug | question | target | evaluator |
|---|---|---|---|
| `exp02-series-regulator` | Q1, exp 2.14 | LTspice | LTspice 26.0.2.1 |
| `exp03-regulated-supply` | Q3, exp 3 | LTspice | LTspice 26.0.2.1 |
| `exp08-priority-encoder` | Q2, exp 8.2 | Logisim 2.7.1 | Logisim Evolution 4.1.0 |

Three, not five. See the seeding note under "v1.0 SCOPE" — what is missing is
question TEXT, not code.

Facts worth keeping from doing it:

- **Published filenames follow the SLUG, not the local JSON's name.** A
  student downloading `exp08-priority-encoder.circ` can tell what it is;
  `q2.circ` tells them nothing, and renaming the input file would otherwise
  rename the download. The slug is the stable published identity, so it names
  the file too.
- **Regeneration is byte-identical**, verified by diffing the tree before and
  after re-running two of the three. `generated` being passed in rather than
  read from the clock is what buys that.
- **The Logisim deliverable can make a STRONGER claim than the `.asc` can, so
  it is checked rather than asserted.** A `.circ` has one run and no
  directives, so the deliverable really is the file Logisim evaluated — the
  CLI proves it with `filecmp` against the scratch run file and only then
  writes `verified_by: byte-identical to exhaustive.circ ... compared, not
  assumed`. If they ever differ it falls back to the weaker same-description
  claim and says so. The `.asc` cannot make that claim at all: its deliverable
  carries every run with one active and the rest commented, so the exact bytes
  shipped were never handed to LTspice.
- `tests/test_seeded_library.py` checks the committed library as the published
  artefact it is: every manifest validates, `index.json` matches disk, every
  deliverable exists and matches its sha256, every `question.json` reloads AS
  THE TARGET IT WAS SOLVED FOR, and no published result is `internal`.

## The .circ emitter (landed 2026-08-24)

`ohmwork/logisim_emitter.py`. **Its acceptance test is Logisim, not our own
parser**: emit Q2 from the JSON, run `--tty table`, require the same 32 rows
Logisim produced from `exp8_gates.circ`, a file a student drew. One
comparison covers the emitter, the placement, the routing, the crossing rule
and the label constraint at once — against a tool we did not write, using a
reference we did not compute. `--tty stats` is asserted too, because a truth
table can be right while a gate is missing or invented if the extra gate
happens not to change the function.

That is strictly stronger than the LTspice round trip, which proves only that
our emitter and our parser agree.

**The mutation check.** A test that cannot fail is worth nothing, so the
acceptance comparison was deliberately broken four ways: removing the
inverter, turning an OR into an AND, ungating the enable, and swapping which
pin drives which net. All four were caught (the last one is pinned as a
test). Worth knowing for the next person: swapping two NETS' contents between
their dict keys is a no-op — it renames them and the connectivity is
identical — so a mutation showing no difference is not automatically evidence
of a weak test.

### How the layout makes the shorting hazard structurally impossible

Both routing hazards come from Logisim connecting by geometry:

1. **Never split a wire at a crossing.** A segment boundary at an
   intersection turns an X into a junction — same picture, different
   circuit. Every straight run is emitted as ONE segment, and a test asserts
   no endpoint coincides with any crossing point.
2. **An endpoint on another wire's span IS a connection.** A route
   terminating mid-span of an unrelated net shorts them with no visual cue
   at all. This is the failure the format makes easiest to create and
   hardest to see.

The placement removes the second hazard by construction rather than hoping:

- **One row per component, globally** — anchors `ROW_PITCH` apart across the
  whole circuit, not per column, with `ROW_PITCH` wider than the 40-unit span
  of a gate's input pins. No two components share a port y, so a horizontal
  run sits at a y no foreign port occupies.
- **One vertical channel per net**, in the gap immediately right of its
  source's column, strictly inside the gap. A vertical cannot pass through a
  port, and distinct channels stop two nets' verticals overlapping.
- Sinks are always in a later column than their source, since a gate's depth
  is one past its deepest driver. Routes only ever run rightwards.

`validate_wiring` still runs on every emitted circuit, because a structural
argument that is never tested is an assumption. It also rejects dangling
routes, and it permits a mid-span endpoint only when both wires are on the
SAME net — that is a T-junction, which is what fan-out looks like.

`_depths` doubles as the acyclicity check: a combinational loop has no finite
depth, and saying so is better than emitting a file Logisim renders as an
error state.

## Generated layouts are mechanical, and must say so

The `.circ` emitter places inputs in a left column, outputs in a right
column, and gates in columns by logic depth, then routes orthogonally. It
crosses wires freely, which the connection rule makes safe.

**No placer that tries to look good.** Layout quality is v1.1. What v1.0 owes
the student is a correct file and an honest label: the output states that the
layout is mechanically generated, so nobody opens one expecting it to
resemble a hand-drawn schematic and concludes the tool is broken when it does
not. Same species as the `.plt` "UNVERIFIED" line — say what the artefact is,
rather than hoping it passes.

This is also why the Q3 layout ugliness (11 components in a single 2496-wide
row) was left alone rather than patched: a placer built to fix one circuit
would have to generalise over Logisim too, and neither target has enough real
examples yet to say what it would need to generalise over.

## The incident list (for the README, collect as we go)

Each entry is a case where something LOOKED correct and was not, paired with
the defence that now exists because of it. A README that shows the defences
without the incidents reads like over-engineering; with them it reads like
earned caution. Keep this list current — it is the most persuasive part of
the story and it is otherwise scattered across this file.

**THE TABLE NOW LIVES IN `ARCHITECTURE.md`, WHICH IS THE ONLY COPY.** It was
duplicated here for one day and that is exactly the failure this project
warns about elsewhere (see Q4's verbatim text): a text held in two places is
a text that will eventually disagree with itself, and the incident table is
the most quoted thing in the repo. Add new incidents THERE. This section
keeps only the reason the table exists — a README that shows the defences
without the incidents reads like over-engineering; with them it reads like
earned caution.

Two more that are limits rather than incidents, and belong in the README's
"cannot verify" section rather than here: the `.plt` file has no machine check
of any kind, and the geometric round trip proves self-consistency only,
because emitter and parser share the pin table.

## README spec (WRITTEN 2026-08-25 — see README.md)

This will be public, so the README is the artefact people actually read. It
is not a feature list. Structure, in order:

1. **Lead with the design principle**: the simulation comes FROM the generated
   file, not alongside it. The failure it prevents is concrete and should be
   told as a story — an LLM emitted a netlist and an `.asc` separately, the
   netlist had the zener correctly as a shunt and the schematic had it
   forward-biased, and both looked fine.
2. **One worked example, with real numbers and their provenance.** Q1 or Q3,
   showing the question text, the generated file, the measured values, and
   which LTspice version produced them on what date.
3. **What the tool cannot verify.** This section is the point, and it is what
   will make an experienced engineer read the rest properly. **Do not soften
   it.** It must list, plainly:
   - the `.plt` file: no machine check exists for it at all, batch mode does
     not read plot files, and its only check is a human looking at it once
   - a wrong-but-consistent misread of a question: simulation cannot catch it,
     because a wrong circuit simulates perfectly well. Only a human comparing
     the extracted values against the original can
   - the internal fallback when Logisim is absent: our own evaluator computes
     the result AND anything it would be checked against
   - the self-consistency limit of the geometric round trip: emitter and
     parser share the pin table, so a wrong offset is invisible to it. The
     only ground truth is a real-file measurement
4. What it does not do: not a homework-answer service, ungraded self-study
   questions, explanations matter as much as files.

Write it last, from what the code actually does, and check every number in it
against `tests/baselines.py` rather than retyping from memory.

**AS BUILT (2026-08-25).** All four sections are there, plus two additions
worth keeping:

- A section on **why the defences look excessive**, enumerating the four times
  careful reasoning lost to a mechanical check on this project (the zener
  orientation, `.step` ordering, the crossing rule, and the router's channel
  band caught by `validate_wiring` on its first run), plus the evaluator
  asymmetry as a fifth of a different kind — a documented architectural
  constraint that rested on a factual error nobody had checked by running the
  command. Without the incidents the defences read as over-engineering; with
  them they read as earned.
- A `Try it` command per seeded question, because the README is also how
  someone runs this for the first time.

"Check every number against baselines.py" is itself now a test, not a
practice: `tests/test_readme.py` asserts each quoted measurement against its
Baseline, recomputes the derived claims, and refuses the void unanchored
values (7.939 / 8.749) in case they ever get quoted again from memory. It
caught two drifted renderings on its first run. Adding a number to the README
means adding it to that test.

One number the README wanted did NOT exist and was measured rather than
recalled: "vb barely moves while vout drops" came from an ngspice run three
rounds and one device policy ago. Re-measured on this build (VB_NOLOAD /
VB_FULLLOAD / VOUT_NOLOAD / VOUT_FULLLOAD in baselines.py): vb moves 1.03 mV
across 100k -> 500R while vout drops 137.6 mV, a factor of 133. The
conclusion survived; the numbers were not the old ones.

## Known failure modes

Written down because they are cheap to record now and expensive to rediscover
after the code is built. Not all need handling in v1, but none should be a
surprise.

### The simulator divergence problem (decide this first)

The circuit file is for LTspice. The verification runs in ngspice. These are
different simulators with different device libraries.

Real lab files use LTspice standard-library parts like `2N3904` and `UMZ8_2T`.
ngspice has neither. The prototype worked around this with generic
`.model QN NPN(BF=100)` and `D(BV=8.3)` cards, which means the verified numbers
are not the numbers LTspice will show.

Verification currently proves topology, not values. Three options:

1. Emit explicit `.model` cards so both simulators see identical devices. Cheap,
   but diverges from what the lab manual specifies.
2. Drive LTspice headlessly (`LTspice.exe -b file.asc` produces a `.raw`) and
   parse the raw file. Removes the divergence entirely. Requires LTspice on the
   machine, which the student has anyway.
3. Only claim topology verification and label numeric answers as approximate.

Option 2 is the honest one. Choose before building the simulate layer.

### Extraction from images

- Value misread: `1.8k` as `1.8M`, `470uF` as `470pF`. Simulates fine, answer is
  confidently wrong. Unfalsifiable by simulation. Mitigate only by echoing
  extracted values back for confirmation.
- Greek and symbol characters (mu, ohm, beta) mangled in low-res screenshots.
- Subscripts: `V_z`, `R_L` render as `Vz`, `RL` or worse.
- No image at all: Q3 describes the circuit entirely in prose. The pipeline must
  handle text-only topology description too.
- Multiple images for one question.
- Phone screenshot of a PDF page, which is the actual real-world input format,
  not a clean PDF render.

### Generation

- Model emits a component type not in the pin table. Must fail loudly, not
  silently place nothing.
- Net referenced by only one pin: floating node, ngspice gives singular matrix.
  Detect before simulating.
- No net named `0`: no ground reference, simulation fails. Check explicitly.
- Duplicate InstName.
- Component count exceeds the grid layout. Needs a wrap or a bigger sheet.
- Value strings LTspice accepts but ngspice does not, and vice versa (`1.8K`
  vs `1.8k`, `Meg` vs `M`).

### Simulation

- Transient non-convergence. Q3 (bridge + 470uF + inductor) is a classic case.
  May need `.tran 0 100m 0 10u uic`, or startup ramping, or `.options` tweaks.
- Simulation that never terminates. Needs a hard timeout.
- Only startup transient captured, steady state never reached. Set the stop time
  to several supply periods and discard the first N.
- Operating point found but physically absurd (transistor saturated, zener not
  in breakdown). Sanity-check the regime, not just convergence.

### Correctness limits

- A wrong-but-internally-consistent circuit simulates cleanly. Simulation cannot
  catch a misread question. Only a human can.
- Some questions have no unique right answer. "Explain your design choices"
  (Q2) is not verifiable.
- Some asks are non-computational: "discuss how your circuit behaves when
  multiple inputs are active". These need the LLM, and cannot be checked.
- Distinguish clearly in the output between numbers that came from simulation
  and prose that came from the model. Never present the latter with the
  authority of the former.

### Logisim specific

- Combinational loops.
- Unconnected gate inputs: Logisim shows error state rather than failing.
- Bit width mismatch between a multi-bit component and a single-bit wire.
- Tunnel labels are case sensitive.
- The 7447 needs Logisim Evolution. Plain 2.7.1 will silently not have it.

### Operational

- API rate limit or failure mid-run.
- LTspice not installed, so the file cannot be checked locally.
- Windows vs Linux path handling, since LTspice is a Windows app and the tool
  may run elsewhere.
- Non-deterministic model output: the same question producing different circuits
  on different runs. Cache by question hash to make it stable.

## Working agreement

- Explain code before I accept it. I am learning to code properly through this
  project, so a working file I cannot read is a failure, not a success.
- Write tests before implementation.
- Prefer small, readable functions over clever ones.
- Never hardcode a pin offset that was not derived from a real file.
- Never pin a plausible number. Measure or delete. Every simulation-derived
  expected value lives in tests/baselines.py as a Baseline with structured
  provenance (backend+version, date, generating fixture/command) — a bare
  float as an expected simulation value is banned. This rule exists because
  estimated values were once nearly pinned as baselines; unlike a wrong
  device model, a wrong pin corrupts the reference itself, after which
  every downstream check agrees with the error. Never backfill provenance
  you are not certain of — re-measure or delete instead.
