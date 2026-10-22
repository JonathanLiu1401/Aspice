"""Parser for Cadence SKILL Finder .fnd files.

The SKILL Finder database stores API documentation in ``*.fnd`` files under
``<ic_install_path>/doc/finder/SKILL/<functionArea>/``.

File format (per entry, 3 lines)::

    ("functionName"
    "syntaxString"
    "Description.")

Lines beginning with ``;`` are comments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SkillEntry:
    """A single SKILL API entry parsed from a .fnd file."""

    name: str
    syntax: str
    description: str
    source_file: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "syntax": self.syntax,
            "description": self.description,
            "source_file": self.source_file,
        }


# Pattern matches a 3-line SKILL entry block:
#   ("name"
#   "syntax"
#   "description.")
_ENTRY_PATTERN = re.compile(
    r'\("([^"]+)"\s*\n\s*"((?:[^"\\]|\\.)*)"\s*\n\s*"((?:[^"\\]|\\.)*)"\s*\)',
    re.DOTALL,
)


def parse_fnd_file(path: Path) -> list[SkillEntry]:
    """Parse a single .fnd file and return a list of SkillEntry objects.

    Parameters
    ----------
    path : Path
        Path to the .fnd file.

    Returns
    -------
    list[SkillEntry]
        All entries found in the file.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    # Remove comment lines (lines starting with ';')
    lines = [line for line in content.splitlines() if not line.startswith(";")]

    # Join back into a single string for regex matching
    normalized = "\n".join(lines)

    entries: list[SkillEntry] = []
    for m in _ENTRY_PATTERN.finditer(normalized):
        name = m.group(1).strip()
        syntax = m.group(2).strip()
        description = m.group(3).strip()
        if name and syntax:
            entries.append(
                SkillEntry(
                    name=name,
                    syntax=syntax,
                    description=description,
                    source_file=path.name,
                )
            )

    return entries


def parse_fnd_directory(root: Path) -> list[SkillEntry]:
    """Recursively parse all .fnd files under a root directory.

    Parameters
    ----------
    root : Path
        Root directory containing functionArea subdirectories,
        e.g. ``<ic_install_path>/doc/finder/SKILL/``.

    Returns
    -------
    list[SkillEntry]
        All entries found, deduplicated by name (first occurrence wins).
    """
    all_entries: list[SkillEntry] = []
    seen: set[str] = set()

    if not root.exists():
        return []

    for fnd_file in root.rglob("*.fnd"):
        for entry in parse_fnd_file(fnd_file):
            if entry.name not in seen:
                seen.add(entry.name)
                all_entries.append(entry)

    return all_entries
