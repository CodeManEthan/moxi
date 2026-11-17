"""Run Ruff's repository-wide naming and boolean checks."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import ruff


_RULE = re.compile(r"^([A-Z][A-Z0-9]+) (.+)$")
_LOCATION = re.compile(r"^\s*-->\s+(.+):(\d+):(\d+)$")


@dataclass(frozen=True, order=True)
class Hit:
    path: str
    line: int
    column: int
    code: str
    message: str

    def __str__(self) -> str:
        return (f"{self.path}:{self.line}: {self.code}: ruff: {self.message}; "
                f"fix the code, or add # noqa: {self.code} -- <why>")


def scan(root: Path | str) -> list[Hit]:
    """Run the Ruff binary installed beside moxi-dev and return each finding."""
    root = Path(root)
    command = [ruff.find_ruff_bin(), "check", "--no-cache", "."]
    done = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if done.returncode not in (0, 1):
        raise subprocess.CalledProcessError(done.returncode, command, done.stdout, done.stderr)

    pending: tuple[str, str] | None = None
    hits = []
    for line in done.stdout.splitlines():
        rule = _RULE.fullmatch(line)
        if rule:
            pending = (rule.group(1), rule.group(2))
            continue
        location = _LOCATION.fullmatch(line)
        if pending is not None and location:
            code, message = pending
            hits.append(Hit(location.group(1), int(location.group(2)),
                            int(location.group(3)), code, message))
            pending = None
    if done.returncode == 1 and not hits:
        detail = done.stderr.strip() or done.stdout.strip() or "Ruff reported a failure"
        raise subprocess.SubprocessError(detail)
    return sorted(hits)


__all__ = ["Hit", "scan"]
