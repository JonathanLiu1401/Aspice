"""Maestro simulation history discovery and lock management."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from virtuoso_bridge.virtuoso.ops import escape_skill_string
from virtuoso_bridge.virtuoso.skill_output import parse_sexpr

if TYPE_CHECKING:
    from virtuoso_bridge import VirtuosoClient


_HISTORY_TAG = "VB_MAESTRO_HISTORY_V1"
_LOCK_TAG = "VB_MAESTRO_HISTORY_LOCK_V1"
_UNKNOWN_TRANSPORT_MARKERS = (
    "socket timeout",
    "socket error",
    "connection refused",
)


class MaestroHistoryError(RuntimeError):
    """A Maestro history probe or lock operation failed."""


class MaestroHistoryOutcomeUnknown(MaestroHistoryError):
    """A lock mutation may have executed, but its final state is unconfirmed."""


class MaestroHistory(BaseModel):
    """One history entry observed in a named Maestro session."""

    model_config = ConfigDict(frozen=True)

    name: str
    locked: bool
    current: bool = False


class MaestroHistoryLockResult(BaseModel):
    """Verified result of requesting one history lock state."""

    model_config = ConfigDict(frozen=True)

    session: str
    history: str
    requested_locked: bool
    before: MaestroHistory
    after: MaestroHistory
    changed: bool
    outcome: Literal["applied", "already_satisfied", "confirmed_after_unknown"]


def _require_name(value: str, label: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"{label} must be a non-empty string.")
    return normalized


def _history_probe_skill(session: str) -> str:
    escaped_session = escape_skill_string(session)
    return f'''prog((vbProbe vbSdb vbHistory vbNames vbCurrent vbRows
                      vbName vbEntry vbLocked)
  unless(isCallable('axlGetMainSetupDB)
    return(list("{_HISTORY_TAG}" "unsupported" "axlGetMainSetupDB")))
  unless(isCallable('axlGetHistory)
    return(list("{_HISTORY_TAG}" "unsupported" "axlGetHistory")))
  unless(isCallable('axlGetHistoryEntry)
    return(list("{_HISTORY_TAG}" "unsupported" "axlGetHistoryEntry")))
  unless(isCallable('axlGetHistoryLock)
    return(list("{_HISTORY_TAG}" "unsupported" "axlGetHistoryLock")))
  unless(isCallable('axlGetCurrentHistory)
    return(list("{_HISTORY_TAG}" "unsupported" "axlGetCurrentHistory")))

  vbProbe=errset(axlGetMainSetupDB("{escaped_session}"))
  unless(vbProbe
    return(list("{_HISTORY_TAG}" "probe_error" "axlGetMainSetupDB")))
  vbSdb=car(vbProbe)
  unless(vbSdb
    return(list("{_HISTORY_TAG}" "session_not_found" "{escaped_session}")))

  vbProbe=errset(axlGetHistory(vbSdb))
  unless(vbProbe
    return(list("{_HISTORY_TAG}" "probe_error" "axlGetHistory")))
  vbHistory=car(vbProbe)
  unless(vbHistory && listp(vbHistory) && length(vbHistory) >= 2
    return(list("{_HISTORY_TAG}" "malformed" "axlGetHistory")))
  vbNames=cadr(vbHistory)
  unless(listp(vbNames)
    return(list("{_HISTORY_TAG}" "malformed" "history_names")))

  vbProbe=errset(axlGetCurrentHistory("{escaped_session}"))
  unless(vbProbe
    return(list("{_HISTORY_TAG}" "probe_error" "axlGetCurrentHistory")))
  vbCurrent=car(vbProbe)
  vbRows=nil
  foreach(vbName vbNames
    vbProbe=errset(axlGetHistoryEntry(vbSdb vbName))
    unless(vbProbe && car(vbProbe)
      return(list("{_HISTORY_TAG}" "probe_error" vbName)))
    vbEntry=car(vbProbe)
    vbProbe=errset(axlGetHistoryLock(vbEntry))
    unless(vbProbe
      return(list("{_HISTORY_TAG}" "probe_error" vbName)))
    vbLocked=car(vbProbe)
    vbRows=cons(list(vbName vbLocked
                     if(vbCurrent == vbEntry then t else nil)) vbRows))
  return(list("{_HISTORY_TAG}" "ok" reverse(vbRows)))
)'''


def _set_history_lock_skill(session: str, history: str, locked: bool) -> str:
    escaped_session = escape_skill_string(session)
    escaped_history = escape_skill_string(history)
    lock_value = "t" if locked else "nil"
    return f'''prog((vbProbe)
  unless(isCallable('maeSetHistoryLock)
    return(list("{_LOCK_TAG}" "unsupported" "maeSetHistoryLock")))
  vbProbe=errset(maeSetHistoryLock("{escaped_history}" {lock_value}
                                  ?session "{escaped_session}"))
  unless(vbProbe
    return(list("{_LOCK_TAG}" "probe_error" "maeSetHistoryLock")))
  return(list("{_LOCK_TAG}" "ok" car(vbProbe)))
)'''


def _result_errors(result) -> str:
    return "; ".join(getattr(result, "errors", []) or [])


def _parse_history_payload(raw: str) -> list[MaestroHistory]:
    value = parse_sexpr(raw)
    if not isinstance(value, list) or len(value) != 3 or value[0] != _HISTORY_TAG:
        raise MaestroHistoryError("Malformed Maestro history probe envelope.")
    status, payload = value[1], value[2]
    if status != "ok":
        detail = payload if isinstance(payload, str) else repr(payload)
        raise MaestroHistoryError(f"Maestro history probe failed: {status}: {detail}")
    if payload is None:
        return []
    if not isinstance(payload, list):
        raise MaestroHistoryError("Malformed Maestro history row list.")

    histories: list[MaestroHistory] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, list) or len(row) != 3:
            raise MaestroHistoryError("Malformed Maestro history row.")
        name, locked, current = row
        if not isinstance(name, str) or not name or name in seen:
            raise MaestroHistoryError("Duplicate or invalid Maestro history name.")
        if ((locked is not True and locked is not None)
                or (current is not True and current is not None)):
            raise MaestroHistoryError("Malformed Maestro history lock/current flag.")
        seen.add(name)
        histories.append(MaestroHistory(
            name=name,
            locked=locked is True,
            current=current is True,
        ))
    return histories


def _parse_lock_payload(raw: str) -> bool:
    value = parse_sexpr(raw)
    if not isinstance(value, list) or len(value) != 3 or value[0] != _LOCK_TAG:
        raise MaestroHistoryError("Malformed Maestro history lock response.")
    status, payload = value[1], value[2]
    if status != "ok":
        detail = payload if isinstance(payload, str) else repr(payload)
        raise MaestroHistoryError(f"Maestro history lock failed: {status}: {detail}")
    if payload is not True and payload is not None:
        raise MaestroHistoryError("Malformed Maestro history lock return value.")
    return payload is True


def _transport_outcome_unknown(result) -> bool:
    detail = _result_errors(result).lower()
    return not getattr(result, "ok", False) and any(
        marker in detail for marker in _UNKNOWN_TRANSPORT_MARKERS
    )


def list_histories(
    client: "VirtuosoClient", session: str, *, timeout: float = 30,
) -> list[MaestroHistory]:
    """List histories and lock state for one explicit Maestro session."""
    session = _require_name(session, "session")
    result = client.execute_skill(_history_probe_skill(session), timeout=timeout)
    if not getattr(result, "ok", False):
        raise MaestroHistoryError(
            "Maestro history probe execution failed"
            + (f": {_result_errors(result)}" if _result_errors(result) else ".")
        )
    return _parse_history_payload((getattr(result, "output", "") or "").strip())


def get_history(
    client: "VirtuosoClient", history: str, *, session: str, timeout: float = 30,
) -> MaestroHistory:
    """Return one exact history from one explicit Maestro session."""
    session = _require_name(session, "session")
    history = _require_name(history, "history")
    matches = [item for item in list_histories(client, session, timeout=timeout)
               if item.name == history]
    if not matches:
        raise MaestroHistoryError(
            f"History {history!r} was not found in Maestro session {session!r}."
        )
    if len(matches) != 1:
        raise MaestroHistoryError(
            f"History {history!r} is ambiguous in Maestro session {session!r}."
        )
    return matches[0]


def set_history_lock(
    client: "VirtuosoClient",
    history: str,
    locked: bool,
    *,
    session: str,
    timeout: float = 30,
) -> MaestroHistoryLockResult:
    """Set and verify one history lock without retrying an uncertain mutation."""
    session = _require_name(session, "session")
    history = _require_name(history, "history")
    if not isinstance(locked, bool):
        raise TypeError("locked must be a bool.")

    before = get_history(client, history, session=session, timeout=timeout)
    if before.locked == locked:
        return MaestroHistoryLockResult(
            session=session,
            history=history,
            requested_locked=locked,
            before=before,
            after=before,
            changed=False,
            outcome="already_satisfied",
        )

    mutation = None
    mutation_exception: Exception | None = None
    try:
        mutation = client.execute_skill(
            _set_history_lock_skill(session, history, locked),
            timeout=timeout,
            retry_connect=False,
        )
    except Exception as exc:
        mutation_exception = exc

    uncertain_transport = mutation_exception is not None or (
        mutation is not None and _transport_outcome_unknown(mutation)
    )
    if mutation is not None and not uncertain_transport:
        if not getattr(mutation, "ok", False):
            raise MaestroHistoryError(
                "maeSetHistoryLock execution failed"
                + (f": {_result_errors(mutation)}" if _result_errors(mutation) else ".")
            )
        if not _parse_lock_payload((getattr(mutation, "output", "") or "").strip()):
            raise MaestroHistoryError(
                f"maeSetHistoryLock returned nil for history {history!r}."
            )

    try:
        after = get_history(client, history, session=session, timeout=timeout)
    except Exception as exc:
        raise MaestroHistoryOutcomeUnknown(
            f"History {history!r} lock request was sent, but post-state could not be "
            f"verified: {exc}"
        ) from exc

    if uncertain_transport:
        if after.locked == locked:
            return MaestroHistoryLockResult(
                session=session,
                history=history,
                requested_locked=locked,
                before=before,
                after=after,
                changed=True,
                outcome="confirmed_after_unknown",
            )
        raise MaestroHistoryOutcomeUnknown(
            f"History {history!r} lock outcome is unknown after transport failure; "
            f"observed locked={after.locked}, requested locked={locked}."
        )

    if after.locked != locked:
        raise MaestroHistoryError(
            f"History {history!r} lock post-state mismatch: "
            f"observed locked={after.locked}, requested locked={locked}."
        )

    return MaestroHistoryLockResult(
        session=session,
        history=history,
        requested_locked=locked,
        before=before,
        after=after,
        changed=True,
        outcome="applied",
    )


def lock_history(
    client: "VirtuosoClient", history: str, *, session: str, timeout: float = 30,
) -> MaestroHistoryLockResult:
    """Lock one history and verify the resulting state."""
    return set_history_lock(
        client, history, True, session=session, timeout=timeout,
    )


def unlock_history(
    client: "VirtuosoClient", history: str, *, session: str, timeout: float = 30,
) -> MaestroHistoryLockResult:
    """Unlock one history and verify the resulting state."""
    return set_history_lock(
        client, history, False, session=session, timeout=timeout,
    )


__all__ = [
    "MaestroHistory",
    "MaestroHistoryError",
    "MaestroHistoryLockResult",
    "MaestroHistoryOutcomeUnknown",
    "get_history",
    "list_histories",
    "lock_history",
    "set_history_lock",
    "unlock_history",
]
