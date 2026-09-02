"""Refusing a question the digital loop cannot answer.

WHY THIS MODULE EXISTS, in one incident. The real Q3 -- bridge rectifier,
C-L-C filter, zener regulator, 12 V RMS at 50 Hz, "using LTspice" -- was
typed into the digital endpoint. Nothing refused it. The model wrote a
"specification":

    RECT_OUT = AC,  FILTER_OUT = AC,  REG_OUT = AC,  LOAD_CURR = LOAD

designed wires for it, Logisim confirmed the wires computed the wires, and
the answer rendered as VERIFIED with a download button.

Every claim in that chain was true and the result was worthless. "Verified"
only ever meant *the circuit matches the specification*; nothing downstream
of the spec can notice the spec was a category error. So a question outside
this loop's domain has to be refused AT or BEFORE the spec, and in more than
one way, because each check here has a different blind spot:

1. `check_digital` -- a deterministic screen over the question's words. No
   model call, no tokens, cannot be argued with.
2. the prompt's refusal channel (see design.SPEC_PROMPT) -- for analog
   questions written in words this screen does not know.
3. `check_spec_has_logic` -- a structural check on the spec that came back.
   It catches the SHAPE the incident produced, which means it also catches
   domains nobody has thought of yet.

THE COST OF BEING WRONG IS ASYMMETRIC, and the thresholds here reflect it.
A missed analog question produces a confident meaningless answer, which is
the worst outcome available. A false refusal produces an annoyed user who
retypes their question -- bad, but recoverable, and the message says exactly
what tripped it so they can see the mistake. The screen is still kept narrow:
it wants TWO independent signals, or one naming a simulator outright.
"""

import re
from dataclasses import dataclass

#: Naming one of these is decisive on its own. A question that asks for
#: LTspice is asking for something this endpoint cannot do, whatever else it
#: says -- and the same is true of the tool it would be answered with.
SIMULATORS = ("ltspice", "pspice", "ngspice", "multisim", "proteus",
              "tinkercad", "spice")

#: Analog vocabulary. Each is ordinary in an analog question and rare in a
#: digital-logic one, but any single word can appear in either -- so no word
#: here is decisive alone.
ANALOG_WORDS = (
    "rectifier", "zener", "capacitor", "capacitance", "inductor",
    "inductance", "transistor", "diode", "op-amp", "opamp",
    "operational amplifier", "amplifier", "oscillator", "regulator",
    "waveform", "transient", "ripple", "rms", "peak-to-peak", "biasing",
    "bias point", "load current", "voltage divider", "smoothing",
    "half-wave", "full-wave", "cutoff frequency", "gain", "impedance",
    "reactance", "resistive load", "power supply", "breakdown",
)

#: A number with an analog unit attached. Deliberately requires the NUMBER:
#: bare "V" or "F" appear all over digital text, and matching them alone
#: would refuse real questions.
ANALOG_QUANTITY = re.compile(
    r"""\d+(?:\.\d+)?\s*(?:
          [munpk]?\s*(?:F|H|Ω|ohms?)\b        # 470 uF, 1 mH, 1 kΩ
        | [munk]?\s*(?:A)\b                    # 20 mA
        | \s*(?:V\s*(?:rms|pp|peak))           # 12 V RMS
        | \s*(?:Hz|kHz|MHz)\b                  # 50 Hz
        | \s*(?:dB)\b
      )""",
    re.IGNORECASE | re.VERBOSE | re.UNICODE)

#: Micro sign in either encoding people actually paste, plus the Greek mu.
_MICRO = re.compile(r"[µμ]\s*(?:F|H|A)\b", re.IGNORECASE)

#: Sequential logic. The loop builds COMBINATIONAL circuits and verifies them
#: with an exhaustive truth table, which is only meaningful when the outputs
#: depend on nothing but the present inputs. A counter has state; its "truth
#: table" is not a function of its inputs, and the comparison the whole loop
#: rests on has no meaning. Found while writing this file: a 4-bit counter
#: question was in the test set as an example that should PASS.
SEQUENTIAL_WORDS = (
    "counter", "flip-flop", "flipflop", "flip flop", "latch", "register",
    "shift register", "state machine", "sequence detector", "clock",
    "clocked", "synchronous", "asynchronous", "memory", "ram", "rom",
    "edge-triggered", "d flip", "jk", "t flip", "debounce", "one-shot",
)

