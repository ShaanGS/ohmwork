# Test fixtures

Real files, not synthesised ones. The point of every file here is that
nobody on this project drew it, so it can catch an error that a
self-consistent round trip cannot. See CLAUDE.md, "Verification limits".

## REDACTED: personal data removed 2026-08-24

These files were drawn by students who put their name and registration
number on the canvas. Those elements were removed before the files were
committed. **Nothing else was changed** — each file was split on its own
line ending, the offending lines dropped, and the remainder rejoined
byte-for-byte. No wire, symbol, component, attribute or coordinate was
touched, and the geometric measurements are unaffected because a Text
element has no ports.

| file | removed | at |
|---|---|---|
| `logisim/priority_plexers.circ` | `<comp name="Text">` x2 | (376,274), (372,258) |
| `logisim/adder_subtractor.circ` | `<comp name="Text">` x2 | (463,115), (210,116) |
| `ltspice/handdrawn_pn_forward.asc` | `TEXT` comment x2 | (704,56), (712,88) |
| `ltspice/handdrawn_pn_reverse.asc` | `TEXT` comment x2 | (664,168), (672,208) |
| `ltspice/handdrawn_series_regulator.asc` | `TEXT` comment x1 | (288,96) |
| `ltspice/handdrawn_voltage_multiplier.asc` | `TEXT` comment x1 | (576,56) |

`logisim/exp8_gates.circ` contained no Text elements and is unredacted.

Non-personal annotations were KEPT, because they are part of what a real
file looks like: the `!.dc` / `!.step` / `!.op` / `!.tran` directive lines,
and the `;PN FORWARD`, `;PN REVERSE`, `;load regulation` comments.

The coordinates above are retained deliberately. They are the evidence for
one measured fact — **Text is the only element type not on the 10-unit
grid**, because the text tool places freely while components and wires
snap. All six removed elements are off-grid; every surviving component in
every fixture is on it. Nothing else depended on those elements.

If more real files are added, redact the same way and extend the table.
The originals live outside the repo and are not ours to publish.

## logisim/

Hand-drawn Logisim **2.7.1** files. Not Logisim Evolution — see CLAUDE.md.

- `exp8_gates.circ` — gate-level 4-to-2 priority encoder with enable and
  valid, primitives only. Completely explains the measured pin table: 33
  ports, 33 dead-end wire endpoints, identical sets.
- `priority_plexers.circ` — the same question solved with the built-in
  Plexers Priority Encoder. The signature `primitives_only` must reject.
- `adder_subtractor.circ` — 4-bit adder/subtractor, and a deliberately
  imperfect file: one unwired XOR input, every pin declared as an input,
  and 26 dead-end stubs hidden inside component bodies.
- `shuffled_libs.circ` — DERIVED, not hand-drawn. `priority_plexers.circ`
  with its `<lib>` indices permuted, so a parser that hardcodes `lib="2"`
  as Plexers fails while one that resolves through the `<lib>` block
  passes. The only fixture here that was edited for a purpose beyond
  redaction; that is why it is named for what it is.

## ltspice/

Hand-drawn `.asc` files by three different students. Between them they
exercise `res`, `cap`, `voltage` and `diode` at R0, R90 and R270.

`handdrawn_voltage_multiplier.asc` contains the byte `0xB5` (a micro sign
in cp1252, from `100µ`). It is deliberately not UTF-8-clean: it is the
regression fixture for `ohmwork.parser` reading real files.

## Other

`mini.bjt`, `mini.dio` — verbatim entries copied from LTspice's own
component libraries. See `test_parts.py`.
