"""Hook class mode into bridge-lite's client (aspice student version).

install() wraps VirtuosoClient.execute_skill and VirtuosoClient.load_il: a refused request returns an ERROR
VirtuosoResult with the teaching hint and is never sent. scripts/install_student.py arranges for this to run at
interpreter start-up in the bridge-lite Python (a .pth file), so the CLI and every script are covered.
"""
from __future__ import annotations

import classmode


def _refusal(verdict, skill: str):
    from virtuoso_bridge.models import ExecutionStatus, VirtuosoResult
    classmode.log_refusal(verdict, skill)
    return VirtuosoResult(status=ExecutionStatus.ERROR, errors=[verdict.hint],
                          metadata={"request_sent": False, "outcome": "class_mode_refused",
                                    "class_mode": {"category": verdict.category, "match": verdict.match}})


def install() -> bool:
    try:
        from virtuoso_bridge.virtuoso.basic.bridge import VirtuosoClient
    except Exception:  # noqa: BLE001  (not a bridge-lite Python: nothing to guard)
        return False
    if getattr(VirtuosoClient, "_aspice_class_mode", False):
        return True
    orig_exec, orig_load = VirtuosoClient.execute_skill, VirtuosoClient.load_il

    def execute_skill(self, skill_code, *a, **kw):
        if classmode.enabled():
            v = classmode.check(skill_code)
            if not v.allowed:
                return _refusal(v, skill_code)
        return orig_exec(self, skill_code, *a, **kw)

    def load_il(self, path, *a, **kw):
        if classmode.enabled():
            v = classmode.check_load(path)
            if not v.allowed:
                return _refusal(v, f"load {path}")
        return orig_load(self, path, *a, **kw)

    VirtuosoClient.execute_skill, VirtuosoClient.load_il = execute_skill, load_il
    VirtuosoClient._aspice_class_mode = True
    return True
