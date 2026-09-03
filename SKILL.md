---
name: aspice
description: Use when solving, simulating, or checking analog CMOS integrated-circuit work - MOSFET biasing, saturation and overdrive checks, gm/ID sizing, common-source/cascode/source-follower/differential-pair/current-mirror analysis, op-amp gain and phase margin, frequency response and Miller effect, thermal and flicker noise, feedback and stability, Razavi "Design of Analog CMOS Integrated Circuits" exercises - and when reading, debugging, simulating, tuning, or re-exporting a Cadence ADE / Spectre netlist (.scs), including any large netlist that must be summarised before it can be reasoned about.
---

# aspice - agentic SPICE for analog design

## Overview

Two jobs, one toolkit.

**Analog design and analysis.** Textbook answers go wrong in two places: a
device silently sitting in triode instead of saturation, and a hand formula
never checked against a simulator. Every answer here is produced twice - once
by hand from the small-signal model, once by simulation - and reconciled in a
printed table. A number that exists only one way is not an answer yet.

**Cadence ADE / Spectre netlists.** A real ADE netlist is tens of thousands of
lines and must never be read raw. Parse it, read a compact digest, and pull
raw fragments only where detail is actually needed.

## Setup

| Thing | Where |
|---|---|
| Python + PySpice + ngspice | the venv interpreter that has PySpice (here: `~/.venvs/pyspice/Scripts/python.exe`) |
| Libraries | `<skill>/lib/` |
| Technology models | `<skill>/models/` - if empty, run `scripts/fetch_models.py` |
| Verifiers | `scripts/analog_selftest.py`, `test_parser.py`, `test_digest.py`, `test_integration.py`, `test_translate.py`, `test_export.py`, `test_pdk_plots.py` |

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
  without any error.

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

## Verifying this without Cadence

Everything here runs with no Cadence, no Spectre and no licensed PDK. To prove
the toolchain end to end in one command:

    python scripts/offline_loop.py

That walks the full production round trip on `corpus/03_diffpair_ade.scs`, a
netlist written the way Virtuoso writes them (CDF names, `$PDK ... section=tt`
include, `simulator lang=spectre`): ingest and byte-identical round trip,
digest instead of raw text, substitute an open model card for the licensed PDK,
bias it in ngspice, tune two knobs to hit an output-common-mode spec, re-export
a configured ADE netlist that still points at the real PDK, then re-simulate
*from the exported file* to confirm it reproduces the target.

The eight suites in `scripts/` are the per-component version of the same thing:

    analog_selftest test_parser test_digest test_integration
    test_translate  test_export test_pdk_plots offline_loop

Run them from a clean clone. A lint false positive once appeared only when the
repo path contained a hex-looking temp directory.

**What offline testing genuinely covers.** Parsing, digesting and editing are
exact and round-trip byte-identically, including the writer/parser fixed point
(`emit -> parse -> emit` is stable). Circuits really are biased and swept, by
ngspice, which is an independent implementation and not this code. Numbers are
checked against closed-form hand analysis, not golden files.

**What it cannot cover, and what the Linux box is for.** Four things:

1. **Spectre's numbers.** ngspice is a different simulator with different model
   implementations and convergence. Simulating a Spectre netlist here is a
   translation, and the translation reports what it dropped. Nothing in this
   skill has been checked against a real Spectre run.
2. **The real PDK.** Substituting an open card for a licensed one moves the
   operating point, sometimes a lot. `offline_loop.py` demonstrates this rather
   than hiding it: the corpus diff pair was drawn for gpdk045 and, biased
   against a 180 nm PTM card, its tail starves and both outputs sit near VDD
   until the bias is retuned. Never carry a bias point across a model swap.
3. **`spiceIn` import.** Whether Virtuoso actually builds the cellviews and
   binds CDF parameters is only answerable on the machine with Virtuoso.
4. **`.lib` corner and section semantics.** Which `section=tt` resolves to,
   and how corners are organised, is a property of the kit you have.

`export_package` records `verified_locally: False` in its manifest for exactly
this reason. It is a claim about where the netlist has and has not been run.

## References

- `references/pyspice-cookbook.md` - API recipes and the PySpice bugs patched here
- `references/razavi-map.md` - chapter-to-simulation map with the key formulas
- `references/ade-netlist-errors.md` - Spectre failure catalogue
- `references/spectre-syntax.md` - Spectre syntax reference
- `examples/` - runnable, verified scripts
- `corpus/` - 19 realistic ADE netlists with ground-truth JSON
