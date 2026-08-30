# Architecture

This document carries the three things about ohmwork that are worth reading if
you are evaluating the code rather than using it:

1. the **layer map** — what each module owns, and where the boundaries are
2. the **derived format facts** — the `.asc` and `.circ` geometry this project
   measured from real files, and the method used to measure it
3. the **incident table** — every case where something looked correct and was
   not, paired with the defence that now exists because of it

[README.md](README.md) is the user-facing document: what the tool does, one
worked example with real numbers, and what it cannot verify. This one is for
the reader who wants to know why it is built the way it is.

---

## 1. The layer map

```
question.json                         a human wrote it, or reviewed it
  |
  |  question.py        THE GATE: strict schema, device policy, coverage,
  |                     origins, warnings. Unknown keys are errors.
  |  targets.py         picks LTspiceTarget or LogisimTarget, and runs ONLY
  |                     that target's chain.
  v
circuit description                   components + nets. NO coordinates.
  |
  |  emitter.py         places on a grid, writes .asc
  |  logisim_emitter.py places in columns by logic depth, routes, writes .circ
  v
THE FILE                              the deliverable, and the thing simulated
  |
  |  parser.py          reads the .asc back, rebuilds connectivity from
  |                     SYMBOL + FLAG geometry alone, compares to the
  |                     description. (No .circ parser -- see section 5.)
  |  simulate.py        LTspiceBackend (-b -ascii + spicelib), NgspiceBackend
  |  logisim_backend.py Logisim Evolution --tty table, or the internal
  |                     fallback evaluator
  v
results
  |
  |  analysis.py        runs the experiment plan; scalar / derived /
  |                     waveform_stats / table measurements; regime checks
  |  prose.py           three tiers of prose ask, grounded in evidence rows
  |  captions.py        the ONE place a model writes text a student reads
  |  library.py         the published manifest, and the index over it
  v
library/<slug>/                       manifest.json + question.json + files
```

Above that sits the **design loop**, which is how a question typed in plain
English reaches the gate at all. There are two of them, and `domain.classify`
decides which — a guess made from the question's words, disclosed rather than
taken silently, and safe to get wrong because the loop it picks runs its own
domain check and refuses with the reason:

```
question text
  |
  |  domain.py    classify -> analog or digital, and refuse outright what
  |               neither loop can honestly answer. Before a token is spent.
  |
  +-- DIGITAL ------------------------------------------------------------
  |  spec.py      the model writes one boolean expression per output, from the
  |               question's WORDS. No gates. Its signal names are authoritative.
  |  design.py    the plan is DERIVED in Python; the model writes only
  |               components and nets; the gate's rejection is fed back verbatim
  |  partcheck.py for a question naming a measured part: evaluate a BARE one
  |               first, and require the design to reproduce it through the
  |               wiring. The chip is its own reference; no datasheet is recalled
  |
  +-- ANALOG -------------------------------------------------------------
  |  intent.py    the model writes the numeric TARGETS from the question's
  |               words. No components. Its net names are authoritative, the
  |               plan and the regime assertions are DERIVED from it and from
  |               the parts list, and a tolerance wide enough to admit any
  |               plausible circuit is refused
  |  analog.py    the same design/gate/retry loop, verified by running LTspice
  |               on the emitted file and checking its numbers against the
  |               intent. NO ORACLE EXISTS HERE -- see section 4
  v
question.json  ->  the gate above
```

`basis.py` holds the one shape all three verification stories render through,
so a reader can always tell which claim an answer is making.

Supporting modules: `symbols.py` and `logisim_symbols.py` hold the measured pin
tables (and refuse anything unmeasured); `parts.py` holds the device policy;
`plt.py` writes LTspice plot files and is **frozen**; `llm.py` is the provider
seam; `extract.py` is the vision layer's entry point.

### Two boundaries that matter

**The gate.** `question.py` is where a human stops writing input and machine
generation begins. It is strict on purpose: an unknown key anywhere is a
path-shaped error (`circuit.components[1]: unknown key(s) ['resistance']`),
because a model drifting from the schema must fail loudly rather than silently
default. Semantic problems — implausible values, a 500x resistor spread, a run
with no regime assertions — *warn*, because the human confirmation step is what
they exist to feed.

