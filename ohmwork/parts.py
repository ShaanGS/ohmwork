"""Match a question's device spec to a real part in LTspice's libraries.

Why this exists: a hand-rolled `.model DZ D(BV=8.3)` does not describe an
8.3 V zener, because SPICE defines BV at IBV (default 1 mA) while datasheet
Vz is quoted at a test current, usually 5 mA. Synthesised cards therefore
produce numbers no lab manual agrees with. Since LTspice is the
authoritative backend, we use the parts its own libraries ship — the same
parts a student picking devices by hand would use.

Empirical facts about the LTspice 26.0.2.1 libraries (derived from the
actual files, not documentation):
  - they live in %LOCALAPPDATA%/LTspice/lib/cmp/ (standard.dio, .bjt, ...)
  - standard.dio is plain ASCII; standard.bjt is UTF-16LE with NO BOM
  - entries may continue over lines starting with '+'
  - zeners are marked `type=Zener` and carry `Vpk=<nominal Vz>`, which is
    the matching key; BV alone is NOT the nominal voltage
  - a diode can carry Vpk without being a zener (1N4148 has Vpk=75,
    type=silicon), so filter on type, never on Vpk presence

Substitutions must always be reported to the user, never made silently:
a misleading number from a quietly swapped device is worse than an error.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

MODEL_RE = re.compile(
    r"^\.model\s+(?P<name>\S+)\s+(?P<kind>\w+)\s*\((?P<params>.*)\)\s*$",
    re.IGNORECASE,
)
VPK_RE = re.compile(r"\bVpk=([0-9.]+)", re.IGNORECASE)
TYPE_RE = re.compile(r"\btype=(\S+)", re.IGNORECASE)

# Preferred defaults when a question says just "npn"/"pnp" with no part
# number. Both verified present in the 26.0.2.1 libraries; if a machine
# lacks them we fall back to the alphabetically first part of the right
# polarity, which keeps the choice deterministic.
PREFERRED_BJTS = {"npn": ["2N3904", "2N2222"], "pnp": ["2N3906", "2N2907"]}

# When a question says only "rectifier"/"diode" with no part number.
# 1N4007 (1 A / 1000 V general-purpose rectifier) first, small-signal
# 1N4148 as fallback; both verified present in the 26.0.2.1 library.
PREFERRED_DIODES = ["1N4007", "1N4148"]


class UnknownPartError(KeyError):
    """A requested part is not in the local libraries."""


@dataclass(frozen=True)
class Zener:
    name: str
    vpk: float


@dataclass(frozen=True)
class Bjt:
    name: str
    polarity: str  # "npn" | "pnp"


@dataclass(frozen=True)
class Substitution:
    """A real part chosen for a requested spec. Always show describe()."""

    part: str
    nominal: float    # the part's actual Vz
    requested: float  # what the question asked for

    @property
    def exact(self) -> bool:
        return self.nominal == self.requested

    def describe(self) -> str:
        if self.exact:
            return f"using {self.part}, Vz={self.nominal} V (exact match)"
        return (
            f"using {self.part}, Vz={self.nominal} V "
            f"(question asked for {self.requested} V)"
        )


@dataclass(frozen=True)
class DeviceChoice:
    """The outcome of the device policy. `report` is always shown.

    Policy, in priority order (see CLAUDE.md, "Device models"):
      "named"       - the question named a part; use exactly that.
      "synthesized" - the question specified a parameter value; a real
                      part nearby would silently answer a slightly
                      different question, so synthesise a model anchored
                      at exactly the asked value.
      "nearest"     - the question was vague; nearest real part, with
                      the substitution spelled out.
    """

    part: str
    directive: str | None  # .model card when synthesized, else None
    policy: str            # "named" | "synthesized" | "nearest"
    report: str


class PartsLibrary:
    def __init__(self, dio_path: Path, bjt_path: Path):
        self.dio_path = Path(dio_path)
        self.bjt_path = Path(bjt_path)

    @staticmethod
    def locate_lib_dir() -> Path | None:
        """The bundled component libraries, or None if not installed."""
        candidates = []
        if os.environ.get("OHMWORK_LTSPICE_LIB"):
            candidates.append(Path(os.environ["OHMWORK_LTSPICE_LIB"]))
        if os.environ.get("LOCALAPPDATA"):
            candidates.append(
                Path(os.environ["LOCALAPPDATA"]) / "LTspice" / "lib" / "cmp"
            )
        for c in candidates:
            if (c / "standard.dio").is_file():
                return c
        return None

    @classmethod
    def locate(cls) -> "PartsLibrary":
        lib_dir = cls.locate_lib_dir()
        if lib_dir is None:
            raise FileNotFoundError(
                "LTspice component libraries not found. Looked in "
                "%LOCALAPPDATA%/LTspice/lib/cmp and $OHMWORK_LTSPICE_LIB. "
                "Install LTspice or set OHMWORK_LTSPICE_LIB."
            )
        return cls(lib_dir / "standard.dio", lib_dir / "standard.bjt")

    # ------------------------------------------------------------ inventory

    def zeners(self) -> list[Zener]:
        found = []
        for name, kind, params in _models(self.dio_path):
            if kind.upper() != "D":
                continue
            type_m = TYPE_RE.search(params)
            vpk_m = VPK_RE.search(params)
            if type_m and type_m[1].lower() == "zener" and vpk_m:
                found.append(Zener(name, float(vpk_m[1])))
        return found

    def bjts(self) -> list[Bjt]:
        return [
            Bjt(name, kind.lower())
            for name, kind, _ in _models(self.bjt_path)
            if kind.upper() in ("NPN", "PNP")
        ]

    def find_diode(self, name: str) -> str:
        """Validate a named plain diode (path a for type 'diode')."""
        for model, kind, _ in _models(self.dio_path):
            if model == name and kind.upper() == "D":
                return name
        raise UnknownPartError(f"{name} is not a diode in {self.dio_path}")

    # ------------------------------------------------------------- matching

    def find_zener(self, vz: float) -> Substitution:
        zeners = self.zeners()
        if not zeners:
            raise UnknownPartError(f"no zeners found in {self.dio_path}")
        # Nearest Vpk wins; alphabetical name breaks ties so the same
        # question always gets the same part.
        best = min(zeners, key=lambda z: (abs(z.vpk - vz), z.name))
        return Substitution(part=best.name, nominal=best.vpk, requested=vz)

    def choose_zener(
        self,
        *,
        part: str | None = None,
        vz: float | None = None,
        exact: bool = True,
    ) -> DeviceChoice:
        """Apply the device policy for a zener spec. Never silent.

        `exact=True` means the question stated the value as a requirement
        ("Vz = 8.3 V"); `exact=False` means the value is only a hint
        distilled from a vague question ("an ~8V zener"). The LLM layer
        decides which was meant; this function just honours it.
        """
        if part is not None:
            if part not in {z.name for z in self.zeners()}:
                raise UnknownPartError(
                    f"{part} is not a zener in {self.dio_path}"
                )
            return DeviceChoice(
                part=part,
                directive=None,
                policy="named",
                report=f"using {part}, the part the question names",
            )
        if vz is not None and exact:
            name, directive = synthesize_zener(vz)
            return DeviceChoice(
                part=name,
                directive=directive,
                policy="synthesized",
                report=(
                    f"using a synthesised model: Vz={vz} V anchored at a "
                    f"5m test current, because the question specifies "
                    f"exactly {vz} V and a nearby real part would answer "
                    "a slightly different question"
                ),
            )
        if vz is not None:
            sub = self.find_zener(vz)
            return DeviceChoice(
                part=sub.part,
                directive=None,
                policy="nearest",
                report=sub.describe(),
            )
        raise ValueError("choose_zener needs a part name or a vz")

    def choose_diode(self, *, part: str | None = None) -> DeviceChoice:
        """Plain diodes: path (a) named, else path (c) library default.
        There is no path (b): a generic diode has no single parameter a
        question states the way a zener has Vz."""
        if part is not None:
            return DeviceChoice(
                part=self.find_diode(part), directive=None, policy="named",
                report=f"using {part}, the part the question names",
            )
        zeners = {z.name for z in self.zeners()}
        available = {name for name, kind, _ in _models(self.dio_path)
                     if kind.upper() == "D" and name not in zeners}
        for preferred in PREFERRED_DIODES:
            if preferred in available:
                return DeviceChoice(
                    part=preferred, directive=None, policy="nearest",
                    report=(
                        f"question names no diode; using {preferred} "
                        "(general-purpose rectifier default)"
                    ),
                )
        if not available:
            raise UnknownPartError(f"no diodes in {self.dio_path}")
        fallback = min(available)
        return DeviceChoice(
            part=fallback, directive=None, policy="nearest",
            report=f"question names no diode; using {fallback} "
                   "(first available)",
        )

    def choose_bjt(
        self,
        polarity: str,
        *,
        part: str | None = None,
        params: dict | None = None,
    ) -> DeviceChoice:
        """The same three-path policy as choose_zener, for BJTs.
        (a) named part; (b) question-specified parameters, synthesised
        exactly; (c) vague, library default with the choice reported."""
        if part is not None:
            return DeviceChoice(
                part=self.find_bjt(polarity, part),
                directive=None,
                policy="named",
                report=f"using {part}, the part the question names",
            )
        if params:
            name, directive = synthesize_bjt(polarity, params)
            spec = " ".join(f"{k}={v}" for k, v in params.items())
            return DeviceChoice(
                part=name,
                directive=directive,
                policy="synthesized",
                report=(
                    f"using a synthesised {polarity.upper()}({spec}) "
                    "model, the parameters the question specifies"
                ),
            )
        default = self.find_bjt(polarity)
        return DeviceChoice(
            part=default,
            directive=None,
            policy="nearest",
            report=(
                f"question names no {polarity} part; "
                f"using {default} (library default)"
            ),
        )

    def find_bjt(self, polarity: str, name: str | None = None) -> str:
        available = {b.name for b in self.bjts() if b.polarity == polarity}
        if name is not None:
            if name not in available:
                raise UnknownPartError(
                    f"{name} is not a {polarity} in {self.bjt_path}"
                )
            return name
        for preferred in PREFERRED_BJTS[polarity]:
            if preferred in available:
                return preferred
        if not available:
            raise UnknownPartError(f"no {polarity} in {self.bjt_path}")
        return min(available)


# ------------------------------------------------------------ file reading


def _read_lib(path: Path) -> str:
    """Decode a library file, handling LTspice's mixed encodings."""
    raw = Path(path).read_bytes()
    if b"\x00" in raw[:64]:  # standard.bjt: UTF-16LE, no BOM
        return raw.decode("utf-16-le")
    return raw.decode("latin-1")


