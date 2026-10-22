#!/usr/bin/env python3
"""Audit a saved layout: area budget, aspect, rows, dummies, pair symmetry, common centroid.

    python layout_audit.py LIB CELL --area-budget 400 --pairs M1:M2,M3:M4
    python layout_audit.py LIB CELL --pairs-from tb.scs        # pairs/mirrors found by opcheck
    python layout_audit.py LIB CELL --groups A='^MA',B='^MB'   # split devices: common centroid

Runs batch Virtuoso on --cds-lib (default ./cds.lib). Exit 1 on FAIL. See
references/layout-guidelines.md.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

import laygrid as LG  # noqa: E402
import layaudit as LA  # noqa: E402


def pairs_from_netlist(path):
    import opcheck as OP
    rep_pairs = []
    rep = OP.audit(path)
    # rebuild groups from the device rows of the first point
    if not rep['points']:
        return rep_pairs
    import spectre_netlist as SN  # noqa: F401
    devs = {}
    for r in rep['points'][0]['devices']:
        d = OP.Device(r['device'], r['master'], r['nets']['d'], r['nets']['g'], r['nets']['s'],
                      r['nets']['b'], {}, {'vth': r['vth']})
        d.role = r['role']
        devs[d.path] = d
    pairs, mirrors = OP.find_groups(devs)
    leaf = lambda p: p.split('.')[-1]  # noqa: E731
    rep_pairs += [(leaf(a.path), leaf(b.path)) for a, b in pairs]
    for ref, outs in mirrors:
        rep_pairs += [(leaf(ref.path), leaf(m.path)) for m in outs]
    return rep_pairs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('lib')
    ap.add_argument('cell')
    ap.add_argument('--view', default='layout')
    ap.add_argument('--area-budget', type=float, help='um2')
    ap.add_argument('--pairs', help='A:B,C:D instance names that must match')
    ap.add_argument('--pairs-from', help='Spectre deck: take diff pairs and mirrors from opcheck')
    ap.add_argument('--groups', help='name=regex,... split devices whose centroids must coincide')
    ap.add_argument('--device-re', help='regex on lib/cell of instances that count as devices')
    ap.add_argument('--leaf-height', type=float, help='um: sub-cells this short are standard-cell rows, exempt from the aspect check')
    ap.add_argument('--cds-lib', default='cds.lib')
    a = ap.parse_args(argv)
    pairs = [tuple(p.split(':')) for p in a.pairs.split(',')] if a.pairs else []
    if a.pairs_from:
        pairs += pairs_from_netlist(a.pairs_from)
    groups = dict(g.split('=', 1) for g in a.groups.split(',')) if a.groups else None
    run = LG.batch_runner(cds_lib=str(Path(a.cds_lib).resolve()))
    cellbox, insts = LA.read_layout(run, a.lib, a.cell, a.view)
    f = LA.audit(cellbox, insts, a.area_budget, pairs, groups, a.device_re, lib=a.lib, leaf_height=a.leaf_height)
    print(LA.format_findings(f))
    return 1 if any(x[0] == 'FAIL' for x in f) else 0


if __name__ == '__main__':
    sys.exit(main())
