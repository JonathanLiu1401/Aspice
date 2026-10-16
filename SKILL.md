---
name: aspice
description: "Use when solving, simulating, or checking analog CMOS circuits - MOSFET biasing and saturation, gm/ID sizing, CS/cascode/source-follower/diff-pair/current-mirror analysis, op-amp gain, phase margin, frequency response, noise, Razavi exercises - when reading, debugging, tuning or re-exporting a Cadence ADE / Spectre netlist (.scs), and when operating Cadence: Virtuoso (headless or a live session via virtuoso-bridge / CIW / SKILL), schematic, symbol or layout editing and readback, netlisting, Spectre with a real PDK (gpdk045, FreePDK45, sky130), ADE Maestro setup and PSF results, GDS stream, DRC/LVS with Calibre or PVS."
---

# aspice - analog design, Spectre netlists, and Cadence Virtuoso

## Overview

One skill for analog work from hand analysis to a live Virtuoso session.

- **Never trust a number that exists only one way.** Every design answer is
  produced by hand from the small-signal model *and* by simulation, then
  reconciled in a printed table. Most errors are a device silently in triode.
- **Never read a netlist raw.** Parse it and read a digest (Part 2).
- **Never write the user's library or `~/simulation` unless asked.** Work on
  scratch copies. A request naming a library ("draw X in my lib Y") is
  permission to add that cell there, never to overwrite or delete an existing one.

## Pick the backend

| Situation | Use | Where |
|---|---|---|
| Hand formula check, textbook exercise, exploratory sweep, no Cadence | ngspice + PTM via `analog_spice` | Part 1 |
| An ADE / Spectre `.scs` netlist to read, edit, tune, re-export | `spectre_netlist`, `netlist_digest`, `spectre_to_ngspice` | Part 2 |
| Cadence on this machine, **no GUI needed** (read/check/netlist/simulate a cell, PSF, DRC/LVS) | `cadence as CD` - batch `virtuoso -nograph`, OCEAN, Spectre, Calibre | Part 3 |
| A **running Virtuoso** the user is looking at: draw/edit schematics, symbols, layout, set up ADE Maestro, CIW | `virtuoso-bridge` (SKILL daemon in that CIW) | Part 4 |
| Standalone Spectre netlist runs through the bridge, parallel sim jobs | the `spectre` skill | - |
| PDK scan, gm/ID charts, exporting a design to another Cadence machine | `pdk`, `plots`, `netlist_export` | `references/pdk-charts-export.md` |

**Which simulator answers which question.** If `CD.environment().has('spectre')`,
any number presented as a prediction for a real design comes from **Spectre +
the real PDK**. ngspice + PTM verifies hand formulas and runs on machines
without Cadence. Never present a PTM number as a gpdk045 prediction: at the same
W/L/bias PTM-45nm-HP passes 3-60x the gpdk045 current at L=45n and 0.7-1.5x at
L=180n-1u. The simulators agree with each other (same BSIM4 card: within 0.05%);
the models do not. A generic or textbook question with no PDK named stays on
ngspice + PTM, labelled as such.

## Setup

| Thing | Where |
|---|---|
| Python for aspice | `~/.claude/skills-venv/bin/python` (PySpice; ngspice 47 at `~/opt/ngspice`). PySpice is not on the system Python |
| Python for the bridge | `~/EMiT/virtuoso-mcp-eval/.venv/bin/{python,virtuoso-bridge}` (imports the checkout in `~/EMiT/virtuoso-mcp-eval/tools/virtuoso-bridge-lite`; the bridge docs in `references/virtuoso/` track the newer `~/src/virtuoso-bridge-lite`, so when they disagree the installed package wins: `inspect.signature` it) |
| Libraries | `<skill>/lib/`; models in `<skill>/models/` (empty -> `scripts/fetch_models.py`) |
| Cadence projects | `print(CD.doctor())`. **EE332** gpdk045 `~/EE332/cadence`; **EE476** FreePDK45 `~/EE476/cadence` (`PDK_DIR` carried by the project); **EMiT** multi-PDK `~/EMiT/cadence` (launch GUI: `~/EMiT/cadence/start_virtuoso.sh`). Select with `$ASPICE_PROJECT`, `CD.use_project(name)`, or `cds_lib=` per call. Pin it - an auto-picked cds.lib once switched projects mid-session |
| Broken? | `scripts/analog_selftest.py` checks 13 groups against closed-form answers |

