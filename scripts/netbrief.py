#!/usr/bin/env python3
"""Compact view of a netlist. Print this; do not open the file.

    python scripts/netbrief.py scs design.scs
    python scripts/netbrief.py pex extracted.sp
    python scripts/netbrief.py pex extracted.sp --top 8
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

import netbrief as NB  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('kind', choices=('scs', 'pex'))
    ap.add_argument('path')
    ap.add_argument('--top', type=int, default=12, help='worst nets to show for a pex netlist')
    a = ap.parse_args(argv)
    text = Path(a.path).read_text(errors='replace')
    if a.kind == 'pex':
        print(NB.pex_brief(text, top=a.top))
    else:
        print(NB.scs_brief(text))
    return 0


if __name__ == '__main__':
    sys.exit(main())
