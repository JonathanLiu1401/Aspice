# Standard Simulation Flow (GUI Mode)

Use this for an explicitly authorized GUI-based simulation. Standalone netlist
simulation uses the Spectre skill and needs no Maestro session.

## Lifecycle scope

The first example assumes a dedicated CIW whose Maestro lifecycle belongs to
this workflow. `open_gui_session` may clean up other sessions/windows; it is not
a harmless way to attach to a human's existing session. In a shared CIW, use the
existing-session path below with a compatible guarded client. For all modes,
an uncertain run/save is not permission to close the session, restart the daemon
or submit a second run. Read [Shared CIW Dialog Protection](shared-ciw-dialogs.md).

> **Why GUI mode?** Background sessions (`open_session` / `maeOpenSetup`) can
> read/write config but cannot run simulations reliably: completion callbacks
> may not fire, and `close_session` cancels in-flight runs. GUI mode is required
> for the supported simulation flow.

## The standard flow

```python
from virtuoso_bridge import VirtuosoClient

client = VirtuosoClient.from_env()
LIB, CELL = "myLib", "myTestbench"

# ── Step 1: Open the workflow's dedicated Maestro session ────────
# This helper can clean other sessions/windows. Use only in the authorized
# dedicated CIW; shared CIWs use an explicitly resolved existing session.
session = client.maestro.open_gui_session(LIB, CELL)

# ── Step 2: (Optional) Modify variables, outputs, etc. ──────────
# client.execute_skill(f'maeSetVar("CL" "1p" ?session "{session}")')

# ── Step 3: Save + run + wait ────────────────────────────────────
# save_setup persists changes; run_and_wait starts the simulation
# with a completion callback and polls via SSH.
# SKILL channel stays free during the wait.
client.maestro.save_setup(LIB, CELL, session=session)
history, status = client.maestro.run_and_wait(session=session, timeout=600)
history = history.strip('"')
print(f"Simulation {status}: {history}")

# ── Step 4: Read results ─────────────────────────────────────────
results = client.maestro.read_results(session, lib=LIB, cell=CELL, history=history)
for point in results.get("points", []):
    print(f"Point {point['point']}: {point.get('parameters', {})}")
    for name, info in point.get("outputs", {}).items():
        print(f"  {name}: {info.get('value', '')} {info.get('pass_fail', '')}")

# ── Step 5: (Optional) Export waveforms ──────────────────────────
# client.maestro.export_waveform(session, 'VT("/VOUT")', "output/vout.txt",
#                 analysis="tran", history=history)
```

## When you already have an open GUI session

Reuse the already configured client and resolve the exact session from the
selected library/cell/window. Do not choose `car(maeGetSessions())`: the first
session may belong to another design. Saving or changing the user's setup still
requires authorization.

With a compatible upgraded daemon, an explicitly authorized shared-CIW run can
use the opt-in guard before any SKILL operation:

```python
def run_existing_session(client, lib, cell, session):
    # session is the caller's verified library/cell/window binding.
    client.dialogs.enable_guard(protect_inflight=True)
    state = client.maestro.get_session_state(session)
    if (
        state.context != "gui"
        or state.access != "editing"
        or state.unsaved is None
        or state.session != session
        or (state.lib, state.cell, state.view) != (lib, cell, "maestro")
    ):
        raise RuntimeError("Resolve exact session state before saving/running")

    client.maestro.save_setup(lib, cell, session=session)
    history, status = client.maestro.run_and_wait(session=session, timeout=600)
    history = history.strip('"')
    results = client.maestro.read_results(
        session, lib=lib, cell=cell, history=history,
    )
    return history, status, results
```

Guard failures and unknown request outcomes stop the workflow. Preserve the
request handle and acknowledged history/marker for reconciliation, not a retry.
Do not downgrade to unguarded legacy execution when these capabilities are
unavailable. Constructing a new ordinary client or reloading a daemon is not
popup recovery.

## How `run_and_wait` works

1. Defines a SKILL callback procedure that writes a marker file when simulation finishes
2. Calls `maeRunSimulation(?callback "proc_name")` — callback is registered atomically with the simulation start (no race condition)
3. Polls the marker file via SSH, or the local filesystem in local mode, without using the SKILL channel
4. Returns `(history, status)` when marker appears

`timeout` is one end-to-end budget shared by acceptance of the
`maeRunSimulation` request and completion polling. Remote poll commands and
sleep intervals are capped to the remaining deadline. After the run request is
accepted, the SKILL channel remains free during the wait, so callers can read
configuration or use out-of-band X11 recovery.