```python
import sys; sys.path.insert(0, '/homes/jliu1401/.claude/skills/aspice/lib')
from analog_spice import *              # analog design (import instead of PySpice: it patches two bugs)
import spectre_netlist as SN            # ADE netlist parse / edit / write
import netlist_digest as ND             # compact views of a big netlist
import spectre_to_ngspice as TR         # translate, simulate, tune, re-export
import netlist_export as NX, pdk, plots # export, PDK reading, design charts
import cadence as CD                    # headless Spectre, Virtuoso, OCEAN, Calibre
```

## Running Cadence on the UW ECE lab machines

The lab hosts share one NFS home, and heavy Cadence I/O has tipped hosts into
NFS stalls (Virtuoso starts of 15-60 min, processes stuck in D state).

1. Pick a fast idle host yourself (no need to ask): time 20x `touch`+`stat`
   on candidates (`ssh linux-lab-1NN.ece.uw.edu`, BatchMode works; 136, 137,
   143, 144, 145 have been clean). Healthy is ~0.1 s total; a sick host takes
   seconds and must not run Cadence. Check `uptime`/`who` for idleness.
2. ssh shells lack the Cadence PATH: `source ~/EMiT/virtuoso-mcp-eval/bin/cadence_env.sh`
   first (it also puts Cadence `pvs` ahead of `/sbin/pvs`).
3. Simulate and run DRC/LVS on **local copies** (`/var/tmp/...`), not on
   NFS-hosted libraries; copy results back once.
4. Launch anything long fully detached: `(setsid nohup CMD > log 2>&1 < /dev/null &)`.
   Children of the agent shell die when the session moves hosts.
5. Many sims (corners, sweeps): one detached Python script per host that loops
   `CD.run_spectre` / `CD.simulate_cell` serially over `section=` values on the
   local copy. Do not fan out dozens of parallel Spectre jobs onto NFS.
6. Never `kill -9` a Cadence process in D state, and never `pkill -f PATTERN`
   from a command line that contains PATTERN (it kills its own shell). Kill by PID.

---

# Part 1: analog design (ngspice)

## Workflow

1. **Bias it and prove the region.** `operating_point()`, then
   `op.check_saturation()`. Before computing anything else.
2. **Read small-signal parameters from the simulator** (`op.table()`), not from
   a square-law formula the model does not obey.
3. **Hand analysis** using those extracted gm / ro / gmb values.
4. **Simulate the same quantity** (AC gain, bandwidth, noise).
5. **Reconcile** with `Reconcile` and print the table. Investigate any
   disagreement - it is usually a missing gmb term, a device out of saturation,
   or a body effect that was forgotten.

## Quick reference

| Task | Call |
|---|---|
| Bias point + device regions | `op = operating_point(circuit)`; `op.check_saturation()` |
| gm, gmb, ro, gm/ID, gm*ro table | `op.table()` |
| One device | `d = op.devices['m1']`; `d.gm`, `d.ro`, `d.vdsat`, `d.region`, `d.gm_id` |
| Node voltage / source current | `op.v('out')`, `op.i('vdd')` |
| Input bias for a target output | `solve_bias(build_fn, 'out', 0.9, low, high)` |
| AC transfer function | `freq, data = ac(circuit)`; `data['out']` is complex |
| Gain, f_3dB, UGB, phase margin | `bode_summary(freq, data['out'])` |
| Noise + who causes it | `nz = noise(...)`; `noise_table(nz)` |
| DC sweep (I-V curves) | `dc_sweep(circuit, Vd=slice(0, 1.8, 0.01))` |
| Gain / Rin / Rout at once | `transfer_function(circuit, 'v(out)', 'vin')` |
| Transit frequency | `f_t(circuit, 'vg', 'vdd')` |
| Hand-vs-SPICE table | `r = Reconcile(); r.add('Av', sim, hand); r.report()` |
| Switch technology node | `circuit.include(tech('65nm'))` - models are always `nch`/`pch` |

