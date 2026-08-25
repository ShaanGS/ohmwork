"""Refusing a question this loop cannot answer.

THE INCIDENT THIS FILE EXISTS FOR (2026-08-26). The real Q3 -- a bridge
rectifier, a C-L-C filter with 470 uF and 1 mH, a zener regulator, 12 V RMS
at 50 Hz into a 1 k load, asked explicitly "using LTspice" -- was typed into
the digital endpoint. Nothing refused it. The model produced a
"specification":

    RECT_OUT   = AC
    FILTER_OUT = AC
    REG_OUT    = AC
    LOAD_CURR  = LOAD

It designed wires for it, Logisim evaluated the wires, the wires agreed with
the specification on all four input combinations, and the page reported
**VERIFIED** in green with a download button.

Every individual claim there was true. The circuit really does compute that
specification, and Logisim really did confirm it. The result was still
worthless, because the specification was not a reading of the question -- it
was a category error, treating 12 V RMS waveforms as boolean signals. This is
the single worst output this project can produce: confident, externally
checked, and meaningless.

The lesson is that "verified" only ever meant *the circuit matches the spec*.
Nothing downstream of the spec can notice that the spec was nonsense, so the
refusal has to happen at or before the spec -- and in more than one way,
because each layer here has a different blind spot:

1. **A deterministic screen**, before any model call. Costs nothing, cannot
   be talked out of it, and catches the case above on "LTspice", "470 uF",
   "50 Hz", "12 V RMS", "rectifier", "zener".
2. **A refusal channel in the prompt**, for analog questions written in
   words the screen does not know.
3. **A structural check on the spec itself**: if every output is a bare copy
   of an input, the "specification" contains no logic. That is the shape the
   incident actually produced, and it catches whatever the first two miss --
   including questions in a domain nobody has thought of yet.
"""

import pytest

from ohmwork import domain


# The verbatim question from the incident. Kept whole: a paraphrase of the
# thing that broke is not the thing that broke.
Q3 = (
    "Design and simulate a regulated 6.2 V DC power supply using LTspice. The "
    "circuit should consist of a bridge rectifier, a C-L-C smoothing filter "
    "with C = 470 µF and L = 1 mH, followed by a Zener-diode voltage "
    "regulator. The bridge rectifier is driven by a 12 V RMS, 50 Hz AC supply, "
    "and a 1 kΩ resistive load is connected at the regulated output. "
    "Obtain and observe the input AC waveform, bridge rectifier output "
    "waveform, C-L-C filter output waveform, regulated DC output waveform, and "
    "load current waveform using LTspice."
)

DIGITAL = [
    "Design a 2-to-4 decoder with an active-high enable.",
    "Design a 4-to-2 priority encoder with an enable input and a valid "
    "output, using basic gates only.",
    "Design a circuit that outputs 1 when exactly two of its three inputs "
    "are high.",
    "Implement a full adder using only NOT, AND and OR gates.",
    "Design a 4-bit even parity generator.",
]

# The real Q4, verbatim from the lab manual. It is a DIGITAL question and it
# is still refused, for a reason that has nothing to do with analog: the 7447
# and the seven-segment display live in Logisim Evolution's TTL and I/O
# libraries, and no file containing them has ever been measured here. The pin
# table refuses anything unmeasured, so the circuit could be drawn but every
# wire on it would be a guess.
Q4 = (
    "Using Logisim Evolution, design a BCD-to-seven-segment display circuit "
    "using the 7447-decoder IC. Connect the decoder outputs to a "
    "seven-segment display and test all 16 possible 4-bit input "
    "combinations. Record the segment pattern displayed for each input and "
    "identify which codes correspond to valid BCD digits (0-9) and which "
    "correspond to invalid BCD codes (10-15)."
)


# ------------------------------------------------ layer 1: the screen


def test_the_question_from_the_incident_is_refused():
    with pytest.raises(domain.DomainError) as caught:
        domain.check_digital(Q3)
    message = str(caught.value)
    # The refusal must show its evidence, not just assert a verdict. A reader
    # who disagrees has to be able to see what triggered it.
    assert "LTspice" in message
    assert "470" in message or "µF" in message or "uF" in message


def test_a_quantity_is_quoted_whole():
    """"470 uF", not "0 uF".

    The evidence line is there to be checked against the question by eye, and
    a fragment that does not appear in the question is worse than no evidence
    at all -- the reader goes looking for it and finds nothing.
    """
    found = domain.analog_evidence("a C-L-C filter with C = 470 uF and L = 1 mH")
    assert "470 uF" in found
    assert "1 mH" in found


def test_a_simulator_named_in_the_question_is_enough_on_its_own():
    """One word is decisive here. A question that names LTspice is asking for
    something this endpoint cannot do, whatever else it says."""
    with pytest.raises(domain.DomainError):
        domain.check_digital("Build something in LTspice.")