## Detecting Maestro session state

There is **no direct SKILL API** known to query whether a Maestro session is
read-only, editable, or has unsaved changes. The `axl*` and `mae*` APIs
previously tested for this purpose (for example `maeIsEditable` and
`axlGetSetupMode`) return `nil` in the validated IC6.1.8 environment.

Use the structured probe, which atomically binds windows to sessions and then
parses recognized **window title** shapes:

```python
state = client.maestro.get_session_state(session)
if state.access == "unknown" or state.unsaved is None:
    raise RuntimeError("Maestro state is not safe for a lifecycle mutation")
```

| Title pattern | State |
|---------------|-------|
| `...Assembler Editing: LIB CELL maestro` | Editable, no unsaved changes |
| `...Assembler Editing: LIB CELL maestro*` | Editable, **has unsaved changes** (trailing `*`) |
| `...Assembler Reading: LIB CELL maestro` | Read-only |

Title parsing is version- and locale-sensitive. Unrecognized titles remain
`unknown`; they are never assumed to mean Reading or clean. Use this state
before calling `maeMakeEditable()` or closing a window to avoid targeting the
wrong session or triggering an ASSEMBLER-8127/modal path.

## Closing Maestro sessions

The examples below apply only to a verified, workflow-owned session with no
pending or uncertain request/run. A force-close can cancel a simulation.

### GUI-opened sessions (`maeCloseSession` won't work)

Sessions opened via the Virtuoso GUI (File → Open) **cannot be closed** with `maeCloseSession` — it returns ASSEMBLER-8051. You must close the GUI window:

```python
# Save first if modified (check for trailing * in title)
client.execute_skill(f'maeSaveSetup(?lib "{LIB}" ?cell "{CELL}" ?view "maestro" ?session "{session}")')

# Close by finding the window with matching session
client.execute_skill(f'''
foreach(w hiGetWindowList()
  when(car(errset(axlGetWindowSession(w))) == "{session}"
    hiCloseWindow(w)))
''')
```

### Background sessions (`maeOpenSetup`)

These can be closed with `maeCloseSession`:

```python
client.execute_skill(f'maeCloseSession(?session "{session}" ?forceClose t)')
```

### Cleanup scope

Close only the session/window owned by the workflow, after its request and
simulation outcomes are known. On a shared CIW leave the user's sessions open.
Purging cellviews, deleting lock files or closing every session is a separate
maintenance operation requiring evidence that no active run owns them and
explicit authorization for the entire affected scope.

## Common pitfalls

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| Stale internal edit lock | ASSEMBLER-8127 | Verify the owning session/run; clean only proven stale state within the authorized scope |
| Using `open_session` for simulation | `run_and_wait` hangs / returns immediately | Use `open_gui_session` (GUI mode), not `open_session` (background) |
| Skipping `save_setup` | Simulation uses stale parameters | Always save before running |
| `maeCloseResults` leaves Maestro read-only | Next `maeRunSimulation` fails | Inspect the exact session before an authorized access-mode change; do not clean other windows |
| `maeCloseSession` on GUI-opened session | ASSEMBLER-8051: "opened from UI" | Use `close_gui_session` instead |
| `window:N` in multi-line SKILL | `unbound variable - window` | Use `foreach(w hiGetWindowList() ...)` to find windows by `w~>windowNum` |

## Optimization loops

For sweeping parameters and re-running simulation:

```python
for val in ["1p", "2p", "5p", "10p"]:
    client.execute_skill(f'maeSetVar("CL" "{val}" ?session "{session}")')
    client.maestro.save_setup(LIB, CELL, session=session)
    history, status = client.maestro.run_and_wait(session=session, timeout=600)
    history = history.strip('"')
    results = client.maestro.read_results(session, lib=LIB, cell=CELL, history=history)
    # ... process results ...
```

For shared CIWs, enable `client.dialogs.enable_guard(protect_inflight=True)` with
an upgraded daemon before the loop (plain `enable_guard()` is preflight-only). Preserve
user dialogs and inspect through SSH/X11 rather than dismissing current forms.
On an uncertain start, query the original request receipt and completion marker;
do not invoke `run_and_wait()` again to recover an already accepted run.
See [Shared CIW Dialog Protection](shared-ciw-dialogs.md). Failed/uncertain runs
must be reconciled before any explicit retry.