## Choosing a device model

- **`tech('180nm')`** - BSIM3, fits most Razavi examples. Reports
  gm/gds/vth/vdsat but **no capacitances**.
- **`tech('65nm')`, `tech('45nm_hp')` and below** - BSIM4. Adds capacitances;
  use for short-channel and nanometer chapters.
- **A square-law `level=1` model** when the point is to verify a hand formula:
  it reproduces textbook equations to 10 significant figures, so any mismatch
  is the algebra, not the device physics.

PTM cards are academic and predictive, not a foundry PDK, and each is extracted
at its node's nominal length. Using L = 0.5 um on the 180 nm card (as Razavi
often does) is an extrapolation - fine for coursework, worth stating.

## Common mistakes

| Mistake | What happens | Fix |
|---|---|---|
| Assuming saturation | Gain off by 2-10x, silently | `op.check_saturation()` every time |
| `Vov = Vgs - Vth` as the saturation test | Wrong for short-channel devices | Compare `|Vds|` with the model's `d.vdsat` |
| fT from reported Cgs/Cgd | Overestimates >2x at 45 nm; ngspice reports intrinsic caps only | `f_t()`, which measures `|h21| = 1` |
| Current source as `100e-6 @ u_A` | Emits `0.0001A`; ngspice reads `A` as **atto**, giving 1e-22 A | Plain float, or `@u_uA`. `lint_netlist` catches it |
| Missing `ac_magnitude` on the input | AC analysis returns zeros | `SinusoidalVoltageSource(..., ac_magnitude=1@u_V)` |
| Comparing AC gain to a hand formula from the same gm | Circular; proves only topology | Add a DC finite difference - it never touches gm |
| Ignoring gmb | Tens of percent error when the source is off the bulk | Check `d.gmb`; zero only when Vbs = 0 |
| Single-frequency `.noise` | ngspice omits the per-device breakdown entirely | Sweep at least a decade |

---

# Part 2: Cadence ADE / Spectre netlists

## Never read a netlist raw

Start with the cheapest view and escalate only as needed. On a 5,000-line
netlist this is a 270x token reduction (83,825 tokens raw, 309 for a summary):

```python
nl = SN.parse_file('design.scs')
print(ND.outline(nl))       # hierarchy + counts, ~10 lines
print(ND.summary(nl))       # + parameters, analyses, devices, nets, ~50 lines
print(ND.device_rollup(nl)) # 500 identical fingers collapse to one row
```

Only then reach for detail: `ND.show_subckt(nl, 'amp')`,
`ND.net_report(nl, 'vout')`, `ND.grep(nl, 'pattern')`,
`ND.show_lines(nl, 400, 460)`.

## Quick reference

| Task | Call |
|---|---|
| Parse | `nl = SN.parse_file(path)` / `SN.parse_netlist(text)` |
| Cheapest overview | `ND.outline(nl)` |
| Full digest | `ND.summary(nl)` |
| Collapse repeated devices | `ND.device_rollup(nl)` |
| Everything on one net | `ND.net_report(nl, 'vout')` |
| Find floating nodes | `ND.find_dangling(nl)` |
| Raw fragment | `ND.show_subckt(nl, name)`, `ND.show_lines(nl, a, b)` |
| Search | `ND.grep(nl, pattern)` |
| Edit a parameter | `SN.set_param(nl, 'Wn', '4u')` |
| Edit an instance | `SN.set_instance_param(nl, 'M0', 'w', '4u')` |
| Find devices | `SN.find_instances(nl, master='nch')` |
| Re-export | `SN.write_netlist(nl)` |
| Confirm an edit did only what was intended | `ND.diff_digest(before, after)` |

