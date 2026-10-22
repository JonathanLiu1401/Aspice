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

    def test_scs_is_an_outline(self):
        text = NB.scs_brief(SCS)
        self.assertIn('inv', text)
        self.assertNotIn('MP (y a vdd', text)


if __name__ == '__main__':
    unittest.main()
