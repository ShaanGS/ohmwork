"""Checking an IC question against the PART, not against a recollection.

WHY THIS EXISTS, in one incident. Q4 -- "design a BCD-to-seven-segment
display circuit using the 7447-decoder IC" -- reached the design loop's
comparison and failed there, for a reason that was not a bug in either half.
The loop's reference was the model's SPEC: one boolean expression per output,
written from the question's words. For a question that names a chip, that
means written from the model's MEMORY of a datasheet. Its memory said BCD 0000
lights nothing. A real 7447 shows a nought. The chip is right.

Verifying a part against a recollection is backwards, so for a question that
names a part the reference is the part:

    1. PROBE    a bare 7447 with one Pin on every port, handed to the same
                evaluator that will judge the answer. That table is the
                chip's own behaviour. Nothing was recalled to produce it.
    2. WIRING   read out of the design's own nets: which question signal
                reaches which pin, which pins are held at a level, and which
                pin drives which output.
    3. PREDICT  push the probe's table through that wiring.
    4. COMPARE  against what the evaluator makes of the emitted FILE, using
                the same `spec.compare_tables` the gate-level path uses.

WHAT THAT PROVES: the file handed over routes the question's signals through
a real 7447 exactly as the design says it does, and the part inside it decodes
as a bare one does. Every failure of the emitter, the router, the pin table or
the constants shows up as a disagreement between steps 3 and 4.

WHAT IT DOES NOT PROVE, and it must never be blurred: that the wiring is the
right READING of the question. Steps 3 and 4 both read the same nets, so a
design that puts a signal on the wrong pin agrees with itself and passes. That
is the same shape of hole the spec basis has, and it gets the same answer --
the wiring map is OUTPUT, printed for a person to check, exactly as
`Spec.render()` is.

The one slice of that hole closed mechanically is `name_conflicts`: a design
that wires the question's signal `A` to the part's pin `D` is refused, because
the part has a pin named `A`. That constraint comes from the NAMES, which sit
outside the wiring, so it is not self-confirming. It is a naming check and not
a behavioural one, and it is written down as such.

GATES IN THE PATH ARE EVALUATED, NOT REFUSED, and that was decided by trying
it. The first live run of a real 7447 question failed four times over, because
the model kept putting inverters on the segment outputs -- an entirely
reasonable reading of "the 7447 has active-low outputs", and one this module
originally rejected on the grounds that it had no logic engine. Refusing a
sound design because the checker is thin is the checker's problem. So step 3
walks the netlist: a gate is evaluated from `GATE_LOGIC`, a constant holds its
level, and the part is looked up in the probe.

That adds something rather than costing something. The gate semantics here are
OURS and Logisim's are Logisim's, so a disagreement between steps 3 and 4 is
now evidence about the emitter, the router, the pin table AND the gates. What
is still refused, loudly, is a component type this module has no logic for:
`GATE_LOGIC` is pinned against the emitter's own vocabulary by a test, so a
type added there without being taught here fails immediately rather than
quietly becoming unpredictable.

`tests/test_partcheck.py` is the spec for this module.
"""

import re
from dataclasses import dataclass
from itertools import product

from ohmwork.basis import Basis
from ohmwork.spec import SpecTable

#: The part's ref inside a probe circuit. Not a port name of anything, so it
#: cannot collide with the pins named for the ports they sit on.
PROBE_REF = "PART"

#: Component types that carry no logic: a pin, or a held level.
PASSIVE_TYPES = ("input_pin", "output_pin", "high", "low")

#: What a constant holds. Named here rather than inlined so the two places
#: that read it cannot drift.
LEVELS = {"high": 1, "low": 0}

#: Gate semantics, written HERE and not imported from anywhere.
#:
#: That is the point of them. Logisim evaluates the emitted file with its own
#: implementation; this predicts the same circuit with a different one. Two
#: implementations of "or4" disagreeing is a finding. One implementation
#: agreeing with itself is not.
#:
#: Arguments arrive in the order `pin_names` reports the type's input ports,
#: which is the same order the emitter wires them.
GATE_LOGIC = {
    "not": lambda values: 1 - values[0],
    "and2": lambda values: values[0] & values[1],
    "or2": lambda values: values[0] | values[1],
    "xor2": lambda values: values[0] ^ values[1],
    "or4": lambda values: values[0] | values[1] | values[2] | values[3],
}