**Never serve a number nobody checked.** This rule used to read *"no model in
the hot path"*, with the operational test *"if anything in a request path
imports `ohmwork/llm.py`, the rule has been broken"*. It was **rewritten, not
dropped**, when its premise turned out to be false — and the distinction is
worth stating precisely, because "the rule became inconvenient" and "the rule
rested on a factual error" look identical from outside.

The stated reason was: *the server CANNOT simulate, so it could only ever
produce results labelled UNVERIFIED.* True for analog, and permanently so:
LTspice is a Windows GUI application, and ngspice is not a substitute because
it cannot read LTspice's device libraries. **False for digital.** Logisim
Evolution is Java, runs on an ordinary Linux host, and is the same external
evaluator the CLI uses.

So the rule now reads: **no response may carry a circuit or a table the
evaluator did not confirm, and every response names the evaluator that
confirmed it.** `ohmwork/server.py` imports `llm.py` deliberately, and the
spirit of the old rule is enforced harder rather than weaker — `design.solve`
*raises* rather than returning a circuit Logisim disagreed with, so a failed
solve produces an error and no download at all.

Two consequences kept in code rather than in prose: the analog path is not
served from the web endpoint at all, and the offline fallback evaluator
(`verification: "internal"`, which computes the result *and* anything the
result would be checked against) is labelled as such in every response that
carries it.

---

## 2. Derived format facts

Everything in this section was measured from real files — files written by
LTspice and Logisim, or drawn by students. None of it comes from documentation.

**The standing rule: never add a pin offset that was not derived from a real
file.** Not from a datasheet, not from documentation, not by interpolating
between two measured cases. Both pin tables enforce this in code rather than by
convention: an unmeasured component, or a measured component at an unmeasured
size / rotation / input count, raises an error naming the pair and saying a real
file is required.

### The derivation method

**A port is a coordinate where exactly one wire terminates. Nothing else is
evidence.** Proximity to a component is not evidence — a human routes wires
around and into component bodies, so "the endpoint nearest the gate" picks up
corners and stray stubs just as readily as pins.

The XOR gates in `tests/fixtures/logisim/adder_subtractor.circ` are the worked
example. Counting how many wires touch each coordinate, across four instances:

```
(-50,-20)   degree 1, 1, 1, 1     port
(-50,+20)   degree 2, 1, 1, 0     port
(-60,-20)   degree 2, 2, 2, 2     NOT a port -- a bend
(-60,+20)   degree 2, 2, 2, 2     NOT a port -- a bend
```

Degree 2 with both wires *ending* there is a corner: a horizontal and a vertical
segment meeting. Proximity filtering lands on (-60,±20), and that was in fact
tried, and was wrong.

Two cautions the same example carries:

- Degree 1 **proves** a port. Degree ≠ 1 does **not disprove** one — (-50,+20)
  is degree 2 on one XOR because the human routed a corner onto the pin, and
  degree 0 on another because that input is simply unwired.
- So dead ends give *candidates*. Confirmation is the hypothesis holding across
  every instance, plus a whole file explained with nothing left over.

### LTspice `.asc`

```
Version 4.1
SHEET 1 880 680
SYMBOL <symname> <x> <y> <rotation>
SYMATTR InstName R1
SYMATTR Value 1.8k
WIRE <x1> <y1> <x2> <y2>
FLAG <x> <y> <netname>
TEXT <x> <y> Left 2 !<spice directive>
```

Pin offsets from the SYMBOL anchor, at rotation R0:

| symbol  | pins |
|---------|------|
| res     | (16,16), (16,96) |
| cap     | (16,0), (16,64) |
| ind     | (16,16), (16,96) |
| voltage | (0,16)=+, (0,96)=− |
| diode   | (16,0)=anode, (16,64)=cathode |
| zener   | (16,0)=anode, (16,64)=cathode |
| npn     | (64,0)=C, (0,48)=B, (64,96)=E |
| pnp     | (64,0)=C, (0,48)=B, (64,96)=E |

