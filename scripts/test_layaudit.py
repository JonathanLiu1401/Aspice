"""Unit tests for lib/layaudit.py (no Virtuoso): python scripts/test_layaudit.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

import layaudit as LA  # noqa: E402
from laygrid import Rect  # noqa: E402


def inst(name, master, x0, y0, w, h, orient='R0', nets=None):
    return LA.LInst(name, master, orient, Rect(x0, y0, x0 + w, y0 + h), {}, nets or {})


class T(unittest.TestCase):
    def test_subblock_aspect_from_phase2_t3(self):
        # bridge-lite t3's top cell: top 1.53 (warn), cdac 2.76 and samp_sw 3.67 (fail), comp 1.4 ok
        insts = {i.name: i for i in [
            inst('I0', 'sarlib/cdac', 0, 20, 80.7, 29.2), inst('I1', 'sarlib/samp_sw', 0, 50, 19.8, 5.4),
            inst('I2', 'sarlib/comp', 35, 10, 10.5, 7.5), inst('I3', 'sarlib/sar_logic', 30, 0, 24.9, 15.3),
            inst('D0', 'gpdk/nmos', 0, 0, 0.5, 3.0)]}
        f = LA.audit(Rect(0, 0, 80.7, 52.6), insts, lib='sarlib')
        where = {(x[0], x[2]) for x in f if x[1] == 'aspect'}
        self.assertIn(('WARN', '-'), where)
        self.assertIn(('FAIL', 'sarlib/cdac'), where)
        self.assertIn(('FAIL', 'sarlib/samp_sw'), where)
        self.assertNotIn(('FAIL', 'sarlib/comp'), where)
        self.assertFalse(any(x[2] == 'gpdk/nmos' for x in f))     # PDK cells are not blocks

    def test_brief_rolls_up_masters(self):
        insts = {('M%d' % i): inst('M%d' % i, 'pdk/nmos', i * 1.0, 0, 0.5, 1.0) for i in range(20)}
        insts['I0'] = inst('I0', 'sarlib/cdac', 0, 2, 40, 20)
        text = LA.brief(Rect(0, 0, 20, 22), insts)
        self.assertIn('21 instances', text)
        self.assertIn('20  pdk/nmos', text)
        self.assertNotIn('M0', text)

    def test_worst_instance_of_a_master_is_checked(self):
        insts = {
            'I0': inst('I0', 'sarlib/cdac', 0, 0, 10, 10),
            'I1': inst('I1', 'sarlib/cdac', 0, 12, 40, 10),
        }
        f = LA.audit(Rect(0, 0, 40, 30), insts, lib='sarlib')
        self.assertTrue(any(x[0] == 'FAIL' and x[1] == 'aspect' and x[2] == 'sarlib/cdac' for x in f))

    def test_blank_pin_is_not_a_dummy(self):
        mend = inst('Mend', 'pdk/nmos', 6, 0, 1, 1, nets={'D': 'sig', 'G': '', 'S': '', 'B': ''})
        self.assertFalse(mend.is_dummy())

    def test_missing_centroid_group_warns(self):
        insts = {'MA1': inst('MA1', 'pdk/nmos', 0, 0, 1, 1), 'MA2': inst('MA2', 'pdk/nmos', 2, 0, 1, 1)}
        f = LA.audit(Rect(0, 0, 4, 2), insts, groups={'A': '^MA', 'B': '^NO_SUCH'})
        self.assertTrue(any(x[1] == 'centroid' and x[0] == 'WARN' and 'NO_SUCH' in x[3] for x in f))

    def test_reversed_bbox_is_not_infinite(self):
        text = LA.brief(Rect(5, 5, 0, 0), {})
        self.assertIn('aspect 1.00', text)
        self.assertNotIn('inf', text)
        self.assertIn('aspect n/a', LA.brief(Rect(0, 0, 0, 0), {}))

    def test_leaf_height_exempts_standard_cell_rows(self):
        insts = {'X': inst('X', 'lib/dffnr', 0, 0, 5.9, 2.3)}
        f = LA.audit(Rect(0, 0, 6, 6), insts, lib='lib', leaf_height=2.5)
        self.assertFalse(any(x[1] == 'aspect' and x[2] == 'lib/dffnr' for x in f))

    def test_dummies_and_centroid(self):
        insts = {i.name: i for i in [
            inst('Mdummy1', 'p/n', 0, 0, 1, 1), inst('MA1', 'p/n', 1, 0, 1, 1), inst('MB1', 'p/n', 2, 0, 1, 1),
            inst('MB2', 'p/n', 3, 0, 1, 1), inst('MA2', 'p/n', 4, 0, 1, 1), inst('MX', 'p/n', 5, 0, 1, 1)]}
        f = LA.audit(Rect(0, 0, 6, 3), insts, groups={'A': '^MA', 'B': '^MB'})
        self.assertTrue(any(x[1] == 'dummies' and 'right end' in x[3] for x in f))
        self.assertFalse(any(x[1] == 'dummies' and 'left end' in x[3] for x in f))
        self.assertTrue(any(x[1] == 'centroid' and x[0] == 'INFO' for x in f))


if __name__ == '__main__':
    unittest.main()
