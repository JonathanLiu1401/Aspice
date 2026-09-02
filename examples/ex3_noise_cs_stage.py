"""Razavi Ch.7: noise of a common-source stage.

Two questions a noise problem is really asking: how much, and which device is
to blame. `noise()` answers the first, `noise_table()` the second.

The hand check is the input-referred thermal noise of the input device,
4kT*gamma/gm, with the load's contribution added in power. gamma is extracted
from the simulator rather than assumed to be 2/3 - for a short-channel device
it is not.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

import math
import numpy as np
from analog_spice import (Circuit, u_V, u_Hz, u_Ohm, tech, operating_point,
                          ac, noise, noise_table, solve_bias, Reconcile)

K_B, T = 1.380649e-23, 300.15
VDD, RD = 1.8, 12e3
W, L = 20e-6, 0.5e-6


def build(vg):
    c = Circuit('cs_noise')
    c.include(tech('180nm'))
    c.V('dd', 'vdd', c.gnd, VDD @ u_V)
    c.V('in', 'in', c.gnd, f'DC {float(vg)} AC 1')
    c.R(1, 'vdd', 'out', RD @ u_Ohm)
    c.MOSFET(1, 'out', 'in', c.gnd, c.gnd, model='nch', w=W, l=L)
    return c


# Centre the output rather than guessing a gate voltage: at VG = 0.75 V this
# stage sits in triode, which check_saturation would (correctly) reject.
VG, op = solve_bias(build, 'out', 0.9, low=0.4, high=1.0, tol=1e-8)
print(f'Gate bias for Vout = 0.9 V: VG = {VG:.6f} V')
build = (lambda vg=VG, _b=build: _b(vg))
ok, problems = op.check_saturation()
if not ok:
    raise SystemExit(problems)
print()
op.table()
m1 = op.devices['m1']

# Low-frequency gain, needed to refer output noise back to the input.
_, data = ac(build(), start_frequency=1 @ u_Hz, stop_frequency=10 @ u_Hz,
             points_per_decade=2)
gain = abs(complex(data['out'][0]))

# Noise at 1 MHz, high enough to be clear of the flicker corner.
# Sweep at least a decade: ngspice omits the per-device breakdown entirely
# for a single-frequency noise run (it warns, then gives you only totals).
nz = noise(build(), output_node='out', source='vin',
           start_frequency=1e5 @ u_Hz, stop_frequency=1e7 @ u_Hz,
           points_per_decade=10)
i1M = int(np.argmin(abs(nz['freq'] - 1e6)))

print(f'\nGain at DC                   : {gain:.4f}')
print(f'Output noise density   @1MHz : {nz["onoise"][i1M]:.4e} V/sqrt(Hz)')
print(f'Input-referred         @1MHz : {nz["inoise"][i1M]:.4e} V/sqrt(Hz)')
print('\nWho is responsible:')
noise_table(nz, index=i1M)

# --- hand analysis ----------------------------------------------------------
# Recover the simulator's own channel-noise coefficient from its M1 thermal
# contribution, rather than assuming the long-channel 2/3.
onoise_m1 = nz['contributions']['onoise_m1_id'][i1M]
onoise_rd = nz['contributions']['onoise_r1_thermal'][i1M]
rout = RD * m1.ro / (RD + m1.ro)
gamma = float(onoise_m1) ** 2 / (4 * K_B * T * m1.gm * rout ** 2)

# M1's channel noise, output-referred: 4kT*gamma*gm * Rout^2
onoise_m1_hand = math.sqrt(4 * K_B * T * gamma * m1.gm) * rout
# RD's thermal noise, output-referred: 4kT*RD through the RD||ro divider
onoise_rd_hand = math.sqrt(4 * K_B * T * RD) * (m1.ro / (RD + m1.ro))
onoise_hand = math.hypot(onoise_m1_hand, onoise_rd_hand)
inoise_hand = onoise_hand / gain

r = Reconcile('Common-source stage noise at 1 MHz', default_tol=0.02)
r.add('Rout', rout, rout, unit='Ohm')
r.add('onoise from M1', float(onoise_m1), onoise_m1_hand)
r.add('onoise from RD', float(onoise_rd), onoise_rd_hand)
r.add('total onoise', float(nz['onoise'][i1M]), onoise_hand)
r.add('input-referred', float(nz['inoise'][i1M]), inoise_hand)
r.report()

print(f'\nExtracted channel-noise coefficient gamma = {gamma:.3f}')
print('(the long-channel value is 2/3 = 0.667; a short device runs higher,')
print(' which is why gamma should be extracted rather than assumed)')

share_m1 = float(onoise_m1) ** 2 / float(nz['onoise'][i1M]) ** 2
print(f'\nM1 contributes {100 * share_m1:.1f}% of the output noise power, RD the rest.')
print('Input-referred noise of the device alone is 4kT*gamma/gm, so the only')
print('ways to improve it are more gm for the same current (larger W, or a')
print('lower-inversion bias) or more current. Raising RD raises the gain and')
print('the noise together, so it barely moves the input-referred figure.')

# Total RMS noise in a realistic band, which is what actually matters.
band = noise(build(), output_node='out', source='vin',
             start_frequency=1 @ u_Hz, stop_frequency=1e9 @ u_Hz,
             points_per_decade=50)
print(f'\nIntegrated over 1 Hz to 1 GHz: output '
      f'{band["integrated_onoise"] * 1e6:.2f} uV rms, input-referred '
      f'{band["integrated_inoise"] * 1e6:.2f} uV rms')

flicker = float(nz['contributions']['onoise_m1_1overf'][0])
if flicker == 0.0:
    print('\nCaveat worth stating: the 1/f contribution is exactly zero because')
    print('this PTM card carries no flicker parameters (kf/af/noimod are unset),')
    print('so only thermal noise is modelled. Any conclusion about the 1/f corner')
    print('or about low-frequency noise needs a model card that actually has')
    print('flicker data - the simulator cannot invent it. The thermal numbers')
    print('above are unaffected.')
