# ohmwork

Takes an electronics-lab **open ended question** in plain English and produces
two things: a circuit file you can open in the real tool (LTspice `.asc`,
Logisim `.circ`), and the analysis the question actually asks for — computed by
running a simulator, not guessed by a language model.

These are ungraded self-study questions from an ECE lab. Nothing is submitted.
The point is understanding, so the explanation matters as much as the file.

---

## The design principle

**The simulation must come FROM the generated file, not alongside it.**

Here is the failure that rule exists to prevent, and it is not hypothetical —
it happened in the first hours of this project. A language model was asked for
a series voltage regulator. It produced a SPICE netlist and an LTspice
schematic, side by side. Both looked fine. The netlist had the zener correctly
oriented as a shunt, cathode to the base node; the schematic had it forward
biased. Nothing disagreed, because nothing compared them. Simulating the
netlist "verified" a circuit that was not the circuit in the file being handed
over.

So there is exactly one pipeline, and it has no shortcut:

```
question text
  -> a JSON circuit description: components and nets, NO coordinates
  -> the emitter places components and writes the .asc / .circ
  -> the simulator is handed THAT FILE
  -> results are read back out of what the simulator produced
```

The model never produces coordinates and never produces the thing that gets
simulated. Everything downstream of the description is deterministic Python.
For LTspice there is a second check on top: the emitted `.asc` is read back and
its connectivity rebuilt from the geometry alone, and the recovered netlist must
match the description. For Logisim there is something stronger — Logisim itself
evaluates the file we wrote.

---

## A worked example

`examples/q1_anchored.json`, transcribed by a human from a screenshot of the lab
manual, section 2.14:

> Calculate the output voltage in both load and line regulation and Zener diode
> current in the regulator circuit shown below using LTspice.

The circuit is in the manual as a picture: 15 V unregulated in, 1.8 kΩ series
resistor, 8.3 V zener from base to ground, an NPN pass transistor with β = 100,
2 kΩ load. Run it:

```bash
python -m ohmwork examples/q1_anchored.json --dry-run
```

That prints every extracted value for you to check against the image, then
exits without simulating. Drop `--dry-run` and it emits the `.asc`, runs
LTspice headlessly on it, and reports:

| quantity | value |
|---|---|
| output voltage at 15 V in, 2 kΩ load | **7.48403 V** |
| base node `vb` | **8.29214 V** |
| zener current | **3.68954 mA** |
| line regulation, 12 V → 20 V in | **0.399224 %** |
| load regulation, 100 kΩ → 500 Ω | **1.847626 %** |

Provenance: LTspice 26.0.2.1, `F:\LTspice.exe -b -ascii`, measured 2026-08-24.
Every one of those numbers is pinned in [`tests/baselines.py`](tests/baselines.py)
with the backend, version, date and generating command that produced it. A bare
float as an expected simulation value is banned in this repo.

The zener is not a real part here: the question specifies Vz = 8.3 V and names
no device, so the tool synthesises a model card at exactly that value and says
so in the report. Which is worth one sentence, because getting it wrong cost
three rounds. `D(BV=8.3)` is **not** an 8.3 V zener — in SPICE, `BV` is the
voltage at which reverse current equals `IBV`, which defaults to 1 mA, while a
datasheet Vz is quoted at a test current. With that under-specified card,
LTspice and ngspice disagreed about `vb` by roughly 0.4 V and the argument was
about which simulator to trust. With the current one, `D(BV=8.3 IBV=5m)`,
LTspice gives 8.292139 V and ngspice gives 8.292262 V: **123 µV apart on two
independently written simulators.** When two simulators disagree, suspect an
under-specified device before suspecting the simulators.

**And this is the part that is actually worth a student's time.** Across the
full load sweep, `vb` moves from 8.292391 V to 8.291356 V — about **1 mV** —
while `vout` drops from 7.585122 V to 7.447520 V, about **138 mV**. The output
moves 133 times further than the node that is supposed to be setting it. So
essentially none of this regulator's load regulation comes from the zener
sagging; it is Vbe rising with emitter current. That falls out of the sweep
data, and it is the answer to the question in a way a percentage is not.

