"""Unit tests for lib/opcheck.py (no simulator needed): python scripts/test_opcheck.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

import opcheck as OP  # noqa: E402
import spectre_netlist as SN  # noqa: E402

DECK = '''simulator lang=spectre
global 0
include "models.scs" section=tt
subckt ota (inp inn out vdd vss vb)
  M1 (x inp tail vss) nch w=2u l=180n nf=2
  M2 (out inn tail vss) nch w=2u l=180n nf=2
  M3 (x x vdd vdd) pch w=4u l=180n nf=2
  M4 (out x vdd vdd) pch w=4u l=180n nf=2
  M5 (tail vb vss vss) nch w=4u l=360n nf=4
  MS (out clk vss vss) nch w=1u l=45n
ends ota
VDD (vdd 0) vsource dc=1.1
X1 (inp inn out vdd 0 vb) ota
dcOp dc
tr tran stop=1n
'''


def rec(region, vgs, vds, vth, vdsat, ids, gm, gds):
    return {'region': float(region), 'vgs': vgs, 'vds': vds, 'vth': vth, 'vdsat': vdsat,
            'ids': ids, 'gm': gm, 'gds': gds}


def good_structs():
    return {
        'X1.M1': rec(2, 0.55, 0.45, 0.45, 0.09, 10e-6, 150e-6, 4e-6),
        'X1.M2': rec(2, 0.55, 0.45, 0.45, 0.09, 10e-6, 150e-6, 4e-6),
        'X1.M3': rec(2, -0.50, -0.50, -0.42, -0.08, -10e-6, 160e-6, 4e-6),
        'X1.M4': rec(2, -0.50, -0.50, -0.42, -0.08, -10e-6, 160e-6, 4e-6),
        'X1.M5': rec(2, 0.55, 0.25, 0.43, 0.11, 20e-6, 260e-6, 5e-6),
        'X1.M5.xrg.r1': {'r': 10.0},
        'X1.MS': rec(1, 1.1, 0.001, 0.45, 0.3, 1e-9, 1e-6, 1e-3),
    }


class T(unittest.TestCase):
    def setUp(self):
        self.nl = SN.parse_netlist(DECK)
        self.flat = OP.flatten(self.nl)

    def test_flatten_maps_ports_and_internal_nets(self):
        m, nets, p = self.flat['X1.M1']
        self.assertEqual(nets, ['X1.x', 'inp', 'X1.tail', '0'])
        self.assertEqual(self.flat['X1.M3'][1][2], 'vdd')
        self.assertEqual(p['nf'], '2')

    def test_supply_voltage(self):
        self.assertAlmostEqual(OP.supply_voltage(self.nl), 1.1)

    def test_match_ignores_non_mos_and_prefix_maps(self):
        s = good_structs()
        s['X1.M2.main'] = s.pop('X1.M2')
        devs = OP.match_devices(self.flat, s)
        self.assertIn('X1.M2', devs)
        self.assertNotIn('X1.M5.xrg.r1', devs)
        self.assertEqual(devs['X1.M2'].g, 'inn')

    def test_good_ota_passes_and_switch_recognised(self):
        devs = OP.match_devices(self.flat, good_structs())
        f = OP.check_devices(devs, OP.Rules(), 1.1)
        self.assertFalse([x for x in f if x.level == 'FAIL'])
        self.assertEqual(devs['X1.MS'].role, 'switch')
        self.assertTrue(any(x.rule == 'switch' and x.device == 'X1.MS' for x in f))

    def test_triode_fails(self):
        s = good_structs()
        s['X1.M5'] = rec(1, 0.8, 0.05, 0.43, 0.35, 20e-6, 100e-6, 200e-6)
        devs = OP.match_devices(self.flat, s)
        f = OP.check_devices(devs, OP.Rules(), 1.1)
        self.assertTrue(any(x.level == 'FAIL' and x.device == 'X1.M5' for x in f))

    def test_headroom_and_gain_warn(self):
        s = good_structs()
        s['X1.M5'] = rec(2, 0.55, 0.15, 0.43, 0.11, 20e-6, 260e-6, 50e-6)
        f = OP.check_devices(OP.match_devices(self.flat, s), OP.Rules(), 1.1)
        rules = {x.rule for x in f if x.device == 'X1.M5' and x.level == 'WARN'}
        self.assertEqual(rules, {'headroom', 'gm*ro'})

    def test_groups_found(self):
        devs = OP.match_devices(self.flat, good_structs())
        OP.check_devices(devs, OP.Rules(), 1.1)
        pairs, mirrors = OP.find_groups(devs)
        self.assertEqual({(a.path, b.path) for a, b in pairs} | {(b.path, a.path) for a, b in pairs},
                         {('X1.M1', 'X1.M2'), ('X1.M2', 'X1.M1')})
        self.assertEqual([(r.path, [m.path for m in o]) for r, o in mirrors], [('X1.M3', ['X1.M4'])])

    def test_mirror_error_warns(self):
        s = good_structs()
        s['X1.M4'] = rec(2, -0.50, -0.20, -0.42, -0.08, -9e-6, 150e-6, 4e-6)
        devs = OP.match_devices(self.flat, s)
        OP.check_devices(devs, OP.Rules(), 1.1)
        f = OP.check_groups(devs, OP.Rules())
        w = [x for x in f if x.rule == 'mirror' and x.level == 'WARN']
        self.assertEqual(len(w), 1)
        self.assertIn('-300 mV', w[0].msg)

    def test_pair_imbalance_warns(self):
        s = good_structs()
        s['X1.M2'] = rec(2, 0.57, 0.45, 0.45, 0.09, 12e-6, 160e-6, 4e-6)
        devs = OP.match_devices(self.flat, s)
        OP.check_devices(devs, OP.Rules(), 1.1)
        self.assertTrue(any(x.rule == 'pair' and x.level == 'WARN' for x in OP.check_groups(devs, OP.Rules())))

    def test_prepare_deck_comments_analyses_and_adds_ours(self):
        deck, _ = OP.prepare_deck(DECK, Path('/x/y'), section='ss', temp=125, at=[1e-9])
        self.assertIn('// dcOp dc', deck)
        self.assertIn('// tr tran stop=1n', deck)
        self.assertIn('include "/x/y/models.scs" section=ss', deck)
        self.assertIn('aspice_opck_temp options temp=125', deck)
        self.assertIn('infotimes=[1e-09] infoname=%s' % OP.TINFO, deck)

    def test_corner_name_uses_the_deck_section(self):
        text = 'include "gpdk045.scs" section=fs\n'
        self.assertEqual(OP.corner_name(None, None, text), 'fs')
        self.assertEqual(OP.corner_name(None, 27, text), 'fs/27C')
        self.assertEqual(OP.corner_name('tt', None, text), 'tt')

    def test_unequal_pair_and_paren_sizes(self):
        nl = SN.parse_netlist(DECK.replace('M2 (out inn tail vss) nch w=2u', 'M2 (out inn tail vss) nch w=(6u)')
                              .replace('M1 (x inp tail vss) nch w=2u', 'M1 (x inp tail vss) nch w=(2u)'))
        devs = OP.match_devices(OP.flatten(nl), good_structs())
        self.assertAlmostEqual(devs['X1.M1'].num('w'), 2e-6)
        OP.check_devices(devs, OP.Rules(), 1.1)
        f = OP.check_groups(devs, OP.Rules())
        self.assertTrue(any(x.rule == 'pair' and 'unequal sizes' in x.msg for x in f))

    def test_mirror_ratio_convention_from_betaeff(self):
        nl = SN.parse_netlist(DECK.replace('M4 (out x vdd vdd) pch w=4u l=180n nf=2',
                                           'M4 (out x vdd vdd) pch w=4u l=180n nf=4'))
        s = good_structs()
        # w per finger: M4 is 2x M3 -> betaeff ratio ~2 selects the per-finger convention
        s['X1.M3']['betaeff'], s['X1.M4']['betaeff'] = 1e-3, 2.1e-3
        s['X1.M4']['ids'] = -20e-6
        devs = OP.match_devices(OP.flatten(nl), s)
        want, per_finger = OP.size_ratio(devs['X1.M4'], devs['X1.M3'])
        self.assertTrue(per_finger)
        self.assertAlmostEqual(want, 2.0)

    def test_ignore_regex(self):
        devs = OP.match_devices(self.flat, good_structs())
        f = OP.check_devices(devs, OP.Rules(ignore=r'M5$'), 1.1)
        self.assertFalse(any(x.device == 'X1.M5' for x in f))


if __name__ == '__main__':
    unittest.main()
