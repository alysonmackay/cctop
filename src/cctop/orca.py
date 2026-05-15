from __future__ import annotations

import enum
import re
import time
from pathlib import Path

from .models import (
    ActiveOccupation,
    Calculation,
    ConfigWeight,
    JobType,
    LocalizedOrbital,
    LocalizedOrbitals,
    McscfState,
    NevptResult,
    Status,
    Warning,
)


FLOAT_RE = r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[Ee][-+]?\d+)?"

FATAL_PATTERNS = [
    ("ORCA_ERROR", "ORCA finished by error termination", re.compile(r"\bORCA finished by error termination\b", re.I)),
    ("SCF_NOT_CONVERGED", "SCF did not converge", re.compile(r"\bSCF\s+NOT\s+CONVERGED\b", re.I)),
    ("ERROR", "Error marker found", re.compile(r"\b(error termination|aborting the run|fatal error)\b", re.I)),
]

SUSPICIOUS_PATTERNS = [
    (
        "GEOM_NOT_CONVERGED",
        "Geometry optimization did not converge",
        re.compile(
            r"(?:geometry\s+)?optimization\s+(?:has\s+)?(?:did\s+not|not)\s+converge(?:d)?\b",
            re.I,
        ),
    ),
    (
        "MAX_ITER",
        "Maximum iteration limit was reached",
        re.compile(
            r"(?:maximum number of.*iterations|maxiter).*(?:reached|exceeded)"
            r"|(?:reached|exceeded).*(?:maximum number of.*iterations|maxiter)",
            re.I,
        ),
    ),
]

BASIS_HINTS = (
    "def2",
    "cc-p",
    "aug-cc",
    "6-",
    "3-",
    "pc-",
    "pcseg",
    "ano",
    "sto-",
    "ma-",
)

METHOD_SKIP = {
    # job/runtime keywords
    "opt",
    "optts",
    "freq",
    "numfreq",
    "anfreq",
    "engrad",
    "sp",
    # SCF convergence + grid keywords
    "tightscf",
    "verytightscf",
    "veryslowscf",
    "slowscf",
    "normalscf",
    "looseopt",
    "tightopt",
    "easyconv",
    "normalconv",
    "tightconv",
    "verytightconv",
    "sloweconv",
    "slowconv",
    "normalprint",
    "largeprint",
    "miniprint",
    "printbasis",
    "printgap",
    "printmos",
    "nopop",
    "anlyt",
    "numerical",
    "kdiis",
    "soscf",
    "nososcf",
    "uno",
    "noiter",
    "allowrhf",
    "allowuhf",
    "noopt",
    # RI / auxiliary basis fitting flags (not methods)
    "ri",
    "rijk",
    "rij",
    "ri-jk",
    "ri-j",
    "rijcosx",
    "ricc2",
    "autoaux",
    # initial guess / MO control
    "moread",
    "patom",
    "hueckel",
    "pmodel",
    "pmoread",
    "bs",
    "bsguess",
    # PNO tiers
    "tightpno",
    "loosepno",
    "normalpno",
    # grid keywords
    "grid3",
    "grid4",
    "grid5",
    "grid6",
    "grid7",
    "finalgrid3",
    "finalgrid4",
    "finalgrid5",
    "finalgrid6",
    "defgrid1",
    "defgrid2",
    "defgrid3",
    # dispersion
    "d3",
    "d3bj",
    "d3zero",
    "d4",
    # parallelism / units
    "pal",
    "pal2",
    "pal4",
    "pal8",
    "pal16",
    "pal32",
    "bohrs",
    "angstrom",
    # misc
    "conv",
    "noautostart",
    "nofrozencore",
    "frozencore",
    "useless",
}

def _strip_echo_prefix(line: str) -> str:
    return re.sub(r"^\|\s*\d+>\s*", "", line).strip()