Note the height inconsistency: `res` is 96 tall, `diode` is 64. That is exactly
why offsets are derived and never assumed. Rotation transforms the offset about
the anchor: R0 `(x,y)`, R90 `(−y,x)`, R180 `(−x,−y)`, R270 `(y,−x)`. Mirrored
placements (M0/M90/M180/M270) are **not** derived, and the parser refuses them
rather than guessing.

Verified against four more hand-drawn files by three different students, in
`tests/fixtures/ltspice/`: every pin of every symbol lands exactly on a wire
endpoint or a flag the student drew. This is the check the round trip
structurally cannot make — see section 4.

**Encoding: a real `.asc` is not ASCII or UTF-8.** LTspice writes the micro sign
as the single byte `0xB5` (cp1252), so a `100µ` in a real file is invalid UTF-8.
`parser.ASC_ENCODING` is cp1252. The emitter writes pure ASCII (`100u`, never
the micro sign), and a test holds that line.

**Connectivity uses net labels, never routed wires.** Both formats determine
connectivity geometrically — a wire connects to a pin only if an endpoint lands
exactly on it — and auto-routing that is a real pathfinding problem that gets
silently wrong. So for LTspice the emitter gives each pin a 16-unit stub with a
`FLAG` on the free end; same label, same net. Net `0` is ground. Nothing is
routed.

### Logisim `.circ` (2.7.1)

```xml
<project source="2.7.1" version="1.0">
<lib desc="#Gates" name="1"/>
<wire from="(420,150)" to="(420,220)"/>
<comp lib="1" loc="(620,280)" name="AND Gate">
  <a name="inputs" val="2"/>
</comp>
```

Offsets relative to `loc`, at default size, facing east:

| component            | offsets from `loc`                             | instances measured |
|----------------------|------------------------------------------------|---|
| Pin (in or out)      | (0,0)                                          | 30 |
| NOT Gate             | in (−30,0); out (0,0)                          | 1  |
| AND/OR/XOR, 2 inputs | in (−50,−20), (−50,20); out (0,0)              | 10 |
| OR Gate, 4 inputs    | in (−50,−20), (−50,−10), (−50,10), (−50,20)    | 1  |
| Adder (width 1)      | A (−40,−10), B (−40,10), Cin (−20,−20), Cout (−20,20), S (0,0) | 4 |
| Priority Encoder     | in (−40,−10..+20 by 10), EN (−20,30), out (0,0), GS (0,10) | 1 |

Input spacing is **not** a single linear rule: two inputs sit 40 apart, four sit
at −20,−10,+10,+20. Derive, never extrapolate.

There is an output at `loc` for every component measured, but it is not always
the only one — Adder and Priority Encoder each carry a second output beside it.
"The last port is the output" is false, and was briefly baked into the table
before the Adder caught it.

Three further facts worth stating because each was a wrong assumption first:

- `comp/@lib` is an index into **this file's** `<lib>` block, not a global id.
  Always resolve it through the block. `tests/fixtures/logisim/shuffled_libs.circ`
  is a permuted copy that fails any check written against the literal `lib="2"`.
- An output Pin carries `output=true`. Absence means **input** — the default is
  not neutral, so a file can silently declare an output as an input pin.
- Line endings are not an invariant. Two of the three hand-drawn fixtures are
  CRLF and one is bare LF, and all three are files Logisim wrote.

Strongest single piece of evidence for the table: `exp8_gates.circ` is
*completely* explained by it. 33 distinct port coordinates, 33 dead-end wire
endpoints, and the two sets are identical. A wrong offset would leave either an
unexplained dead end or an unwired port.

### The connection rule

```
CONNECT:      two wires sharing an endpoint
CONNECT:      an endpoint lying on another wire's span   (a T)
DO NOT:       two spans intersecting where NEITHER ends  (an X)
```

This decides what the circuit *is*, and the two readings are indistinguishable
on screen. `exp8_gates.circ` contains 20 true crossings, 19 of which join wires
on genuinely different nets:

| model | nets | nets with more than one driver |
|---|---|---|
| crossings do NOT connect | 13 | 0 |
| crossings DO connect | 5 | 2, with 5 drivers each |

