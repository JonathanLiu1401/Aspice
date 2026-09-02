"""Razavi Ch.3: common-source stage with a PMOS current-source load.

The canonical worked problem, done the way this skill recommends: bias it,
prove the region, extract the small-signal parameters, do the hand analysis
from those, simulate the same quantity, and reconcile.

Note the third, genuinely independent check. The AC gain and the hand formula
-gm1/(gds1+gds2) are the *same* linearisation of the *same* model, so their
agreement proves the topology and the algebra but not the physics. The DC
finite difference never touches gm or gds, so it is the real cross-check.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

import numpy as np
from analog_spice import (Circuit, u_V, u_Hz, tech, operating_point, ac,
                          bode_summary, solve_bias, Reconcile)

VDD, VBIAS, VOUT_TARGET = 1.8, 0.9, 0.9


def build(vin):
    """The stage, biased with gate voltage `vin`."""
    c = Circuit('cs_current_source_load')
    c.include(tech('180nm'))
    c.V('dd', 'vdd', c.gnd, VDD @ u_V)
    c.V('b', 'vb', c.gnd, VBIAS @ u_V)
    c.V('in', 'in', c.gnd, float(vin) @ u_V)
    # M1: NMOS input device. M2: PMOS current-source load, bulk to VDD.
    c.MOSFET(1, 'out', 'in', c.gnd, c.gnd, model='nch', w=20e-6, l=0.5e-6)
    c.MOSFET(2, 'out', 'vb', 'vdd', 'vdd', model='pch', w=40e-6, l=0.5e-6)
    return c


def build_ac(vin):
    """Same stage with an AC-driven gate, for the .ac run."""
    c = build(vin)
    c.Vin.detach()
    c.SinusoidalVoltageSource('in', 'in', c.gnd, dc_offset=float(vin) @ u_V,
                              ac_magnitude=1 @ u_V)
    return c


# --- 1. Bias so the output sits at mid-supply -------------------------------
vin, op = solve_bias(build, 'out', VOUT_TARGET, low=0.3, high=1.2, tol=1e-8)
print(f'Input bias that centres the output: Vin = {vin:.6f} V')
print(f'Output: V(out) = {op.v("out"):.6f} V   supply current = '
      f'{-op.i("vdd") * 1e6:.3f} uA\n')

# --- 2. Prove both devices are saturated ------------------------------------
ok, problems = op.check_saturation()
if not ok:
    raise SystemExit(f'bias is not usable: {problems}')

# --- 3. Small-signal parameters, read from the model ------------------------
print()
op.table()
m1, m2 = op.devices['m1'], op.devices['m2']

# --- 4. Hand analysis, from those extracted parameters ----------------------
# Both bulks are tied to their own sources, so Vbs = 0 and the gmb generators
# are dead. That is worth checking rather than assuming: gmb1/gm1 is ~0.33, so
# wrongly including it would move the answer by tens of percent.
assert abs(m1.vbs) < 1e-9 and abs(m2.vbs) < 1e-9, 'body effect is active here'
rout_hand = 1.0 / (m1.gds + m2.gds)
av_hand = -m1.gm * rout_hand

# --- 5. Simulate the same quantity ------------------------------------------
freq, data = ac(build_ac(vin), start_frequency=1 @ u_Hz,
                stop_frequency=1e9 @ u_Hz, points_per_decade=20)
av_ac = float(np.real(data['out'][0]))
summary = bode_summary(freq, data['out'])

# --- 6. Independent check: DC finite difference -----------------------------
delta = 5e-4
av_fd = ((operating_point(build(vin + delta)).v('out')
          - operating_point(build(vin - delta)).v('out')) / (2 * delta))

# --- 7. Reconcile -----------------------------------------------------------
r = Reconcile('Common-source stage with current-source load', default_tol=1e-3)
r.add('Rout', rout_hand, 1.0 / (m1.gds + m2.gds), unit='Ohm')
r.add('Av (AC vs hand)', av_ac, av_hand)
r.add('Av (AC vs DC f.d.)', av_ac, av_fd, tol=1e-2)
r.report()

print(f'\nIntrinsic gain of M1: gm*ro = {m1.intrinsic_gain:.2f}')
print(f'Stage gain           : {abs(av_ac):.2f}')
print(f'M1 ro = {m1.ro / 1e3:.2f} kOhm, M2 ro = {m2.ro / 1e3:.2f} kOhm')
print('The load, not the input device, sets the gain: the PMOS has the lower')
print('ro, so it dominates the parallel combination and the stage falls well')
print('short of M1\'s intrinsic gain. Cascoding the load, or lengthening M2,')
print('is what recovers it.')
print(f'\n-3 dB bandwidth (no explicit load cap): {summary["f_3db"] / 1e6:.2f} MHz')
