"""Read-only SOS installation diagnostics and uncertain-operation reconciliation."""

from __future__ import annotations

from dataclasses import asdict
import math
import shlex

from .environment import resolve_soscmd, sos_runner
from .workarea import _is_calibre_target
from .cellview import (
    SOSCellViewState, SOSCellViewTarget, _absolute_path, _decode, _precondition,
    _skill, _target_precondition, _text, _verified, operate_cellview,
)


def _target(lib, cell, view, timeout):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive.")
    return SOSCellViewTarget(*(_text(value, label) for value, label in (
        (lib, "lib"), (cell, "cell"), (view, "view"),
    )))


def diagnose_cellview(owner, lib, cell, view, *, timeout=60, soscmd=None) -> dict:
    target = _target(lib, cell, view, timeout)
    checks = []
    result = None
    executable = None
    runner = sos_runner(owner)
    checks.append({"name": "filesystem_runner", "ok": runner is not None,
                   "detail": "Available" if runner is not None else "No explicit GUI filesystem runner."})
    for action in ("co", "ci"):
        try:
            reply = _decode(owner.execute_skill(_skill(target, required_action=action), timeout=timeout))
            checks.append({"name": f"ciw_target_and_gdm_{action}_api", "ok": reply[0] == "ok",
                           "detail": "Target and required APIs resolved; this is not a license/write test."
                           if reply[0] == "ok" else str(reply[1:])})
        except Exception as exc:
            checks.append({"name": f"ciw_target_and_gdm_{action}_api", "ok": False, "detail": str(exc)})
            break
    try:
        executable = resolve_soscmd(owner, soscmd, timeout=timeout)
        version = runner.run_command(shlex.join([executable, "version"]), timeout=timeout)
        checks.append({"name": "sos_executable", "ok": version.returncode == 0,
                       "detail": (version.stdout.strip() or version.stderr.strip()
                                  or f"Version command exit {version.returncode}"),
                       "executable": executable})
    except Exception as exc:
        checks.append({"name": "sos_executable", "ok": False, "detail": str(exc)})
    if executable and all(check["ok"] for check in checks):
        result = operate_cellview(owner, "status", lib, cell, view, timeout=timeout,
                                  soscmd=executable, include_unmanaged=True)
        target = result.target
        checks.append({"name": "workarea_and_sos_state", "ok": result.ok,
                       "detail": "; ".join(result.diagnostics) or "One target state obtained."})
    eligibility = {}
    if result and result.before:
        for action in ("co", "cancel_co", "ci", "register"):
            decision = _target_precondition(target) or _precondition(action, result.before)
            if action in {"cancel_co", "ci", "register"} and _is_calibre_target(
                target.lib, target.cell, target.view, target.directory, target.master,
            ):
                decision = ("blocked", "Calibre-related targets cannot be mutated.")
            eligibility[action] = {"outcome": decision[0] if decision else "dry_run",
                                   "detail": decision[1] if decision else "Preconditions currently satisfied."}
    ok = result is not None and result.ok and all(check["ok"] for check in checks)
    return {"ok": ok, "action": "doctor", "outcome": "success" if ok else "blocked",
            "target": asdict(target), "checks": checks, "eligibility": eligibility,
            "before": asdict(result.before) if result and result.before else None,
            "diagnostics": ["Read-only checks; no license acquisition test, save, checkout or checkin."]}


def validate_receipt(lib, cell, view, receipt: dict, timeout=60):
    """Validate evidence before connecting, including when CIW is unavailable."""
    target = _target(lib, cell, view, timeout)
    if not isinstance(receipt, dict):
        raise ValueError("receipt must be a prior SOS result object.")
    if isinstance(receipt.get("sos"), dict):
        receipt = receipt["sos"]
    action = receipt.get("action")
    if action not in {"co", "cancel_co", "ci", "register"} or receipt.get("outcome") != "unknown":
        raise ValueError("Only an unknown co/cancel_co/ci/register receipt can be reconciled.")
    previous = receipt.get("target")
    if not isinstance(previous, dict) or any(previous.get(key) != getattr(target, key)
                                            for key in ("lib", "cell", "view")):
        raise ValueError("Receipt target does not match the explicit lib/cell/view.")
    for key in ("directory", "master", "workarea"):
        _absolute_path(previous.get(key))
    _text(previous.get("view_type"), "receipt view type")
    try:
        before = SOSCellViewState(**receipt["before"])
        for value in asdict(before).values():
            _text(value, "receipt state field")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Receipt must contain a complete pre-operation SOS state.") from exc
    return action, previous, before


def reconcile_cellview(owner, lib, cell, view, *, receipt: dict, timeout=60, soscmd=None) -> dict:
    action, previous, before = validate_receipt(lib, cell, view, receipt, timeout)

    current = operate_cellview(owner, "status", lib, cell, view, timeout=timeout,
                               soscmd=soscmd, include_unmanaged=True)
    assessment = "unavailable"
    if current.ok and current.before:
        if any(previous[key] != getattr(current.target, key)
               for key in ("directory", "master", "workarea", "view_type")):
            assessment = "target_changed"
        elif _verified(action, before, current.before):
            assessment = "expected_state_observed"
        else:
            assessment = "expected_state_not_observed"
    # Current state cannot attribute a revision to a particular lost request.
    # Keep uncertainty explicit, even when the intended postcondition is visible.
    return {"ok": False, "action": "reconcile", "outcome": "unknown",
            "operation": action, "assessment": assessment, "operation_confirmed": False,
            "target": asdict(current.target), "before": asdict(before),
            "after": asdict(current.before) if current.before else None,
            "diagnostics": list(current.diagnostics) + [
                "Only fresh status was queried; no operation was retried or checkpoint marked verified.",
                "Current state does not prove the lost request completed. Inspect SOS before deciding the next action.",
            ]}
