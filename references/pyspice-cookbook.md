# PySpice + ngspice cookbook

Everything here was verified against PySpice 1.4.3 + ngspice 44.2 on Python
3.14. `scripts/selftest.py` re-checks all of it.

## The two bugs this skill patches

Importing `analog_spice` fixes both. Do not import PySpice directly instead.

**1. Benign notes treated as fatal errors.** PySpice 1.4.3 marks any ngspice
stderr line that does not start with `Warning:` as an error and raises
`NgSpiceCommandError: Command 'run' failed`. ngspice 40+ routes notes such as
`Note: vg: dc value used for op instead of transient time=0 value.` to stderr.
The result is an exception raised *after* a perfectly good analysis completed -
the data is in the plot, but you never get it. Symptom: any `.ac` or `.tran` on
a circuit whose source has both a transient spec and a DC value.

**2. `Unsupported Ngspice version 44`.** PySpice's vector-type table stops at
ngspice 32. The enum has not changed, so newer versions are aliased onto it.

## Getting device small-signal parameters

There is no PySpice API for this. Three ways, in order of quality:

| Method | Precision | Model coverage |
|---|---|---|
| `show <dev> : all` parsed | ~6 sig figs | every model; also tells you which params exist |
| `.save @m1[gm]` via `save_internal_parameters` | full double | needs the param name known in advance; **loses node voltages** |
| `let v = @m1[gm]` then read the plot vector | full double | best: full precision, keeps node voltages, one simulation |

`operating_point()` uses `show` to discover the parameter list, then re-reads
each value with `let` - full precision, one simulation, no guessing about which
parameters a model publishes.

**Do not** use `save_internal_parameters` and then expect node voltages:
PySpice's `.save all` does not restore them, and `analysis.nodes` comes back
empty.

## What each model level publishes

| Parameter | level=1 | BSIM3 (49) | BSIM4 (54) |
|---|---|---|---|
| `id`, `vgs`, `vds`, `vbs`, `gm`, `gmbs`, `gds`, `vdsat` | yes | yes | yes |
| `vth` | no | yes | yes |
| `cgg`, `cgs`, `cgd`, `cbd`, `cbs`, `cdd`, `csd`, `cbb` | no | no | yes |

`DeviceOp` returns `None` for anything the model does not publish, never a
substituted estimate.

**BSIM4 capacitance signs and scope.** They are signed Jacobian entries, so
`cgs` is normally negative - use `cgs_abs`. More importantly they are
*intrinsic only*: the gate overlap capacitance is excluded. At 45 nm,
`gm/(2*pi*(|Cgs|+|Cgd|))` gives 644 GHz where the measured fT is 287 GHz. Use
`f_t()`, which finds the frequency where the current gain `|h21|` reaches 1.

## Analyses

```python
op   = operating_point(circuit)                       # .op
freq, data = ac(circuit, 1, 1e12, points_per_decade=20)   # .ac dec
sweep, an  = dc_sweep(circuit, Vd=slice(0, 1.8, 0.01))    # .dc
t, an      = transient(circuit, step_time=1@u_ps, end_time=10@u_ns)
tf   = transfer_function(circuit, 'v(out)', 'vin')    # gain, Rin, Rout
nz   = noise(circuit, 'out', 'vin', 1, 1e9)           # .noise + breakdown
```

### AC

The input source must carry an AC magnitude or every result is zero:

```python
c.SinusoidalVoltageSource('in', 'in', c.gnd, dc_offset=0.7@u_V, ac_magnitude=1@u_V)
```

With `ac_magnitude=1`, node arrays *are* the transfer functions.

### Noise

PySpice's `Analysis` object cannot classify noise vectors (it warns
`Unit is None for onoise_spectrum`) and they never reach `analysis.nodes`.
`noise()` reads them straight off the ngspice plot instead.

`points_per_summary=1` is what makes ngspice emit the per-generator breakdown
(`onoise_m1_id`, `onoise_r1_thermal`, ...). Those are output-referred
*densities* that add in power: `sqrt(sum(c**2)) == onoise`. Verified exactly.

Only the `noise1` plot (spectral density) is produced; integrate it yourself
for total RMS - `noise()` already returns `integrated_onoise`.

## Netlist gotchas

- **`.op` values are 1-element arrays.** `float(np.asarray(analysis['out'])[0])`,
  not `float(analysis['out'])`. `op.v('out')` does this for you.
- **Node named `in`** is a Python keyword; PySpice warns but works. Access it as
  `data['in']`.
- **Include paths**: use forward slashes on Windows. `tech()` handles this.
- **AD/AS/PD/PS**: BSIM warns `Pd = 0 is less than W` if you leave them at zero.
  Junction capacitance is then wrong, which affects HF results only. Pass them
  explicitly when frequency response matters.
- **PMOS sign convention**: ngspice reports `vgs`, `vds`, `vth`, `vdsat` as
  negative for PMOS. Compare magnitudes. `DeviceOp.region` already does.
- **Device names are lower-cased** by ngspice: `MOSFET(1, ...)` is `op.devices['m1']`.

## Independence of checks

An AC gain and a hand formula built from the *same* extracted gm and ro are not
independent - both are the same linearisation, so agreement to 7 digits proves
only that your topology and algebra are right. To test the physics, add a DC
finite difference, which never touches gm:

```python
d = 5e-4
av = (operating_point(build(vin + d)).v('out')
      - operating_point(build(vin - d)).v('out')) / (2 * d)
```

## Running

```
python your_script.py      # the venv interpreter that has PySpice
```

ngspice lives inside the venv at
`Lib/site-packages/PySpice/Spice/NgSpice/Spice64_dll/`; its `spinit` was
patched to point at that directory (the shipped file hard-codes `C:/Spice64`).
A standalone `ngspice_con.exe` from the same ngspice distribution is useful
for running raw netlists.
