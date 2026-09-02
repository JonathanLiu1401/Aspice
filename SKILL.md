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
| Verifiers | `scripts/analog_selftest.py`, `test_parser.py`, `test_digest.py`, `test_integration.py`, `test_translate.py` |

Always use that interpreter; PySpice is not on the system Python. If anything
looks broken, run `scripts/analog_selftest.py` - it checks 13 groups of results
against closed-form answers and prints exactly what failed.

```python
import sys; sys.path.insert(0, '<skill>/lib')   # here: ~/.claude/skills/aspice/lib
from analog_spice import *              # analog design
import spectre_netlist as SN            # ADE netlist parse / edit / write
import netlist_digest as ND             # compact views of a big netlist
import spectre_to_ngspice as TR         # translate, simulate, tune, re-export
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

## Cadence is not installed here

There is no Spectre binary on this machine, so netlists are simulated with
ngspice. The parser, digest and editing paths are exact; simulation is a
translation and should be described that way. Nothing in this skill has been
verified against a real Spectre run.

## References

- `references/pyspice-cookbook.md` - API recipes and the PySpice bugs patched here
- `references/razavi-map.md` - chapter-to-simulation map with the key formulas
- `references/ade-netlist-errors.md` - Spectre failure catalogue
- `references/spectre-syntax.md` - Spectre syntax reference
- `examples/` - runnable, verified scripts
- `corpus/` - 19 realistic ADE netlists with ground-truth JSON
