---
name: aspice
description: Use when solving, simulating, or checking analog CMOS integrated-circuit work - MOSFET biasing, saturation and overdrive checks, gm/ID sizing, common-source/cascode/source-follower/differential-pair/current-mirror analysis, op-amp gain and phase margin, frequency response and Miller effect, thermal and flicker noise, feedback and stability, Razavi "Design of Analog CMOS Integrated Circuits" exercises - and when reading, debugging, simulating, tuning, or re-exporting a Cadence ADE / Spectre netlist (.scs), including any large netlist that must be summarised before it can be reasoned about - and, on a machine with Cadence, when driving Virtuoso / Spectre / Calibre headlessly: reading or checking schematics and layouts, netlisting a cell, simulating with the real PDK (gpdk045, FreePDK45), reading ADE/Maestro PSF results, editing a design and re-verifying it, or running DRC/LVS.
---

# aspice - agentic SPICE for analog design

## Overview

Three jobs, one toolkit.

**Analog design and analysis.** Textbook answers go wrong in two places: a
device silently sitting in triode instead of saturation, and a hand formula
never checked against a simulator. Every answer here is produced twice - once
by hand from the small-signal model, once by simulation - and reconciled in a
printed table. A number that exists only one way is not an answer yet.

**Cadence ADE / Spectre netlists.** A real ADE netlist is tens of thousands of
lines and must never be read raw. Parse it, read a compact digest, and pull
raw fragments only where detail is actually needed.

**Cadence itself, headlessly (Part 4).** When Spectre / Virtuoso / Calibre are
on this machine, everything runs here: read and schCheck schematics, build or
edit a cell (on a scratch copy), netlist it, simulate it in Spectre against the
real PDK, read ADE results, stream layout out and run DRC/LVS. No copying files
to another machine.

**Which simulator answers which question.** If `CD.environment().has('spectre')`,
any number that is a prediction for a real design (bias, gain, swing, margins)
comes from **Spectre + the real PDK**. ngspice + PTM is for verifying hand
formulas, fast exploratory sweeps, and machines without Cadence. Never present a
PTM number as a gpdk045 prediction: measured at the same W/L/bias, PTM-45nm-HP
passes 3-60x the gpdk045 current at L=45n and 0.7-1.5x at L=180n-1u, and no PTM
card tracks gpdk045 across L. The simulators themselves agree: the same BSIM4
card in Spectre and in ngspice gives node voltages within 0.05%.

## Setup

| Thing | Where |
|---|---|
| Python + PySpice + ngspice | the venv interpreter that has PySpice (here: `~/.claude/skills-venv/bin/python`; ngspice 47 at `~/opt/ngspice`) |
| Libraries | `<skill>/lib/` |
| Technology models | `<skill>/models/` - if empty, run `scripts/fetch_models.py` |
| Cadence (optional) | found automatically: `CD.doctor()` lists tools, licenses, the `cds.lib` in use. Pin the project with `CD.configure(cds_lib='~/EE332/cadence/cds.lib')` |
| Verifiers | `scripts/analog_selftest.py`, `test_parser.py`, `test_digest.py`, `test_integration.py`, `test_translate.py`, `test_export.py`, `test_pdk_plots.py`, `test_cadence.py`; end to end on your data: `cadence_loop.py` |

Always use that interpreter; PySpice is not on the system Python. If anything
looks broken, run `scripts/analog_selftest.py` - it checks 13 groups of results
against closed-form answers and prints exactly what failed.

```python
import sys; sys.path.insert(0, '<skill>/lib')   # here: ~/.claude/skills/aspice/lib
from analog_spice import *              # analog design
import spectre_netlist as SN            # ADE netlist parse / edit / write
import netlist_digest as ND             # compact views of a big netlist
import spectre_to_ngspice as TR         # translate, simulate, tune, re-export
import netlist_export as NX             # describe a circuit, export it to Cadence
import pdk, plots                       # read a PDK; draw design charts
import cadence as CD                    # Spectre, Virtuoso, OCEAN, Calibre, headless
```

Import `analog_spice` **instead of** importing PySpice directly - it repairs
two PySpice/ngspice incompatibilities at import time.

---

# Part 1: analog design

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

# Part 3: your PDK, design charts, and export

## Point it at a PDK you have

Cadence GPDK kits (gpdk045 and friends) are **proprietary and licensed**, not
open source, so nothing here downloads a PDK. `pdk.scan` reads a directory you
supply, on a machine where you are licensed to have it. Open PDKs work
identically: FreePDK45 (Apache 2.0, the closest open analogue to gpdk045),
sky130, GF180MCU, IHP SG13G2 - `pdk.OPEN_PDKS` lists them with links.

