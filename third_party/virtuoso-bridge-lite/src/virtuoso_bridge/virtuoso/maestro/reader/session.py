"""Window-state probes for the focused maestro session.

Helpers used by ``snapshot()`` and the CLI brief:

* ``_fetch_window_state`` — 1 SKILL call → focused title + davSession
  + window list + session list, title parsed into lib/cell/view/mode.
* ``natural_sort_histories`` — filter a results/maestro listing for
  history anchors and natural-sort by name.  Fragile when history
  names mix numbered + custom prefixes; keep as last-resort fallback.
* ``sort_histories_by_mtime`` — preferred ordering: sort histories by
  their newest on-disk mtime.  Matches the "what did I last run"
  intuition even when names mix Interactive.N / ExplorerRun.N /
  custom strings.
"""

from __future__ import annotations

import re

from virtuoso_bridge import VirtuosoClient

from .state import get_session_state, parse_maestro_title


# A history is anchored by its .rdb metadata file (any user-given name —
# Interactive.0.RO, closeloop_PVT_postsim, sweep_set.3, etc.).  Bare
# directories matching Cadence's ``Interactive.N`` / ``MonteCarlo.N``
# shape are also accepted for setups without .rdb anchors.
_HISTORY_RDB_RE = re.compile(r"^(?!\.)[^/\\]+\.rdb$")
_HISTORY_DIR_RE = re.compile(r"^(Interactive|MonteCarlo)\.[0-9]+(?:\.[A-Z]{2,4})?$")


def _parse_mae_title(titles) -> dict:
    """Return parsed fields from the first maestro-shaped title, or
    ``{}`` if none match.

    Title shape: ``ADE {Assembler|Explorer} {Editing|Reading}: LIB CELL VIEW[*]``
    (trailing ``*`` = unsaved changes).  Fields:
    ``application / lib / cell / view / mode / unsaved``.
    """
    for n in titles or ():
        if not n:
            continue
        parsed = parse_maestro_title(n)
        if parsed is None:
            continue
        return {
            "application": parsed["application"],
            "lib": parsed["lib"], "cell": parsed["cell"], "view": parsed["view"],
            "mode": str(parsed["access"]).title(),
            "unsaved": parsed["unsaved"],
        }
    return {}


def _fetch_window_state(client: VirtuosoClient) -> dict:
    """One SKILL round-trip → focused window info, title parsed.

    Keys: ``session`` (davSession — ``""`` if focus isn't a maestro
    window), ``title``, a structured ``state``, plus the parsed fields
    from the focused title: ``application / lib / cell /
    view / mode / unsaved`` (empty / None when nothing parsed).

    davSession is Cadence's own attribute for the bound maestro
    session on ADE Assembler windows — avoids sdb-scp disambiguation.
    """
    state = get_session_state(client)
    title = state.title or ""
    return {
        "session":      state.session or "",
        "title":        title,
        "application":  state.application,
        "lib":          state.lib or "",
        "cell":         state.cell or "",
        "view":         state.view or "",
        "mode":         state.access.title() if state.access != "unknown" else "Unknown",
        "unsaved":      state.unsaved,
        "state":        state.model_dump(mode="json"),
    }


def _history_name_for_file(fname: str) -> str | None:
    """Return the history name a file belongs to, or None if it's not a
    history artifact.  Recognises ``<name>.rdb`` anchors, the three
    companion extensions ``.log`` / ``.msg.db``, and bare
    ``Interactive.N`` / ``MonteCarlo.N`` directories."""
    if _HISTORY_RDB_RE.match(fname):
        return fname[:-4]   # strip .rdb
    if fname.endswith(".msg.db"):
        return fname[:-len(".msg.db")]
    if fname.endswith(".log"):
        return fname[:-4]   # strip .log
    if _HISTORY_DIR_RE.match(fname):
        return fname
    return None


def natural_sort_histories(hist_files: list[str]) -> list[str]:
    """Extract + naturally-sort history names from a results/maestro listing.

    Anchors on ``<name>.rdb``; also accepts bare ``Interactive.N`` /
    ``MonteCarlo.N`` dirs.  ``Interactive.2`` sorts before
    ``Interactive.10``.  Pure function.
    """
    seen: set[str] = set()
    for h in hist_files:
        if _HISTORY_RDB_RE.match(h):
            seen.add(h[:-4])
        elif _HISTORY_DIR_RE.match(h):
            seen.add(h)

    def _natkey(s: str):
        return [
            (int(tok) if tok.isdigit() else 0, tok)
            for tok in re.findall(r"\d+|\D+", s)
        ]

    return sorted(seen, key=_natkey)


def sort_histories_by_mtime(
    hist_files_mtime: list[tuple[str, int]],
) -> list[str]:
    """Extract history names from a ``[(filename, mtime), ...]`` listing
    and sort by newest mtime first.

    A history is identified by its ``<name>.rdb`` anchor; companion
    files (``<name>.log``, ``<name>.msg.db``) contribute too, and we
    take the *maximum* mtime across all files of a given history as
    that history's timestamp.  This handles the common mixed-naming
    case where ``results/maestro/`` holds
    ``Interactive.142..151.{rdb,log,msg.db}`` + ``ExplorerRun.0.*`` +
    custom-named histories like ``calibre_rcc_norc_sch.*`` —
    alphabetic / natural sort gives a meaningless "latest"; mtime
    matches the user's "what did I last run" intuition.
    """
    bucket: dict[str, int] = {}
    for fname, mtime in hist_files_mtime:
        hist = _history_name_for_file(fname)
        if hist is not None:
            bucket[hist] = max(bucket.get(hist, 0), mtime)
    return sorted(bucket, key=lambda h: bucket[h], reverse=True)