### The other two

```bash
python -m ohmwork examples/q3.json --dry-run   # bridge + C-L-C + zener, transient
python -m ohmwork examples/q2.json --dry-run   # 4-to-2 priority encoder, Logisim
```

Q2 also has two asks no measurement can answer. `--write-prose` sends the
**selected evidence rows and nothing else** — not the circuit, not the
netlist, not the rest of the table — to Claude, shows you what came back, and
saves it into the question file only if you say yes. That constraint is the
point: with nothing but the printed rows to work from, anything the caption
says that the rows do not support is visible as unsupported.

```bash
python -m ohmwork examples/q2.json --write-prose
```

Needs the model layer configured — see below. Without it the command says so
and carries on; every other result is unaffected, and no test in this repo
touches the network.

Q3 is a design question: the topology is given in prose, but the series
resistor value is ours, so it is marked `designed`, carries a rationale, and
renders in its own section headed *"these are choices, not given"*. Q2 is
digital: 32 rows, evaluated by Logisim, with the three digital regime
assertions checked and reported.

---

## What the tool CANNOT verify

This is the section that matters. None of it is softened.

**The `.plt` plot-settings file has no machine check of any kind.** LTspice's
batch mode does not read plot files, so nothing this repo can run will tell you
whether the panes render correctly. Its format was transcribed from real files
shipped with LTspice rather than invented, and that is the entire basis for
believing it. Its only real check is a human opening one in the GUI, once. The
CLI prints `UNVERIFIED` next to every `.plt` it writes, and the published
manifest carries the same statement, because an artefact with no verification
path must say so everywhere it ships.

**Simulation cannot catch a misread question.** If the vision layer reads
`1.8k` as `1.8M`, or `470 µF` as `470 pF`, the resulting circuit is wrong but
perfectly self-consistent. It emits, it round-trips, it converges, and it
reports a confident number that answers a question nobody asked. There is no
downstream check that can catch this, because every downstream check is
checking internal agreement. The only defence is a human comparing the
extracted values against the original image, which is what `--dry-run` exists
for and why it prints values before anything else.

**The geometric round trip proves self-consistency, not correctness.** The
emitter and the `.asc` parser both import the same pin table. A wrong pin
offset makes the emitter place a flag at the wrong coordinate and makes the
parser look for it at the same wrong coordinate, and the round trip passes.
The only ground truth for those offsets is measurement against real hand-drawn
files, which is why every symbol in the table has a corresponding real-file
test and why no offset may be added from a datasheet or from documentation.

**Without Logisim installed, digital results are computed by our own
evaluator.** That means one program produces both the answer and anything the
answer would be checked against; a bug in it breaks both sides identically and
they agree forever. Such results are labelled `internal` at every level — in
the report, in the manifest, and in the library index — and the library refuses
to publish one without a warning attached. With Logisim present the results are
`external` and carry the same standing as LTspice's.

**Prose is not verified and cannot be.** "Explain your design choices" has no
computable answer. The best available is *local falsifiability*: for
"discuss how your circuit behaves when multiple inputs are active", the tool
selects the rows of the truth table where two or more inputs are high, prints
them, and puts the sentence directly underneath, so you can check the claim
against the evidence without leaving the page. That makes the prose checkable.
It does not make it verified, and the section says so in its header.

A stored caption can also go **stale**, which is subtler and worse. It is
saved into the question file so the library regenerates identically — and it
then outlives the rows it describes. Change a gate, re-run, and the same
confident sentence sits over different evidence while still looking grounded.
So every stored answer records a fingerprint of the rows it was reviewed
against, and each run reports `fresh`, `STALE`, or `evidence not recorded` —
that last one being its own state, never quietly folded into "fresh".

**A hand-drawn file is not necessarily a valid file.** One of the real fixtures
in this repo declares all fourteen of its pins as inputs — including four
driven by adder outputs — and leaves one XOR input unwired, with the wire meant
to feed it dead-ending ten units short. It is kept precisely because it is
wrong. Anything reading real input has to survive that and report it, not
assume the input is correct.