@pytest.mark.parametrize("question", DIGITAL)
def test_real_digital_questions_are_not_refused(question):
    """The cost of a false refusal is the whole product, so the digital
    questions this project has actually seen are pinned as a set."""
    domain.check_digital(question)


def test_one_incidental_analog_word_does_not_refuse_a_digital_question():
    """A threshold, not a keyword. "5 V supply" appears in perfectly ordinary
    digital questions, and a screen that fires on it would refuse more real
    questions than fake ones."""
    domain.check_digital(
        "Design a 3-input majority circuit that runs from a 5 V supply.")


# ------------------------------- the other two things it cannot answer
#
# Both found by the owner typing the real Q4 into the box, and both were
# hiding behind a broken word matcher: `\b` had been mangled into a literal
# backspace character, so every one of these lookups silently matched
# nothing. The screen reported "clean" because it was blind, which is this
# project's oldest failure shape wearing a new hat.


def test_a_sequential_circuit_is_refused_because_a_truth_table_cannot_check_it():
    """A counter has state. Its outputs are not a function of its present
    inputs, so the exhaustive-table comparison the entire loop rests on has
    nothing to compare -- and this test file previously listed a counter as a
    question that SHOULD pass.
    """
    with pytest.raises(domain.DomainError, match="SEQUENTIAL"):
        domain.check_digital("Design a 4-bit binary counter with a clock.")

    message = str(pytest.raises(
        domain.DomainError,
        domain.check_digital,
        "Design a D flip-flop based shift register.").value)
    assert "truth table" in message


def test_the_real_Q4_is_refused_for_the_PART_not_for_being_analog():
    """Q4 is a digital question, and the refusal has to say the real reason.

    Telling someone their digital question was refused as "analog" answers an
    objection nobody made, and sends them off to fix something that was never
    wrong.
    """
    with pytest.raises(domain.DomainError) as caught:
        domain.check_digital(Q4)

    message = str(caught.value)
    assert "seven-segment" in message
    assert "measured" in message
    assert "ANALOG" not in message
    # It must say WHY the part is missing: a measurement nobody has made, not
    # a feature nobody has written.
    assert "real file" in message


def test_the_refusal_names_the_part_the_question_actually_used():
    with pytest.raises(domain.DomainError, match="7447"):
        domain.check_digital("Wire a 7447 to drive the segments.")


def test_the_refusal_says_what_to_do_instead():
    """Analog is not unsupported by this PROJECT -- only by this endpoint.
    A refusal that does not say so reads as "your question is invalid"."""
    with pytest.raises(domain.DomainError, match="LTspice"):
        domain.check_digital(Q3)
    assert "command line" in domain.ANALOG_ADVICE.lower() or \
           "locally" in domain.ANALOG_ADVICE.lower()


def test_the_evidence_is_reported_without_raising_too():
    """The same screen, as a value rather than an exception, so a caller can
    show what it found without catching anything."""
    found = domain.analog_evidence(Q3)
    assert len(found) >= 3
    assert domain.analog_evidence(DIGITAL[0]) == []


# --------------------------------- layer 3: a spec with no logic in it


def test_a_spec_whose_outputs_are_bare_copies_of_inputs_is_refused():
    """The exact shape the incident produced: RECT_OUT = AC, REG_OUT = AC.

    Verifiable, verified, and empty. Nothing downstream can tell the
    difference between this and a real answer, which is why it is caught
    here rather than reported as a warning next to a green badge.
    """
    from ohmwork.spec import Spec

    spec = Spec(inputs=("AC", "LOAD"),
                outputs=("RECT_OUT", "FILTER_OUT", "LOAD_CURR"),
                expressions={"RECT_OUT": "AC", "FILTER_OUT": "AC",
                             "LOAD_CURR": "LOAD"},
                notes=())
    with pytest.raises(domain.DomainError, match="no logic"):
        domain.check_spec_has_logic(spec)


def test_a_spec_with_real_logic_passes_even_if_ONE_output_is_a_copy():
    """A pass-through output beside real logic is a legitimate design. The
    check fires only when there is no logic ANYWHERE -- a narrow rule, so
    that it stays trustworthy when it does fire."""
    from ohmwork.spec import Spec

    spec = Spec(inputs=("EN", "I0", "I1"), outputs=("Y", "PASS"),
                expressions={"Y": "EN & (I0 | I1)", "PASS": "EN"},
                notes=())
    domain.check_spec_has_logic(spec)


def test_a_constant_output_is_not_mistaken_for_logic():
    from ohmwork.spec import Spec

    spec = Spec(inputs=("A",), outputs=("Y", "Z"),
                expressions={"Y": "A", "Z": "0"}, notes=())
    with pytest.raises(domain.DomainError, match="no logic"):
        domain.check_spec_has_logic(spec)