class WiringError(Exception):
    """This module will not vouch for the circuit it was given.

    Every message is written to be fed back to the model that designed it, so
    it names the component and says what to do instead. A rejection that does
    not say what to fix spends a whole retry teaching nothing.
    """


# ------------------------------------------------------------ the basis
#
# `Basis` itself lives in ohmwork/basis.py: an analog result needs the same
# shape, and nothing describing a voltage regulator should have to import a
# module about seven-segment displays to say what it was checked against.

SPEC_LIMIT = (
    "that the specification is the right reading of the question. The "
    "circuit was checked against the algebra above, not against the "
    "sentence it came from; if the algebra misreads the question, every "
    "check still passes. Read it.")

PART_LIMIT = (
    "that this wiring is the right reading of the question. The prediction "
    "and the evaluation both read the same nets, so a signal on the wrong "
    "pin agrees with itself -- except where the signal's own name names a "
    "pin, which is refused outright. Read the map above.")


def spec_basis(spec) -> Basis:
    """The gate-level basis: the circuit computes the specification."""
    return Basis(
        kind="spec",
        headline=("the specification read from the question's words, "
                  "evaluated over every input combination"),
        reading=spec.render(),
        limit=SPEC_LIMIT,
        summary="; ".join(f"{name} = {spec.expressions.get(name, '(missing)')}"
                          for name in spec.outputs),
    )


def part_basis(wiring, probe, notes=()) -> Basis:
    """The IC basis: the circuit reproduces the part, through this wiring.

    `notes` are the choices the question left open, carried over from the
    spec. They belong in the reading for the same reason the map does: the
    first live 7447 solve tied the ripple-blanking pin LOW, which blanks a
    leading zero and is a real decision about what the answer means. The map
    showed it; a note saying so in words is what makes it noticeable.
    """
    return Basis(
        kind="part",
        headline=(f"the {wiring.part_name} itself -- a bare "
                  f"{wiring.part_name} was evaluated by {probe.backend} over "
                  f"{len(probe.rows)} input combinations, and the emitted "
                  f"circuit must reproduce that through the wiring below. "
                  f"No datasheet was recalled."),
        reading=render_wiring(wiring, notes),
        limit=PART_LIMIT,
        summary=summarise_wiring(wiring),
    )


# ------------------------------------------------------------- the probe

def _ports(type_name, target):
    try:
        names = target.pin_names(type_name)
    except Exception as exc:                                    # noqa: BLE001
        # An unmeasured part is a hard error one layer down, by design. It
        # is re-raised in this module's vocabulary so a caller has one
        # exception type to handle.
        raise WiringError(
            f"{type_name!r} is not a part this build can place: {exc}") from exc
    return [(name, target.is_source(type_name, name)) for name in names]


def probeable(type_name, target) -> bool:
    """Can this part be its own reference?

    Only if it produces something. A seven-segment display has eight ports
    and every one of them is an input, so probing it yields a table with no
    output columns -- not a reference to anything. Structural rather than a
    list of part names, so a part added later sorts itself out.
    """
    try:
        ports = _ports(type_name, target)
    except WiringError:
        return False
    return any(is_out for _, is_out in ports)


def probe_circuit(type_name, target) -> dict:
    """A bare part with one Pin on every port, as a circuit description.

    Deliberately a description rather than hand-written XML: it goes through
    the same emitter and the same router as the answer does. A probe only
    this module could write would be a second emitter, free to disagree with
    the one that writes the file the student gets.

    Each pin is NAMED for the port it sits on, so the evaluator's columns come
    back as the part's own pin names and no mapping is needed between the
    probe and the wiring.
    """
    ports = _ports(type_name, target)
    if not any(is_out for _, is_out in ports):
        raise WiringError(
            f"{type_name!r} has no output ports, so a probe of it would "
            f"produce a truth table with no outputs. It cannot be its own "
            f"reference.")

    components = [{"ref": PROBE_REF, "type": type_name}]
    nets = {}
    for name, is_out in ports:
        components.append({"ref": name,
                           "type": "output_pin" if is_out else "input_pin"})
        nets[f"n_{name}"] = [f"{PROBE_REF}.{name}", f"{name}.pin"]
    return {"components": components, "nets": nets}