---

## Why the defences look excessive

They do look excessive. Here is the reason, which is that on this project
**careful reasoning has lost to a mechanical check four times**, and every one
of those times the reasoning felt settled beforehand.

1. **The zener orientation.** A netlist and a schematic, emitted together,
   reviewed, both plausible. They disagreed. Only simulating the generated file
   found it. → the design principle above.

2. **`.step` ordering.** A sweep listed its values in the question's order, and
   the derived regulation figure was computed by indexing that order. LTspice
   runs `.step LIST` in ascending numeric order regardless of how it is
   written, which silently flips the sign of a derived quantity. → point
   selection now parses the requested value and finds it in the axis trace the
   raw file itself recorded.

3. **The crossing rule.** In Logisim, does a wire crossing another wire connect
   to it? The two readings are indistinguishable on screen, and arguing about
   it produced confident answers in both directions. What settled it was
   counting: under "crossings connect", a student's working encoder has two
   nets with five drivers each, shorting all four data inputs together —
   electrically impossible. Under "crossings do not connect", 13 nets, every
   port wired, exactly one driver each. → the rule is pinned in both
   directions, with fixtures.

4. **The router's channel band.** The emitter's wire-routing geometry was
   reasoned about carefully, written up in the module docstring as structurally
   safe, and believed correct. On its very first run `validate_wiring` reported
   that the vertical channel band overlapped the next column's input x: the
   4-input OR gate's ports sit at exactly x=190, inside the band, shorting two
   data inputs. The bug was in gap arithmetic that double-counted the gate
   body. The check earned its place immediately, on code specifically written
   to make that failure impossible.

There is a fifth of a different kind, worth listing because it is the most
uncomfortable: this project spent weeks believing Logisim had no batch
simulator, and documented the resulting "evaluator asymmetry" as permanent and
unavoidable. It has one. `logisim-evolution --tty table` enumerates every input
combination and prints the truth table. A carefully reasoned architectural
constraint rested on a factual error nobody had checked by running the command.

Two rules follow from all of this and are enforced throughout:

- **Never pin a plausible number.** Measure it or delete it. Every expected
  simulation value carries structured provenance. This exists because estimated
  regulation figures were once nearly written straight into tests as pins —
  and unlike a wrong device model, which a simulator catches, a wrong pin
  corrupts the reference itself, after which every downstream check agrees with
  the error.
- **An unrun check must announce itself.** A check that can be skipped has to
  say it was skipped, in the output. Silence is indistinguishable from a pass,
  and a reader looking at a clean screen cannot tell whether everything was
  examined and found good or whether nothing was examined at all. The dry run
  has a `checks` section listing what ran and what did not, with reasons; the
  published manifest carries the same list; and the library index flags any
  question with skipped checks. The same rule applies in mirror image to
  checks that pass — every regime assertion reports what it examined, so
  "held" is never confusable with "nobody looked".

---

## What ships: the library

LTspice is a Windows GUI application. It does not run on a server, and
substituting ngspice would not help: ngspice cannot read LTspice's device
libraries, so every question naming a real part would fall back to a
synthesised model and answer a slightly different question. So a hosted
ohmwork cannot simulate, and the architecture says so plainly:

| piece | role |
|---|---|
| the CLI | the **generator**. Runs locally, where the simulators are. |
| `library/` | the **product**. Per question: the input JSON, the circuit file, every result with full provenance, and the explanation. Committed. |
| the site | a **viewer**. Static. No backend, no simulation, no model in the hot path. |

A question that is not in the library shows **"not solved yet"**, which is a
real answer rather than a failure state. There is deliberately no third path
that looks like an instant answer but is secretly unverified generation.

Write an entry with:

```bash
python -m ohmwork examples/q1_anchored.json --library library --id exp02-series-regulator
```