```python
import pdk
p = pdk.scan('/path/to/pdk')     # or a single model file
print(p.summary())               # files, corners, devices, model families
p.corners()                      # ['tt', 'ss', 'ff', ...]
p.device_names()                 # {'nmos': [...], 'pmos': [...]}
pdk.probe(model_file, 'nch', length=45e-9)   # does it actually bias?
p.techmap_skeleton()             # a techmap draft to check against the PDK
```

`probe` matters more than `scan`: reading a card proves it exists, running it
proves it works. It reports region, Vth, gm, ro, gm/ID and whether the model
carries capacitances.

## Design charts

Every chart runs the simulator against your PDK and writes a PNG (headless, so
it works over SSH).

| Chart | Call | What it answers |
|---|---|---|
| Id-Vds family | `plots.iv_family(models, 'nch', w=, l=)` | where saturation starts, how flat it is |
| Id and gm vs Vgs | `plots.transfer(...)` | threshold, subthreshold slope, peak gm |
| **gm/ID design chart** | `plots.gm_id_chart(models, 'nch', lengths=[...])` | **how to size a device without guessing** |
| Bode with PM | `plots.bode(freq, data['out'])` | gain, f_3dB, UGB, phase margin |
| Transient | `plots.transient(t, {'out': v})` | step response, slewing |
| Any sweep | `plots.sweep_metric(build, values, measure, target=)` | the exact value that hits a target |

`gm_id_chart` is the one to reach for when sizing. gm/ID is exactly independent
of W (measured here: 0.001 1/V across an 8x width change) and nearly independent
of L, so you pick an operating point from the curve - high gm/ID for efficiency
and gain, low for speed - read off ID/W, and the width follows from the current
you need. No guess-and-iterate.

`sweep_metric` refines its target crossing by bisection rather than reading it
off the polyline. On a steep width sweep, plain interpolation missed a 0.9 V
target by 110 mV; the refined value lands within 10 uV. `target_crossing_error`
reports what was actually achieved.

## Generate a netlist and export it

For the case where the design happens here and Cadence lives on another machine.
Describe the circuit once; render it for both.

```python
from netlist_export import Design, Techmap, export_package

d = Design(name='ota_5t', techmap=Techmap.load('techmap.json'))
s = d.subckt('ota_5t', ['VDD', 'VSS', 'VINP', 'VINN', 'VBIAS', 'VOUT'])
s.device('M1', 'nmos', ['DIODE', 'VINP', 'TAIL', 'TAIL'], l='1u', w_f='2u', n_f=2)
...
export_package(d, 'out/ota_5t', models=tech('180nm'))
```

That writes `.cdl` and `.scs` carrying the PDK's cell and CDF parameter names
for the Cadence machine, `.sp` carrying SPICE names so it simulates here, plus
`techmap.json`, a `manifest.json` recording structural warnings, and a README.
`d.check()` catches floating nets, duplicate instance names and missing sizing
before the transfer rather than after.

**The trap this exists to avoid.** A Cadence netlist carries CDF parameter names
(`fw`, `fingers`, `w`); SPICE knows only `w`, `l`, `m`. Feeding a CDL straight to
ngspice fails with `unknown parameter (fw)`. Worse, a PDK's `w` is a finger width
in one process and a total width in another, and nothing warns you. So `w_f` is
always the finger width here, `n_f` the count, SPICE gets `w=w_f` and `m=n_f`,
and a PDK total-width parameter is computed rather than assumed.

---

# Part 4: Cadence on this machine, headless

All of it runs in scratch directories (`ASPICE_WORKDIR`). User libraries are
opened read-only; anything that must write (schCheck, edits, builds, stream-in)
works on a copy in the scratch library `aspice_scratch`. OCEAN's project dir is
redirected (left alone it writes `~/simulation/<cell>`). `edit_instance_params`
refuses a user library unless `allow_write=True` - only pass that when the user
explicitly asks to change their design.

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

- `references/pyspice-cookbook.md` - API recipes and the PySpice bugs patched here
- `references/razavi-map.md` - chapter-to-simulation map with the key formulas
- `references/ade-netlist-errors.md` - Spectre failure catalogue
- `references/spectre-syntax.md` - Spectre syntax reference
- `references/cadence-headless.md` - how the headless Cadence backend works, and the tool behaviours it works around
- `examples/` - runnable, verified scripts
- `corpus/` - 19 realistic ADE netlists with ground-truth JSON