def probe_table(type_name, target, backend, path):
    """Evaluate a bare part and return its own truth table.

    The result is the reference. It is measured in the SAME evaluator that
    judges the answer, which is the point: a difference between the two
    tables is then a fact about the wiring rather than about two tools
    modelling one chip differently.
    """
    from ohmwork.logisim_emitter import write_circ

    circuit = probe_circuit(type_name, target)
    write_circ(circuit, path)
    ports = _ports(type_name, target)
    return backend.truth_table(
        path,
        [name for name, is_out in ports if not is_out],
        [name for name, is_out in ports if is_out],
    )


# ------------------------------------------------------- reading the wiring

@dataclass(frozen=True)
class Netlist:
    """The design reduced to what a value can be computed from.

    Separate from `PartWiring` because the two answer different questions.
    This one is machinery: enough to evaluate the circuit. `PartWiring` is
    for a person, and shows only what reaches the part directly.
    """

    part_ref: str
    types: dict              # ref -> component type
    port_net: dict           # "REF.pin" -> net name
    driver: dict             # net name -> (ref, pin) of the port driving it
    input_ports: dict        # component type -> its input port names, in order


@dataclass(frozen=True)
class PartWiring:
    """How the question's signals reach the part, read out of the nets."""

    ref: str                 # the part's instance ref in the design, e.g. U1
    type_name: str           # the question JSON's type, e.g. ttl7447
    part_name: str           # what Logisim calls it, e.g. 7447
    #: part input port -> ("signal", input) | ("level", 0|1) | ("logic", desc)
    inputs: dict
    #: question output -> ("port", part output) | ("signal", input)
    #:                  | ("level", 0|1) | ("logic", desc)
    outputs: dict
    #: refs of components that only listen -- a display, typically. They
    #: change no value, and naming them is how the map stays readable.
    sinks: tuple = ()
    #: (ref, question-JSON type) for each sink, so the map can DISCLOSE a
    #: display's polarity -- the one property of a sink that changes what a
    #: person sees on screen (issue #1) while changing no row of the table.
    sink_types: tuple = ()
    #: The evaluable form of the same circuit. Carried here rather than
    #: derived twice: two passes over one set of nets is two accounts of one
    #: fact, free to disagree.
    netlist: object = None


def _split(member: str):
    ref, _, pin = member.partition(".")
    if not ref or not pin:
        raise WiringError(f"net member {member!r} is not REF.pin")
    return ref, pin


def _driver(net_name, members, types, target):
    """The one port on a net that drives it."""
    drivers = [(ref, pin) for ref, pin in (_split(m) for m in members)
               if ref in types and target.is_source(types[ref], pin)]
    if len(drivers) != 1:
        raise WiringError(
            f"net {net_name!r} has {len(drivers)} drivers; it must have "
            f"exactly one")
    return drivers[0]


