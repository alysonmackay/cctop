from __future__ import annotations

import os
import sys

from .models import Status

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"

STATUS_COLOR = {
    Status.DONE: GREEN,
    Status.FAILED: RED,
    Status.SUSPICIOUS: YELLOW,
    Status.RUNNING: CYAN,
    Status.UNKNOWN: DIM,
}


def colors_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("CCTOP_FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def paint(text: str, code: str) -> str:
    if not colors_enabled() or not code:
        return text
    return f"{code}{text}{RESET}"


def status_text(status: Status) -> str:
    return paint(status.value, STATUS_COLOR.get(status, ""))


def header(text: str) -> str:
    return paint(text, BOLD)


def dim(text: str) -> str:
    return paint(text, DIM)


def accent(text: str) -> str:
    return paint(text, CYAN)
