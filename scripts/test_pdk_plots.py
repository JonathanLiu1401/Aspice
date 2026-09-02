"""Tests for lib/pdk.py and lib/plots.py.

A chart that renders is not a chart that is right, so these check the numbers
behind the picture: that the I-V family really saturates, that gm/ID lands in
the physically expected range and is nearly independent of W, that intrinsic
gain rises with channel length, and that the Bode annotations match the
analytic corner frequency.

    python scripts/test_pdk_plots.py
"""
from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib'))

import numpy as np
import pdk as PDK
import plots as PL
from analog_spice import Circuit, u_V, u_Ohm, u_F, u_Hz, ac

FAILURES = []
MODELS = ROOT / 'models' / 'ptm_180nm.lib'


def check(name, cond, detail=''):
    print(f'  [{"PASS" if cond else "FAIL"}] {name}{"  " + str(detail) if detail else ""}')
    if not cond:
        FAILURES.append(name)
    return cond


if not MODELS.is_file():
    print('models/ptm_180nm.lib missing - run scripts/fetch_models.py first')
    sys.exit(1)

# ---------------------------------------------------------------------------
print('\n1. PDK scanning')
p = PDK.scan(ROOT / 'models')
check('found model files', len([f for f in p.files if f.models]) >= 4,
      f'{len([f for f in p.files if f.models])} files')
check('found model cards', len(p.models) >= 8, f'{len(p.models)} cards')
devices = p.device_names()
check('classified nmos and pmos', 'nmos' in devices and 'pmos' in devices,
      sorted(devices))
check('nch recognised as nmos', 'nch' in devices.get('nmos', []))
check('pch recognised as pmos', 'pch' in devices.get('pmos', []))
families = {m.model_family for m in p.models}
check('model family identified from level',
      'BSIM3v3' in families and 'BSIM4' in families, sorted(families))
check('find() locates by regex', len(p.find('^nch$')) >= 1)
check('summary is compact', len(p.summary().splitlines()) < 30,
      f'{len(p.summary().splitlines())} lines')

skeleton = p.techmap_skeleton()
check('techmap skeleton names real cells',
      skeleton['cells']['nmos'] == 'nch' and skeleton['cells']['pmos'] == 'pch',
      skeleton['cells'])
check('techmap skeleton flags the CDF guess', '_check_me' in skeleton)

print('\n2. Scanning something that is not a PDK fails honestly')
with tempfile.TemporaryDirectory() as tmp:
    (Path(tmp) / 'notes.txt').write_text('nothing to see here\n')
    empty = PDK.scan(tmp)
    check('reports no models rather than inventing them',
          empty.models == [] and any('no model cards' in w for w in empty.warnings),
          empty.warnings)
missing = PDK.scan(Path(tempfile.gettempdir()) / 'aspice_does_not_exist')
check('missing directory reported', any('does not exist' in w for w in missing.warnings))

print('\n3. Probing a device actually biases it')
r = PDK.probe(MODELS, 'nch', kind='nmos', length=0.18e-6, width=10e-6, vdd=1.8)
check('probe succeeded', r['ok'], r.get('error', ''))
check('device is in saturation', r.get('region') == 'saturation', r.get('region'))
check('threshold is physical', r.get('vth') and 0.1 < r['vth'] < 0.8,
      f"Vth = {r.get('vth'):.4f} V" if r.get('vth') else 'none')
check('gm and ro positive', r.get('gm', 0) > 0 and r.get('ro', 0) > 0)
check('BSIM3 has no capacitances (reported, not faked)',
      r.get('has_capacitances') is False)
bad = PDK.probe(MODELS, 'no_such_model', kind='nmos')
check('probing a missing model fails cleanly',
      bad['ok'] is False and 'error' in bad, bad.get('error', '')[:60])

# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp)

    print('\n4. I-V family: the curves really saturate')
    iv = PL.iv_family(MODELS, 'nch', w=10e-6, l=0.18e-6, vdd=1.8,
                      out=out / 'iv.png')
    check('png written', (out / 'iv.png').is_file() and (out / 'iv.png').stat().st_size > 5000,
          f"{(out / 'iv.png').stat().st_size} bytes")
    check('one saturation point per Vgs',
          len(iv['saturation_points']) == len(iv['vgs_list']))
    # Physics: at high Vds the curve must be far flatter than at low Vds. Tested
    # on a long device, where channel-length modulation is weak and the
    # saturation region is genuinely flat. A 0.18 um device gives only about a
    # 13x ratio, which is not a defect: short-channel CLM really is that strong,
    # and it is the reason the intrinsic gain check below separates so clearly
    # by length.
    from analog_spice import dc_sweep

    def slope_ratio(length):
        c = Circuit('slope')
        c.include(str(MODELS).replace('\\', '/'))
        c.V('d', 'd', c.gnd, 0 @ u_V)
        c.V('g', 'g', c.gnd, 1.2 @ u_V)
        c.MOSFET(1, 'd', 'g', c.gnd, c.gnd, model='nch', w=10e-6, l=length)
        sweep, an = dc_sweep(c, Vd=slice(0, 1.8, 0.01))
        idd = -np.asarray(an.branches['vd'])
        grad = np.gradient(idd, sweep)
        return grad[5:15].mean() / grad[-30:].mean()

    long_ratio = slope_ratio(1e-6)
    short_ratio = slope_ratio(0.18e-6)
    check('long device is far flatter in saturation than in triode',
          long_ratio > 40, f'L = 1 um ratio {long_ratio:.1f}')
    check('short device saturates too, but less flatly (CLM)',
          8 < short_ratio < long_ratio,
          f'L = 0.18 um ratio {short_ratio:.1f} vs L = 1 um {long_ratio:.1f}')

    print('\n5. Transfer curve finds a sane threshold')
    tf = PL.transfer(MODELS, 'nch', w=10e-6, l=0.18e-6, vdd=1.8,
                     out=out / 'transfer.png')
    check('png written', (out / 'transfer.png').is_file())
    check('Vth matches the probe', abs(tf['vth'] - r['vth']) < 0.15,
          f"plot {tf['vth']:.4f} vs probe {r['vth']:.4f}")

    print('\n6. gm/ID chart is physically right')
    gm = PL.gm_id_chart(MODELS, 'nch', w=10e-6, lengths=[0.18e-6, 1e-6],
                        vdd=1.8, points=40, out=out / 'gmid.png')
    check('png written', (out / 'gmid.png').is_file())
    short = np.array(gm['data']['0.18um']['gm_id'], dtype=float)
    long_ = np.array(gm['data']['1um']['gm_id'], dtype=float)
    check('gm/ID peaks in the weak-inversion range 20-40 1/V',
          20 < np.nanmax(short) < 40, f'max {np.nanmax(short):.1f} 1/V')
    check('gm/ID falls in strong inversion', np.nanmin(short) < 5,
          f'min {np.nanmin(short):.2f} 1/V')
    # gm/ID is exactly independent of W (checked below) but only approximately
    # independent of L: short-channel effects shift the curve, most visibly in
    # the weak-inversion tail. The measured spread here is about 5 1/V at the
    # extremes, so the honest claim is "same shape and range", not "identical".
    overlap = min(len(short), len(long_))
    diff = np.abs(short[:overlap] - long_[:overlap])
    check('gm/ID curves for different L share the same range',
          abs(np.nanmax(short) - np.nanmax(long_)) < 6.0,
          f'peak gm/ID: L=0.18u {np.nanmax(short):.1f}, L=1u {np.nanmax(long_):.1f}')
    check('gm/ID agrees closely over most of the sweep',
          float(np.nanmedian(diff)) < 1.5,
          f'median difference {np.nanmedian(diff):.2f} 1/V, '
          f'max {np.nanmax(diff):.2f} 1/V at the extremes')
    gs = np.array(gm['data']['0.18um']['gain'], dtype=float)
    gl = np.array(gm['data']['1um']['gain'], dtype=float)
    check('intrinsic gain rises with channel length',
          np.nanmax(gl) > 2 * np.nanmax(gs),
          f'gm*ro max: L=0.18u {np.nanmax(gs):.1f}, L=1u {np.nanmax(gl):.1f}')

    print('\n7. gm/ID is independent of W (the property that makes it useful)')
    a = PL.gm_id_chart(MODELS, 'nch', w=5e-6, lengths=[0.5e-6], points=25,
                       out=out / 'w5.png')
    b = PL.gm_id_chart(MODELS, 'nch', w=40e-6, lengths=[0.5e-6], points=25,
                       out=out / 'w40.png')
    ga = np.array(a['data']['0.5um']['gm_id'], dtype=float)
    gb = np.array(b['data']['0.5um']['gm_id'], dtype=float)
    check('8x width change barely moves gm/ID',
          np.nanmax(np.abs(ga - gb)) < 1.0,
          f'max difference {np.nanmax(np.abs(ga - gb)):.3f} 1/V')

    print('\n8. Bode annotations agree with the analytic answer')
    R, C = 1e3, 1e-9
    fc = 1 / (2 * math.pi * R * C)
    rc = Circuit('rc')
    rc.SinusoidalVoltageSource('in', 'in', rc.gnd, ac_magnitude=1 @ u_V)
    rc.R(1, 'in', 'out', R @ u_Ohm)
    rc.C(1, 'out', rc.gnd, C @ u_F)
    freq, data = ac(rc, start_frequency=1 @ u_Hz, stop_frequency=1e9 @ u_Hz,
                    points_per_decade=100)
    bd = PL.bode(freq, data['out'], out=out / 'bode.png')
    check('png written', (out / 'bode.png').is_file())
    check('marked f_3dB matches 1/(2*pi*R*C)',
          abs(bd['f_3db'] - fc) / fc < 0.01,
          f"plot {bd['f_3db']:.1f} Hz vs analytic {fc:.1f} Hz")

    print('\n9. sweep_metric finds the value that hits a target')
    def build(w):
        c = Circuit('sw')
        c.include(str(MODELS).replace('\\', '/'))
        c.V('dd', 'vdd', c.gnd, 1.8 @ u_V)
        c.V('g', 'g', c.gnd, 0.9 @ u_V)
        c.R(1, 'vdd', 'out', 10e3 @ u_Ohm)
        c.MOSFET(1, 'out', 'g', c.gnd, c.gnd, model='nch', w=float(w), l=0.5e-6)
        return c

    sw = PL.sweep_metric(build, np.linspace(1e-6, 40e-6, 12),
                         lambda op: op.v('out'), xlabel='W [m]',
                         ylabel='V(out) [V]', target=0.9, out=out / 'sweep.png')
    check('png written', (out / 'sweep.png').is_file())
    check('target crossing found', sw['target_crossing'] is not None,
          f"W = {sw['target_crossing']:.3e} m" if sw['target_crossing'] else 'none')
    if sw['target_crossing']:
        from analog_spice import operating_point
        v = operating_point(build(sw['target_crossing'])).v('out')
        check('the reported W really produces the target', abs(v - 0.9) < 0.01,
              f'V(out) = {v:.5f} V at the reported W')
        # Without refinement the same sweep is read off the polyline, which on a
        # curve this steep is visibly wrong. Showing both is the justification
        # for refining rather than a decorative extra check.
        raw = PL.sweep_metric(build, np.linspace(1e-6, 40e-6, 12),
                              lambda op: op.v('out'), target=0.9,
                              refine=False, out=out / 'sweep_raw.png')
        v_raw = operating_point(build(raw['target_crossing'])).v('out')
        check('refinement beats plain interpolation',
              abs(v - 0.9) < abs(v_raw - 0.9),
              f'refined {v:.5f} V vs interpolated {v_raw:.5f} V (target 0.9)')

print('\n' + '=' * 64)
if FAILURES:
    print(f'{len(FAILURES)} check(s) failed:')
    for f in FAILURES:
        print(f'  - {f}')
    sys.exit(1)
print('ALL CHECKS PASSED')