def _models(path: Path):
    """Yield (name, kind, params) for each .model entry, with '+'
    continuation lines joined."""
    joined = []
    for line in _read_lib(path).splitlines():
        if line.startswith("+"):
            if joined:
                joined[-1] += " " + line[1:].strip()
        else:
            joined.append(line.strip())
    for line in joined:
        if m := MODEL_RE.match(line):
            yield m["name"], m["kind"], m["params"]


# ------------------------------------------------------- policy compliance

_DIODE_MODEL_RE = re.compile(r"^\.model\s+\S+\s+D\s*\(", re.IGNORECASE)
_HAS_BV_RE = re.compile(r"\bBV\s*=", re.IGNORECASE)
_HAS_IBV_RE = re.compile(r"\bIBV\s*=", re.IGNORECASE)


def unanchored_diode_card(directive: str) -> bool:
    """True if this is a diode .model card with BV but no IBV.

    Such a card puts the breakdown voltage at SPICE's 1 mA default
    instead of the datasheet test current, i.e. it describes a device
    nobody asked for. This class of card once reached a deliverable and
    four pinned baselines despite the policy existing and being tested
    (see CLAUDE.md, "Why anchored models matter"), so it is banned
    mechanically wherever it appears, not by convention.
    """
    return bool(
        _DIODE_MODEL_RE.match(directive)
        and _HAS_BV_RE.search(directive)
        and not _HAS_IBV_RE.search(directive)
    )


# -------------------------------------------------------------- synthesis


def synthesize_zener(vz: float, test_current: str = "5m") -> tuple[str, str]:
    """Fallback when no adequate real part exists: a card anchored at the
    datasheet test current. Results using it must be labeled approximate.

    BV is defined as the voltage where reverse current equals IBV, so a
    card without IBV puts Vz at SPICE's 1 mA default instead of the
    datasheet's quoted test current (usually 5 mA).
    """
    name = "DZ" + str(vz).replace(".", "V")
    return name, f".model {name} D(BV={vz} IBV={test_current})"


def synthesize_bjt(polarity: str, params: dict) -> tuple[str, str]:
    """A BJT card at exactly the question's parameters (policy path b)."""
    kind = polarity.upper()
    name = "Q" + kind + "".join(
        f"_{k}{v}" for k, v in params.items()
    ).replace(".", "V")
    spec = " ".join(f"{k}={v}" for k, v in params.items())
    return name, f".model {name} {kind}({spec})"
