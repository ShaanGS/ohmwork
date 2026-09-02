"""Targets: what a circuit is FOR, and therefore how it is checked.

WHY THIS EXISTS. Probing Q2 against the input gate showed the problem is not
a missing schema key. `load_question` unconditionally ran
`parse_asc(emit(circuit))` — the LTspice emitter and the `.asc` geometric
parser — plus the LTspice device policy and a check for a net named `0`.
Those are not LTspice-flavoured, they are LTspice-SEMANTIC. A Logisim circuit
has no ground net, no SPICE devices, and no `.asc` to round-trip through, and
it was being told its components were "not in the verified pin table" —
meaning LTspice's table.

Threading a flag through those checks would have spread the assumption rather
than removed it. So a Target owns its own:

  - component vocabulary (which types exist, and what their pins are called)
  - structural rules (LTspice needs a ground; Logisim needs safe labels)
  - emitter and geometric parser, hence its own round trip
  - device policy, or none
  - evaluation backend, and the verification standing that comes with it
  - error vocabulary, so a message names the right table

`load_question` selects the target FIRST and then runs only that target's
chain.

SCOPE NOTE, because this looks like a reversal and is not. CLAUDE.md says do
not build an abstraction for Logisim Evolution's dialect. That still stands:
it is about anticipating a variant of a target we cannot test. This is two
targets that both exist, are both required, and are both already in the repo.
"""

from dataclasses import dataclass, field

from ohmwork import logisim_symbols, symbols
from ohmwork.emitter import CircuitError, emit
from ohmwork.parser import ParseError, parse_asc


class TargetError(Exception):
    """A circuit does not satisfy the rules of the target it declares."""


class UnknownTargetError(TargetError):
    pass


@dataclass(frozen=True)
class RoundTrip:
    """The outcome of emit-then-parse, or an honest statement that it did not run."""

    ran: bool
    reason: str = ""            # why not, when ran is False


class LTspiceTarget:
    """Analog. External evaluator, full round trip, SPICE semantics."""

    name = "ltspice"
    extension = ".asc"
    deliverable_kind = "ltspice-schematic"
    #: SPICE needs a reference node. This is the check that fired on Q2 and
    #: proved the gate was target-locked.
    requires_ground = True
    uses_device_policy = True
    #: LTspice identifies a component by its InstName, which is the ref.
    extra_component_keys = frozenset()

    def known_types(self):
        return set(symbols.SYMBOLS)

    def describe_unknown_type(self, ref, type_name):
        return (f"{ref} has unknown type {type_name!r}: not in the verified "
                f"LTspice pin table. Known types: "
                f"{', '.join(sorted(self.known_types()))}")

    def pin_names(self, type_name):
        return [p.name for p in symbols.pins_of(type_name)]

    def check_labels(self, circuit):
        return []                       # LTspice has no label rewriting

    def check_constraints(self, circuit, constraints):
        if constraints.get("primitives_only"):
            return ["constraints.primitives_only has no meaning for an "
                    "LTspice circuit: there is no library-component shortcut "
                    "to ban"]
        return []

    def backend(self):
        from ohmwork.simulate import LTspiceBackend
        return LTspiceBackend()

    def round_trip(self, circuit) -> RoundTrip:
        """Emit, parse the emitted text back, and require identical nets."""
        try:
            recovered = parse_asc(emit(circuit))
        except (CircuitError, ParseError) as e:
            raise TargetError(str(e)) from None
        if {n: sorted(p) for n, p in recovered["nets"].items()} != {
            n: sorted(p) for n, p in circuit["nets"].items()
        }:
            raise TargetError(
                "emit/parse round trip changed the netlist — emitter bug, "
                "do not proceed"
            )
        return RoundTrip(ran=True)