**Round-trip is guaranteed.** `write_netlist(parse_netlist(text))` is
byte-identical, and an edit changes only the affected line. That is what makes
re-exporting a configured ADE netlist safe.

## Simulate and tune a netlist

`spectre_to_ngspice` closes the loop: read an ADE netlist, hit a target, export
a configured one.

| Task | Call |
|---|---|
| Spectre IR to an ngspice deck | `tr = translate(nl)`; read `tr.warnings` and `tr.unsupported` |
| Simulate a netlist or deck | `simulate(nl_or_deck)` |
| Hit a target by moving a knob | `tune(nl, knobs, targets)` |
| Write the result back | `apply_tuned(nl, knobs)`, then `SN.write_netlist(nl)` |

Always read `tr.unsupported`. `stb`, `pss`, `xf`, `pz`, `sp` and `montecarlo`
have no ngspice equivalent, so they are listed there rather than silently
replaced with something that looks like an answer. `tune` reports the closest
value it reached and says plainly when a target was not met.

## Spectre gotchas that cause silent wrong answers

- **`simulator lang=spectre` / `lang=spice` switches mid-file.** SPICE dot-cards
  read in spectre mode become garbage. The parser tracks this.
- **`M` means mega in Spectre and milli in SPICE** - a silent 1e9 error.
- **Trailing `A` is atto and `F` is femto in ngspice**, not amp and farad.
- **`include ... section=tt`** picks a corner; a missing section changes results
  without any error. ADE defaults gpdk045 to `section=mc`.
- **`gnd` is not ground in Spectre.** Only node `0` is; a net named `gnd` floats
  (seen in a real ADE run: `gnd` at 0.348 V). ngspice grounds `gnd` silently, so
  the translator renames it `gnd_net` and warns. `gnd!` via `global` is separate.
- **ADE parenthesizes literals:** `w=(245.1u)`, `m=(1)`, `r=(1M)`. The translator
  unwraps them and rewrites suffixes inside expressions (`(1M)` was once emitted
  as `{(1M)}` = 1 milliohm in ngspice).

`references/ade-netlist-errors.md` catalogues these with symptoms, detection
and fixes. `references/spectre-syntax.md` is the syntax lookup table.

---

# Part 3: Cadence on this machine, headless

Every call runs in its own scratch workdir (`ASPICE_WORKDIR`) with a `cds.lib`
that INCLUDEs the user's. Cellviews open read-only; schCheck, edits, builds and
stream-in act on a copy in `aspice_scratch`. OCEAN's project dir is redirected
away from `~/simulation`. `edit_instance_params` refuses a user library unless
`allow_write=True` - pass that only when the user asks to change their design.

