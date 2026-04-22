from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    DONE = "DONE"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    SUSPICIOUS = "SUSPICIOUS"
    UNKNOWN = "UNKNOWN"

class JobType(str, Enum):
    SP = "SP"
    OPT = "OPT" 
    FREQ = "FREQ" 
    OPTTS = "OPTTS" 
    CASSCF = "CASSCF"
    MRCI = "MRCI" 
    TDDFT = "TDDFT"
    UNKNOWN = "UNKNOWN"

@dataclass(slots=True)
class Warning:
    code: str
    message: str
    line: int | None = None


@dataclass(slots=True)
class Calculation:
    path: Path
    program: str = "UNKNOWN"
    version: str | None = None
    status: Status = Status.UNKNOWN
    method: str | None = None
    basis: str | None = None
    charge: int | None = None
    multiplicity: int | None = None
    job_type: JobType = JobType.UNKNOWN
    final_energy: float | None = None
    gibbs_energy: float | None = None
    imaginary_frequency_count: int | None = None
    lowest_frequency: float | None = None
    runtime_seconds: int | None = None
    termination: str | None = None
    warnings: list[Warning] = field(default_factory=list)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    def to_record(self, root: Path | None = None) -> dict[str, object]:
        record = asdict(self)
        record["path"] = str(self.path if root is None else self.path.relative_to(root))
        record["status"] = self.status.value
        record["warnings"] = [asdict(warning) for warning in self.warnings]
        record["warning_count"] = self.warning_count
        return record


EXPORT_FIELDS = [
    "path",
    "status",
    "program",
    "version",
    "method",
    "basis",
    "charge",
    "multiplicity",
    "final_energy",
    "gibbs_energy",
    "imaginary_frequency_count",
    "lowest_frequency",
    "runtime_seconds",
    "termination",
    "warning_count",
]