SEQUENTIAL_ADVICE = (
    "This loop builds COMBINATIONAL logic and checks it with an exhaustive "
    "truth table. A circuit with state does not have one -- its outputs "
    "depend on what happened before, not only on the present inputs -- so "
    "there is nothing here that could honestly verify it."
)

#: Parts whose geometry this project has never measured from a real file.
#: The pin table refuses anything unmeasured (see logisim_symbols), so a
#: question naming one of these cannot be built at all -- and the reason is
#: worth saying out loud, because it is a missing MEASUREMENT, not a missing
#: feature.
#: MEASURED 2026-08-26 and therefore NO LONGER REFUSED: the 7447 and the
#: seven-segment display. What is listed here is what remains unmeasured --
#: every other TTL part. The list shrinks as real files arrive, which is the
#: only way it is allowed to shrink.
UNMEASURED_PARTS = (
    "7400", "7402", "7404", "7408", "7410", "7411", "7420", "7432", "7442",
    "7448", "7474", "7476", "7483", "7485", "7486", "74138", "74151",
    "74153", "74157", "74161", "74163", "74181", "74192", "74193",
)

UNMEASURED_ADVICE = (
    "Every component this tool can place had its pin geometry MEASURED from "
    "a real file, and it refuses to guess at one it has never seen. To add "
    "this one, a real .circ containing it has to be measured -- the circuit "
    "could be drawn without that, but every wire on it would be a guess."
)

ANALOG_ADVICE = (
    "This is an ANALOG question and it reached the digital loop, which "
    "checks answers with Logisim and cannot simulate voltages or waveforms. "
    "Ohmwork answers analog questions locally with LTspice: the desktop app and the "
    "CLI route there automatically, so if you see this, say 'in LTspice' in "
    "the question or pass --domain analog."
)


class DomainError(Exception):
    """The question is outside what this loop can honestly answer.

    A refusal, not a failure. The distinction matters in the output: a
    failure means the loop tried and could not, and a refusal means it
    should never have tried.
    """


def analog_evidence(question: str) -> list[str]:
    """Every analog signal found in the question, verbatim.

    Returned rather than merely counted so a refusal can SHOW its reasoning.
    A verdict without evidence is not something a reader can disagree with.
    """
    hits: list[tuple[int, str]] = []

    for name in (*SIMULATORS, *ANALOG_WORDS):
        match = re.search(rf"\b{re.escape(name)}\b", question, re.IGNORECASE)
        if match:
            # The question's own casing, so it is findable on screen by eye.
            hits.append((match.start(), match.group(0)))

    for pattern in (ANALOG_QUANTITY, _MICRO):
        hits += [(match.start(), match.group(0).strip())
                 for match in pattern.finditer(question)]

    # Sorted by where they appear, NOT by which table matched them: the list
    # is read by a person against the question in front of them, and a
    # message that lists the last clause first is one they have to decode.
    seen, unique = set(), []
    for _, item in sorted(hits):
        key = item.lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def names_a_simulator(question: str) -> str | None:
    for name in SIMULATORS:
        if re.search(rf"\b{re.escape(name)}\b", question, re.IGNORECASE):
            return name
    return None


def _found(question: str, words) -> list[str]:
    hits = []
    for word in words:
        match = re.search(rf"\b{re.escape(word)}\b", question, re.IGNORECASE)
        if match:
            hits.append((match.start(), match.group(0)))
    return [text for _, text in sorted(hits)]


def check_digital(question: str) -> None:
    """Refuse a question this loop cannot honestly answer, before spending
    anything on it. Three families, each with its own reason."""
    # Unmeasured parts first: a question naming the 7447 is a question about
    # THAT part, and telling someone their question is "analog" or
    # "sequential" would be answering a different objection than the real one.
    parts = _found(question, UNMEASURED_PARTS)
    if parts:
        raise DomainError(
            f"This question is built around a part this tool has never "
            f"measured: {', '.join(parts)}.\n\n{UNMEASURED_ADVICE}")

    stateful = _found(question, SEQUENTIAL_WORDS)
    if stateful:
        raise DomainError(
            f"This reads as a SEQUENTIAL circuit -- one with state.\n\n"
            f"What the question says: {', '.join(stateful[:6])}\n\n"
            f"{SEQUENTIAL_ADVICE}")

    simulator = names_a_simulator(question)
    evidence = analog_evidence(question)

    # One decisive signal, or two independent ones. A single stray "5 V
    # supply" in a counter question is not enough, and must not be: a screen
    # that refuses real digital questions costs more than it saves.
    if simulator is None and len(evidence) < 2:
        return

    raise DomainError(
        f"This reads as an ANALOG question, so it was refused before "
        f"anything was designed.\n\nWhat the question says: "
        f"{', '.join(evidence[:8])}\n\n{ANALOG_ADVICE}")