class LogisimTarget:
    """Digital. Emitter built and externally verified; geometric parser not.

    Everything this target CAN check, it checks. What it does not do is say
    nothing about what it skipped — an unrun round trip that looked like a
    passed one would be the worst possible failure here.
    """

    name = "logisim-2.7.1"
    extension = ".circ"
    deliverable_kind = "logisim-circuit"
    requires_ground = False             # a Logisim circuit has no ground net
    uses_device_policy = False          # gates take neither value nor part
    #: A Logisim Pin carries a LABEL distinct from our ref, and that label is
    #: what appears as a column heading in --tty table output. It is
    #: load-bearing, so it is part of this target's component vocabulary.
    extra_component_keys = frozenset({"label"})

    #: question-JSON type name -> (Logisim component name, discriminating attrs)
    #: Routed through logisim_symbols so an unmeasured shape hard-fails rather
    #: than being interpolated.
    TYPE_MAP = {
        "input_pin":  ("Pin", {}),
        "output_pin": ("Pin", {"output": "true", "facing": "west"}),
        "not":        ("NOT Gate", {}),
        # Gate families measured 2026-09-02 with Evolution as the instrument
        # (logisim_symbols.GATE_INPUT_X). 2, 3, 4 and 8 inputs: what a
        # NAND-only adder, a 4:1 mux (AND3) or an 8:1 mux (AND4 + OR8) need.
        # XOR/XNOR stay 2-input; Evolution's multi-input XOR semantics are
        # not what a textbook means and are not modelled here.
        "and2":       ("AND Gate", {"inputs": "2"}),
        "and3":       ("AND Gate", {"inputs": "3"}),
        "and4":       ("AND Gate", {"inputs": "4"}),
        "and8":       ("AND Gate", {"inputs": "8"}),
        "or2":        ("OR Gate", {"inputs": "2"}),
        "or3":        ("OR Gate", {"inputs": "3"}),
        "or4":        ("OR Gate", {"inputs": "4"}),
        "or8":        ("OR Gate", {"inputs": "8"}),
        "nand2":      ("NAND Gate", {"inputs": "2"}),
        "nand3":      ("NAND Gate", {"inputs": "3"}),
        "nand4":      ("NAND Gate", {"inputs": "4"}),
        "nand8":      ("NAND Gate", {"inputs": "8"}),
        "nor2":       ("NOR Gate", {"inputs": "2"}),
        "nor3":       ("NOR Gate", {"inputs": "3"}),
        "nor4":       ("NOR Gate", {"inputs": "4"}),
        "nor8":       ("NOR Gate", {"inputs": "8"}),
        "xor2":       ("XOR Gate", {"inputs": "2"}),
        "xnor2":      ("XNOR Gate", {"inputs": "2"}),

        # Logisim Evolution parts, measured 2026-08-26. NOT primitives: they
        # come from #TTL and #I/O, so a question declaring primitives_only
        # rejects them -- which is the first time that check has had
        # anything real to reject.
        "ttl7447":       ("7447", {}),

        # THE DISPLAY'S POLARITY IS ALWAYS WRITTEN, NEVER DEFAULTED -- the
        # Constant doctrine below, learned again the hard way as issue #1:
        # a 7447's active-low outputs wired straight to a display whose
        # `active` attribute was left to Logisim's default (true: a segment
        # lights on 1) rendered every digit as its photographic negative,
        # while the solve reported verified -- truthfully, because only the
        # output PINS are in the verified table and the display is not.
        # Attribute name and default from Logisim Evolution 4.1.0's own
        # source (std/io/SevenSegment.java), confirmed behaviourally by the
        # incident. Two types so the polarity is the MODEL'S EXPLICIT
        # CHOICE, disclosed in the wiring map and checked by
        # partcheck.polarity_conflicts.
        "seven_segment": ("7-Segment Display", {"active": "true"}),
        "seven_segment_active_low":
            ("7-Segment Display", {"active": "false"}),

        # Hold a wire at a fixed level without adding an input pin. An input
        # pin would double the truth table, and a part's control pins are not
        # part of the question's input space -- the 7447's three would turn
        # 16 rows into 128. The value is always written out: Logisim's own
        # default for a Constant is 1, and relying on a default that a file
        # does not state is how a circuit means something other than it says.
        "high":          ("Constant", {"value": "0x1"}),
        "low":           ("Constant", {"value": "0x0"}),
    }

    def known_types(self):
        return set(self.TYPE_MAP)

    def describe_unknown_type(self, ref, type_name):
        return (f"{ref} has unknown type {type_name!r}: not a Logisim "
                f"component this build can place. Known types: "
                f"{', '.join(sorted(self.known_types()))}. Adding one "
                f"requires its geometry measured from a real .circ.")

    def pin_names(self, type_name):
        name, attrs = self.TYPE_MAP[type_name]
        return [p.name for p in logisim_symbols.ports_of(name, attrs)]

    def is_source(self, type_name, pin) -> bool:
        """Does this port DRIVE its net?

        A Pin's geometry is one port at its loc whichever way it faces, so its
        direction comes from the `output` attribute and NOT from the measured
        table -- which is why logisim_symbols records every Pin port as "in"
        and refuses to guess. An input Pin drives the circuit; an output Pin
        listens to it. For everything else the measured `kind` decides.
        """
        if type_name == "input_pin":
            return True
        if type_name == "output_pin":
            return False
        name, attrs = self.TYPE_MAP[type_name]
        ports = {p.name: p for p in logisim_symbols.ports_of(name, attrs)}
        return ports[pin].kind == "out"

    def check_labels(self, circuit):
        """Every emitted label must survive Logisim unchanged.

        Logisim rewrites labels to VHDL-safe names and appends a hash we
        cannot reproduce: a pin labelled "E IN" comes back as
        "E_IN_ef467da7". Reading a foreign file we can prefix-match around
        that, but a label WE emit that triggers it becomes unmatchable in our
        own results, so it is a hard error rather than a convention.
        """
        problems = []
        for comp in circuit.get("components", []):
            label = comp.get("label", comp["ref"])
            if not logisim_symbols.SAFE_LABEL.match(label):
                problems.append(
                    f"{comp['ref']}: label {label!r} is not VHDL-safe, so "
                    f"Logisim would rewrite it and append an unreproducible "
                    f"hash. Labels must match "
                    f"{logisim_symbols.SAFE_LABEL.pattern}"
                )

        # MEASURED 2026-08-26, and it cost three design attempts to find:
        # Logisim's labels are unique CASE-INSENSITIVELY. A circuit with
        # inputs A, B, C, D and outputs a, b, c, d came back from --tty table
        # with columns A, B, C, D, x, y, z, u -- the clashing outputs
        # silently renamed to letters nobody chose. The result is
        # unmatchable, and nothing in the file says it happened.
        seen = {}
        for comp in circuit.get("components", []):
            label = comp.get("label", comp["ref"])
            first = seen.setdefault(label.lower(), label)
            if first != label:
                problems.append(
                    f"{comp['ref']}: label {label!r} differs from {first!r} "
                    f"only by case. Logisim treats labels case-insensitively "
                    f"and RENAMES the clash to a letter of its own choosing "
                    f"(a, b, c, d beside A, B, C, D came back as x, y, z, u), "
                    f"which makes the signal unmatchable in the results. "
                    f"Rename one of them to something that differs by more "
                    f"than case."
                )
        return problems

    def check_constraints(self, circuit, constraints):
        """`primitives_only`: no component from outside PRIMITIVE_LIBS.

        Checked rather than assumed. Since 2026-08-26 it CAN fail: TYPE_MAP
        holds the 7447 (#TTL) and the seven-segment display (#I/O), so a
        primitives_only question that names one is refused here. It was
        written before either existed, when TYPE_MAP held only Pin and gates
        and there was no way to ASK for the Plexers Priority Encoder that
        answers Q2 in one drop -- written down then so that the day a
        non-primitive type arrived, this is what would stop it slipping past a
        question that declared the constraint.
        """
        if not constraints.get("primitives_only"):
            return []
        problems = []
        for comp in circuit.get("components", []):
            if comp["type"] not in self.TYPE_MAP:
                continue                     # reported by the type check
            name, _ = self.TYPE_MAP[comp["type"]]
            lib = logisim_symbols.LIB_OF.get(name)
            if lib not in logisim_symbols.PRIMITIVE_LIBS:
                problems.append(
                    f"{comp['ref']} is a {name} from {lib}, but the question "
                    f"declares constraints.primitives_only. Primitive "
                    f"libraries: {', '.join(sorted(logisim_symbols.PRIMITIVE_LIBS))}"
                )
        return problems

    def backend(self):
        """Logisim if installed, our own engine otherwise — and the caller
        must surface which, because they carry different verification."""
        from ohmwork.logisim_backend import best_available_backend
        return best_available_backend()

    def round_trip(self, circuit) -> RoundTrip:
        return RoundTrip(
            ran=False,
            reason="the .circ geometric parser is not built (deferred to "
                   "v1.1 with check-mine mode), so no round trip ran. "
                   "Structure, types, pins and labels were checked here; "
                   "geometry was not. Note what replaces it and is stronger: "
                   "Logisim itself evaluates the emitted file, so a geometry "
                   "bug shows up as a wrong truth table rather than as our "
                   "parser agreeing with our emitter's mistake.",
        )


_TARGETS = {
    "ltspice": LTspiceTarget,
    "logisim": LogisimTarget,
    "logisim-2.7.1": LogisimTarget,
}

#: Questions written before targets existed are LTspice ones.
DEFAULT_TARGET = "ltspice"


def get_target(name: str | None):
    key = (name or DEFAULT_TARGET).lower()
    if key not in _TARGETS:
        raise UnknownTargetError(
            f"unknown target {name!r}. Known: {', '.join(sorted(_TARGETS))}"
        )
    return _TARGETS[key]()


def check_component_types(target, components):
    """Validate types and pin references in the TARGET's vocabulary."""
    errors = []
    known = target.known_types()
    for comp in components:
        if comp["type"] not in known:
            errors.append(target.describe_unknown_type(comp["ref"], comp["type"]))
    return errors


def valid_pins(target, components):
    """Every `<ref>.<pin>` the target says exists, for net validation."""
    pins = set()
    for comp in components:
        if comp["type"] not in target.known_types():
            continue
        for pin in target.pin_names(comp["type"]):
            pins.add(f"{comp['ref']}.{pin}")
    return pins
