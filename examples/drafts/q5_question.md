# Q5 (Exp 4.7) — verbatim question text

Source: lab manual PDF "4. DESIGN OF CLIPPING CIRCUIT USING PN DIODE",
page 14, section "4.7 Open ended Question". Received 2026-09-02 as a PDF;
text extracted with pypdf and checked by eye against the page.
Extractor: pypdf text layer + human check.

## Verbatim

> (i) Design and simulate a positive clamper circuit in LT spice using an
> input voltage of 10𝑉𝑝𝑝 and a frequency of 1 𝑘𝐻𝑧. Observe the input and
> output waveforms and determine the DC level shift.
> (ii) Design a biased positive clamper circuit in LT spice using an 12𝑉𝑝𝑝,
> 1 𝑘𝐻𝑧 input signal and a bias voltage of +2𝑉. Observe how the bias voltage
> changes the DC level of the output waveform

## Transcription notes — flagged, not silently normalised

- The quantities are set in MATHEMATICAL ITALIC Unicode (`𝑉𝑝𝑝`, `𝑘𝐻𝑧`, `𝑉`,
  U+1D449 etc.), which is how the PDF's equation font came through. A student
  typing this will write `10Vpp`, `1kHz`, `+2V`. Both forms must route and
  read the same; the italics are kept here as the page has them.
- "LT spice" has a space in the source (twice). Keep it: the domain screen
  must recognise it.
- "using an 12𝑉𝑝𝑝" — the article is "an" in the source.
- No period after "output waveform" at the end of (ii).

## What a solve must produce

Two circuits, not one: the unbiased clamper (i) and the biased clamper (ii),
each with its own input and output waveforms. The 2026-09-02 live run read
the question correctly (two sets of targets, `vin/vout` and `vin2/vout2`)
and then designed ONE circuit with both outputs on the same net — a
plausible design that answers half the question. The reading exposed it;
nothing mechanical caught it.

"Determine the DC level shift" is a number the student is asked to find, not
one the question states, so it is REPORTED, not checked. The right statistic
is (max + min)/2 of the output waveform, or its mean for a symmetric input;
today only the mean exists as a target kind.