def check_spec_has_logic(spec) -> None:
    """Refuse a "specification" that contains no logic at all.

    The shape the incident produced: every output a bare copy of an input, or
    a constant. Such a spec is perfectly verifiable -- wires satisfy it, and
    an evaluator confirms them -- which is exactly why it has to be stopped
    here. Downstream, it is indistinguishable from a real answer.

    Narrow on purpose. A pass-through output BESIDE real logic is a
    legitimate design, so this fires only when nothing anywhere in the spec
    combines two signals. A check that fires on ordinary designs gets ignored,
    and then it does not fire on the one that matters.
    """
    operators = set("&|^~!+*.'")
    has_logic = any(
        any(character in operators for character in str(expression))
        for expression in spec.expressions.values())
    if has_logic:
        return

    lines = ", ".join(f"{name} = {expression}"
                      for name, expression in spec.expressions.items())
    raise DomainError(
        f"The reading of this question contains no logic -- every output is "
        f"just a copy of an input or a constant ({lines}).\n\nA circuit CAN "
        f"be built for that and an evaluator WILL confirm it, which is why it "
        f"is refused rather than answered: the result would look verified and "
        f"mean nothing. This usually means the question was not a digital "
        f"logic question.\n\n{ANALOG_ADVICE}")


#: Question wording -> the component type that answers it. Only parts whose
#: geometry is MEASURED appear here; a part named in a question but absent
#: from the pin table is refused by check_digital above, not silently
#: substituted with something else.
PART_WORDS = {
    "7447": "ttl7447",
    "74ls47": "ttl7447",
    "seven-segment": "seven_segment",
    "seven segment": "seven_segment",
    "7-segment": "seven_segment",
    "7segment": "seven_segment",
}


def named_parts(question: str) -> list[str]:
    """Component types the question explicitly asks for.

    A question that says "using the 7447-decoder IC" is not asking for an
    equivalent built from gates. Answering it with gates would be answering a
    neighbouring question -- correct, and not what was asked.
    """
    found = []
    for word, type_name in PART_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", question, re.IGNORECASE):
            if type_name not in found:
                found.append(type_name)
    return found


# ------------------------------------------------- the analog half

#: Digital vocabulary. Same discipline as ANALOG_WORDS: each is ordinary in a
#: digital question and rare in an analog one, and no single word decides
#: anything on its own. "decoder" is here and also appears in the 7447
#: question, which is exactly the point -- that question IS digital.
DIGITAL_WORDS = (
    "logic gate", "truth table", "boolean", "karnaugh", "k-map",
    "sum of products", "product of sums", "encoder", "decoder",
    "multiplexer", "demultiplexer", "adder", "subtractor", "comparator",
    "parity", "nand", "nor", "xor", "xnor", "inverter gate", "and gate",
    "or gate", "not gate", "bcd", "gray code", "seven-segment",
    "seven segment", "combinational", "logic circuit", "logic levels",
    "active-low", "active low", "active-high", "active high",
)

#: Naming this is decisive on its own, the way LTspice is for the analog side.
DIGITAL_SIMULATORS = ("logisim",)

#: Analog devices whose SYMBOL geometry this project has never measured. The
#: pin table refuses anything unmeasured, so a question built on one cannot be
#: drawn at all -- and that is a missing MEASUREMENT, not a missing feature.
#: The list shrinks only when a real `.asc` containing the part arrives.
UNMEASURED_ANALOG = (
    "op-amp", "opamp", "op amp", "operational amplifier", "741", "555",
    "mosfet", "jfet", "fet", "field effect transistor", "field-effect",
    "igbt", "thyristor", "scr", "triac", "diac", "ujt",
    "optocoupler", "opto-coupler", "photodiode", "phototransistor",
    "transformer", "relay", "crystal oscillator", "voltage regulator ic",
    "7805", "7812", "lm317", "ic 7805", "ne555",
)