Under "crossings connect", all four data inputs short together with the NOT gate
output, and the enable pin shorts to three gate outputs. That is electrically
impossible, and the file is a student's working priority encoder. So crossings
do not connect — and under the correct rule the file gives 13 nets covering all
33 ports, nothing floating, exactly one driver per net.

Two consequences, one for each side:

- **Routing gets easier.** The emitter may cross wires freely. No crossing
  avoidance, no vias, no channel routing.
- **One hazard to hold.** The distinction turns entirely on whether an endpoint
  exists at a coordinate, so splitting one wire into two segments that meet at a
  crossing silently converts an X into a junction. Identical drawing, different
  circuit. An emitter must **never** split a wire at a crossing point, and a
  test pins exactly that failure.

### How the Logisim layout makes the shorting hazard structurally impossible

The second routing hazard — a route terminating mid-span of an unrelated net,
which shorts them with no visual cue at all — is removed by construction:

- **One row per component, globally.** Anchors are spaced wider than the 40-unit
  span of a gate's input pins, so no two components share a port *y*, so a
  horizontal run sits at a *y* no foreign port occupies.
- **One vertical channel per net**, strictly inside the gap right of its
  source's column. A vertical cannot pass through a port, and distinct channels
  stop two nets' verticals overlapping.
- **Routes only run rightwards**, since a gate's depth is one past its deepest
  driver.

`validate_wiring` still runs on every emitted circuit, because a structural
argument that is never tested is an assumption. It caught incident 9 on the
first run.

---

## 3. The incident table

Each row is a case where something looked correct and was not, paired with the
defence that exists because of it.

