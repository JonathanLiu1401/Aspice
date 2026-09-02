# Razavi chapter map: which simulation answers which question

Page numbers refer to *Design of Analog CMOS Integrated Circuits*, 2nd ed.
Formulas below are the hand-analysis side; the simulation column is what
confirms it. Extract gm, gmb, ro from `op.table()` rather than from square-law
algebra - on a real model they differ, and the extracted values are the ones
the simulator's own AC result will agree with.

## Ch. 2 - MOS device physics

| Question | Simulation |
|---|---|
| I-V curves, triode/saturation boundary | `dc_sweep(c, Vd=slice(0, VDD, 0.01))` at several Vg |
| Threshold, body effect Vth(Vsb) | sweep the bulk source, read `d.vth` |
| Transconductance gm = 2*Id/Vov (long channel) | `d.gm`, compare with `2*Id/d.vov` |
| Channel-length modulation | `d.ro` vs L; sweep L and plot |

Body effect: `gmb = gm * gamma / (2*sqrt(2*phi_F + Vsb))`, typically
`gmb/gm = 0.1..0.3`. Read it directly as `d.gmb`.

## Ch. 3 - Single-stage amplifiers

| Stage | Gain (hand) | Notes |
|---|---|---|
| CS, resistive load | `-gm*(RD || ro)` | |
| CS, diode load | `-gm1/gm2` (ignoring body effect) | with gmb: `-gm1/(gm2+gmb2)` |
| CS, current-source load | `-gm1*(ro1 || ro2)` | the load's ro usually dominates |
| CS, source degeneration | `-gm*RD/(1 + (gm+gmb)*RS)` | |
| Source follower | `(gm*ro)/(1 + (gm+gmb)*ro)` -> `gm/(gm+gmb)` | body effect sets the ceiling |
| Common gate | `(gm+gmb)*(RD || ro)` | Rin = `1/(gm+gmb)` |
| Cascode | `-gm1*[(gm2+gmb2)*ro2*ro1 || RD]` | Rout = `(gm2+gmb2)*ro2*ro1` |

Confirm gain with `ac()` at low frequency, and Rout with
`transfer_function(c, 'v(out)', 'vin')`.

## Ch. 4 - Differential amplifiers

- Differential gain: half circuit, then the Ch. 3 formulas.
- `Vin,max` for full switching: `sqrt(2)*Vov`.
- Common-mode gain with tail resistance `RSS`: `-RD/(1/gm + 2*RSS)`.
- CMRR = `|Adm/Acm|`. Simulate both: drive `vinp`/`vinn` differentially, then
  tie them together for the CM run.
- Mismatch: perturb one device's W and re-run to get the offset.

## Ch. 5 - Current mirrors

- Accuracy vs Vds mismatch: sweep the output voltage and watch Iout.
- Cascode mirror Rout = `(gm2+gmb2)*ro2*ro1`; check headroom with
  `op.check_saturation()` - cascodes are where devices fall into triode.
- Five-transistor OTA: gain `gm1*(ro2 || ro4)`.

## Ch. 6 - Frequency response

| Quantity | Simulation |
|---|---|
| Dominant pole, -3 dB | `bode_summary(freq, data['out'])['f_3db']` |
| Unity-gain bandwidth | `['ugb']` |
| Miller multiplication of Cgd | compare `f_3db` with and without the load |
| fT of a device | `f_t(circuit, 'vg', 'vdd')` - do not use Cgs/Cgd |

Miller: input capacitance `Cin = Cgs + Cgd*(1 + |Av|)`. The input pole is then
`1/(2*pi*Rs*Cin)`. Use a BSIM4 node (65 nm and below) so capacitances exist.

## Ch. 7 - Noise

- Thermal noise of a MOSFET: `4kT*gamma*gm`, gamma ~ 2/3 long channel, higher
  when short.
- Resistor: `4kTR`. Verified exactly by the self-test.
- Input-referred noise of a CS stage: `4kT*gamma/gm` plus the load's share.
- Flicker: `K/(Cox*W*L*f)`.

`noise()` plus `noise_table()` ranks the actual contributors, which is the
question most noise problems are really asking ("which device dominates?").
Contributions add in power.

## Ch. 8-10 - Feedback, op-amps, stability

- Loop gain: break the loop, drive the break, measure. Phase margin comes from
  `bode_summary(...)['phase_margin']` at the unity-gain crossing.
- Two-stage op-amp with Miller compensation: dominant pole
  `1/(2*pi*Rout1*Cc*gm2*Rout2)`, second pole ~ `gm2/Cl`.
- Right-half-plane zero at `gm2/Cc`; a nulling resistor `Rz = 1/gm2` cancels it.
- Slew rate `= Itail/Cc`. Confirm with `transient()` and a large step.
- Settling time: transient, then find when the output stays inside the band.

Phase margin below about 60 degrees shows as ringing in the step response -
always cross-check the AC phase margin against a transient step.

## Ch. 11, 17 - Nanometer and short-channel effects

Use `tech('45nm_hp')` or below.

- Velocity saturation: gm saturates as Vov grows; `Id` becomes closer to linear
  in Vov than quadratic. Sweep Vgs and plot `d.gm` to see it directly.
- Intrinsic gain `gm*ro` collapses at short L - sweep L and plot it.
- DIBL: sweep Vds and watch `d.vth` move.
- gm/ID design: `d.gm_id` ranges from ~25-30 1/V in weak inversion down towards
  `2/Vov` in strong inversion. Sweep the bias to build the gm/ID curve, pick a
  point, then size for the required gm.

## Ch. 12-16

- **Bandgap**: CTAT `Vbe` plus PTAT `Vt*ln(n)`. Sweep temperature with the
  `temperature=` argument and find the zero-TC point.
- **Switched capacitor**: `transient()` with pulse sources for the clocks;
  charge injection shows up as a step at the switching instant.
- **Nonlinearity**: transient with a sine, then FFT for HD2/HD3; or a DC sweep
  and fit a polynomial.
- **Oscillators**: ring oscillator period from `transient()`; for an LC VCO,
  check the loop gain with `ac()` first.
- **PLL**: usually better handled with a behavioural model than transistor-level
  SPICE.

## Choosing the node

Razavi's numeric examples mostly assume a 0.18 um-like process; `tech('180nm')`
matches them. Move to 65/45 nm when the question is specifically about
short-channel behaviour. PTM cards are extracted at each node's nominal length,
so a 0.5 um device on the 180 nm card is an extrapolation - usable, worth
saying.