UNMEASURED_ANALOG_ADVICE = (
    "Every SYMBOL this tool can place had its pin geometry MEASURED from a "
    "real LTspice file, and it refuses to guess at one it has never seen. "
    "What it can build today: resistors, capacitors, inductors, voltage "
    "sources, diodes, zeners and bipolar transistors."
)

DIGITAL_ADVICE = (
    "This is the ANALOG loop, which designs circuits for LTspice. A digital "
    "logic question is answered by the digital loop instead, which builds "
    "gate-level circuits and checks them against an exhaustive truth table "
    "computed by Logisim -- a far stronger guarantee than anything analog "
    "verification can offer."
)


def digital_evidence(question: str) -> list[str]:
    """Every digital signal found in the question, verbatim and in order.

    The mirror of `analog_evidence`, and returned for the same reason: a
    refusal that cannot show its reasoning is not one a reader can disagree
    with.
    """
    hits = []
    for name in (*DIGITAL_SIMULATORS, *DIGITAL_WORDS):
        match = re.search(rf"\b{re.escape(name)}\b", question, re.IGNORECASE)
        if match:
            hits.append((match.start(), match.group(0)))
    seen, unique = set(), []
    for _, item in sorted(hits):
        if item.lower() not in seen:
            seen.add(item.lower())
            unique.append(item)
    return unique


def names_a_digital_simulator(question: str) -> str | None:
    for name in DIGITAL_SIMULATORS:
        if re.search(rf"\b{re.escape(name)}\b", question, re.IGNORECASE):
            return name
    return None


def check_analog(question: str) -> None:
    """Refuse a question the ANALOG loop cannot honestly answer.

    The mirror of `check_digital`, and it exists for the mirror of that
    incident: a digital question that reached this loop would be handed to
    LTspice, which has no gates, and the failure would surface as a pin-table
    rejection four layers down -- true, expensive and unhelpful.

    The asymmetry of costs is the same. A missed digital question produces a
    confident answer to something else; a false refusal produces an annoyed
    person who rephrases, and the message says exactly what tripped it.
    """
    parts = _found(question, UNMEASURED_ANALOG)
    if parts:
        raise DomainError(
            f"This question is built around a component this tool has never "
            f"measured: {', '.join(parts)}.\n\n{UNMEASURED_ANALOG_ADVICE}")

    simulator = names_a_digital_simulator(question)
    evidence = digital_evidence(question)
    if simulator is None and len(evidence) < 2:
        return

    raise DomainError(
        f"This reads as a DIGITAL logic question, so it was refused before "
        f"anything was designed.\n\nWhat the question says: "
        f"{', '.join(evidence[:8])}\n\n{DIGITAL_ADVICE}")


@dataclass(frozen=True)
class Reading:
    """Which loop a question was routed to, and the evidence for it.

    Disclosure rather than a silent decision. Routing is a guess made from
    words, and the loop it picks then runs its OWN check_* -- so a misroute
    degrades to a refusal that names the mistake, never to a confident answer
    from the wrong half of the tool.
    """

    domain: str                  # "analog" | "digital"
    reason: str

    def render(self) -> str:
        return f"read as an {self.domain.upper()} question: {self.reason}"


def classify(question: str) -> Reading:
    """Route a question to the analog or the digital loop.

    Naming a simulator decides it outright -- a question that says LTspice is
    asking for LTspice whatever else it says. Otherwise the two evidence
    lists are counted against each other, and a tie goes to digital, which is
    the half with the stronger verification: if the routing is wrong there,
    `check_digital` refuses and says why, which is a better failure than an
    analog answer to a logic question.
    """
    analog_sim = names_a_simulator(question)
    digital_sim = names_a_digital_simulator(question)
    if analog_sim and not digital_sim:
        return Reading("analog", f"it names {analog_sim}")
    if digital_sim and not analog_sim:
        return Reading("digital", f"it names {digital_sim}")

    analog = analog_evidence(question)
    digital = digital_evidence(question)
    if len(analog) > len(digital):
        return Reading("analog", ", ".join(analog[:6]))
    if digital:
        return Reading("digital", ", ".join(digital[:6]))
    return Reading(
        "digital",
        "nothing in it names a simulator or an analog quantity, so it was "
        "read as digital by default -- the half whose answers are checked "
        "against an exhaustive truth table")
