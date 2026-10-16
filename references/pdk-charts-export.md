# PDKs, design charts, and netlist export

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
