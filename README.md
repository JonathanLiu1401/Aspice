# aspice - agentic SPICE

A Claude Code skill for transistor-level analog CMOS design and for working with
Cadence ADE / Spectre netlists, built on [PySpice](https://pyspice.fabrice-salvaire.fr/)
and [ngspice](https://ngspice.sourceforge.io/).

Two things it is meant to fix:

1. **Analog answers that are never checked.** Every result is produced twice -
   once by hand from the small-signal model, once by simulation - and printed
   in a reconciliation table. A number that exists only one way is not an
   answer yet.
2. **Netlists too large to read.** A real ADE netlist runs to tens of thousands
   of lines. Parsing it into a compact digest is a ~270x token reduction, so an
   agent reads a summary and pulls raw text only where it matters.

## What is in it

| Module | Purpose |
|---|---|
| `lib/analog_spice.py` | Operating points with per-device small-signal parameters and region checks, AC/Bode with gain-bandwidth extraction, noise with per-device contributions, DC sweeps, transient, bias solving, fT measurement, hand-vs-SPICE reconciliation |
| `lib/spectre_netlist.py` | Spectre/ADE netlist parser and writer with byte-identical round-trip; edits change only the affected line |
| `lib/netlist_digest.py` | Progressive-disclosure views of a large netlist: outline, summary, device rollup, net reports, dangling-node detection, digest diffing |

Plus 19 realistic ADE netlists with ground-truth JSON in `corpus/`, three
verified worked examples in `examples/`, and reference material in
`references/`.

## Setup

Requires Python 3, PySpice 1.4.3 and ngspice (>= 33; tested on 44.2).

```bash
python -m venv .venv
.venv/Scripts/python -m pip install "PySpice==1.4.3"
python scripts/fetch_models.py      # download the transistor models
```

`analog_spice` patches two PySpice/ngspice incompatibilities at import time, so
import it rather than importing PySpice directly:

- PySpice 1.4.3 treats every ngspice stderr line that is not prefixed
  `Warning:` as fatal. ngspice >= 40 sends benign `Note:` lines there, so a
  perfectly good `.ac` or `.tran` raises after the analysis already succeeded.
- PySpice's vector-type table stops at ngspice 32, producing a spurious
  "Unsupported Ngspice version" warning on anything newer.

### Transistor models

Model cards are **not** included. `scripts/fetch_models.py` downloads the
Predictive Technology Model cards (180 nm down to 22 nm) and normalises every
card's devices to `nch` / `pch`, so a circuit changes technology node by
changing one `tech()` call.

PTM originated at ptm.asu.edu, which is offline; the script pulls from a
third-party mirror that carries no licence, which is exactly why the files are
fetched rather than vendored here. They are predictive academic models, not a
foundry PDK - good for studying trends and checking hand analysis, not for
signing off silicon.

## Usage

```python
import sys; sys.path.insert(0, 'lib')
from analog_spice import *

def build(vin):
    c = Circuit('cs')
    c.include(tech('180nm'))
    c.V('dd', 'vdd', c.gnd, 1.8 @ u_V)
    c.V('b',  'vb',  c.gnd, 0.9 @ u_V)
    c.V('in', 'in',  c.gnd, float(vin) @ u_V)
    c.MOSFET(1, 'out', 'in', c.gnd, c.gnd, model='nch', w=20e-6, l=0.5e-6)
    c.MOSFET(2, 'out', 'vb', 'vdd', 'vdd', model='pch', w=40e-6, l=0.5e-6)
    return c

vin, op = solve_bias(build, 'out', 0.9, low=0.3, high=1.2)
op.check_saturation()      # prove the region before trusting anything else
op.table()                 # gm, gmb, ro, gm/ID, gm*ro per device
```

For netlists:

```python
import spectre_netlist as SN, netlist_digest as ND

nl = SN.parse_file('design.scs')
print(ND.outline(nl))                  # start cheap
print(ND.net_report(nl, 'vout'))       # escalate only where needed
SN.set_param(nl, 'Wn', '4u')
open('tuned.scs', 'w').write(SN.write_netlist(nl))   # only that line changed
```

## Verifying the install

```bash
python scripts/analog_selftest.py     # 13 groups vs closed-form answers
python scripts/test_parser.py         # 151 parser tests
python scripts/test_digest.py         # 33 digest tests
python scripts/test_integration.py    # parser + digest over the whole corpus
python scripts/bench_tokens.py        # compression benchmark
```

The self-test compares against independently computed answers rather than
golden files: a resistive divider, an RC corner frequency, an RC step at one
time constant, `.tf` gain/Rin/Rout, resistor thermal noise against 4kTR,
square-law Id/gm/gds against the closed-form bias solution, region detection,
and fT against a measured current gain.

## Things worth knowing

Found by measurement while building this, and encoded in the tools:

- **ngspice reads a trailing `A` as atto (1e-18) and `F` as femto (1e-15).**
  PySpice renders `100e-6 @ u_A` as `0.0001A`, which simulates as 1e-22 A - a
  dead circuit, no warning. `lint_netlist` runs before every analysis and
  refuses the netlist.
- **fT cannot be computed from the reported capacitances.** ngspice publishes
  BSIM4's intrinsic charge-model capacitances, excluding gate overlap. At 45 nm
  `gm/(2*pi*(|Cgs|+|Cgd|))` gives 644 GHz where the measured `|h21| = 1`
  crossing is 287 GHz. `f_t()` measures it.
- **`show` output is truncated to ~6 significant figures.** Device parameters
  are re-read as raw doubles via ngspice vectors, in the same simulation.
- **A single-frequency `.noise` run silently omits the per-device breakdown.**
  Sweep at least a decade.
- **In Spectre `M` is mega; in SPICE it is milli.** A silent 1e9 error.

## Scope and limits

- **Cadence is not required and was not available.** Netlists are simulated
  with ngspice, so simulation of a Spectre netlist is a translation. Parsing,
  digesting and editing are exact and round-trip byte-identically; nothing here
  has been checked against a real Spectre run.
- PTM models are predictive and academic. Each is extracted at its node's
  nominal channel length, so a much longer L is an extrapolation.
- The flicker-noise parameters are absent from the PTM cards, so 1/f
  contributions come back as exactly zero. Thermal noise is modelled properly.

## Licence

MIT, see `LICENSE`. Transistor model cards are downloaded at setup time and are
not covered by it.