def derive_wiring(circuit, type_name, target) -> PartWiring:
    """Read the design's nets into a map of what reaches which pin.

    Refuses anything it cannot account for. This build has no logic engine,
    so a gate between the question's pins and the part's is a path whose
    value it cannot predict -- and predicting only the paths it happens to
    understand would produce a check that passes because it looked away.
    """
    # Defensive about SHAPE, because this runs on model output before the
    # question gate has seen it. The gate's own errors are better than
    # anything written here, so this only has to avoid raising KeyError from
    # inside a helper -- a traceback is not a rejection a model can act on.
    components = circuit.get("components") or []
    types = {}
    for comp in components:
        if not isinstance(comp, dict) or "ref" not in comp or "type" not in comp:
            raise WiringError(
                f"every component needs a 'ref' and a 'type'; got {comp!r}")
        types[comp["ref"]] = comp["type"]

    # An unknown type, named in the TARGET's vocabulary and against the REF
    # that carries it. Measured on a live run: without this, a model that
    # wrote a nonsense type got back "'type' is not a part this build can
    # place: 'type'", which names nothing it could act on and spent an
    # attempt. The gate says the same thing later; saying it here means the
    # rejection is about the mistake rather than about a helper's KeyError.
    known = target.known_types()
    for this, kind in types.items():
        if kind not in known:
            raise WiringError(target.describe_unknown_type(this, kind))

    part_refs = [ref for ref, kind in types.items() if kind == type_name]
    if not part_refs:
        raise WiringError(
            f"the question asks for a {type_name}, and the design contains "
            f"none. Place one and wire the question's signals to its pins.")
    if len(part_refs) > 1:
        raise WiringError(
            f"the design contains {len(part_refs)} {type_name} components "
            f"({', '.join(sorted(part_refs))}). This build checks a circuit "
            f"against ONE named part; use a single {type_name}.")
    ref = part_refs[0]
    part_name, _ = target.TYPE_MAP[type_name]

    sinks = []
    for comp in components:
        kind, this = comp["type"], comp["ref"]
        if this == ref or kind in PASSIVE_TYPES or kind in GATE_LOGIC:
            continue
        if any(is_out for _, is_out in _ports(kind, target)):
            # Something that DRIVES a net and whose behaviour this module
            # cannot compute. Refused rather than skipped: a prediction that
            # quietly omits a driver is a check that passed by looking away.
            raise WiringError(
                f"{this} is a {kind}, which drives a net and is neither the "
                f"{part_name} being checked nor a gate this build can "
                f"evaluate ({', '.join(sorted(GATE_LOGIC))}). A circuit is "
                f"checked against the named part's own measured behaviour, "
                f"and nothing here can predict what a second driving part "
                f"would do.")
        sinks.append(this)

    port_net = {}
    for net_name, members in (circuit.get("nets") or {}).items():
        if not isinstance(members, (list, tuple)):
            raise WiringError(
                f"net {net_name!r} must be a list of REF.pin strings")
        for member in members:
            if member in port_net:
                raise WiringError(f"{member} appears on more than one net")
            port_net[member] = net_name

    nets = circuit.get("nets") or {}
    driver, inputs, outputs = {}, {}, {}
    for net_name, members in nets.items():
        driver[net_name] = _driver(net_name, members, types, target)

    def named(source_ref, source_pin) -> str:
        """One driver in a few words, for the inside of a logic entry."""
        kind = types[source_ref]
        if source_ref == ref:
            return f"{part_name} pin {source_pin}"
        if kind == "input_pin":
            return source_ref
        if kind in LEVELS:
            return "HIGH" if LEVELS[kind] else "LOW"
        return source_ref

    def source_of(source_ref, source_pin):
        """One driver, described for a person reading the map.

        A gate is expanded ONE level -- "N_a (not) of 7447 pin QA" -- because
        "through N_a" alone tells a reader that something happened without
        telling them what. One level is where it stops: past that the map
        becomes a netlist, and a netlist is what the reader came here to
        avoid reading.
        """
        kind = types[source_ref]
        if source_ref == ref:
            return ("port", source_pin)
        if kind == "input_pin":
            return ("signal", source_ref)
        if kind in LEVELS:
            return ("level", LEVELS[kind])
        feeding = []
        for port in (name for name, is_out in _ports(kind, target)
                     if not is_out):
            member = port_net.get(f"{source_ref}.{port}")
            feeding.append("unwired" if member is None
                           else named(*driver[member]))
        return ("logic", f"{source_ref} ({kind}) of {', '.join(feeding)}")

    for port, is_out in _ports(type_name, target):
        if is_out:
            continue
        net_name = port_net.get(f"{ref}.{port}")
        if net_name is None:
            raise WiringError(f"{part_name} pin {port} is on no net")
        inputs[port] = source_of(*driver[net_name])

    for comp in components:
        if comp["type"] != "output_pin":
            continue
        signal = comp["ref"]
        net_name = port_net.get(f"{signal}.pin")
        if net_name is None:
            raise WiringError(f"output {signal} is on no net")
        outputs[signal] = source_of(*driver[net_name])

    input_ports = {kind: tuple(name for name, is_out in _ports(kind, target)
                               if not is_out)
                   for kind in set(types.values())}
    return PartWiring(
        ref=ref, type_name=type_name, part_name=part_name,
        inputs=inputs, outputs=outputs, sinks=tuple(sinks),
        sink_types=tuple((s, types[s]) for s in sinks),
        netlist=Netlist(part_ref=ref, types=types, port_net=port_net,
                        driver=driver, input_ports=input_ports))


