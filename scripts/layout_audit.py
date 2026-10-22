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
    flat = OP.flatten(OP.SN.parse_netlist(Path(path).read_text(errors='replace')))
    devs = {}
    for r in rep['points'][0]['devices']:
        master, nets, params = flat.get(r['device'], (r['master'], [r['nets'][k] for k in 'dgsb'], {}))
        d = OP.Device(r['device'], master, nets[0], nets[1], nets[2], nets[3], params,
                      {'vth': r['vth']})
        d.role = r['role']
        devs[d.path] = d
    pairs, mirrors = OP.find_groups(devs)

    def leaf(path, all_paths):
        name = path.split('.')[-1]
        # Two different devices named MP must not collapse into a pair of a device with itself.
        if sum(p.split('.')[-1] == name for p in all_paths) > 1:
            return None
        return name

    paths = [d.path for d in devs.values()]
    for a, b in pairs:
        la, lb = leaf(a.path, paths), leaf(b.path, paths)
        if la and lb and la != lb:
            rep_pairs.append((la, lb))
    for ref, outs in mirrors:
        for m in outs:
            la, lb = leaf(ref.path, paths), leaf(m.path, paths)
            if la and lb and la != lb:
                rep_pairs.append((la, lb))
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
    print(LA.brief(cellbox, insts))
    print(LA.format_findings(f))
    return 1 if any(x[0] == 'FAIL' for x in f) else 0


if __name__ == '__main__':
    sys.exit(main())
