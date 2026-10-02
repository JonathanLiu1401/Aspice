"""End-to-end check of aspice against YOUR Cadence setup, non-destructively.

    python scripts/cadence_loop.py                       # everything it can find
    python scripts/cadence_loop.py --n-ade 10 --lib lab5
    python scripts/cadence_loop.py --gds top.gds --top top \\
        --drc-rules kit/calibreDRC.rul --lvs-rules kit/calibreLVS.rul --src top.cdl

Steps (each reported OK / FAIL / SKIP):
  1. doctor: tools, licenses, the cds.lib in use, missing libraries
  2. library files: every library directory and its cells/views, from disk
  3. ADE history: index ~/simulation, then re-run the newest N points in
     Spectre from copies and compare the operating point with the stored PSF
     (proves netlist + models + simulator reproduce what ADE produced)
  4. schematics: read and schCheck (on a scratch copy) the cells those runs
     simulated, or every schematic in --lib
  5. physical (with --gds): Calibre DRC and LVS on copies of the GDS

Nothing under your libraries or ~/simulation is written; every run gets a
scratch directory (ASPICE_WORKDIR sets where).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import cadence as CD  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('--cds-lib')
ap.add_argument('--ade-root', default='~/simulation')
ap.add_argument('--n-ade', type=int, default=5)
ap.add_argument('--lib', help='schCheck every schematic in this library')
ap.add_argument('--gds')
ap.add_argument('--top')
ap.add_argument('--src', help='source netlist (SPICE/CDL) for LVS')
ap.add_argument('--drc-rules')
ap.add_argument('--lvs-rules')
args = ap.parse_args()
if args.cds_lib:
    import os
    os.environ['ASPICE_CDS_LIB'] = str(Path(args.cds_lib).expanduser())
    CD.environment(refresh=True)

RESULTS = []


def report(step, ok, detail=''):
    tag = {True: 'OK  ', False: 'FAIL', None: 'SKIP'}[ok]
    RESULTS.append((step, ok))
    print('[%s] %s%s' % (tag, step, ('  ' + detail) if detail else ''))


print('== 1. environment')
print(CD.doctor(live=True))
ce = CD.environment()
report('spectre available', bool(ce.tools['spectre']) or None)
report('virtuoso available', bool(ce.tools['virtuoso']) or None)
report('calibre available', bool(ce.tools['calibre']) or None)

print('\n== 2. library files')
libs = CD.read_cds_lib(ce.cds_lib) if ce.cds_lib else {}
user_libs = {k: v for k, v in libs.items() if v['exists'] and str(Path(v['path'])).startswith(
    str(Path(ce.cds_lib).resolve().parent)) } if ce.cds_lib else {}
for lib, info in sorted(user_libs.items()):
    cells = CD.library_cells(info['path'])
    print('  %-12s %2d cells  %s' % (lib, len(cells), ', '.join(
        '%s[%s]' % (c, '/'.join(v)) for c, v in list(cells.items())[:6])))
missing = sorted(k for k, v in libs.items() if not v['exists'])
report('all cds.lib libraries exist', not missing, ', '.join(missing))

print('\n== 3. ADE history -> Spectre re-run')
runs = [r for r in CD.ade_runs(args.ade_root) if r['status'] == 'ok' and r['psf']]
print('  %d ADE points with results under %s' % (len(runs), args.ade_root))
seen, picked = set(), []
for r in sorted(runs, key=lambda r: -r['mtime']):
    text = Path(r['netlist']).read_text(errors='ignore')
    if text in seen:
        continue
    seen.add(text)
    picked.append(r)
    if len(picked) >= args.n_ade:
        break
cells_seen = []
if not ce.tools['spectre']:
    report('ADE re-run', None, 'no spectre')
for r in picked if ce.tools['spectre'] else []:
    label = '%s/%s %s' % (r['lib'], r['cell'], r['history'] or r['point'])
    try:
        stored = CD.read_psf_dir(r['psf'])
        ref = next((d for d in stored.values() if isinstance(d, CD.PsfData)
                    and d.analysis_type == 'dc' and d.sweep is None), None)
        new = CD.run_spectre(r['netlist'])
        if not new.ok:
            report('re-run ' + label, False, '; '.join(new.errors)[:200])
            continue
        if ref is None:
            report('re-run ' + label, True, 'ran; no stored operating point to compare')
            continue
        op = new.op()
        common = [k for k, v in ref.traces.items() if isinstance(v, float) and k in op]
        worst = max((abs(ref.traces[k] - op[k]), k) for k in common) if common else (0, '-')
        report('re-run ' + label, worst[0] < 1e-6,
               '%d values, max |diff| %.2g at %s' % (len(common), worst[0], worst[1]))
        if (r['lib'], r['cell']) not in cells_seen:
            cells_seen.append((r['lib'], r['cell']))
    except Exception as exc:
        report('re-run ' + label, False, repr(exc)[:200])

print('\n== 4. schematics')
if not ce.tools['virtuoso']:
    report('schematics', None, 'no virtuoso')
else:
    targets = cells_seen
    if args.lib:
        info = libs.get(args.lib)
        targets = [(args.lib, c) for c, v in CD.library_cells(info['path']).items()
                   if 'schematic' in v] if info else []
    for lib, cell in targets:
        try:
            s = CD.schematic(lib, cell)
            chk = CD.check_schematic(lib, cell)
            fl = s.floating_nets()
            report('schematic %s/%s' % (lib, cell), chk['errors'] == 0,
                   '%d inst, %d nets; schCheck %d err %d warn%s' % (
                       len(s.instances), len(s.nets), chk['errors'], chk['warnings'],
                       '; floating: ' + ','.join(fl) if fl else ''))
        except Exception as exc:
            report('schematic %s/%s' % (lib, cell), False, repr(exc)[:200])

print('\n== 5. physical verification')
if not args.gds:
    report('DRC/LVS', None, 'pass --gds/--top with --drc-rules and/or --lvs-rules')
else:
    top = args.top or Path(args.gds).stem
    if args.drc_rules:
        d = CD.calibre_drc(args.gds, top, args.drc_rules)
        report('DRC %s' % top, d['ok'] and d['total'] == 0,
               'total %s %s' % (d.get('total'), d.get('violations') or d.get('error', '')[:200]))
    if args.lvs_rules and args.src:
        lv = CD.calibre_lvs(args.gds, top, args.src, args.lvs_rules)
        report('LVS %s' % top, lv['match'], '%s %s' % (lv['status'], lv.get('counts', '')))

fails = [s for s, ok in RESULTS if ok is False]
print('\n%d OK, %d FAIL, %d SKIP' % (sum(1 for _, ok in RESULTS if ok),
                                     len(fails), sum(1 for _, ok in RESULTS if ok is None)))
sys.exit(1 if fails else 0)
