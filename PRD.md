# Ohmwork — product requirements

Decided 2026-08-26. This document steers the work; `ARCHITECTURE.md` explains
how the machine is built and `README.md` is what a stranger reads.

---

## 1. The end goal, in one paragraph

An ECE student at SRM opens **Ohmwork**, types the open-ended question printed
at the end of their lab experiment, and gets back three things: the circuit
file to open in LTspice or Logisim, the numbers or truth table the question
actually asks for — computed by that simulator running **the exact file they
were handed** — and a plain statement of what was checked and what was not.
It runs on their own laptop, against their own free model key, and it works
for both the analog and the digital experiments.

**Done means:** a classmate who has never seen the repository installs one
file, adds one key, types a question from their manual, and gets a correct,
checkable answer — without asking you anything.

---

## 2. Who it is for

The ECE batch at SRM doing the electronics lab. Order of 20–100 people, all
working through the same set of experiments, all with LTspice already
installed because the lab requires it.

That audience is narrow on purpose and it decides several things:

- **Windows first.** The lab runs Windows and LTspice is installed there.
  macOS is supported for the digital half; see §7 for why analog on macOS is
  not claimed.
- **The four experiments we have real questions for are the acceptance set.**
  Exp 2 (series regulator), Exp 3 (regulated supply), Exp 8 (priority
  encoder), Exp 9 (BCD to seven-segment). All four are solved today.
- **Unsigned builds are not good enough.** Asking twenty classmates to click
  through a SmartScreen warning is asking them to learn a bad habit.

## 3. The claim, and why it is not ChatGPT

Anyone can ask a language model for a circuit. It will answer confidently and
sometimes correctly, and the student has no way to tell which.

Ohmwork's claim is narrower and checkable:

> **Every number and every row comes from a simulator that ran the file you
> are downloading. And the tool tells you what it did not check.**

Both halves matter. The first is what makes an answer worth reading; the
second is what makes the tool honest when it is wrong. A tool that verifies
and then hides its limits has replaced one confident guess with another.

The whole design follows from that: the model writes only *intent* (boolean
expressions, or numeric targets), Python derives everything derivable, an
outside simulator judges the emitted file, and a mismatch is fed back and
retried rather than smoothed over.

## 4. What it is not

- **Not a homework-answer service.** These are ungraded self-study questions.
  The explanation is the point, and the output is built to be read, not
  copied.
- **Not a general circuit simulator.** It refuses what it cannot honestly
  answer: sequential logic, parts whose geometry has never been measured, and
  anything outside the two targets.
- **Not a replacement for opening the file.** The deliverable is a real
  `.asc`/`.circ` the student opens in the real tool. Waveform questions are
  answered by them looking at waveforms.

---

## 5. User flow

### 5.1 First run

1. **Install.** One signed installer. Windows `.exe`, macOS `.dmg`.
2. **Open.** No account, no login, no server.
3. **Add a key.** Key icon → paste one or more free provider keys → save. The
   app restarts and is ready. Keys are encrypted by the OS and never leave
   the machine except as requests to that provider.
4. **Analog check.** The app looks for LTspice. If it is missing, the analog
   half is disabled with a message naming the download — it does not pretend.

Logisim ships **inside** the installer, so the digital half works with no
setup at all.

### 5.2 Every use

1. Type or paste the question.
2. **The reading appears first** — what the tool understood, in amber, before
   any answer. Boolean expressions for a digital question, a target table for
   an analog one. This screen is not skippable and not collapsible.
3. Rejected design attempts stream as they happen, each with why.
4. The answer arrives: the table or the measured numbers, what evaluated it,
   what it was checked against, and **what that does not establish**.
5. Download the circuit file. Open it in LTspice or Logisim.

### 5.3 When it cannot answer

Three distinct outcomes, rendered differently on purpose:

| outcome | means | shown as |
|---|---|---|
| **refused** | out of domain — sequential, an unmeasured part, wrong half | amber, with the evidence quoted from the question |
| **unavailable** | no model provider could be reached | neutral, with when the earliest one wakes |
| **failed** | it tried and could not produce a design that passed | red, with every attempt and its reason |

"The loop tried and could not" and "the loop should never have tried" are
different facts about a question. Collapsing them tells someone to rephrase
when the answer is "use the other tool".

---

## 6. The output contract

What a student receives, by question type. This is the definition of "proper
output" and it is the same in the app, the CLI and the published library.

**Every answer, always:**

- the **reading** — what was understood, before the answer
- the **basis** — what it was checked against, naming the evaluator
- the **limit** — one sentence on what that does not establish
- the **file** — and an honest note on what the file's own bytes prove

**A digital question additionally gets:** the full truth table, every row
computed by Logisim Evolution 4.1.0 from the emitted `.circ`; for a question
naming a chip, the wiring map that was checked against the chip's own probed
behaviour.

**An analog question additionally gets:** each measured quantity with its
value and units; the regime assertions and whether they held; a clear split
between targets that carried a number and quantities merely reported; and a
`.plt` so the waveforms are already plotted when the file opens — labelled
UNVERIFIED, because nothing can machine-check a plot file.

---

## 7. Where we are, and what v1 still needs

### Working today, verified by running it

| | |
|---|---|
| digital design loop | question → verified circuit, proven in a browser |
| IC questions | verified against the chip's own probed behaviour |
| analog design loop | question → `.asc` meeting the stated intent, proven on the CLI |
| domain routing | analog vs digital, from the question's words, with the reason shown |
| desktop shell | Electron, loopback-only, per-launch password, OS-encrypted keys |
| the container | proven green in CI — kept for the hosted digital option |

### The gaps between here and v1

