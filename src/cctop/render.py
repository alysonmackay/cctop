from __future__ import annotations

import re
from pathlib import Path

from .colors import (
    CYAN as STATUS_COLOR_CYAN,
    DIM as STATUS_COLOR_DIM,
    GREEN as STATUS_COLOR_GREEN,
    RED as STATUS_COLOR_RED,
    YELLOW as STATUS_COLOR_YELLOW,
    accent,
    dim,
    header,
    paint,
    status_text,
)
from .models import Calculation, JobType, McscfState, NevptResult, Status
from .scan import summarize_status


def format_seconds(seconds: int | None) -> str:
    if seconds is None:
        return "--"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return "--"
    return f"{value:.{digits}f}"


def status_line(calculations: list[Calculation]) -> str:
    counts = summarize_status(calculations)
    return (
        f"{len(calculations)} calculations | "
        f"{paint(str(counts[Status.DONE]) + ' done', STATUS_COLOR_GREEN)} | "
        f"{paint(str(counts[Status.FAILED]) + ' failed', STATUS_COLOR_RED)} | "
        f"{paint(str(counts[Status.SUSPICIOUS]) + ' suspicious', STATUS_COLOR_YELLOW)} | "
        f"{paint(str(counts[Status.RUNNING]) + ' running', STATUS_COLOR_CYAN)} | "
        f"{paint(str(counts[Status.UNKNOWN]) + ' unknown', STATUS_COLOR_DIM)}"
    )


def plain_report(calculations: list[Calculation], root: Path | None = None) -> str:
    if len(calculations) == 1:
        return single_report(calculations[0], root=root)

    rows = [
        header("cctop"),
        status_line(calculations),
        "",
        header(f"{'Status':<11} {'File':<42} {'Energy Eh':>16} {'Imag':>5} {'Runtime':>9}"),
        dim("-" * 88),
    ]
    for calc in calculations:
        path = _display_path(calc, root)
        status_cell = _pad_visible(status_text(calc.status), 11)
        rows.append(
            f"{status_cell} {path:<42.42} "
            f"{format_float(calc.final_energy):>16} "
            f"{_none_dash(calc.imaginary_frequency_count):>5} "
            f"{format_seconds(calc.runtime_seconds):>9}"
        )
    return "\n".join(rows)


def single_report(calc: Calculation, root: Path | None = None) -> str:
    path = _display_path(calc, root)
    fields = [
        ("File", path),
        ("Status", status_text(calc.status)),
        ("Program", _none_dash(calc.program)),
        ("Version", _none_dash(calc.version)),
        ("Job type", accent(calc.job_type.value)),
        ("Method", _none_dash(calc.method)),
        ("Basis", _none_dash(calc.basis)),
        ("Charge/Mult", _charge_mult(calc)),
        ("Final energy", _energy(calc.final_energy)),
        ("Gibbs energy", _energy(calc.gibbs_energy)),
        ("Imaginary frequencies", _none_dash(calc.imaginary_frequency_count)),
        ("Lowest frequency", _frequency(calc.lowest_frequency)),
        ("Runtime", format_seconds(calc.runtime_seconds)),
        ("Resources", _resources(calc)),
        ("Termination", _none_dash(calc.termination)),
        ("Warnings", _warning_count(calc.warning_count)),
    ]
    width = max(len(label) for label, _ in fields)
    rows = [header(path), ""]
    rows.extend(f"{label + ':':<{width + 1}} {value}" for label, value in fields[1:])
    if calc.warnings:
        rows.append("")
        rows.append(header("Warning markers:"))
        rows.extend(f"- {paint(warning.code, STATUS_COLOR_YELLOW)}: {warning.message}" for warning in calc.warnings)

    rows.extend(_mcscf_sections(calc))
    return "\n".join(rows)


def _mcscf_sections(calc: Calculation) -> list[str]:
    rows: list[str] = []
    if calc.casscf_final_energy is not None or calc.casscf_states:
        rows.append("")
        rows.append(header("CASSCF:"))
        if calc.casscf_final_energy is not None:
            rows.append(f"  Final/averaged energy: {calc.casscf_final_energy:.8f} Eh")
        rows.extend(_state_lines(calc.casscf_states))
    if calc.mrci_states:
        rows.append("")
        rows.append(header("MRCI:"))
        rows.extend(_state_lines(calc.mrci_states))
    if calc.nevpt2_results:
        rows.append("")
        rows.append(header("NEVPT2:"))
        for result in calc.nevpt2_results:
            rows.append(f"  {accent(_nevpt_label(result))}")
            rows.append(f"    Reference E0  : {result.reference_energy:.8f} Eh")
            rows.append(f"    Correction dE : {result.correction:.8f} Eh")
            rows.append(f"    Total (E0+dE) : {result.total_energy:.8f} Eh")
    return rows


def _state_lines(states: list[McscfState]) -> list[str]:
    rows: list[str] = []
    for state in states:
        rows.append(f"  {accent(_state_label(state))}")
        for weight in state.weights:
            rows.append(f"    {weight.weight:7.4f}  {weight.occupation}")
    return rows


def _state_label(state: McscfState) -> str:
    parts = [f"Root {state.root}"]
    if state.block is not None:
        parts.append(f"block {state.block}")
    if state.multiplicity is not None:
        parts.append(f"mult {state.multiplicity}")
    label = ", ".join(parts)
    extras = [f"E = {state.energy:.8f} Eh"]
    if state.reference_weight is not None:
        extras.append(f"W(ref) = {state.reference_weight:.4f}")
    return f"{label}: " + ", ".join(extras)


def _nevpt_label(result: NevptResult) -> str:
    if result.multiplicity is not None:
        return f"Root {result.root}, mult {result.multiplicity}"
    return f"Root {result.root}"


def _display_path(calc: Calculation, root: Path | None) -> str:
    if root is None:
        return str(calc.path)
    try:
        return str(calc.path.relative_to(root))
    except ValueError:
        return str(calc.path)


def _none_dash(value: object | None) -> str:
    return dim("--") if value is None else str(value)


def _energy(value: float | None) -> str:
    return dim("--") if value is None else f"{value:.8f} Eh"


def _frequency(value: float | None) -> str:
    return dim("--") if value is None else f"{value:.2f} cm^-1"


def _charge_mult(calc: Calculation) -> str:
    if calc.charge is None or calc.multiplicity is None:
        return dim("--")
    return f"{calc.charge}/{calc.multiplicity}"


def _resources(calc: Calculation) -> str:
    if calc.nprocs is None and calc.maxcore_mb is None:
        return dim("--")
    nprocs = str(calc.nprocs) if calc.nprocs is not None else "?"
    maxcore = f"{calc.maxcore_mb} MB" if calc.maxcore_mb is not None else "? MB"
    return f"{nprocs} proc, {maxcore}/proc"


def _warning_count(count: int) -> str:
    if count == 0:
        return "0"
    return paint(str(count), STATUS_COLOR_YELLOW)


def _pad_visible(text: str, width: int) -> str:
    visible = _visible_len(text)
    if visible >= width:
        return text
    return text + " " * (width - visible)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))
