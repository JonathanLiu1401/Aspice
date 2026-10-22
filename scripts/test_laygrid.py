"""Unit tests for lib/laygrid.py (no Virtuoso needed): python scripts/test_laygrid.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

import laygrid as LG  # noqa: E402
from laygrid import Rect  # noqa: E402


def fake_tech():
    t = LG.Tech('pdk', grid=0.005)
    t.min_width = {'M1': 0.06, 'M2': 0.08, 'PO': 0.05}
    t.min_spacing = {'M1': 0.06, 'M2': 0.07, 'OD': 0.1, 'NW': 0.6}
    t.spacing_table = {'M1': [(0.1005, 0.3205, 0.1)]}
    t.min_spacing2 = {('PO', 'OD'): 0.05, ('OD', 'PO'): 0.05}
    t.min_area = {'M1': 0.02}
    t.function = {'M1': 'metal', 'M2': 'metal', 'OD': 'diff', 'PO': 'poly', 'NW': 'nwell'}
    t.vias = {('PO', 'M1'): 'V0', ('M1', 'M2'): 'V1'}
    t.via_size = {'V0': Rect(-0.05, -0.06, 0.05, 0.06), 'V1': Rect(-0.04, -0.065, 0.04, 0.065)}
    return t


def fake_mos():
    m = LG.Template('pdk', 'nmos', params={'w': '1u'})
    m.bbox = Rect(-0.2, -0.1, 0.6, 1.1)
    m.layers = {'OD': Rect(-0.14, 0.0, 0.54, 1.0), 'M1': Rect(-0.11, 0.0, 0.51, 1.0),
                'PO': Rect(0.0, -0.1, 0.4, 1.1), 'NW': Rect(-0.19, -0.09, 0.59, 1.09)}
    m.shape_width = {'OD': 0.68, 'M1': 0.06, 'PO': 0.1, 'NW': 0.78}
    m.pins = {'D': [('M1', Rect(0.17, 0.0, 0.23, 1.0))], 'G': [('PO', Rect(0.0, 1.05, 0.1, 1.1))]}
    return m


class T(unittest.TestCase):
    def setUp(self):
        self.t = fake_tech()
        self.m = fake_mos()

    def test_rule_value_parses_number_and_table(self):
        self.assertEqual(LG._rule_value('0.06'), (0.06, None))
        v, tab = LG._rule_value('((("width" "length") 0.06) ((0.0005 0.0005) 0.06 (0.1005 0.3205) 0.1))')
        self.assertEqual(v, 0.06)
        self.assertEqual(tab, [(0.0005, 0.0005, 0.06), (0.1005, 0.3205, 0.1)])

    def test_spacing_table_and_pair(self):
        self.assertEqual(self.t.spacing('M1'), 0.06)
        self.assertEqual(self.t.spacing('M1', width=0.2, length=0.5), 0.1)
        self.assertEqual(self.t.spacing('PO', other='OD'), 0.05)

    def test_gap_from_facing_edges_and_well_fill(self):
        L = LG.Layout(self.t, 'lib', 'c')
        g, fills = L.gap_fill(self.m, self.m)
        # OD: clearances 0.06 + 0.06 = 0.12 >= 0.1; M1: 0.09 + 0.09 = 0.18 >= 0.06 -> abut
        self.assertEqual(g, 0.0)
        self.assertEqual(fills, ['NW'])   # wells 0.02 apart: bridged, not spaced

    def test_gap_grows_when_rule_needs_it(self):
        self.t.min_spacing['OD'] = 0.2
        L = LG.Layout(self.t, 'lib', 'c')
        self.assertAlmostEqual(L.gap(self.m, self.m), 0.08)

    def test_relative_placement_mirror_and_fill(self):
        L = LG.Layout(self.t, 'lib', 'c')
        a = L.place('A', self.m, (0, 0))
        b = L.right_of('B', self.m, a, orient='MY')
        self.assertAlmostEqual(b.bbox.x0, a.bbox.x1)
        self.assertEqual(b.orient, 'MY')
        self.assertTrue(any(ly == 'NW' for ly, _, _ in L.rects))
        c = L.mirror_x('C', a, axis_x=5.0)
        self.assertAlmostEqual(c.bbox.x1, 10.0 - a.bbox.x0)
        with self.assertRaises(ValueError):
            L.place('X', self.m, (0.1, 0.1))

    def test_orient_pins(self):
        L = LG.Layout(self.t, 'lib', 'c')
        a = L.place('A', self.m, (1.0, 0.0), orient='MY')
        (ly, r), = a.pin('D')
        self.assertEqual(ly, 'M1')
        self.assertAlmostEqual(r.cx - a.bbox.x0, self.m.bbox.x1 - 0.2)

    def test_patterns(self):
        self.assertEqual(LG.common_centroid('A', 'B', 2), list('ABBA'))
        self.assertEqual(LG.common_centroid('A', 'B', 4), list('ABBAABBA'))
        with self.assertRaises(ValueError):
            LG.common_centroid('A', 'B', 3)
        self.assertEqual(LG.interdigitate('A', 'B', 2), list('ABAB'))
        self.assertEqual(LG.with_dummies(['A'], 2), ['D', 'D', 'A', 'D', 'D'])
        r0, r1 = LG.cross_coupled('A', 'B', 1)
        self.assertEqual((r0, r1), (['A', 'B'], ['B', 'A']))

    def test_row_centroids_equal(self):
        L = LG.Layout(self.t, 'lib', 'c')
        L.row('M', LG.with_dummies(LG.common_centroid('A', 'B', 2)), {'A': self.m, 'B': self.m, 'D': self.m})
        c = L.centroids({'A': r'MA\d', 'B': r'MB\d'})
        self.assertAlmostEqual(c['A'][0], c['B'][0])

    def test_tracks_pitch_includes_via_pad(self):
        self.assertAlmostEqual(self.t.pitch('M2'), 0.2)     # 0.13 pad + 0.07
        tr = LG.Layout(self.t, 'lib', 'c').tracks('M2', 'h', 2.0, 3)
        self.assertEqual([x.coord for x in tr], [2.0, 2.2, 2.4])

    def test_connect_adds_stubs_vias_and_min_area_patch(self):
        L = LG.Layout(self.t, 'lib', 'c')
        a = L.place('A', self.m, (0, 0))
        b = L.right_of('B', self.m, a)
        tr = L.tracks('M2', 'h', 1.5, 1)[0]
        L.connect(a.pin('G') + b.pin('G'), tr)
        self.assertEqual([v[0] for v in L.vias], ['V0', 'V1', 'V0', 'V1'])
        self.assertTrue(any(ly == 'M1' for ly, _, _ in L.rects))   # landing patch for minArea
        self.assertTrue(any(ly == 'M2' for ly, _, _ in L.paths))

    def test_skill_emission(self):
        L = LG.Layout(self.t, 'lib', 'c')
        self.m.pparams = [('w', 'float', '1e-06')]
        a = L.place('A', self.m, (0, 0))
        L.wire('M1', [(0, 0), (1, 0)])
        L.via('V1', (0.5, 0), cutColumns=2)
        L.pin('vout', 'M2', Rect(0, 0, 0.1, 0.1))
        code = L.skill()
        self.assertIn('error("lib/c/layout exists: pass overwrite=True")', code)
        self.assertIn('dbCreateParamInstByMasterName(aspCv "pdk" "nmos" "layout" "A"', code)
        self.assertIn('list("w" "float" 1e-06)', code)
        self.assertIn('dbCreatePath(aspCv list("M1" "drawing") list(-0.03:0 1.03:0) 0.06)', code)
        self.assertIn('list(list("cutColumns" 2))', code)
        self.assertIn('dbCreateTerm(n "vout" "inputOutput")', code)
        self.assertNotIn('exists: pass', L.skill(overwrite=True))

    def test_report_flags_aspect_and_budget(self):
        L = LG.Layout(self.t, 'lib', 'c')
        L.row('M', ['A'] * 6, {'A': self.m})
        rep = L.report(area_budget=1.0)
        self.assertIn('OVER BUDGET', rep)
        self.assertIn('fold the long rows', rep)

    def test_records_parse(self):
        self.assertEqual(LG._parse('"ASPICE|A|1\\nASPICE|B|2"'), [['A', '1'], ['B', '2']])


if __name__ == '__main__':
    unittest.main()
