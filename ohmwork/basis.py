"""What a verified circuit was actually checked AGAINST.

There is more than one answer to that, and they are different claims:

    spec    the circuit computes the boolean specification read from the
            question's words. An exhaustive table, checked row by row.
    part    the circuit reproduces a named chip's OWN measured behaviour
            through its wiring. The chip was probed, not recalled.
    intent  the circuit meets the numeric targets the question stated, its
            regime assertions held, and it converged. Analog has no oracle,
            so this is the weakest of the three and says so.

A reader who cannot tell them apart has been shown the strongest one by
default, so the basis travels with every solution and the CLI, the web
payload and the published manifest all render these strings rather than each
inventing its own wording.

The constructors live beside what they describe -- `spec_basis` and
`part_basis` in `partcheck.py`, `intent_basis` in `intent.py`. Only the shape
lives here, so that nothing has to import a module about seven-segment
displays to describe an analog result.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Basis:
    """One statement of what a result was checked against, and what it is not."""

    #: "spec" | "part" | "intent". The machine-readable half, for a manifest.
    kind: str
    #: One line: what the circuit was required to reproduce, and who computed
    #: the reference.
    headline: str
    #: The thing a human is asked to check, because nothing downstream can.
    reading: str
    #: What this basis cannot prove. Never empty: a claim with no stated edge
    #: reads as a claim with none.
    limit: str
    #: The reading flattened to one line, for a design note. The manifest's
    #: design_notes are single-line `choice` strings, and flattening at the
    #: source keeps the published note and the printed reading from being two
    #: independently-written accounts of the same fact.
    summary: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "headline": self.headline,
                "reading": self.reading, "limit": self.limit,
                "summary": self.summary}
