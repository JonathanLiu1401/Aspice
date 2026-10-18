"""Python side of schlint.il: build the SKILL call and parse its result.

    import sys; sys.path.insert(0, "<aspice>/lib"); import schlint
    client.execute_skill(schlint.lint_skill("mylib", "comp", allow_nets=("VDD", "VSS", "CLK")))
    # or batch: put the same text in a replay file;  schlint.parse_lint(log_text) -> [{..., "pass": bool}]
"""
from __future__ import annotations

from pathlib import Path

IL = Path(__file__).with_name("schlint.il")
SUPPLIES = ("VDD", "VSS", "GND", "gnd!", "vdd!", "vss!", "AVDD", "AVSS", "DVDD", "DVSS")


def _q(x) -> str:
    return '"' + str(x).replace("\\", "\\\\").replace('"', '\\"') + '"'


def lint_skill(lib: str, cell: str, max_share: float = 0.34, allow_nets=SUPPLIES, load: bool = True) -> str:
    """Labels on allow_nets (supplies, clocks, buses labelled on purpose) and on globals (name!) are not counted."""
    allow = "list(" + " ".join(_q(n) for n in allow_nets) + ")"
    return (f"load({_q(IL)})\n" if load else "") + f"aspLint({_q(lib)} {_q(cell)} {max_share:g} {allow})"


def parse_lint(text: str) -> list[dict]:
    """ASPLINT lines -> [{lib, cell, wires, labels, share, stubs, crossings, joined, pass}]."""
    out = []
    for line in text.splitlines():
        i = line.find("ASPLINT ")
        if i < 0 or "printf" in line:
            continue
        p = line[i:].split()
        kv = dict(x.split("=", 1) for x in p[3:-1])
        out.append({"lib": p[1], "cell": p[2], **{k: float(v) if "." in v else int(v) for k, v in kv.items()},
                    "pass": p[-1] == "PASS"})
    return out