| # | what looked fine | what was wrong | defence it bought |
|---|---|---|---|
| 1 | A netlist and an `.asc` emitted side by side | the netlist had the zener correctly as a shunt, the schematic had it forward-biased | **the core design principle**: simulate the file we generated, never a netlist emitted alongside it |
| 2 | A deliverable, report and four baselines: emitted, round-tripped, converged, plausible number | built on the unanchored `D(BV=8.3)` card the device policy had already outlawed, because the policy was never applied to the actual input. Caught only because a human recognised the number from three rounds earlier | `emit()` hard-fails on any diode card with BV and no IBV; a test sweeps every example JSON in the repo; reports name each device's policy path |
| 3 | A `.step` sweep with values listed in the question's order | LTspice runs `.step` LIST in ascending numeric order regardless, silently flipping a derived sign | `at` selection parses the requested value and locates it in the recorded axis trace — the file's own account of what ran |
| 4 | Line and load regulation figures about to be pinned as test baselines | they were *estimated*, not measured. A wrong device model is caught by a simulator; a wrong pin corrupts the reference itself, after which every downstream check agrees with the error | `tests/baselines.py`: every simulation-derived expected value carries backend, version, date and generating command. A bare float is banned |
| 5 | A transient mean of 7.6 V for a symmetric AC source | physically impossible; the measurement was reading the wrong window | time-weighted (trapezoidal) waveform stats, and `.tran` Tstart set to the settle time so the saved data *is* the post-settle window |
| 6 | `exp8_gates.circ` read with crossings treated as connections | 19 of its 20 crossings join different nets. That reading shorts the four data inputs together and the enable to three gate outputs | the connection rule, pinned in both directions, plus a test that splitting a wire at a crossing changes the circuit |
| 7 | "Logisim has no usable batch simulator, so the evaluator asymmetry is permanent" | false. `--tty table` enumerates every input combination and prints the truth table. The constraint had been documented as accepted for weeks on a factual error nobody had checked by running the command | `LogisimBackend` with `verification = "external"`; digital results now have the same standing as LTspice ones |
| 8 | A question's `question` field | a **paraphrase**, not the verbatim text. Ask coverage read 74% against it and 59% against the real wording — the screen designed to reveal dropped work was grading itself | a test pins distinctive fragments of each question including its Greek characters; `source` blocks record who transcribed what |
| 9 | The router's channel band, reasoned about carefully and believed correct | it overlapped the next column's input *x*. The 4-input OR's ports sit at exactly x=190, inside the band, so two data inputs were shorted. The bug was gap arithmetic that double-counted the gate body | `validate_wiring` caught it on the first run — the check earning its place immediately, on the code written to be safe by construction |
| 10 | Our emitted encoder and the student's file disagreeing on row 1 | they compute the same function. Logisim enumerates in *its* own column order, which differs per file | rows are sorted by the input tuple in our column order. The `.step` lesson again: never trust the order a tool emits rows in, index by the values |
| 11 | A clean dry run with no warnings | for a question with no `asks`, the entire coverage defence had not run. Nothing said so | the unrun-check rule: `SkippedCheck`, the `checks` section, `checks_skipped` in the manifest |
| 12 | `Question.to_dict()` round-tripping every example | it silently dropped `target` and `constraints`. The library writes `question.json` from `to_dict()`, so the first published Logisim question would have shipped a file that reloads as an LTspice one. The round-trip test existed and had no Logisim case | `target`/`constraints` kept on `Question`; a test reloads every published `question.json` and checks the target it comes back as |
| 13 | A regime assertion reporting nothing | a regime that *held* left no trace anywhere — indistinguishable from one nobody evaluated. The unrun-check rule, unnoticed in its mirror image for as long as regimes had existed | `RegimeResult` carries `examined`, rendered in the report and published as `regime_checks`; the manifest refuses a check that does not say what it looked at |
| 14 | The prose spec's evidence filters, designed before an implementation | one predicate per evidence group could not express the first group of the question it was designed for. "Multiple inputs active" means multiple active AND enable on; a bare threshold selects 22 rows, 12 of them disabled rows belonging to the other half of the same sentence. The evidence would have contradicted the caption above it | `select` is a conjunction over a closed three-kind vocabulary; the reason is recorded in the test file rather than quietly amended |
| 15 | A README written carefully, numbers checked by reading | `tests/test_readme.py` caught two drifted renderings on its first run, plus a phrase-presence check silently passing because the phrase it looked for was hard-wrapped | README numbers are asserted against `tests/baselines.py`, and derived claims are computed from the pins rather than typed |
| 16 | Storing a generated caption so the library regenerates identically | the fix created a worse failure than the one it solved: a stored caption outlives the rows it describes. Change a gate, re-run, and the same confident sentence sits over different evidence, still looking grounded | `prose.evidence_fingerprint` + `answer_evidence`; three states (`fresh`/`STALE`/`unknown`), `unknown` never folded into `fresh`; published as `answer_freshness` |
| 17 | A local rehearsal of the CI condition: two override variables pointed at paths that do not exist, green, 25 clean skips | it cleared two of the THREE things a Linux runner lacks. LTspice's component libraries are found under `%LOCALAPPDATA%` regardless of those variables, so 47 tests that cannot run on the runner "passed" the rehearsal and went red on the first real build | `tests/conftest.py` stands up the committed fixture extract when no real library exists, so the manifest contract runs in CI instead of skipping; real-install assertions are keyed on `REAL_PARTS_LIBRARY`, captured before any substitution; `ci.yml` records the three-variable rehearsal command |

