from __future__ import annotations

from pathlib import Path

from .models import Calculation, JobType, Status
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
        f"{counts[Status.DONE]} done | "
        f"{counts[Status.FAILED]} failed | "
        f"{counts[Status.SUSPICIOUS]} suspicious | "
        f"{counts[Status.RUNNING]} running | "
        f"{counts[Status.UNKNOWN]} unknown"
    )


def plain_report(calculations: list[Calculation], root: Path | None = None) -> str:
    if len(calculations) == 1:
        return single_report(calculations[0], root=root)

    rows = [
        "cctop",
        status_line(calculations),
        "",
        f"{'Status':<11} {'File':<42} {'Energy Eh':>16} {'Imag':>5} {'Runtime':>9}",
        "-" * 88,
    ]
    for calc in calculations:
        path = _display_path(calc, root)
        rows.append(
            f"{calc.status.value:<11} {path:<42.42} "
            f"{format_float(calc.final_energy):>16} "
            f"{_none_dash(calc.imaginary_frequency_count):>5} "
            f"{format_seconds(calc.runtime_seconds):>9}"
        )
    return "\n".join(rows)


def single_report(calc: Calculation, root: Path | None = None) -> str:
    path = _display_path(calc, root)
    fields = [
        ("File", path),
        ("Status", calc.status.value),
        ("Program", _none_dash(calc.program)),
        ("Version", _none_dash(calc.version)),
        ("Job type", calc.job_type.value),
        ("Method", _none_dash(calc.method)),
        ("Basis", _none_dash(calc.basis)),
        ("Charge/Mult", _charge_mult(calc)),
        ("Final energy", _energy(calc.final_energy)),
        ("Gibbs energy", _energy(calc.gibbs_energy)),
        ("Imaginary frequencies", _none_dash(calc.imaginary_frequency_count)),
        ("Lowest frequency", _frequency(calc.lowest_frequency)),
        ("Runtime", format_seconds(calc.runtime_seconds)),
        ("Termination", _none_dash(calc.termination)),
        ("Warnings", str(calc.warning_count)),
    ]
    width = max(len(label) for label, _ in fields)
    rows = [path, ""]
    rows.extend(f"{label + ':':<{width + 1}} {value}" for label, value in fields[1:])
    if calc.warnings:
        rows.append("")
        rows.append("Warning markers:")
        rows.extend(f"- {warning.code}: {warning.message}" for warning in calc.warnings)
    return "\n".join(rows)


def _display_path(calc: Calculation, root: Path | None) -> str:
    if root is None:
        return str(calc.path)
    try:
        return str(calc.path.relative_to(root))
    except ValueError:
        return str(calc.path)


def _none_dash(value: object | None) -> str:
    return "--" if value is None else str(value)


def _energy(value: float | None) -> str:
    return "--" if value is None else f"{value:.8f} Eh"


def _frequency(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f} cm^-1"


def _charge_mult(calc: Calculation) -> str:
    if calc.charge is None or calc.multiplicity is None:
        return "--"
    return f"{calc.charge}/{calc.multiplicity}"
