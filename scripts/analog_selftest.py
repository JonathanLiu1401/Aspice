"""Self-test for the analog-cmos-spice skill.

Every check compares a simulator result against an independently computed
closed-form answer. Run it after install, or whenever ngspice/PySpice changes:

    python scripts/analog_selftest.py

using the interpreter of the venv that has PySpice installed.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

import numpy as np
from analog_spice import (Circuit, u_V, u_A, u_Ohm, u_kOhm, u_F, u_Hz, u_kHz,
                          u_s, u_us, u_ms, u_ps, u_uF, u_nF,
                          operating_point, ac, bode_summary, dc_sweep,
                          transient, transfer_function, noise, tech, list_tech,
                          db20, solve_bias, f_t)

FAILURES = []
K_B = 1.380649e-23


def check(name, got, want, tol=1e-6, unit=''):
    if want == 0:
        rel = abs(got)
    else:
        rel = abs(got - want) / abs(want)
    ok = rel <= tol
    status = 'PASS' if ok else 'FAIL'
    print(f'  [{status}] {name:<38} got={got:.6g}{unit}  want={want:.6g}{unit}'
          f'  rel={rel:.2e}')
    if not ok:
        FAILURES.append(name)
    return ok


def check_true(name, cond, detail=''):
    status = 'PASS' if cond else 'FAIL'
    print(f'  [{status}] {name}{"  " + detail if detail else ""}')
    if not cond:
        FAILURES.append(name)
    return cond


# ---------------------------------------------------------------------------
print('\n1. DC operating point - resistive divider')
c = Circuit('divider')
c.V('in', 'in', c.gnd, 10 @ u_V)
c.R(1, 'in', 'out', 1 @ u_kOhm)
c.R(2, 'out', c.gnd, 3 @ u_kOhm)
op = operating_point(c)
check('divider Vout', op.v('out'), 7.5, 1e-9, ' V')

# ---------------------------------------------------------------------------
print('\n2. AC - RC low pass at its own -3 dB point')
R, C = 1e3, 1e-9
fc = 1 / (2 * math.pi * R * C)
c = Circuit('rc')
c.SinusoidalVoltageSource('in', 'in', c.gnd, amplitude=1 @ u_V, ac_magnitude=1 @ u_V)
c.R(1, 'in', 'out', R @ u_Ohm)
c.C(1, 'out', c.gnd, C @ u_F)
freq, data = ac(c, start_frequency=fc / 1000, stop_frequency=fc * 1000,
                points_per_decade=200)
summary = bode_summary(freq, data['out'])
check('RC f_3dB', summary['f_3db'], fc, 5e-3, ' Hz')
check('RC dc gain', summary['dc_gain'], 1.0, 1e-6)

# ---------------------------------------------------------------------------
print('\n3. Transient - RC step at one time constant')
c = Circuit('step')
c.PulseVoltageSource('in', 'in', c.gnd, initial_value=0 @ u_V, pulsed_value=1 @ u_V,
                     delay_time=0 @ u_s, rise_time=1 @ u_ps, fall_time=1 @ u_ps,
                     pulse_width=1 @ u_ms, period=1 @ u_ms)
c.R(1, 'in', 'out', 1 @ u_kOhm)
c.C(1, 'out', c.gnd, 1 @ u_uF)
t, an = transient(c, step_time=1 @ u_us, end_time=5 @ u_ms)
v_tau = float(np.interp(1e-3, t, np.asarray(an['out'])))
check('V(1 tau)/Vf', v_tau, 1 - math.exp(-1), 2e-3, ' V')

# ---------------------------------------------------------------------------
print('\n4. Transfer function - divider gain, Rin, Rout')
c = Circuit('tf')
c.V('in', 'in', c.gnd, 1 @ u_V)
c.R(1, 'in', 'out', 1 @ u_kOhm)
c.R(2, 'out', c.gnd, 3 @ u_kOhm)
tf = transfer_function(c, 'v(out)', 'vin')
check('tf gain', tf['gain'], 0.75, 1e-9)
check('tf Rin', tf['input_impedance'], 4000.0, 1e-9, ' Ohm')
check('tf Rout', tf['output_impedance'], 750.0, 1e-9, ' Ohm')

# ---------------------------------------------------------------------------
print('\n5. Noise - two 10k resistors, output at the midpoint')
T = 300.15
c = Circuit('rnoise')
c.SinusoidalVoltageSource('in', 'in', c.gnd, ac_magnitude=1 @ u_V)
c.R(1, 'in', 'out', 10 @ u_kOhm)
c.R(2, 'out', c.gnd, 10 @ u_kOhm)
nz = noise(c, output_node='out', source='vin',
           start_frequency=1 @ u_kHz, stop_frequency=1 @ u_kHz, points_per_decade=1)
# Vin is an ac short, so the output sees R1 || R2 = 5k of thermal noise.
want = math.sqrt(4 * K_B * T * 5e3)
check('onoise density', float(nz['onoise'][0]), want, 1e-4, ' V/rtHz')
check('inoise = onoise/gain', float(nz['inoise'][0]),
      float(nz['onoise'][0]) / 0.5, 1e-6, ' V/rtHz')
contributions = {k: v for k, v in nz['contributions'].items() if 'thermal' in k}
check_true('per-device noise breakdown present',
           len(contributions) == 2, f'{sorted(contributions)}')
power_sum = math.sqrt(sum(float(v[0]) ** 2 for v in contributions.values()))
check('contributions add in power', power_sum, float(nz['onoise'][0]), 1e-6)

# ---------------------------------------------------------------------------
print('\n6. Square-law MOSFET - Id, gm, gds against closed form')
kp, W, L, VTO, lam, VDD, RD, VG = 200e-6, 10e-6, 0.5e-6, 0.4, 0.1, 1.8, 1e3, 0.8
beta, Vov = kp * W / L, VG - VTO
c = Circuit('cs')
c.model('NM', 'nmos', level=1, kp=kp, vto=VTO, lambda_=lam)
c.V('dd', 'vdd', c.gnd, VDD @ u_V)
c.V('g', 'g', c.gnd, VG @ u_V)
c.R(1, 'vdd', 'd', RD @ u_Ohm)
c.MOSFET(1, 'd', 'g', c.gnd, c.gnd, model='NM', w=W, l=L)
op = operating_point(c)
m1 = op.devices['m1']

# Solve the bias point in closed form: Id = (beta/2) Vov^2 (1 + lambda Vd),
# Vd = VDD - Id RD  =>  linear in Vd.
Vd_hand = (VDD - 0.5 * beta * Vov ** 2 * RD) / (1 + 0.5 * beta * Vov ** 2 * lam * RD)
Id_hand = (VDD - Vd_hand) / RD
gm_hand = beta * Vov * (1 + lam * Vd_hand)
gds_hand = 0.5 * beta * Vov ** 2 * lam

check_true('m1 detected and saturated', m1.region == 'saturation', repr(m1.region))
check('m1 Id', m1.id, Id_hand, 1e-8, ' A')
check('m1 gm', m1.gm, gm_hand, 1e-8, ' S')
check('m1 gds', m1.gds, gds_hand, 1e-4, ' S')
check('m1 gm/Id', m1.gm_id, gm_hand / Id_hand, 1e-8, ' 1/V')
check('m1 gm*ro', m1.intrinsic_gain, gm_hand / gds_hand, 1e-4)

print('\n7. Same stage - AC gain equals the small-signal prediction')
c2 = Circuit('cs_ac')
c2.model('NM', 'nmos', level=1, kp=kp, vto=VTO, lambda_=lam)
c2.V('dd', 'vdd', c2.gnd, VDD @ u_V)
c2.SinusoidalVoltageSource('g', 'g', c2.gnd, dc_offset=VG @ u_V, ac_magnitude=1 @ u_V)
c2.R(1, 'vdd', 'd', RD @ u_Ohm)
c2.MOSFET(1, 'd', 'g', c2.gnd, c2.gnd, model='NM', w=W, l=L)
freq, data = ac(c2, start_frequency=1 @ u_Hz, stop_frequency=10 @ u_Hz,
                points_per_decade=2)
Av_sim = float(np.real(data['d'][0]))
Av_hand = -m1.gm / (1 / RD + m1.gds)
check('CS gain (AC vs gm, ro)', Av_sim, Av_hand, 1e-7)

# ---------------------------------------------------------------------------
print('\n8. Region detection - the same device pushed into triode')
c3 = Circuit('triode')
c3.model('NM', 'nmos', level=1, kp=kp, vto=VTO, lambda_=lam)
c3.V('dd', 'vdd', c3.gnd, VDD @ u_V)
c3.V('g', 'g', c3.gnd, VG @ u_V)
c3.R(1, 'vdd', 'd', 5 @ u_kOhm)
c3.MOSFET(1, 'd', 'g', c3.gnd, c3.gnd, model='NM', w=W, l=L)
op3 = operating_point(c3)
check_true('triode device reported as triode',
           op3.devices['m1'].region == 'triode', repr(op3.devices['m1'].region))
ok, problems = op3.check_saturation(verbose=False)
check_true('check_saturation flags it', (not ok) and len(problems) == 1, str(problems))

# ---------------------------------------------------------------------------
print('\n9. DC sweep - Id/Vds curve reaches the square-law value')
c4 = Circuit('idvds')
c4.model('NM', 'nmos', level=1, kp=kp, vto=VTO, lambda_=0)
c4.V('d', 'd', c4.gnd, 0 @ u_V)
c4.V('g', 'g', c4.gnd, 1.0 @ u_V)
c4.MOSFET(1, 'd', 'g', c4.gnd, c4.gnd, model='NM', w=10e-6, l=1e-6)
sweep, an = dc_sweep(c4, Vd=slice(0, 2, 0.05))
idd = -np.asarray(an.branches['vd'])
check('Id in saturation', float(idd[sweep > 1.0][0]),
      0.5 * kp * 10 * (1.0 - 0.4) ** 2, 1e-6, ' A')

# ---------------------------------------------------------------------------
print('\n10. Technology models load and give physical devices')
check_true('model libraries present', len(list_tech()) >= 4, str(list_tech()))
for node, vdd, length in (('180nm', 1.8, 0.18e-6), ('65nm', 1.1, 65e-9),
                          ('45nm_hp', 1.0, 45e-9)):
    c5 = Circuit(f'probe_{node}')
    c5.include(tech(node))
    c5.V('dd', 'vdd', c5.gnd, vdd @ u_V)
    c5.V('g', 'g', c5.gnd, (0.75 * vdd) @ u_V)
    c5.V('s', 'src', c5.gnd, 0 @ u_V)
    c5.MOSFET(1, 'vdd', 'g', 'src', c5.gnd, model='nch', w=10e-6, l=length)
    op5 = operating_point(c5)
    d = op5.devices['m1']
    sane = (d.region == 'saturation' and d.gm is not None and d.gm > 0
            and d.ro is not None and d.ro > 0 and 0 < d.gm_id < 40)
    check_true(f'{node} nch biases sanely', sane,
               f'gm={d.gm:.3e} S, ro={d.ro:.3e} Ohm, gm/Id={d.gm_id:.2f} 1/V, '
               f'Vth={d.vth:.3f} V')

print('\n11. BSIM4 reports capacitances; BSIM3 does not (and says so)')
c6 = Circuit('caps')
c6.include(tech('45nm_hp'))
c6.V('dd', 'vdd', c6.gnd, 1.0 @ u_V)
c6.V('g', 'g', c6.gnd, 0.7 @ u_V)
c6.MOSFET(1, 'vdd', 'g', c6.gnd, c6.gnd, model='nch', w=10e-6, l=45e-9)
d6 = operating_point(c6).devices['m1']
check_true('BSIM4 reports Cgs', d6.cgs_abs is not None,
           f'|Cgs|={d6.cgs_abs:.3e} F')
c7 = Circuit('nocaps')
c7.include(tech('180nm'))
c7.V('dd', 'vdd', c7.gnd, 1.8 @ u_V)
c7.V('g', 'g', c7.gnd, 1.0 @ u_V)
c7.MOSFET(1, 'vdd', 'g', c7.gnd, c7.gnd, model='nch', w=10e-6, l=0.18e-6)
d7 = operating_point(c7).devices['m1']
check_true('BSIM3 returns None for Cgs rather than a guess', d7.cgs is None)

print('\n12. solve_bias puts the output where it was asked to')


def build_cs(vin):
    c = Circuit('bias')
    c.include(tech('180nm'))
    c.V('dd', 'vdd', c.gnd, 1.8 @ u_V)
    c.V('b', 'vb', c.gnd, 0.9 @ u_V)
    c.V('in', 'in', c.gnd, float(vin) @ u_V)
    c.MOSFET(1, 'out', 'in', c.gnd, c.gnd, model='nch', w=20e-6, l=0.5e-6)
    c.MOSFET(2, 'out', 'vb', 'vdd', 'vdd', model='pch', w=40e-6, l=0.5e-6)
    return c


vin, op_b = solve_bias(build_cs, 'out', 0.9, low=0.3, high=1.2, tol=1e-7)
check('solve_bias hits target Vout', op_b.v('out'), 0.9, 1e-6, ' V')
ok, problems = op_b.check_saturation(verbose=False)
check_true('both devices saturated at the solved bias', ok, str(problems))
m1b, m2b = op_b.devices['m1'], op_b.devices['m2']
# Gain from the extracted parameters must match a direct DC finite difference,
# which never touches gm or gds.
av_ss = -m1b.gm / (m1b.gds + m2b.gds)
d = 5e-4
av_fd = ((operating_point(build_cs(vin + d)).v('out')
          - operating_point(build_cs(vin - d)).v('out')) / (2 * d))
check('gain: small-signal vs DC finite difference', av_ss, av_fd, 2e-3)

print('\n13. fT measured from |h21| = 1, not from the reported capacitances')
c8 = Circuit('ft')
c8.include(tech('45nm_hp'))
c8.V('dd', 'd', c8.gnd, 1.0 @ u_V)
c8.SinusoidalVoltageSource('g', 'g', c8.gnd, dc_offset=0.7 @ u_V, ac_magnitude=1 @ u_V)
c8.MOSFET(1, 'd', 'g', c8.gnd, c8.gnd, model='nch', w=10e-6, l=45e-9)
ft = f_t(c8, gate_source='vg', drain_source='vdd')
check_true('fT measured and physically plausible for 45 nm',
           ft is not None and 50e9 < ft < 500e9, f'fT={ft / 1e9:.1f} GHz')
d8 = operating_point(c8).devices['m1']
ft_cap = d8.gm / (2 * math.pi * (abs(d8.cgs) + abs(d8.cgd)))
check_true('capacitance formula really is optimistic (why f_t exists)',
           ft_cap > 1.5 * ft,
           f'cap formula {ft_cap / 1e9:.0f} GHz vs measured {ft / 1e9:.0f} GHz')

# ---------------------------------------------------------------------------
print('\n' + '=' * 64)
if FAILURES:
    print(f'{len(FAILURES)} CHECK(S) FAILED:')
    for f in FAILURES:
        print(f'  - {f}')
    sys.exit(1)
print('ALL CHECKS PASSED')
