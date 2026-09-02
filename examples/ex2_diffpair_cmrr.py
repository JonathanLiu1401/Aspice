"""Razavi Ch.4: differential pair - differential gain, common-mode gain, CMRR.

The same pair is simulated with two different tails: an ideal current source,
and a real NMOS mirror. Comparing them isolates the point of the exercise -
common-mode gain comes almost entirely from the finite output resistance of
the tail, so CMRR measures the tail, not the pair.

Note `ITAIL` is a plain float, not `100e-6 @ u_A`. PySpice renders that as
"0.0001A" and ngspice reads a trailing A as the atto scale factor (1e-18),
silently simulating a dead circuit. `lint_netlist` (run automatically) catches
it, but the habit is worth having.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

import math
from analog_spice import (Circuit, u_V, u_Hz, u_Ohm, tech, operating_point,
                          ac, Reconcile)

VDD, VCM = 1.8, 0.9
RD = 12e3
W, L = 20e-6, 0.5e-6         # input pair
W5, L5 = 40e-6, 1.0e-6       # tail device, long L for a higher ro
ITAIL = 100e-6


def build(ac_p, ac_n, tail='ideal'):
    c = Circuit('diffpair')
    c.include(tech('180nm'))
    c.V('dd', 'vdd', c.gnd, VDD @ u_V)
    c.V('inp', 'inp', c.gnd, f'DC {VCM} AC {ac_p}')
    c.V('inn', 'inn', c.gnd, f'DC {VCM} AC {ac_n}')
    c.R(1, 'vdd', 'outp', RD @ u_Ohm)
    c.R(2, 'vdd', 'outn', RD @ u_Ohm)
    c.MOSFET(1, 'outp', 'inp', 'tail', c.gnd, model='nch', w=W, l=L)
    c.MOSFET(2, 'outn', 'inn', 'tail', c.gnd, model='nch', w=W, l=L)
    if tail == 'ideal':
        c.I('tail', 'tail', c.gnd, ITAIL)
    else:
        # Mirror the tail from a reference, so the current is set by Iref
        # rather than by a guessed gate voltage.
        c.I('ref', 'vdd', 'vbn', ITAIL)
        c.MOSFET(6, 'vbn', 'vbn', c.gnd, c.gnd, model='nch', w=W5, l=L5)
        c.MOSFET(5, 'tail', 'vbn', c.gnd, c.gnd, model='nch', w=W5, l=L5)
    return c


def gains(tail):
    """Return (|Adm|, |Acm|) at low frequency."""
    _, dif = ac(build(0.5, -0.5, tail), start_frequency=1 @ u_Hz,
                stop_frequency=10 @ u_Hz, points_per_decade=2)
    _, com = ac(build(1.0, 1.0, tail), start_frequency=1 @ u_Hz,
                stop_frequency=10 @ u_Hz, points_per_decade=2)
    a_dm = complex(dif['outp'][0] - dif['outn'][0])   # vid = 1 V by construction
    a_cm = complex(com['outp'][0])                    # single-ended CM gain
    return abs(a_dm), abs(a_cm)


for tail in ('ideal', 'mos'):
    print(f'\n{"=" * 78}\nTail: {tail} current source\n{"=" * 78}')
    op = operating_point(build(0, 0, tail))
    ok, problems = op.check_saturation()
    if not ok:
        raise SystemExit(f'bias unusable: {problems}')
    print()
    op.table()

    m1 = op.devices['m1']
    adm, acm = gains(tail)
    adm_hand = m1.gm * (RD * m1.ro / (RD + m1.ro))

    print(f'\n  Id per side = {m1.id * 1e6:.3f} uA   (tail set to '
          f'{ITAIL * 1e6:.0f} uA)')
    print(f'  |Adm|       = {adm:.4f}  ({20 * math.log10(adm):.2f} dB)')

    r = Reconcile(f'Differential pair, {tail} tail', default_tol=0.02)
    r.add('|Adm| = gm*(RD||ro)', adm, adm_hand)

    if tail == 'mos':
        m5 = op.devices['m5']
        rss = 2 * m5.ro                      # each half sees twice the tail ro
        # Textbook approximation (Razavi eq. 4.44): assumes ro -> infinity and
        # no body effect.
        acm_approx = RD / (1 / m1.gm + rss)
        # Exact degenerated common-source gain. Both corrections matter here:
        # the input devices' bulks sit at ground while their sources ride the
        # tail node, so Vbs is non-zero and gmb is live.
        acm_exact = (m1.gm * m1.ro * RD
                     / (m1.ro + RD + (m1.gm + m1.gmb) * m1.ro * rss + rss))
        print(f'  |Acm|       = {acm:.6f}')
        print(f'  CMRR        = {20 * math.log10(adm / acm):.1f} dB')
        r.add('|Acm| textbook approx', acm, acm_approx, tol=0.05)
        r.add('|Acm| exact (ro, gmb)', acm, acm_exact, tol=0.02)
        r.report()
        print(f'\n  The textbook formula RD/(1/gm + 2*ro_tail) gives '
              f'{acm_approx:.6f}, which is')
        print(f'  {100 * (acm_approx / acm - 1):.0f}% high. It drops the input device\'s ro '
              f'and its body effect,')
        print(f'  and here neither is negligible: Vbs = {m1.vbs:.3f} V, so gmb = '
              f'{m1.gmb * 1e6:.0f} uS is live')
        print(f'  (gmb/gm = {m1.gmb / m1.gm:.2f}). This is exactly the kind of gap the')
        print('  reconciliation table exists to expose rather than hide.')
        print(f'\n  ro of the tail device = {m5.ro / 1e3:.1f} kOhm - that finite')
        print('  resistance is the whole source of common-mode gain. Cascoding the')
        print('  tail, or lengthening it, raises ro and CMRR together.')
    else:
        r.report()
        print(f'  |Acm|       = {acm:.3e}: an ideal tail rejects common mode')
        print('                perfectly, so only the real tail below gives a')
        print('                meaningful CMRR.')