def parse_orca(path: Path) -> Calculation:
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    calc = Calculation(path=path, program="ORCA")

    calc.version = _first_match(text, r"Program Version\s+([^\s]+)") 
    command_line = _find_orca_command(lines)
    if command_line:
        calc.method, calc.basis = _parse_method_basis(command_line)

    calc.job_type = _detect_job_type(lines)
    charge_mult = _find_charge_multiplicity(lines)
    if charge_mult:
        calc.charge, calc.multiplicity = charge_mult

    energies = [float(match) for match in re.findall(r"FINAL SINGLE POINT ENERGY\s+(" + FLOAT_RE + r")", text)]
    if energies:
        calc.final_energy = energies[-1]

    calc.gibbs_energy = _last_float_match(
        text,
        [
            r"Final Gibbs free energy\s+\.+\s+(" + FLOAT_RE + r")",
            r"Total Gibbs free energy\s+\.+\s+(" + FLOAT_RE + r")",
            r"G-E\(el\)\s+\.+\s+(" + FLOAT_RE + r")",
        ],
    )

    frequencies = _parse_frequencies(lines)
    if frequencies:
        calc.lowest_frequency = min(frequencies)
        calc.imaginary_frequency_count = sum(1 for frequency in frequencies if frequency < 0.0)
    else:
        calc.imaginary_frequency_count = None

    calc.runtime_seconds = _parse_runtime_seconds(text)
    calc.nprocs, calc.maxcore_mb = _parse_resources(lines, text)
    calc.termination = _parse_termination(text)

    calc.casscf_final_energy = _parse_casscf_final_energy(text)
    calc.casscf_states = _parse_casscf_states(lines)
    calc.mrci_states = _parse_mrci_states(lines)
    calc.nevpt2_results = _parse_nevpt2_results(lines)
    calc.localized_orbitals = _parse_localized_orbitals(lines)
    calc.active_occupations = _parse_active_occupations(lines, calc.localized_orbitals)
    calc.method = _refine_method(calc.method, lines)

    calc.warnings.extend(_collect_warnings(lines, frequencies))
    calc.status = _classify(calc, text)
    return calc


def looks_like_orca(path: Path) -> bool:
    try:
        head = path.read_text(errors="replace")[:8000]
    except OSError:
        return False
    return "O   R   C   A" in head or "ORCA" in head and "Program Version" in head


def _first_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.I)
    return match.group(1) if match else None