| Task | Call |
|---|---|
| What is installed / licensed / which cds.lib | `print(CD.doctor())` |
| Libraries, cells, views (no Virtuoso needed) | `CD.read_cds_lib()`, `CD.library_cells(path)` |
| Read a schematic: pins, instances, CDF params, nets | `s = CD.schematic(lib, cell)`; `s.summary()`, `s.floating_nets()` |
| schCheck (on a copy) | `CD.check_schematic(lib, cell)` |
| Read a layout: bbox, lpp shape counts, labels, pins | `CD.layout(lib, cell)` |
| Schematic -> Spectre netlist (OCEAN) | `CD.netlist_cell(lib, cell, section='tt', analyses='dcOp dc\n')` |
| Schematic -> netlist -> Spectre -> results | `r = CD.simulate_cell(lib, cell, analyses=..., design_vars={...})` |
| Run any Spectre netlist (path, text, IR) | `r = CD.run_spectre(nl)`; `r.summary()` |
| Results | `r.op()['vout']`, `r.wave('ac', 'vout')`, `r.devices()['NM0']['gm']`, `r.device_table()` |
| Saturation check from Spectre | `print(r.device_table())` - region, gm/Id, gm*ro, Vds-Vdsat |
| Edit a design and re-verify | `cl = CD.copy_cell(lib, cell)`; `CD.edit_instance_params(CD.SCRATCH_LIB, cell, {'M0': {'w': '2u'}}, cds_lib=cl)`; `CD.simulate_cell(CD.SCRATCH_LIB, cell, ..., cds_lib=cl)` |
| Build a cell from a device list | `cl = CD.build_schematic(cell, [(name, lib, cell, params, {term: net}), ...], pins=[...])` |
| ADE/Maestro history | `CD.ade_runs()`; `CD.read_psf_dir(psf)`; transients in PSF XL: `CD.read_ade_waveform(psf, 'tran', ['VOUT'])` |
| Real-PDK device data | `CD.spectre_probe(model_file, 'g45n1svt', w=, l=, vgs=, vds=, sweep_vgs=(0, 1, .01))`; `pdk.probe` routes here for Spectre-dialect kits |
| Spectre vs ngspice on one deck | `CD.format_cross_check(CD.cross_check(text))` |
| GDS out / in | `CD.stream_out(lib, cell)`; `CD.stream_in(gds, tech_lib=...)` (layer map found or generated) |
| DRC / LVS (on copies) | `CD.calibre_drc(gds, top, deck)`; `CD.calibre_lvs(gds, top, src_netlist, deck)`; existing reports: `CD.parse_drc_summary`, `CD.parse_lvs_report` |
| Raw SKILL | `CD.run_skill('printf("ASPICE|K|%s\\n" ...)')` - results come back as `records` |

Workflow for a design question on this machine: `doctor` once -> `schematic`
(+ `check_schematic`) -> `simulate_cell` with the real PDK -> `device_table` to
prove regions -> hand analysis from Spectre's gm/gds -> `Reconcile`. To change
the design: copy, edit, re-simulate, compare; touch the user's library only on
explicit request. `references/cadence-headless.md` has the tool behaviours this
depends on (OCEAN, PSF XL, `$PDK_DIR`, layer maps, SKILL pitfalls).

---

# Part 4: a live Virtuoso session (virtuoso-bridge)

Use when the user has Virtuoso open and wants to see the result there, or the
operation needs the GUI (Maestro setup, drawn schematics, window captures).
Python runs on the client; SKILL runs inside the selected Virtuoso process.

**Connect.** `virtuoso-bridge status`; then a harmless `virtuoso-bridge eval '1+2'`.
If the daemon is not loaded, give the user the exact `load("...")` line to paste
into the CIW. First connection or a changed target: `references/virtuoso/connection.md`.

**Smallest interface first.** One-off SKILL: `virtuoso-bridge eval 'EXPR'`,
`eval --stdin`, `load FILE.il`. Structured: `client.schematic`, `.symbol`,
`.layout`, `.library`, `.maestro`. Anything else: `client.execute_skill(...)`.
Raw SKILL is a supported escape hatch, not a failure.

**Rules that have cost hours when skipped:**

- **Modal dialogs freeze the SKILL socket.** Stream-out completion, "Save
  Changes?", license prompts: every later call hangs (in the EMiT bench, until
  the 1800 s MCP timeout, 60-150 min lost per trial). Prefer non-interactive
  forms of the call, and before popup-prone operations or any timeout recovery
  read `references/virtuoso/shared-ciw-dialogs.md`. Leave unknown dialogs to the
  user; never press Enter/Cancel blindly, never resubmit or restart the daemon
  to unblock a wait. To see what is blocking, capture the screen over
  SSH/X11 (`troubleshooting.md`) and show the user.
- **Verify before calling.** Check SKILL signatures against the installed
  Cadence docs (`references/virtuoso/local-docs.md`) and read real cell pins and
  CDF parameters before placing. `nf` vs `fingers` vs `w` are PDK-specific.
