"""Test-session setup, and one substitution that needs explaining.

WHAT CI FOUND, AND WHY IT IS WORTH RECORDING. Before the first CI run, the
"does this suite survive with no simulators?" rehearsal was done locally by
pointing OHMWORK_LTSPICE and OHMWORK_LOGISIM at paths that do not exist. It
came back green with 25 clean skips, which looked like proof.

It was not. A Linux box lacks a third thing that rehearsal left in place:
LTspice's COMPONENT LIBRARIES, which `PartsLibrary.locate_lib_dir` finds under
%LOCALAPPDATA% on this machine no matter what the two override variables say.
47 tests that had "passed" the rehearsal died on the real runner. The check
looked like it had examined the CI condition and had examined two thirds of
it — the same shape as every other incident in this project, in the tooling
around it rather than in the tool.

THE FIX, AND WHAT IT DOES NOT CLAIM. The 46 manifest-contract tests do not
care WHICH parts library exists; they need the device policy to resolve at
all so a question can be loaded and published. So when no real library is
present, they get the committed fixture extract instead, renamed to the
filenames `locate_lib_dir` looks for. That keeps the manifest contract — the
published-format checks, the ones most worth running on every push — actually
running in CI, rather than skipping 46 tests and calling the result green.

What it deliberately does NOT do is stand in for the real inventory. The
tests that assert facts about a real install (264 zeners, 2N3904 present,
1N4007 as the default rectifier) are gated on REAL_PARTS_LIBRARY below, which
is captured HERE, at import time, before anything is substituted. They skip
in CI and say so. A stand-in library answering a question about the real one
would be a check agreeing with itself, which is the failure this project
spends most of its machinery avoiding.
"""

import os
import shutil

import pytest

from ohmwork.parts import PartsLibrary

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

#: Whether a REAL LTspice component library is installed, captured before the
#: substitution below can hide the answer. Import this rather than calling
#: locate_lib_dir() again from a test module: after the session fixture runs,
#: that call cannot tell a real install from the stand-in.
REAL_PARTS_LIBRARY = PartsLibrary.locate_lib_dir()


@pytest.fixture(scope="session", autouse=True)
def parts_library(tmp_path_factory):
    """Guarantee that *a* parts library exists for the whole session.

    A session fixture, not a `pytest_configure` hook, and the difference
    matters: skipif markers are evaluated at collection time, so a hook would
    run first and the real-install tests would then try to assert real-install
    facts against the fixture extract. REAL_PARTS_LIBRARY above makes that
    ordering explicit instead of relying on it.
    """
    if REAL_PARTS_LIBRARY is not None:
        yield REAL_PARTS_LIBRARY
        return

    stand_in = tmp_path_factory.mktemp("ltspice-lib-stand-in")
    # mini.dio / mini.bjt are extracts of a real install, encodings included:
    # mini.dio is plain ASCII and mini.bjt is UTF-16LE with no BOM, exactly
    # like the files LTspice ships. Renamed here to what locate_lib_dir looks
    # for; the bytes are untouched.
    shutil.copyfile(os.path.join(FIXTURES, "mini.dio"),
                    os.path.join(stand_in, "standard.dio"))
    shutil.copyfile(os.path.join(FIXTURES, "mini.bjt"),
                    os.path.join(stand_in, "standard.bjt"))

    previous = os.environ.get("OHMWORK_LTSPICE_LIB")
    os.environ["OHMWORK_LTSPICE_LIB"] = str(stand_in)
    try:
        yield stand_in
    finally:
        if previous is None:
            os.environ.pop("OHMWORK_LTSPICE_LIB", None)
        else:
            os.environ["OHMWORK_LTSPICE_LIB"] = previous