#: Display types and whether a segment lights on HIGH. Two entries because
#: the polarity is the design's EXPLICIT choice -- issue #1 is what leaving
#: it to Logisim's default produced: every digit rendered as its negative,
#: under a green "verified" that was true of the pins and silent about the
#: screen.
DISPLAY_LIGHTS_ON_HIGH = {"seven_segment": True,
                          "seven_segment_active_low": False}

_ACTIVE_LOW_WORDS = re.compile(r"active[\s-]*low", re.IGNORECASE)

#: The same word shapes domain.py uses to recognise the component.
_DISPLAY_WORDS = re.compile(r"seven[\s-]*segment|7[\s-]*segment",
                            re.IGNORECASE)


def polarity_conflicts(question_text, circuit, target) -> list:
    """A display wired straight to outputs the QUESTION calls active-low
    must itself be active-low, or every digit renders inverted.

    Issue #1, found by a student: logic verified on all 16 rows, display
    showing the photographic negative of each digit. The truth table can
    never catch it -- the display is not in the table -- so this check
    reads the two facts that ARE available: the question's own words
    ("active-low"), and whether a segment input is driven DIRECTLY by the
    named part's output. When the question does not say active-low the
    check DISARMS rather than guessing; the wiring map's polarity line
    remains the defence.
    """
    components = circuit.get("components") or []
    types = {c["ref"]: c["type"] for c in components}
    displays = {ref: kind for ref, kind in types.items()
                if kind in DISPLAY_LIGHTS_ON_HIGH}

    if (question_text and _DISPLAY_WORDS.search(question_text)
            and not displays):
        # Measured on the live repro after the first fix: the model simply
        # LEFT THE DISPLAY OUT and verified on the pins alone. The answer
        # was true and under-delivered -- a drop, the species the coverage
        # checks exist for.
        return [
            "the question asks for a seven-segment display and this design "
            "has none. Add one -- type 'seven_segment_active_low' if its "
            "segment inputs come directly from active-low outputs (a "
            "7447's), 'seven_segment' if they are active-high -- and wire "
            "its a..g inputs. It listens only, so the truth table is "
            "unchanged; the screen is what it is for."]

    if not question_text or not _ACTIVE_LOW_WORDS.search(question_text):
        return []
    if not displays:
        return []

    def part_output(member):
        ref, pin = _split(member)
        kind = types.get(ref)
        if (kind is None or kind in PASSIVE_TYPES or kind in GATE_LOGIC
                or kind in DISPLAY_LIGHTS_ON_HIGH):
            return False
        return any(name == pin and is_out
                   for name, is_out in _ports(kind, target))

    problems = []
    flagged = set()
    for members in (circuit.get("nets") or {}).values():
        if not isinstance(members, (list, tuple)):
            continue
        direct = any(part_output(m) for m in members
                     if isinstance(m, str) and "." in m)
        if not direct:
            continue
        for m in members:
            if not isinstance(m, str) or "." not in m:
                continue
            ref, pin = _split(m)
            if (ref in displays and ref not in flagged
                    and pin in "abcdefg"
                    and DISPLAY_LIGHTS_ON_HIGH[displays[ref]]):
                flagged.add(ref)
                problems.append(
                    f"the question says the segment outputs are ACTIVE-LOW "
                    f"(a 0 lights a segment), and {ref}'s segment inputs "
                    f"are wired directly to those outputs -- but {ref} is "
                    f"an active-HIGH display (type 'seven_segment'), so "
                    f"every digit would render as its photographic "
                    f"negative. The truth table cannot catch this: the "
                    f"display is not in it. Use type "
                    f"'seven_segment_active_low' for {ref}, or put one "
                    f"inverter on each segment line and keep the "
                    f"active-high display.")
    return problems


