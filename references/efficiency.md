# Token cost and wall time

Measured in the EMiT benchmark (Claude agents driving Virtuoso, 2026-10): the expensive
part of a run is rarely the design reasoning. It is long simulations the agent waits on,
screenshots, re-reading big files, and one-call-per-shape GUI edits. Rules:

**Simulation**
1. Block level before top level. A block testbench runs in seconds; the SAR ADC top
   level took 45-80 min per post-layout iteration until runs were steered to <= 16
   conversions (~10 min). Run the long top-level test once, at the end.
2. Iterate at one corner (typical / tt), confirm the others once (`--corners typical`
   while iterating, `rcworst` to confirm; opcheck `--sections` only at the end).
3. Run independent simulations in parallel (corners, blocks) in the background and do
   other work meanwhile; never poll with sleep loops in the conversation.
4. Use `.measure` / scalar outputs; never print waveforms into the context.

**Reading**
5. Never read a netlist, PSF, log or DRC report raw. Schematic: `scripts/netbrief.py scs FILE`
   (outline and device rollup, about the digest's token cut). Extracted layout: `scripts/netbrief.py pex FILE`
   (counts plus the worst parasitic nets). Layout geometry: `layout_audit.py` prints a master rollup, not every
   shape. verify's PASS/FAIL lines are the sign-off view.
6. Read a file once; keep the numbers you need in your notes instead of re-opening it.
7. Screenshots only to judge a drawing (one per finished sheet or layout), not to check
   state: query the database instead (`CD.layout`, `schematic`, bridge readback).

**Editing**
8. The layout is one Python generator per cell (`layout_<cell>.py` via laygrid), written
   once and re-run. Not one SKILL call per shape, and not a GUI loop. Hand edits are final
   touches after that generator is DRC-clean. See `AGENTS.md`.
9. Save, then run batch tools (verify, opcheck) from the shell; they do not touch the GUI.
10. Fix the cause the checker names; do not re-run an unchanged design hoping for a
    different number.

**Order that minimises rework**
schematic -> opcheck PASS (Razavi regions) -> block testbenches -> critical-cell layout
+ verify post (fast blocks first) -> remaining layout -> top-level short test -> one long
test. A failure found late (e.g. a device in triode found after layout) costs a full
layout iteration.
