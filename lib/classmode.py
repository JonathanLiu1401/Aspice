"""Class mode (aspice student version): the bridge helps with chores and debugging, not with the design a student
is meant to learn.

On by default in the student version (installed into the bridge-lite Python by scripts/install_student.py; course
staff can switch it off with ``VB_CLASS_MODE=0``). Every SKILL request the client sends (``execute_skill``,
``load_il`` and everything built on them, including the ``virtuoso-bridge`` CLI) is checked first; a
request that does design work is refused before it reaches Virtuoso, with a hint that tells the assistant how
to help the student instead, and the refusal is logged for the instructor.

Refused (design work the student does):
    topology    placing or wiring devices in a SCHEMATIC (schCreateInst, schCreateWire, dbCreateInst into a
                schematic, schematic import/recreation)
    sizing      setting device sizes or values on schematic instances (w, l, nf, fingers, m, r, c, ...)
    optimize    automatic sizing / optimisation runs

Allowed (chores and debugging): reading anything, layout editing (devices, routing, pins, rings, fill),
symbols from an existing schematic, netlisting, simulation, DRC/LVS/PEX (`virtuoso-bridge verify`),
screenshots, dialog handling, log and error reading.

Settings:
    VB_CLASS_MODE=0                 disable (default: on)
    VB_CLASS_ALLOW=sizing,...       categories the course staff unlock for an assignment
    VB_CLASS_LOG=<file>             refusal log (default: <VB_HOME or ~/.virtuoso-bridge>/class_mode.jsonl)

The check is static (it reads the SKILL text), so it is a guard rail against an assistant doing the work, not
a sandbox against a determined student: a student can always type SKILL in the CIW themselves.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

CATEGORIES = ("topology", "sizing", "optimize")

HINTS = {
    "topology": (
        "Class mode: placing or wiring devices in a schematic is the student's design work, so this request was "
        "not sent. Help the student draw it instead: ask which circuit they chose and why, what each device "
        "does, and which nodes connect; point out wrong or missing connections in what they draw. You may "
        "explain how to use the schematic editor. (Layout work is allowed: if this was a layout edit, open the "
        "layout cellview in the same request.)"),
    "sizing": (
        "Class mode: choosing device sizes or component values is the student's design work, so this request "
        "was not sent. Guide them: ask for their spec, their bias current and overdrive (or gm/ID) targets and "
        "how they derived W/L; check their numbers against simulation and explain discrepancies. Do not state "
        "sizes or values yourself. (Layout work is allowed: if this was a layout edit, open the layout cellview "
        "in the same request, e.g. dbOpenCellViewByType(lib cell \"layout\" \"maskLayout\" \"a\").)"),
    "optimize": (
        "Class mode: automatic sizing and optimisation are disabled. Help the student set up the sweep they want "
        "to run and interpret its results instead."),
}

# (category, pattern) pairs over the SKILL text with comments and strings removed.
# Instance creation and size writes are generic database calls used for layout too. The request does not say
# which cellview a variable like `cv` holds (it may have been opened in an earlier request), so they are refused
# unless the same request visibly works on a layout view.
_LAYOUT_CTX = re.compile(r'"(?:layout|maskLayout)"|\ble[A-Z]\w*\s*\(|\bleHi\w*', re.I)
_RULES: list[tuple[str, re.Pattern, bool]] = [
    # (category, pattern, allowed when the request visibly targets a layout view)
    ("topology", re.compile(r"\bsch(?:CreateInst|CreateInstBox|CreateWire|CreateNetExpression|"
                            r"CreateBus|HiCreateInst|HiCreateWire|Paste)\s*\("), False),
    ("topology", re.compile(r"\bdbCreate(?:Inst|ParamInst|InstByMasterName|ParamInstByMasterName)\s*\("), True),
    ("topology", re.compile(r"\b(?:schematic_create_|import_schematic_manifest|recreate_schematic)\w*"), False),
    ("sizing", re.compile(r"~>\s*(?:w|l|nf|fingers|fw|m|simM|wf|wr|r|c|totalWidth|wt|lt|segments|"
                          r"multiplier)\s*=(?!=)", re.I), True),
    ("sizing", re.compile(r"\bdb(?:ReplaceProp|CreateProp|SetPropValue|Set)\s*\([^)]*\"(?:w|l|nf|fingers|fw|m|"
                          r"simM|wf|r|c|totalWidth|multiplier)\"", re.I), True),
    ("sizing", re.compile(r"\bschHiReplace\b|\bschReplaceProperty\s*\(", re.I), False),
    ("optimize", re.compile(r"\b(?:maeRunOptimization|maeSetOptimizer|axlRunOptimization|"
                            r"maeRunGlobalOptimization|maeRunLocalOptimization)\s*\("), False),
]


def _strip(skill: str) -> str:
    """SKILL text without ``;`` comments (string literals kept: view names and property names live in them)."""
    out = []
    for line in skill.splitlines():
        buf, in_str, esc = [], False, False
        for ch in line:
            if in_str:
                buf.append(ch)
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == ";":
                break
            if ch == '"':
                in_str = True
            buf.append(ch)
        out.append("".join(buf))
    return "\n".join(out)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    category: str = ""
    match: str = ""
    hint: str = ""


def enabled() -> bool:
    return os.environ.get("VB_CLASS_MODE", "1").strip().lower() not in ("0", "false", "no", "off")


def unlocked() -> set[str]:
    return {c.strip() for c in os.environ.get("VB_CLASS_ALLOW", "").split(",") if c.strip()}


def check(skill: str, allow: set[str] | None = None) -> Verdict:
    """Classify one SKILL request. Pure: no environment lookups when *allow* is given."""
    allow = unlocked() if allow is None else allow
    text = _strip(skill)
    layout = bool(_LAYOUT_CTX.search(text))
    for cat, pat, layout_ok in _RULES:
        if cat in allow:
            continue
        m = pat.search(text)
        if m and not (layout_ok and layout):
            return Verdict(False, cat, m.group(0).strip(), HINTS[cat])
    return Verdict(True)


def check_load(path: str | Path, allow: set[str] | None = None) -> Verdict:
    """Check the contents of a .il file about to be loaded (unreadable files are checked by name only)."""
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return Verdict(True)
    return check(text, allow)


def log_refusal(v: Verdict, skill: str) -> None:
    home = os.environ.get("VB_HOME", "").strip() or str(Path.home() / ".virtuoso-bridge")
    path = Path(os.environ.get("VB_CLASS_LOG", "").strip() or Path(home) / "class_mode.jsonl")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "user": getpass.getuser(),
                                "category": v.category, "match": v.match, "skill": skill[:500]}) + "\n")
    except OSError:
        pass