def name_conflicts(wiring, target) -> list:
    """A signal whose own name names a pin must be on that pin.

    The one part of a misreading this basis catches by itself, and the reason
    it works is that it does not come from the wiring: the prediction and the
    evaluation both read the nets, so they agree about a swap, but the NAMES
    are outside that loop. A question with inputs A, B, C, D whose design
    puts A on the part's D pin is a swap, and it is refused.

    It fires only on a name that IS one of the part's pins, so a question
    that calls its inputs D3..D0 is left entirely alone. A naming rule that
    refused ordinary designs would be deleted within a week.
    """
    ports = _ports(wiring.type_name, target)
    input_ports = {name.lower() for name, is_out in ports if not is_out}
    output_ports = {name.lower() for name, is_out in ports if is_out}
    problems = []
    for port, source in sorted(wiring.inputs.items()):
        if source[0] != "signal":
            continue
        signal = source[1]
        if signal.lower() in input_ports and signal.lower() != port.lower():
            problems.append(
                f"the question's signal {signal} is wired to the "
                f"{wiring.part_name}'s pin {port}, but the part has a pin "
                f"named {signal}. A signal whose name names a pin must be on "
                f"that pin -- otherwise the circuit is checked against a "
                f"wiring nobody meant.")
    for signal, source in sorted(wiring.outputs.items()):
        if source[0] != "port":
            continue
        port = source[1]
        if signal.lower() in output_ports and signal.lower() != port.lower():
            problems.append(
                f"the question's output {signal} is driven by the "
                f"{wiring.part_name}'s pin {port}, but the part has a pin "
                f"named {signal}. Put it on that pin.")
    return problems


# ------------------------------------------------------------ the prediction

def _evaluate_row(netlist, probe_lookup, probe, values):
    """Every net's value for one combination of the question's inputs.

    A memoised walk backwards from whatever is asked for, rather than a
    topological sort forwards: a net nobody reads is never evaluated, and the
    recursion is the dependency. `visiting` turns a combinational loop into a
    named error instead of a stack overflow -- the emitter refuses those too,
    but this runs on model output and a checker that crashes on bad input is
    not a checker.
    """
    cache, visiting, part_outputs = {}, set(), {}

    def net_value(net_name):
        if net_name in cache:
            return cache[net_name]
        if net_name in visiting:
            raise WiringError(
                f"net {net_name!r} depends on itself: the circuit has a "
                f"combinational loop, which has no value to predict.")
        visiting.add(net_name)
        try:
            ref, pin = netlist.driver[net_name]
            kind = netlist.types[ref]
            if kind == "input_pin":
                value = values[ref]
            elif kind in LEVELS:
                value = LEVELS[kind]
            elif ref == netlist.part_ref:
                value = part_value(pin)
            elif kind in GATE_LOGIC:
                value = GATE_LOGIC[kind]([
                    net_value(netlist.port_net[f"{ref}.{port}"])
                    for port in netlist.input_ports[kind]])
            else:
                raise WiringError(
                    f"{ref} is a {kind}, and this build has no logic for it, "
                    f"so the value on net {net_name!r} cannot be predicted.")
        finally:
            visiting.discard(net_name)
        cache[net_name] = value
        return value

    def part_value(pin):
        """The part's outputs, looked up ONCE in its own measured table."""
        if not part_outputs:
            key = []
            for port in probe.inputs:
                member = f"{netlist.part_ref}.{port}"
                if member not in netlist.port_net:
                    raise WiringError(
                        f"the part's pin {port} is on no net, so its own "
                        f"table cannot be looked up.")
                key.append(net_value(netlist.port_net[member]))
            produced = probe_lookup.get(tuple(key))
            if produced is None:
                raise WiringError(
                    f"the probe has no row for "
                    f"{dict(zip(probe.inputs, key))}, so the part's behaviour "
                    f"there is not known. The probe must be exhaustive.")
            part_outputs.update(zip(probe.outputs, produced))
        if pin not in part_outputs:
            raise WiringError(f"the probe reports no pin {pin!r}")
        return part_outputs[pin]

    return net_value


