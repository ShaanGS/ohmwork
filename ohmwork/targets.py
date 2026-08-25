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
        "and2":       ("AND Gate", {"inputs": "2"}),
        "or2":        ("OR Gate", {"inputs": "2"}),
        "xor2":       ("XOR Gate", {"inputs": "2"}),
        "or4":        ("OR Gate", {"inputs": "4"}),
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
        return problems

    def check_constraints(self, circuit, constraints):
        """`primitives_only`: no component from outside PRIMITIVE_LIBS.

        Checked rather than assumed. Today it cannot fail — TYPE_MAP contains
        only Pin and gates, so there is no way to ASK for the Plexers Priority
        Encoder that answers Q2 in one drop and defeats the exercise. That is
        exactly why it is worth writing down: the day a Plexers type is added
        to TYPE_MAP, this check is what stops it slipping past a question that
        declared the constraint, instead of the constraint silently becoming
        decorative.
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