def _last_float_match(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        matches = re.findall(pattern, text, re.I)
        if matches:
            return float(matches[-1])
    return None


def _find_orca_command(lines: list[str]) -> str | None:
    in_input = False
    for line in lines:
        stripped = re.sub(r"^\|\s*\d+>\s*", "", line).strip() #stripping input echo line numbers 

        if "INPUT FILE" in line:
            in_input = True
            continue
        if in_input and stripped.startswith("!"):
            return stripped

    for line in lines[:500]:
        stripped = re.sub(r"^\|\s*\d+>\s*", "", line).strip()
        if stripped.startswith("!"):
            return stripped
    return None


def _parse_method_basis(command_line: str) -> tuple[str | None, str | None]:
    cleaned = command_line.lstrip("!").strip()
    tokens = [token.strip() for token in cleaned.split() if token.strip()]
    method = None
    basis = None

    for token in tokens:
        lowered = token.lower()
        if basis is None and lowered.startswith(BASIS_HINTS):
            basis = token
            continue
        if method is None and lowered not in METHOD_SKIP and not lowered.startswith("%"):
            method = token

    return method, basis


def _find_charge_multiplicity(lines: list[str]) -> tuple[int, int] | None:
    pattern = re.compile(r"^\s*\*\s+(?:xyz|int|gzmt|xyzfile)\s+(-?\d+)\s+(\d+)", re.I)
    for line in lines:
        stripped = re.sub(r"^\|\s*\d+>\s*", "", line).strip()
       # match = pattern.search(stripped)
        if pattern.search(stripped):
            return int(pattern.search(stripped).group(1)), int(pattern.search(stripped).group(2))
    return None


def _parse_frequencies(lines: list[str]) -> list[float]:
    latest_frequencies: list[float] = []
    current_frequencies: list[float] = []
    in_block = False
    for line in lines:
        if "VIBRATIONAL FREQUENCIES" in line:
            current_frequencies = []
            in_block = True
            continue
        if in_block and line.strip().startswith("NORMAL MODES"):
            latest_frequencies = current_frequencies
            current_frequencies = []
            in_block = False
            continue
        if not in_block:
            continue

        match = re.search(r"^\s*\d+\s*:\s*(" + FLOAT_RE + r")\s+cm\*\*-1", line)
        if match:
            current_frequencies.append(float(match.group(1)))

    if in_block:
        latest_frequencies = current_frequencies
    return latest_frequencies


def _parse_runtime_seconds(text: str) -> int | None:
    match = re.search(
        r"TOTAL RUN TIME:\s*(\d+)\s+days\s+(\d+)\s+hours\s+(\d+)\s+minutes\s+(\d+)\s+seconds",
        text,
        re.I,
    )
    if not match:
        return None
    days, hours, minutes, seconds = (int(part) for part in match.groups())
    return (((days * 24) + hours) * 60 + minutes) * 60 + seconds


def _parse_resources(lines: list[str], text: str) -> tuple[int | None, int | None]:
    nprocs: int | None = None
    maxcore: int | None = None

    pal_block = re.compile(r"%\s*pal\b.*?nprocs\s+(\d+)", re.I | re.S)
    pal_inline = re.compile(r"nprocs\s+(\d+)", re.I)
    maxcore_inline = re.compile(r"%\s*maxcore\s+(\d+)", re.I)

    for line in lines:
        stripped = _strip_echo_prefix(line)
        if nprocs is None:
            match = pal_inline.search(stripped) if stripped.lower().startswith(("%pal", "nprocs")) else None
            if match:
                nprocs = int(match.group(1))
        if maxcore is None:
            match = maxcore_inline.search(stripped)
            if match:
                maxcore = int(match.group(1))
        if nprocs is not None and maxcore is not None:
            break

    if nprocs is None:
        match = pal_block.search(text)
        if match:
            nprocs = int(match.group(1))
    if nprocs is None:
        match = re.search(r"Program running with\s+(\d+)\s+parallel MPI-processes", text, re.I)
        if match:
            nprocs = int(match.group(1))
    if maxcore is None:
        match = re.search(r"MaxCore\s*=\s*(\d+)\s*MB", text, re.I)
        if match:
            maxcore = int(match.group(1))

    return nprocs, maxcore


def _parse_termination(text: str) -> str | None:
    if re.search(r"\*\*\*\*ORCA TERMINATED NORMALLY\*\*\*\*", text):
        return "normal"
    if re.search(r"\bORCA finished by error termination\b", text, re.I):
        return "error"
    return None

def _collect_warnings(lines: list[str], frequencies: list[float]) -> list[Warning]:
    warnings: list[Warning] = []

    for index, line in enumerate(lines, start=1):
        for code, message, pattern in FATAL_PATTERNS + SUSPICIOUS_PATTERNS:
            if pattern.search(line):
                warnings.append(Warning(code=code, message=message, line=index))
                break

    imaginary_count = sum(1 for frequency in frequencies if frequency < 0.0)
    if imaginary_count:
        warnings.append(
            Warning(
                code="IMAGINARY_FREQUENCIES",
                message=f"{imaginary_count} imaginary frequency value(s) found",
                line=None,
            )
        )

    return _dedupe_warnings(warnings)


def _dedupe_warnings(warnings: list[Warning]) -> list[Warning]:
    seen: set[tuple[str, int | None]] = set()
    unique: list[Warning] = []
    for warning in warnings:
        key = (warning.code, warning.line)
        if key in seen:
            continue
        seen.add(key)
        unique.append(warning)
    return unique


def _classify(calc: Calculation, text: str) -> Status:
    fatal_codes = {"ORCA_ERROR", "ERROR"}
    if any(warning.code in fatal_codes for warning in calc.warnings):
        return Status.FAILED
    if calc.termination == "normal":
        if calc.warnings:
            return Status.SUSPICIOUS
        return Status.DONE
    if any(warning.code == "SCF_NOT_CONVERGED" for warning in calc.warnings):
        return Status.FAILED
    if _looks_recent(calc.path) and "ORCA TERMINATED NORMALLY" not in text:
        return Status.RUNNING
    return Status.UNKNOWN


def _looks_recent(path: Path) -> bool:
    try:
        age_seconds = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age_seconds < 2 * 60 * 60


def _detect_job_type(lines: list[str]) -> JobType:
    keyword_line = ""
    block_keywords = set() 

    for line in lines:
        stripped = _strip_echo_prefix(line) 
        if stripped.startswith("!"):
            keyword_line += " " + stripped.lstrip("!").lower()
        elif stripped.startswith("%"):
            block_keywords.add(stripped.lstrip("%").split()[0].lower())

    if "casscf" in block_keywords:
        return JobType.CASSCF 
    if "mrci" in block_keywords:
        return JobType.MRCI
    if "tddft" in block_keywords:
        return JobType.TDDFT

    #tokens = set(keyword_line.split())
    if "opt" in keyword_line:
        return JobType.OPT

    return JobType.SP


CASSCF_BLOCK_HEADER = re.compile(
    r"CAS-SCF STATES FOR BLOCK\s+(\d+)\s+MULT=\s*(\d+)\s+NROOTS=\s*(\d+)",
    re.I,
)
CASSCF_ROOT_HEADER = re.compile(r"^\s*ROOT\s+(\d+):\s+E=\s*(" + FLOAT_RE + r")\s+Eh", re.I)
CASSCF_WEIGHT_LINE = re.compile(r"^\s+(\d+\.\d+)\s+\[\s*\d+\s*\]:\s*(\S+)")

MRCI_STATE_HEADER = re.compile(
    r"^\s*STATE\s+(\d+):\s+Energy=\s*(" + FLOAT_RE + r")\s+Eh\s+RefWeight=\s*(" + FLOAT_RE + r")",
    re.I,
)
MRCI_WEIGHT_LINE = re.compile(r"^\s+(\d+\.\d+)\s+:\s+(.+?)\s*$")

NEVPT_BLOCK_HEADER = re.compile(r"MULT\s+(\d+)\s*,\s*ROOT\s+(\d+)", re.I)
NEVPT_CORRECTION = re.compile(r"Total Energy Correction\s*:\s*dE\s*=\s*(" + FLOAT_RE + r")", re.I)
NEVPT_REFERENCE = re.compile(r"Reference\s+Energy\s*:\s*E0\s*=\s*(" + FLOAT_RE + r")", re.I)
NEVPT_TOTAL = re.compile(r"Total Energy \(E0\+dE\)\s*:\s*E\s*=\s*(" + FLOAT_RE + r")", re.I)


def _parse_casscf_final_energy(text: str) -> float | None:
    match = re.search(r"Final CASSCF energy\s*:\s*(" + FLOAT_RE + r")\s+Eh", text, re.I)
    return float(match.group(1)) if match else None


def _parse_casscf_states(lines: list[str]) -> list[McscfState]:
    states: list[McscfState] = []
    block: int | None = None
    mult: int | None = None
    current: McscfState | None = None

    for line in lines:
        header = CASSCF_BLOCK_HEADER.search(line)
        if header:
            block = int(header.group(1))
            mult = int(header.group(2))
            current = None
            continue
        if block is None:
            continue

        root = CASSCF_ROOT_HEADER.match(line)
        if root:
            current = McscfState(
                root=int(root.group(1)),
                energy=float(root.group(2)),
                multiplicity=mult,
                block=block,
            )
            states.append(current)
            continue

        if current is None:
            continue

        weight = CASSCF_WEIGHT_LINE.match(line)
        if weight:
            current.weights.append(
                ConfigWeight(weight=float(weight.group(1)), occupation=weight.group(2))
            )
            continue

        stripped = line.strip()
        if stripped and not weight and not stripped.startswith("ROOT"):
            current = None

    return states


def _parse_mrci_states(lines: list[str]) -> list[McscfState]:
    states: list[McscfState] = []
    current: McscfState | None = None
    in_ci_results = False
    phase = "reference"  # flips to "final" once MR-PT SELECTION is seen

    for line in lines:
        if "REFERENCE SPACE CI" in line:
            phase = "reference"
            in_ci_results = False
            current = None
            continue
        if "MR-PT SELECTION" in line:
            phase = "final"
            in_ci_results = False
            current = None
            continue
        if "CI-RESULTS" in line:
            in_ci_results = True
            current = None
            continue
        if not in_ci_results:
            continue

        header = MRCI_STATE_HEADER.match(line)
        if header:
            current = McscfState(
                root=int(header.group(1)),
                energy=float(header.group(2)),
                reference_weight=float(header.group(3)),
                kind=phase,
            )
            states.append(current)
            continue

        if current is None:
            continue

        weight = MRCI_WEIGHT_LINE.match(line)
        if weight:
            current.weights.append(
                ConfigWeight(weight=float(weight.group(1)), occupation=weight.group(2))
            )
            continue

        if line.strip().startswith(("DENSITY", "Storing", "Now choosing", "===")):
            current = None
            in_ci_results = False

    return states


def _parse_nevpt2_results(lines: list[str]) -> list[NevptResult]:
    results: list[NevptResult] = []
    mult: int | None = None
    root: int | None = None
    correction: float | None = None
    reference: float | None = None
    total: float | None = None

    def flush() -> None:
        nonlocal correction, reference, total
        if (
            root is not None
            and correction is not None
            and reference is not None
            and total is not None
        ):
            results.append(
                NevptResult(
                    root=root,
                    multiplicity=mult,
                    reference_energy=reference,
                    correction=correction,
                    total_energy=total,
                )
            )
        correction = reference = total = None

    in_nevpt2 = False
    for line in lines:
        if "NEVPT2 Results" in line:
            in_nevpt2 = True
            continue
        if not in_nevpt2:
            continue

        header = NEVPT_BLOCK_HEADER.search(line)
        if header:
            flush()
            mult = int(header.group(1))
            root = int(header.group(2))
            continue

        if root is None:
            continue

        match = NEVPT_CORRECTION.search(line)
        if match:
            correction = float(match.group(1))
            continue
        match = NEVPT_REFERENCE.search(line)
        if match:
            reference = float(match.group(1))
            continue
        match = NEVPT_TOTAL.search(line)
        if match:
            total = float(match.group(1))
            flush()
            continue

        if line.strip().startswith("TIMINGS"):
            flush()
            in_nevpt2 = False

    flush()
    return results


LOC_RANGE_RE = re.compile(r"Orbital range for localization\s*\.+\s*(\d+)\s+to\s+(\d+)", re.I)
LOC_FOUND_RE = re.compile(
    r"FOUND\s*-\s*(\d+)\s+strongly local.*?-\s*(\d+)\s+two center bond.*?-\s*(\d+)\s+significantly delocalized",
    re.I | re.S,
)
LOC_MO_LINE_RE = re.compile(r"^\s*MO\s+(\d+):\s*(.+?)\s*$")
ACTIVE_ORBITALS_RE = re.compile(r"Active Orbitals\s*:\s*(\d+)\s*-\s*(\d+)", re.I)
LOCALIZING_SUBSPACE_RE = re.compile(r"Localizing Subspace\s+(\d+)\s*-\s*(\d+)", re.I)
NOCC_LINE_RE = re.compile(r"N\(occ\)\s*=\s*((?:\s*" + FLOAT_RE + r")+)")


def _refine_method(current: str | None, lines: list[str]) -> str | None:
    """Override an initial-guess token (RHF/UHF/None) with the actual high-level method.

    A user-friendly method label for multi-reference jobs is the post-HF block
    name, not the SCF starting guess. We promote based on detected ``%block``
    keywords.
    """
    block_keywords: set[str] = set()
    in_input = False
    for line in lines:
        stripped = _strip_echo_prefix(line)
        if "INPUT FILE" in line:
            in_input = True
            continue
        if in_input and stripped.startswith("****END OF INPUT"):
            break
        if stripped.startswith("%"):
            token = stripped.lstrip("%").split()[0].lower() if stripped.lstrip("%").split() else ""
            if token:
                block_keywords.add(token)

    cur = (current or "").lower()
    is_scf_guess = cur in {"", "rhf", "uhf", "rohf", "rks", "uks", "hf", "ks"}

    if "mrci" in block_keywords and (is_scf_guess or current is None):
        return "MRCI"
    if "nevpt2" in block_keywords:
        # NEVPT2 always runs through CASSCF; the NEVPT2 label is the more useful one.
        return current if (current and not is_scf_guess) else "NEVPT2"
    if "casscf" in block_keywords and (is_scf_guess or current is None):
        return "CASSCF"
    if "tddft" in block_keywords and (is_scf_guess or current is None):
        return "TDDFT"
    return current


def _parse_localized_orbitals(lines: list[str]) -> LocalizedOrbitals | None:
    """Capture the LAST LOCALIZED MOLECULAR ORBITAL COMPOSITIONS block in the file.

    Multiple macro-iterations may emit this block; the one printed after CASSCF
    convergence (the final iteration) is the interesting one.
    """
    # First find all block start indices
    starts = [i for i, line in enumerate(lines) if "LOCALIZED MOLECULAR ORBITAL COMPOSITIONS" in line]
    if not starts:
        return None
    start = starts[-1]

    # Find the most recent active range before the localization block
    active_range = _find_active_range(lines, start)

    result = LocalizedOrbitals(active_range=active_range)
    section: str | None = None  # current sub-section within the block

    # Pull "FOUND" counts (may span lines)
    window = "\n".join(lines[start : start + 30])
    found = LOC_FOUND_RE.search(window)
    if found:
        result.strongly_local_count = int(found.group(1))
        result.bond_count = int(found.group(2))
        result.delocalized_count = int(found.group(3))

    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if lower.startswith("strongly local"):
            section = "strong"
            continue
        if lower.startswith("bond-like"):
            section = "bond"
            continue
        if lower.startswith("more delocalized"):
            section = "deloc"
            continue
        if stripped.startswith("Localized MO's were stored"):
            break
        if stripped.startswith("--- Canonicalize") or stripped.startswith("MACRO-ITERATION"):
            break
        match = LOC_MO_LINE_RE.match(line)
        if match and section is not None:
            entry = LocalizedOrbital(mo=int(match.group(1)), composition=match.group(2).strip())
            if section == "strong":
                result.strongly_local.append(entry)
            elif section == "bond":
                result.bonds.append(entry)
            elif section == "deloc":
                result.delocalized.append(entry)

    if (
        result.active_range is None
        and not result.bonds
        and not result.strongly_local
        and not result.delocalized
    ):
        return None
    return result


def _find_active_range(lines: list[str], before: int) -> tuple[int, int] | None:
    # Search backwards from `before` for the most recent range hint.
    for i in range(before, -1, -1):
        match = LOCALIZING_SUBSPACE_RE.search(lines[i])
        if match:
            return int(match.group(1)), int(match.group(2))
        match = LOC_RANGE_RE.search(lines[i])
        if match:
            return int(match.group(1)), int(match.group(2))
    # Fall back to MRCI "Active Orbitals : X - Y"
    for line in lines:
        match = ACTIVE_ORBITALS_RE.search(line)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def _parse_active_occupations(
    lines: list[str], localized: LocalizedOrbitals | None
) -> list[ActiveOccupation]:
    """Extract natural-orbital-style occupations of the active space.

    Uses the final ``N(occ)= ...`` line emitted by the CASSCF driver and maps
    the values to MO indices using the active range.
    """
    last_values: list[float] | None = None
    for line in lines:
        match = NOCC_LINE_RE.search(line)
        if match:
            chunk = match.group(1).strip()
            try:
                last_values = [float(token) for token in chunk.split()]
            except ValueError:
                continue
    if not last_values:
        return []

    active_range: tuple[int, int] | None = (
        localized.active_range if localized and localized.active_range else None
    )
    if active_range is None:
        for line in lines:
            match = ACTIVE_ORBITALS_RE.search(line)
            if match:
                active_range = (int(match.group(1)), int(match.group(2)))
                break

    if active_range is not None:
        start, end = active_range
        mo_indices = list(range(start, end + 1))
        if len(mo_indices) != len(last_values):
            mo_indices = list(range(start, start + len(last_values)))
    else:
        mo_indices = list(range(1, len(last_values) + 1))

    return [ActiveOccupation(mo=mo, occupation=occ) for mo, occ in zip(mo_indices, last_values)]
