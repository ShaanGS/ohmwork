# Q6 (Exp 10.6) — verbatim question text

Source: lab manual PDF "10. DESIGN AND IMPLEMENTATION OF MULTIPLEXER AND
DEMULTIPLEXER USING LOGIC GATES", page 11, section "10.6 Open-ended
Question". Received 2026-09-02 as a PDF; text extracted with pypdf and
checked by eye against the page.
Extractor: pypdf text layer + human check.

## Verbatim

> 1. How many 4:1 multiplexer is required to construct a 16:1 multiplexer?
> Validate your answer by designing and simulating a 16:1 multiplexer using
> 4:1 multiplexer in Logisim.

## Transcription notes

- "multiplexer is required" (singular verb) and "using 4:1 multiplexer" (no
  plural) are as printed.
- The section heading is "Open-ended Question" with a hyphen; Exp 4's is
  "Open ended Question" without. The domain screen and any corpus matcher
  must accept both.

## What a solve must produce

Two things: the NUMBER (five: four in the first stage, one in the second),
and a circuit that uses 4:1 MULTIPLEXERS as parts. This is not a gate-level
question. Building the 16:1 from AND/OR/NOT answers a neighbouring question,
the same way a 7447 question is not asking for an equivalent built from gates
(design.py, NAMED_PARTS_RULE).

Two walls, measured 2026-09-02 through the real loop:

1. The design vocabulary has no multiplexer part. Logisim's Plexers library
   has one (`Multiplexer`, `select` bits = 2 for a 4:1), and its port
   geometry has never been measured here. The exact-pin evaluator probe that
   measured the gate families (tests/test_logisim_gates.py) is the method.
2. 16 data + 4 select = 20 inputs, so an exhaustive table is 2^20 = 1,048,576
   rows. `spec.MAX_INPUTS` is 20, so the loop attempted it; the free model's
   design JSON failed the provider's own validation three times out of four,
   and had it succeeded, Logisim would have been asked for a million rows.
   How long that takes is being measured; the honest options are an
   exhaustive check if it is fast enough, or a refusal above a smaller input
   count that says why.