The manifest is a published contract, and it refuses to publish: a result with
no backend named, an unreliable result with no reason, a deliverable marked
verified with no statement of *how*, a deliverable marked unverified with no
reason, a designed value with no rationale, prose evidence that does not name
what computed it, a prose answer with no recorded authorship, or any unknown
key anywhere. `generated` is passed in rather than read from the clock, so
regenerating an unchanged question produces a byte-identical file — a library
that churns cannot be reviewed.

### Currently seeded

| id | experiment | target | evaluator |
|---|---|---|---|
| `exp02-series-regulator` | 2.14, series voltage regulator | LTspice | LTspice 26.0.2.1 |
| `exp03-regulated-supply` | 3, bridge + C-L-C + zener | LTspice | LTspice 26.0.2.1 |
| `exp08-priority-encoder` | 8.2, 4-to-2 priority encoder | Logisim 2.7.1 | Logisim Evolution 4.1.0 |

Three, not more, because three is how many questions we have verbatim text for
and can currently solve. Experiment 9.7 (BCD to seven-segment with a 7447) has
its text in `examples/drafts/q4_question.md` but is blocked: the 7447 and the
seven-segment display live in Logisim Evolution's TTL and I/O libraries, and
no component's geometry may be added to this repo without being measured from
a real file containing it. Padding the count with an invented question would
be worse than a short list.

---

## What it is not

Not a homework-answer service. These questions are ungraded self-study
questions with no submission attached, and the tool is built for the case where
you want to understand the circuit — which is why the explanation, the design
choices, and the *reasons* behind each number are treated as part of the
deliverable rather than decoration around it.

It will not produce a beautiful schematic. Layout is mechanical: for LTspice a
row of components on a grid, for Logisim inputs in a left column, gates in
columns by logic depth, and orthogonal wires crossing freely. The output says
so when it writes one, because a student opening a generated file and expecting
something hand-drawn would reasonably conclude the tool was broken. A real
placer is future work.

---

## Requirements

- Python 3.12+, `spicelib`
- LTspice, for analog questions. Set `OHMWORK_LTSPICE` if it is not in a
  standard location.
- Logisim Evolution, for digital ones. Set `OHMWORK_LOGISIM` to override. If it
  is absent, digital results fall back to the internal evaluator and are
  labelled `internal` everywhere they appear.
- ngspice is optional and exists only to keep the pipeline testable on
  Linux/CI. It cannot read LTspice's device libraries, so its numbers differ by
  design and are never shown to a student.

### The model layer (optional)

Only needed for generated prose and, later, extraction. Everything else —
every simulation, measurement, regime check and library entry — works without
it.

```bash
pip install -e ".[llm]"
```

Then copy the template and put your key in it:

```bash
cp .env.example .env
```

Open `.env` and replace `gsk_replace_me` with your key from
[console.groq.com](https://console.groq.com). That is the whole setup — no
terminal restart, no `setx`.

`.env` is gitignored, and the ignore rule was added **before** the file
existed, because a key committed once lives in the git history forever even
after you delete it from the working tree. `.env.example` is the committed
template and holds no real values.

A real environment variable, if you set one, wins over the file — setting one
is the more deliberate act, and a stray `.env` must never outrank a CI secret.
There is no CLI flag that takes a key: flags end up in shell history, in
screenshots, and in the process list.

| variable | meaning |
|---|---|
| `GROQ_API_KEY` | the key. Free tier at console.groq.com |
| `OHMWORK_LLM` | `groq` (default) or `anthropic` |
| `OHMWORK_LLM_MODEL` | model id; overrides the built-in default |

**Model ids are not stable and this repo does not pretend otherwise.** Hosted
catalogues change faster than a checked-in default will, so the built-in one
will be wrong eventually. When it is, you get the list of what your account
can actually serve rather than a bare 404 — and you can ask for it directly:

```bash
python -m ohmwork --list-models
```

Where the model call happens is an architectural rule, not a preference: it
runs **locally, at generation time, with a human at the dry-run gate**. The
published site is a static viewer over `library/` and never calls a model. If
a request path ever imports `ohmwork/llm.py`, that rule has been broken.

```bash
python -m pytest
```

Tests that need a simulator skip cleanly when it is absent, and say which one
they wanted.