| # | gap | why it blocks v1 |
|---|---|---|
| 1 | ~~Logisim is not in the installer~~ **CLOSED 2026-08-30**: the pinned 4.1.0 JAR + a jlink runtime ship in the installer, built and verified by `desktop/fetch-logisim.ps1`; a packaged app missing its bundle refuses to start | measured: with no Logisim the backend falls back to an engine that raises on the first solve. Every question fails and the app looks installed |
| 2 | ~~The analog loop is not wired into the request path~~ **CLOSED 2026-08-30**: the endpoint routes on `domain.classify`, and an analog answer arrives as its own `measured` event — a deliberately weaker claim than `verified`. Proven live in a browser, both halves | `server.py` called `design.solve` only. Half the lab's experiments are analog |
| 3 | ~~No LTspice detection or first-run guidance~~ **CLOSED 2026-08-30**: `/api/status` reports keys (names only), evaluators, and LTspice presence; the intro screen says plainly what is missing — or one muted "ready" line when nothing is | analog needs the student's own LTspice. Silence here becomes a confusing failure at solve time |
| 4 | ~~A 30–120 s analog solve has no UI~~ **CLOSED 2026-08-30** with gap 2: routing, the reading, and each attempt stream as they happen, and the working line says an analog solve takes minutes | a transient run is far slower than a truth table. The page must show progress or it reads as hung |
| 5 | **Nothing is signed** | twenty classmates × one SmartScreen warning is twenty people learning to ignore warnings |
| 6 | **You must type the question** | the questions are printed in a manual. Typing a paragraph is the single biggest friction in the flow |

### macOS and analog: not claimed, and here is why

LTspice exists for macOS, but `locate_ltspice` searches Windows paths only,
and **no baseline in this project has ever been measured on macOS**. This
project does not pin numbers it has not measured, so the Mac build ships with
the digital half and refuses analog with a message that says exactly that.
Lifting it is a measurement job, not a coding one — and it is worth doing only
if enough of the batch is on macOS.

---

## 8. v1 scope, frozen

**IN.** Windows and macOS desktop app; digital half everywhere; analog half on
Windows; Logisim bundled; LTspice detected; signed installers; the four
acceptance experiments; bring-your-own-key.

**OUT, and do not start these.**

- **Question capture from a photo.** The vision layer is the obvious next
  feature and it is deliberately v1.1: it is a whole extraction-and-review
  problem, and a misread value is invisible to every check downstream.
- **A hosted website.** The container is proven and can be revived any time,
  but hosting reintroduces the shared-quota problem that bring-your-own-key
  dissolves, and it can never run the analog half.
- **Auto-publishing solves into `library/`.**
- **Sequential logic**, and any part whose geometry is unmeasured.
- **A layout engine.** Generated schematics stay mechanical and say so.

## 9. Ordered plan

Each item is placed where it is because of what it unblocks, not by size.

1. **Bundle Logisim + a `jlink` runtime.** Gap 1. Nothing else matters if the
   installed app cannot verify anything. Pinned to 4.1.0, because that is the
   evaluator every published number was measured against.
2. **Wire the analog loop into `server.py`.** Gap 2, and the only real feature
   work left. `domain.classify` and `analog.solve_analog` exist and are
   tested; what does not exist is a request path, a streamed progress shape
   for a long simulator run, and a result renderer for measured numbers.
   Carries gap 4 with it.
3. **First-run checks.** Gap 3. LTspice present? Key present? Say so plainly
   on the screen where it matters, once.
4. **Solve all four acceptance experiments through the installed app**, on a
   machine that is not this one. This is the honest test of everything above.
5. **Sign and notarize.** Gap 5. Boring, and the difference between a repo and
   a product.
6. **Hand it to one classmate and watch, without helping.** Every place they
   hesitate is a defect.

## 10. Acceptance criteria

v1 ships when all of these are true on a machine that has never had the
repository on it:

- [ ] The Windows installer runs and the app opens without a warning dialog.
- [ ] With no key configured, the app says so and tells you where to get one.
- [ ] With one free key, **Exp 8** (priority encoder) returns a 32-row table
      Logisim computed, and the downloaded `.circ` opens in Logisim.
- [ ] **Exp 9** (7447) returns 16 rows with the wiring map, verified against
      the chip's own probed behaviour.
- [ ] With LTspice installed, **Exp 2** returns an output voltage and a zener
      current LTspice measured, with both regime assertions held.
- [ ] **Exp 3** returns the five waveform quantities and states plainly that
      no numeric target was checked.
- [ ] Without LTspice, an analog question is refused with a message naming the
      download — never answered.
- [ ] Every answer shows the reading before the result, and the limit after
      it.
- [ ] The app quits without an error dialog, and leaves no process behind.

## 11. Risks

| risk | severity | what stands between us and it |
|---|---|---|
| The tool misreads the question and everything downstream agrees | **highest** | the reading screen, shown before the answer and impossible to skip. Nothing else can catch this |
| An analog answer is read as strongly as a digital one | high | the basis and its limit ship with every result, in different words for each |
| A free key's rate limit makes the app feel broken | medium | one key per student rather than one shared key; `unavailable` is its own outcome and says when to come back |
| A question outside the four experiments gets a poor answer | medium | the domain screen refuses what it cannot do; the design loop raises rather than returning an unverified circuit |
| Bundling a JRE bloats the installer | low | ~90 MB total. Acceptable for a one-time install |
| macOS analog silently differs from Windows | medium | not claimed at all until measured. See §7 |

---

## 12. How we will know it worked

Not downloads. **A classmate uses it for an experiment you did not help them
with, and the answer they get is right — and when it is not right, they can
tell.** That second half is the entire reason the reading screen and the
limits exist, and it is the thing no other tool in this space offers.