| 18 | A hand-rolled HTTP client for the free tiers, correct against a fake transport in every field | the first live call came back `HTTP 403: error code: 1010` — a Cloudflare bot challenge, triggered by urllib's default User-Agent. It looks like neither an auth failure nor a model problem, and reads as a dead key | the client sends a named User-Agent, and the test asserts it with the measurement in the comment: a wire-format detail no fake can discover |
| 19 | A model answering `200 OK` with an empty string | a reasoning model asked for 20 tokens spends all of them thinking and returns nothing, successfully. Passed on, it surfaces three layers up as "the model produced invalid JSON" — blaming the model for a budget this side chose | an empty completion is an error at the provider, naming `max_tokens` as the likely cause |
| 20 | An **analog** question typed into the digital endpoint, answered **VERIFIED** in green with a download button | the model wrote `RECT_OUT = AC`, `FILTER_OUT = AC`, `REG_OUT = AC` -- 12 V RMS waveforms as boolean signals -- the loop designed wires for it, and Logisim honestly confirmed that the wires compute the wires. Every claim in the chain was true; the result was worthless. "Verified" only ever meant *the circuit matches the spec*, and nothing downstream of the spec can notice the spec was a category error | `ohmwork/domain.py`: a deterministic screen before any model call (quoting the evidence it found), a refusal channel in the spec prompt, and a structural check that rejects a spec containing no logic at all. A refusal renders as its own thing, never as a failed solve |
| 21 | A 7447 question checked against the model's SPEC, exactly as a gate-level question is | for a question that names a chip, the spec IS the model's memory of a datasheet. It said BCD 0000 lights nothing; a real 7447 shows a nought. The circuit was right and the reference was wrong, so a correct answer failed — and had the recollection been wrong the other way, a wrong answer would have passed | `ohmwork/partcheck.py`: a bare part is evaluated first, in the same evaluator, and the design must reproduce THAT through its own wiring. `Solution.basis` says which reference was used, and the CLI, the UI and the manifest all render it — a part-verified answer and a spec-verified one are different claims |
| 22 | A generated bridge rectifier with a C-L-C filter, which SIMULATED fine and well inside its timeout | LTspice wrote a **335 MB** ASCII result for a 100 ms saved window, because the undamped LC rang and the solver took ever smaller steps. Nothing timed out -- the subprocess had already exited; what would not finish was parsing a third of a gigabyte. The run hung with no error and no ceiling | `simulate.check_raw_size`: a result too large to be about a circuit is refused, and the message says RINGING and what to change, because it is fed back to a design loop |
| 23 | An intent that read the real Q3 almost perfectly, including "load current waveform" | the only waveform kind measured V(net), so the load CURRENT came back as the load NODE's voltage -- reported under a name that says current, with nothing to catch it, because an observation carries no number to fail against | a `current_waveform` kind that takes a role and emits `I(RL)`, and a reading that prints WHAT each target is measured on, so a voltage under a current's name is visible to the one reader who can act on it |
| 24 | A priority-encoder solve, **verified in 1 attempt, externally, 32 of 32 rows** -- over a WRONG spec | the model wrote `Y0 = EN&(D3\|D1)` while its own note said "Priority order is D3 highest, then D2, D1, D0": with D2 and D1 both high the code reads 11 where priority says 10. The circuit implements the spec faithfully, Logisim confirms every row, and nothing mechanical downstream can notice -- this is the documented limit happening live, on an acceptance-set question, caught only because a person read the reading screen before the green badge | a prompt nudge was tried first and FAILED, measurably: the same model re-wrote the same wrong `Y0` and added a note *claiming* the masking the algebra does not do. So the defence is mechanical: `check_priority_encoder`, a deterministic gate that brute-forces whether ANY priority order explains the spec's own table, and feeds the differing rows back. The reading screen remains the only general defence, which is why it is unskippable |

### The two rules most of these converge on

**An unrun check must announce itself.** A check that can be skipped must report
that it was skipped, in the output, not merely decline to add a warning. Silence
is indistinguishable from a pass, and a reader looking at a clean screen cannot
tell whether everything was examined and found good or whether nothing was
examined at all — and the second feels like the first.

**And its mirror: a check that passed must say what it examined.** Any check
whose only output is a warning is invisible when it passes. That is the same
failure reflected, and it went unnoticed for as long as regime assertions had
existed (incident 13).

---

## 4. Verification limits

Stated here in full because they are the part of the design most worth
attacking. The user-facing version is in the README; this one names the
mechanism.

**The geometric round trip proves self-consistency, not correctness.**
`emitter.py` and `parser.py` both import `symbols.py`. A wrong pin offset makes
the emitter place a flag at the wrong coordinate and the parser look for it at
the same wrong coordinate. It passes. The only real ground truth is (a) the
real-file measurements and (b) a simulator successfully reading the emitted
file. Every symbol in the pin table therefore has a corresponding real-file
measurement test, and that suite is extended every time real files arrive.

**The coverage check has the same shape one level up.** Asks-vs-measurements
compares model output against model output: an extractor that drops "load
regulation" from *both* the asks and the measurements produces a clean coverage
screen. The defences that sit outside the system are the verbatim question text
— rendered in full at the top of the dry run, with word-level span coverage, so
unclaimed phrases are visible rather than invisible — and the real-file
measurements.

