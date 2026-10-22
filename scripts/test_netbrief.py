"""Unit tests for lib/netbrief.py. No Virtuoso, no ollama."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

import netbrief as NB  # noqa: E402


PEX = """* extracted cdac
subckt cdac top bot vss
C1 top vss 2e-15
C2 top bot 1e-15
C3 top#1 vss 1e-15
R1 top top 12
R2 bot bot#1 4
M1 d g s b nmos w=1u l=100n
ends cdac
"""

SCS = """subckt inv a y vdd vss
MP (y a vdd vdd) pch w=2u l=180n
MN (y a vss vss) nch w=1u l=180n
ends inv
"""


class T(unittest.TestCase):
    def test_pex_rolls_up_subnodes_and_hides_the_netlist(self):
        text = NB.pex_brief(PEX, top=5)
        self.assertIn('worst nets', text)
        self.assertNotIn('M1 d g', text)
        # C1 and C3 both land on top (2 fF + 1 fF) plus coupling C2 (1 fF) counted on both ends
        self.assertIn('top', text)
        self.assertRegex(text, r'top\s+4\.00 fF')
        self.assertIn('nmos x1', text)
        self.assertIn('max', text)
        self.assertNotIn('43208', text)

    def test_mesh_r_is_max_segment_not_the_sum(self):
        text = NB.pex_brief(
            'R1 a a#1 10\nR2 a#1 a#2 100\nR3 a a#3 5\nC1 a 0 1e-15\n', top=5)
        self.assertRegex(text, r'a\s+1\.00 fF\s+3 segs\s+max\s+100\.0 ohm')
        self.assertNotIn('115.0', text)

    def test_scs_is_an_outline(self):
        text = NB.scs_brief(SCS)
        self.assertIn('inv', text)
        self.assertNotIn('MP (y a vdd', text)

    def test_quantus_continuation_model_and_both_cap_terminals(self):
        # Real Quantus puts the model on a '+' line and names MIM caps as X instances.
        deck = """.SUBCKT cdac top bot vss
MX1 d g s b
+ g45n1svt L=4.5e-08 W=1.2e-07
XCX1 top bot VSS g45cmim l=5e-06 w=5e-06
C1 top vss 2e-15
C2 top bot 1e-15
C3 top#1 vss 1e-15
.ENDS
"""
        text = NB.pex_brief(deck, top=5)
        self.assertIn('g45n1svt x1', text)
        self.assertNotIn('VSS x', text)
        self.assertIn('g45cmim x1', text)
        self.assertIn('subckts: cdac', text)
        self.assertRegex(text, r'vss\s+3\.00 fF')
        self.assertRegex(text, r'bot\s+1\.00 fF')
        self.assertIn('+2 more nets', NB.pex_brief(deck, top=1))

    def test_quantus_model_device_is_not_a_parasitic(self):
        deck = "C1 top bot g45cmim\n+ L=5e-06 W=5e-06\nR1 a b ressnpoly\n+ L=2u W=1u\nC2 top 0 1e-15\n"
        text = NB.pex_brief(deck)
        self.assertIn('g45cmim x1', text)
        self.assertIn('ressnpoly x1', text)
        self.assertIn('counts: 0 R, 1 C, 0 MOS, 2 instances', text)

    def test_shorted_cap_is_not_load_and_bulk_is_not_the_model(self):
        deck = "Cint a#1 a#2 10e-15\nCext a#3 vss 1e-15\nC1 top bot VSS\n+ g45cmim L=3e-6 W=4e-6\n"
        text = NB.pex_brief(deck)
        self.assertRegex(text, r'\ba\s+1\.00 fF')
        self.assertNotIn('11.00', text)
        self.assertIn('g45cmim x1', text)
        self.assertNotIn('VSS x', text)

    def test_ade_include_does_not_drop_the_hierarchy(self):
        deck = """simulator lang=spectre
include "models.scs" section=tt
subckt inv a y vdd vss
MP (y a vdd vdd) pch w=2u l=180n
MN (y a vss vss) nch w=1u l=180n
ends inv
"""
        text = NB.scs_brief(deck)
        self.assertIn('inv', text)
        self.assertIn('nch', text)
        self.assertIn('pch', text)


if __name__ == '__main__':
    unittest.main()
