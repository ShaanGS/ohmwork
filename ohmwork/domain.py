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
UNMEASURED_PARTS = (
    "7447", "7448", "7segment", "seven-segment", "seven segment",
    "7-segment", "74ls47", "74ls48", "display", "multiplexer ic", "74ls",
)

UNMEASURED_ADVICE = (
    "Every component this tool can place had its pin geometry MEASURED from "
    "a real file, and it refuses to guess at one it has never seen. The 7447 "
    "and the seven-segment display live in Logisim Evolution's TTL and I/O "
    "libraries, and no file containing them has ever been measured here -- "
    "so the circuit could be drawn, but every wire on it would be a guess."
)

ANALOG_ADVICE = (
    "This endpoint answers DIGITAL logic questions only, because the only "
    "simulator it can run on a server is Logisim. Analog questions are not "
    "unsupported by ohmwork -- they are solved on the command line, locally, "
    "where LTspice is installed."
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