**A wrong-but-consistent misread of a question is invisible to simulation.**
`1.8k` read as `1.8M` simulates perfectly well and answers a different question
confidently. Nothing downstream can catch it. Only a human comparing the
extracted values against the original can, which is why the dry run prints them
back and why extraction must transcribe rather than summarise.

**The internal fallback computes both sides.** With Logisim absent,
`InternalLogicBackend` computes the result *and* anything the result would be
checked against. It declares `verification = "internal"`, measurements inherit
that, and any report containing internal results leads with why that is weaker.
No published library entry currently carries one, and a test asserts it.

**An independent implementation is not an independent evaluator.**
`exp8_gates.circ` is a circuit we did not write, and comparing our generated
answer against it catches a bug on either side — but through one evaluator it
catches nothing in the evaluator itself, because a broken evaluator breaks both
sides identically and they go on agreeing. The published truth table is
additionally checked against a spec oracle written from the question's own
wording: four lines, no gates, no netlist. That oracle is the only check in the
set that could catch the gate network implementing the wrong *function*.

**A verified answer says WHAT it was checked against, because there are two
answers.** A gate-level question is checked against a specification the model
read from the question's words. A question naming a part is checked against the
PART: a bare one is evaluated first, and the design must reproduce that through
its own wiring (`ohmwork/partcheck.py`). The second exists because the first is
backwards for an IC — the spec there is the model's memory of a datasheet, and
verifying a chip against a recollection can fail a right answer and pass a wrong
one. Both bases have the SAME hole, and it is stated in the output rather than
implied: neither can prove that what it checked against is the right reading of
the question. The spec basis answers that with printed algebra; the part basis
with a printed wiring map. One slice of it is closed mechanically — a signal
whose own name names a pin of the part must be on that pin, which is a
constraint from outside the wiring and so is not self-confirming.

**Analog verification is WEAKER than digital, and nothing may blur that.** A
digital answer is checked against an exhaustive truth table: every row
reproduced by an outside tool, nothing left over. Analog has no such table, so
`intent.py` checks the three things that exist — the circuit converged, its
devices stayed in the operating regimes the results depend on, and the numbers
the question NAMED came out where the question said they should. Two gaps
follow, and both are printed with every analog result:

* the same misreading gap the other bases have — the intent is the model's
  reading of the question, so the reading is output;
* one with **no digital counterpart: meeting a target is not being a good
  design.** A regulator that hits 9.00 V while dissipating six watts in the
  pass transistor, or with ripple nobody asked about, satisfies every check
  here. Correct truth-table rows really are the whole answer; correct
  measurements are not.

Three defences keep the analog check from becoming decorative. A tolerance is
CAPPED (`MAX_TOLERANCE_PCT`), because one wide enough to admit any plausible
circuit cannot fail. An intent with no targets at all is refused — the analog
shape of `check_spec_has_logic`. And the regime assertions are DERIVED from
the parts list rather than requested, so a design cannot omit the one that
would have failed. A target the question gave no number for is an
OBSERVATION: measured, reported, and counted separately, because a run in
which nothing COULD fail must not read like one in which nothing did.

**The part basis predicts with our own gate logic, deliberately.** Steps 3 and 4
both read the same nets, so they agree about a swapped signal; what they do not
share is an implementation. `partcheck.GATE_LOGIC` is written in this repo and
imported from nowhere, Logisim's is Logisim's, and a disagreement is evidence
about the emitter, the router, the pin table or the gates. It is not a
replacement for the deferred `.circ` parser round trip — it is stronger, for the
same reason `--tty table` is: the other side of the comparison is a tool we did
not write.

**The `.plt` file has no machine check of any kind.** Batch mode does not read
plot files, so nothing can verify what LTspice renders from one. Its only check
is a human looking at it once. It is therefore **frozen**: strictly a
transcription of the shape of a vendor file shipped with LTspice, and it must
not grow — the moment it encodes plot styling opinions it becomes unverifiable
surface area that silently rots. Every `.plt` ships labelled UNVERIFIED.