- **`create()` only for a new cell; `modify()` for an existing one.** Creation is
  not permission to delete a cell first. Read back connectivity and parameters,
  check and save, then netlist. For PDK devices run the CDF callbacks.
- **Simulate the saved schematic,** never a hand-written netlist standing in for
  it. For GUI-equivalent results keep the netlist, models, corner and options.
- **Drawn schematics read like a hand drawing.** Wire with entry method
  `"draw"` (the default of `schematic_create_wire_between_instance_terms`; the
  lower-level wire helpers default to `"route"`, so pass `route_style="draw"`), never
  the autorouter. NMOS source down, PMOS source up, gates from the
  left, sources upright (`vdc` R0). No crossings between nets. After saving,
  render a PNG (`capture_schematic_cell`) and look at it.

| Operation | Reference (`references/virtuoso/`) |
|---|---|
| CLI/Python calls, file transfer, CIW output | `client-basics.md` |
| Schematic edit, readback, CDF callbacks | `schematic-python-api.md`, `schematic-skill-api.md` |
| Recreate / import a schematic | `schematic-recreation.md`, `schematic-manifest-import.md` |
| Symbols | `symbol-python-api.md` |
| Layout, vias, GDS | `layout-python-api.md`, `layout-skill-api.md` |
| Libraries, tech binding | `library-python-api.md` |
| Maestro setup, runs, results | `maestro-python-api.md`, `maestro-skill-api.md`, `simulation-flow.md` |
| Netlisting without Maestro | `netlist.md`, `batch-netlist-si.md` |
| Cellview files, locks | `cellview-on-disk-layout.md` |
| SOS, SKILL Finder | `sos-python-api.md`, `skill-finder-python-api.md` |
| Errors, stalls, connection | `troubleshooting.md` |

Read only the branch you need. Repository examples: `rg --files ~/src/virtuoso-bridge-lite/examples/01_virtuoso`.

---

## Verification

Offline (no Cadence): `python scripts/offline_loop.py` plus the eight suites
(`analog_selftest test_parser test_digest test_integration test_translate
test_export test_pdk_plots offline_loop`). Parsing, digesting and editing are
exact and round-trip byte-identically; numbers are checked against closed-form
hand analysis, not golden files.

On a Cadence machine: `python scripts/test_cadence.py` (31 live checks: Spectre
vs closed form, `gnd`/`(1M)` semantics, Spectre-vs-ngspice on one BSIM4 card,
gpdk045 probe, build/read/schCheck/edit/netlist/simulate a schematic, draw a
layout and catch DRC width/spacing) and `python scripts/cadence_loop.py` (your
own data: re-runs recent ADE points and compares with the stored PSF, schChecks
those cells, DRC/LVS with `--gds`). Measured on the UW ECE lab install (IC23.1,
Spectre 23.1, Calibre 2021.1, gpdk045 v6.0): 381 unique ADE netlists parse,
round-trip and translate; ADE re-runs match stored results to 3e-16 V.

Still not covered: `spiceIn` (use `build_schematic` instead), Monte Carlo /
`stb` / `pss` result post-processing beyond raw PSF, PEX, and PVS (gpdk045's
own DRC/LVS decks are PVS, not Calibre). `export_package` still records
`verified_locally: False`: on this machine verify the export by building it
with `build_schematic` and simulating it in Spectre.

## References

- `references/pdk-charts-export.md` - PDK scan/probe, gm/ID and other design charts, netlist export package
- `references/cadence-headless.md` - how the headless backend works, and the tool behaviours it works around
- `references/virtuoso/` - virtuoso-bridge API and live-session references (symlink into `~/src/virtuoso-bridge-lite`)
- `references/ade-netlist-errors.md` - Spectre failure catalogue; `references/spectre-syntax.md` - syntax table
- `references/pyspice-cookbook.md` - PySpice recipes and patched bugs; `references/razavi-map.md` - chapter map
- `examples/` - runnable verified scripts; `corpus/` - 19 ADE netlists with ground truth
