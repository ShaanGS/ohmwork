# Q4 (Exp 9.7) — verbatim question text

Filed here rather than as a question JSON because the schema cannot yet
express it: it needs Logisim Evolution components (7447, seven-segment
display) whose pin geometry has not been derived from a real file. See
CLAUDE.md, "Q4 is BLOCKED ON FIXTURE".

Source: lab manual, section 9.7 Open-ended Question (screenshot).
Extractor: human transcription from screenshot.

## Verbatim

> Using Logisim Evolution, design a BCD-to-seven-segment display circuit using
> the 7447-decoder IC. Connect the decoder outputs to a seven-segment display
> and test all 16 possible 4-bit input combinations. Record the segment pattern
> displayed for each input and identify which codes correspond to valid BCD
> digits (0–9) and which correspond to invalid BCD codes (10–15).
> **Caution:** The 7447 decoder has active-low segment outputs; therefore, a
> logic 0 turns a segment ON, while a logic 1 turns it OFF. Account for this
> active-low operation while wiring the circuit and interpreting the segment
> patterns

## Transcription notes — flagged, not silently normalised

- The digit ranges use EN DASHES in the image: `(0–9)`, `(10–15)`. A typed
  transcription supplied them as hyphens. The image is kept.
- The final sentence ends WITHOUT a period after "patterns" in the image; a
  lone "." appears on the following line, which reads as a layout artefact of
  the PDF rather than part of the sentence. Kept as it appears. If this is
  ever the difference between an ask matching and not matching, re-check the
  original.

## Asks (not yet mapped, since nothing can answer them)

- "test all 16 possible 4-bit input combinations"
- "Record the segment pattern displayed for each input"
- "identify which codes correspond to valid BCD digits (0–9) and which
  correspond to invalid BCD codes (10–15)"

## Why it is blocked

The question text is NOT the missing piece. What is missing is format
evidence: Evolution's TTL and I/O libraries are absent from 2.7.1, and the
7447 and seven-segment component geometry cannot be derived, guessed, or
tested without a real Evolution-saved file. One such file unblocks it.