**Generalised: an artefact with no machine verification path must stay minimal,
and must state its unverifiability wherever it ships.**

---

## 5. Deferred, and why

Deferring these is a scope decision, not a lowered standard. Each is listed with
what unblocks it.

| item | why deferred |
|---|---|
| the geometric `.circ` parser | external verification is strictly stronger than a round trip through our own code — Logisim evaluating the emitted file catches a geometry bug that an emitter and parser sharing a pin table would agree on. The parser earns its place for check-mine mode and for reading foreign files, not for checking our own output. `LogisimTarget.round_trip` therefore returns `ran=False`, and its reason names what stands in its place |
| Logisim Evolution's native geometry, and the 7447 | blocked on a **fixture**. Every `.circ` here is 2.7.1, and no component's geometry may be added without a real file containing it. The verbatim question text for the 7447 question is in hand; the format evidence is not |
| mirrored placements | the emitter never emits them and human-drawn input is out of scope. The parser refuses them rather than guessing. The derivation is a 20-minute job by the same method when needed |
| a layout engine | neither target has enough real examples yet to say what a placer would have to generalise over. What v1.0 owes a student is a correct file and an honest label saying the layout is mechanical |
| the vision / extraction layer | the seam exists (`llm.py`, `extract.py`) and text extraction works. Image extraction is blocked on provider availability — the free tier measured for this project served no multimodal model at all, which is precisely why `--list-models` and a self-correcting model error were built before any key existed. The provider pool added since can include vendors whose free tiers do serve a multimodal model; whether one of them can read a photographed lab-manual page is UNMEASURED here, and stays deferred until it has been |

---

## 6. Device models: why anchored cards

Worth its own section because two rounds of this project were spent on it under
a wrong diagnosis.

Two simulators disagreed by about 0.4 V on the same circuit with the same model
card. The card was `D(BV=8.3 N=1.2)`. In SPICE, `BV` is the voltage at which
reverse current equals `IBV`, and `IBV` defaults to 1 mA — while a datasheet
Vz is quoted at a test current, usually 5 mA. So that card does not describe an
8.3 V zener at all, and each simulator filled in the unspecified knee its own
way. With `D(BV=8.3 IBV=5m)`:

```
LTspice 26.0.2.1:  vb = 8.292139
ngspice:           vb = 8.292262
```

123 µV apart, on two independently implemented simulators.

**When two simulators disagree, suspect an under-specified device before
suspecting the simulators.** A fully specified model is portable; defaults are
not.

The policy that came out of it (`parts.py`), in priority order, with the choice
and the reason reported in the output every time:

- **(a)** the question names a part — that library part, no substitution
- **(b)** the question specifies a parameter with no part ("Vz = 8.3 V", the
  common lab-manual case) — synthesise a model anchored at exactly that value.
  A nearby real part would silently answer a slightly different question
- **(c)** the question is vague ("a zener") — nearest real part, substitution
  reported

Never silently pick.

---

## 7. Testing conventions

- **Write the test first**, and put the spec in the test file.
  `tests/test_analysis.py` and `tests/test_prose.py` carry their layer's
  contract in a module docstring; those files are the specification.
- **Never pin a plausible number.** Every simulation-derived expected value is a
  `Baseline` in `tests/baselines.py` carrying backend + version, date, and the
  generating command. A bare float as an expected simulation value is banned.
  Never backfill provenance you are not certain of — re-measure or delete.
- **Geometry tests import no ohmwork code.** `tests/test_logisim_geometry.py`
  measures the fixtures directly, so it is evidence about the *format* rather
  than agreement with our own table. A separate test asserts our table matches
  it.
- **Fixtures are evidence, including their bytes.** `.gitattributes` disables
  end-of-line conversion on them, because the line-ending fact above is one a
  helpful normalisation would destroy.
- **A test that cannot fail is worth nothing.** The Logisim acceptance
  comparison was deliberately broken four ways — removing the inverter, turning
  an OR into an AND, ungating the enable, swapping which pin drives which net —
  and all four were caught. Worth knowing for the next person: swapping two
  *nets'* contents between their dict keys is a no-op, so a mutation showing no
  difference is not automatically evidence of a weak test.