def predict_table(wiring, probe, inputs, outputs) -> SpecTable:
    """What the design should do: the part's own table, through the netlist.

    Returned as a `SpecTable` so `spec.compare_tables` compares it against the
    evaluator's result exactly as it compares a specification. One comparison,
    one set of messages, one place where a difference is explained -- the two
    bases differ in what the reference IS and in nothing else.
    """
    inputs, outputs = tuple(inputs), tuple(outputs)
    netlist = wiring.netlist
    if netlist is None:
        raise WiringError(
            "this wiring carries no netlist, so nothing can be predicted "
            "from it")

    width = len(probe.inputs)
    probe_lookup = {tuple(row[:width]): tuple(row[width:])
                    for row in probe.rows}

    missing = [name for name in outputs
               if f"{name}.pin" not in netlist.port_net]
    if missing:
        raise WiringError(
            f"output(s) {missing} are on no net, so no value can be "
            f"predicted for them.")

    rows = []
    for combination in product((0, 1), repeat=len(inputs)):
        values = dict(zip(inputs, combination))
        net_value = _evaluate_row(netlist, probe_lookup, probe, values)
        rows.append(combination + tuple(
            net_value(netlist.port_net[f"{name}.pin"]) for name in outputs))

    return SpecTable(inputs=inputs, outputs=outputs, rows=tuple(rows))


def _describe(source, part_name) -> str:
    """One driver, in words. The single place a source becomes text.

    Explicit on every kind rather than falling through to a default: a
    ("logic", ...) entry landing in a "held HIGH or LOW" branch renders a map
    that is confidently wrong, and the map is the whole thing a person is
    asked to check.
    """
    kind = source[0]
    if kind == "signal":
        return f"question input {source[1]}"
    if kind == "port":
        return f"{part_name} pin {source[1]}"
    if kind == "level":
        return "held HIGH" if source[1] else "held LOW"
    if kind == "logic":
        return f"through {source[1]}"
    raise WiringError(f"unknown wiring source {source!r}")


def summarise_wiring(wiring) -> str:
    """The same map on one line, for a design note in the manifest."""
    pieces = [f"{_describe(source, wiring.part_name)} -> {wiring.part_name} "
              f"pin {port}"
              for port, source in sorted(wiring.inputs.items())]
    pieces += [f"{_describe(source, wiring.part_name)} -> output {signal}"
               for signal, source in sorted(wiring.outputs.items())]
    return f"{wiring.part_name} {wiring.ref}: " + "; ".join(pieces)


def render_wiring(wiring, notes=()) -> str:
    """The map a human is asked to check, because nothing else can.

    Reads as sentences rather than as a table: the question this page exists
    to answer is "did it put my signals on the right pins", and that is
    answered by reading left to right.
    """
    lines = [f"part {wiring.ref}: a {wiring.part_name}, checked against its "
             f"own measured behaviour"]
    entries = [(_describe(source, wiring.part_name),
                f"{wiring.part_name} pin {port}")
               for port, source in sorted(wiring.inputs.items())]
    entries += [(_describe(source, wiring.part_name),
                 f"question output {signal}")
                for signal, source in sorted(wiring.outputs.items())]
    width = max(len(left) for left, _ in entries) if entries else 0
    lines += [f"  {left:<{width}}  ->  {right}" for left, right in entries]
    if wiring.sinks:
        # A display is on these nets too. Saying so matters -- the question
        # asks for one -- but it drives no value, so it changes no row of the
        # table and must not read as though it did.
        lines.append(f"  also on these nets, listening only: "
                     f"{', '.join(sorted(wiring.sinks))}")
    for ref, kind in sorted(wiring.sink_types):
        # The one property of a listener that changes what a person SEES
        # while changing no row of the table (issue #1). Disclosed so the
        # human checking this map checks it too.
        if kind in DISPLAY_LIGHTS_ON_HIGH:
            level = "HIGH" if DISPLAY_LIGHTS_ON_HIGH[kind] else "LOW"
            lines.append(f"  {ref} lights a segment on {level} -- check "
                         f"this against the polarity of what feeds it")
    lines += [f"  note: {note}" for note in notes]
    return "\n".join(lines)
