"""Layout audit: area, floorplan shape, dummies, symmetry and common centroid of a saved
layout. Reads the cellview (instances, orientations, bounding boxes, parameters and the
nets on their terminals) through a laygrid runner (batch Virtuoso or the bridge).

Checks (each a FAIL / WARN / INFO finding):
  area        bounding-box area against a budget you give (--area-budget um2)
  aspect      target 1:1 for the cell AND every sub-block of this library in it: WARN above
              1.5, FAIL above 2 (standard-cell-height leaf cells, --leaf-height, are exempt)
  utilisation device area / cell area (low = devices far apart = long wires)
  rows        a row longer than 4x the cell height is "one long row": fold it
  dummies     every row that holds matched devices starts and ends with a dummy
              (all terminals on one net, gate tied off, or named dummy/dum/dmy)
  pairs       matched pairs (from --pairs or from opcheck's diff pairs and mirrors):
              same master and parameters, mirror-symmetric about one shared axis, same row
  centroid    split devices (--groups A=regex,B=regex): centroids coincide
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from laygrid import Rect, _q, _rec

DUMMY_RE = re.compile(r'(?i)(dummy|dum|dmy)')


@dataclass
class LInst:
    name: str
    master: str
    orient: str
    bbox: Rect
    params: dict = field(default_factory=dict)
    nets: dict = field(default_factory=dict)   # term -> net

    @property
    def key(self):
        return self.name.lstrip('|')

    def is_dummy(self):
        if DUMMY_RE.search(self.name):
            return True
        # A blank terminal is unrouted, not tied off. Every pin must be on the same net.
        vals = list(self.nets.values())
        return len(vals) >= 3 and all(vals) and len(set(vals)) == 1


def read_layout(run, lib, cell, view='layout'):
    code = '\n'.join([
        'aspCv = dbOpenCellViewByType(%s %s %s nil "r")' % (_q(lib), _q(cell), _q(view)),
        'unless(aspCv error("cannot open %s/%s/%s"))' % (lib, cell, view),
        'procedure(aspBox(bb) sprintf(nil "%g,%g,%g,%g" caar(bb) cadar(bb) caadr(bb) cadadr(bb)))',
        _rec('CELL|%s', 'aspBox(aspCv~>bBox)'),
        'foreach(i aspCv~>instances',
        ' %s' % _rec('INST|%s|%s/%s|%s|%s', 'i~>name', 'i~>libName', 'i~>cellName', 'i~>orient',
                     'aspBox(i~>bBox)'),
        ' foreach(p i~>prop when(member(p~>valueType list("float" "int" "string")) '
        '  %s))' % _rec('PRM|%s|%s|%L', 'i~>name', 'p~>name', 'p~>value'),
        ' foreach(it i~>instTerms %s))' % _rec('NET|%s|%s|%s', 'i~>name', 'it~>name',
                                               'if(it~>net it~>net~>name "")'),
        'dbClose(aspCv)',
    ])
    cellbox, insts = None, {}
    for r in run(code):
        if r[0] == 'CELL':
            cellbox = Rect(*map(float, r[1].split(',')))
        elif r[0] == 'INST':
            insts[r[1]] = LInst(r[1], r[2], r[3], Rect(*map(float, r[4].split(','))))
        elif r[0] == 'PRM' and r[1] in insts:
            insts[r[1]].params[r[2]] = r[3].strip('"')
        elif r[0] == 'NET' and r[1] in insts:
            insts[r[1]].nets[r[2]] = r[3]
    return cellbox, insts


def rows_of(insts, tol=1e-3):
    """Group instances whose y-ranges overlap into rows, bottom to top."""
    rows = []
    for i in sorted(insts, key=lambda x: (x.bbox.y0, x.bbox.x0)):
        for row in rows:
            # Do not grow the row. A tall device that overlaps this row must not
            # swallow the row above it.
            if i.bbox.y0 < row['y1'] - tol and i.bbox.y1 > row['y0'] + tol:
                row['insts'].append(i)
                break
        else:
            rows.append({'y0': i.bbox.y0, 'y1': i.bbox.y1, 'insts': [i]})
    for row in rows:
        row['insts'].sort(key=lambda x: x.bbox.x0)
    return rows


def audit(cellbox, insts, area_budget=None, pairs=(), groups=None, device_re=None, lib=None, leaf_height=None):
    """Return findings [(level, rule, where, message)]."""
    f = []
    devs = [i for i in insts.values() if not device_re or re.search(device_re, i.master)]
    if cellbox is None or not devs:
        return [('FAIL', 'read', '-', 'no instances found')]
    area = cellbox.w * cellbox.h
    asp = max(cellbox.w, cellbox.h) / max(min(cellbox.w, cellbox.h), 1e-9)
    util = sum(i.bbox.w * i.bbox.h for i in devs) / area if area else 0
    f.append(('INFO', 'area', '-', '%.2f x %.2f um = %.1f um2, aspect %.2f, device utilisation %.0f %%'
              % (cellbox.w, cellbox.h, area, asp, 100 * util)))
    if area_budget:
        lvl = 'FAIL' if area > area_budget else 'INFO'
        f.append((lvl, 'area', '-', 'area %.1f um2 vs budget %.1f um2 (%.0f %%)' % (
            area, area_budget, 100 * area / area_budget)))
    if asp > 2.0:
        f.append(('FAIL', 'aspect', '-', 'aspect ratio %.2f > 2: fold rows or stack blocks; aim for 1:1' % asp))
    elif asp > 1.5:
        f.append(('WARN', 'aspect', '-', 'aspect ratio %.2f: aim for 1:1' % asp))
    # Worst outline of each same-library master, not the first instance only.
    seen = {}
    for i in insts.values():
        if lib and not i.master.startswith(lib + '/'):
            continue
        w, h = abs(i.bbox.w), abs(i.bbox.h)
        if min(w, h) <= 0:
            continue
        a = max(w, h) / min(w, h)
        prev = seen.get(i.master)
        if prev is None or a > prev[0]:
            seen[i.master] = (a, i)
    for m, (_a, i) in sorted(seen.items()):
        w, h = abs(i.bbox.w), abs(i.bbox.h)
        if min(w, h) <= 0 or (leaf_height and min(w, h) <= leaf_height + 1e-6):
            continue
        a = max(w, h) / min(w, h)
        if a > 2.0:
            f.append(('FAIL', 'aspect', m, 'sub-block %.1f x %.1f um, aspect %.2f > 2: re-floorplan it '
                      'toward 1:1' % (w, h, a)))
        elif a > 1.5:
            f.append(('WARN', 'aspect', m, 'sub-block %.1f x %.1f um, aspect %.2f: aim for 1:1' % (w, h, a)))
    if util < 0.3:
        f.append(('WARN', 'utilisation', '-', 'devices cover %.0f %% of the cell: compact the '
                  'placement (empty area is wire length)' % (100 * util)))
    rows = rows_of(devs)
    for k, row in enumerate(rows):
        x0 = min(i.bbox.x0 for i in row['insts'])
        x1 = max(i.bbox.x1 for i in row['insts'])
        if (x1 - x0) > 4 * cellbox.h and len(row['insts']) > 4:
            f.append(('WARN', 'rows', 'row%d' % k, 'row of %d devices is %.1f um long in a %.1f um '
                      'tall cell: one long row, fold it' % (len(row['insts']), x1 - x0, cellbox.h)))
    matched = {n for p in pairs for n in p}
    for g in (groups or {}).values():
        matched |= {i.key for i in insts.values() if re.search(g, i.key)}
    for k, row in enumerate(rows):
        ins = row['insts']
        if not any(i.key in matched for i in ins):
            continue
        left = min(ins, key=lambda i: (i.bbox.x0, i.bbox.x1))
        right = max(ins, key=lambda i: (i.bbox.x1, i.bbox.x0))
        for end, i in (('left', left), ('right', right)):
            if not i.is_dummy():
                f.append(('WARN', 'dummies', 'row%d' % k, '%s end of the row is active device %s: '
                          'add a dummy so matched devices see identical neighbours' % (end, i.name)))
    by_key = {i.key: i for i in insts.values()}
    axes = []
    for a, b in pairs:
        ia, ib = by_key.get(a), by_key.get(b)
        if not ia or not ib:
            f.append(('WARN', 'pairs', '%s/%s' % (a, b), 'not both in the layout (names: %s)' % (
                ', '.join(sorted(by_key)[:12]))))
            continue
        if ia.master != ib.master or ia.params != ib.params:
            diff = {k for k in set(ia.params) | set(ib.params) if ia.params.get(k) != ib.params.get(k)}
            f.append(('WARN', 'pairs', '%s/%s' % (a, b), 'not identical devices (%s)' % (
                ', '.join(sorted(diff)[:6]) or 'master')))
        dy = abs(ia.bbox.cy - ib.bbox.cy)
        axis = 0.5 * (ia.bbox.cx + ib.bbox.cx)
        axes.append(axis)
        sep = abs(ia.bbox.cx - ib.bbox.cx)
        msg = 'axis x=%.3f, separation %.2f um, dy=%.3f, orient %s/%s' % (axis, sep, dy, ia.orient, ib.orient)
        lvl = 'INFO'
        if dy > 1e-3:
            lvl, msg = 'WARN', msg + ': not on the same row'
        if sep > 5 * max(ia.bbox.w, 1e-3):
            lvl, msg = 'WARN', msg + ': far apart (gradients and wire mismatch)'
        f.append((lvl, 'pairs', '%s/%s' % (a, b), msg))
    if len(axes) > 1 and max(axes) - min(axes) > 0.05:
        f.append(('WARN', 'symmetry', '-', 'pairs do not share one symmetry axis (x from %.3f to '
                  '%.3f um)' % (min(axes), max(axes))))
    if groups:
        cents = {}
        for g, rx in groups.items():
            mem = [i for i in insts.values() if re.search(rx, i.key)]
            if not mem:
                f.append(('WARN', 'centroid', g, 'group %s matched no instances (%s)' % (g, rx)))
                continue
            cents[g] = (sum(i.bbox.cx for i in mem) / len(mem), sum(i.bbox.cy for i in mem) / len(mem))
        if len(cents) > 1:
            xs = [c[0] for c in cents.values()]
            ys = [c[1] for c in cents.values()]
            off = max(max(xs) - min(xs), max(ys) - min(ys))
            lvl = 'WARN' if off > 0.05 else 'INFO'
            f.append((lvl, 'centroid', ','.join(cents), 'centroids %s: offset %.3f um%s' % (
                ', '.join('%s=(%.2f,%.2f)' % (k, *v) for k, v in cents.items()), off,
                ' -> rearrange as common centroid (ABBA / cross-coupled)' if lvl == 'WARN' else '')))
    return f


def format_findings(f):
    order = {'FAIL': 0, 'WARN': 1, 'INFO': 2}
    lines = ['%-4s %-11s %-16s %s' % x for x in sorted(f, key=lambda x: order[x[0]])]
    nf = sum(x[0] == 'FAIL' for x in f)
    nw = sum(x[0] == 'WARN' for x in f)
    lines.append('%s: %d FAIL, %d WARN' % ('PASS' if not nf else 'FAIL', nf, nw))
    return '\n'.join(lines)


def brief(cellbox, insts, max_masters=16):
    """One screen of a layout: size and a rollup by master. Not every instance, not every shape."""
    if cellbox is None:
        return 'layout: (empty)'
    w, h = abs(cellbox.w), abs(cellbox.h)
    short = min(w, h)
    aspect = (max(w, h) / short) if short > 1e-9 else None
    lines = ['layout: %.2f x %.2f um   area %.1f um2   aspect %s   %d instances' % (
        w, h, w * h, ('%.2f' % aspect) if aspect is not None else 'n/a', len(insts))]
    by = {}
    for i in insts.values():
        by.setdefault(i.master, []).append(i)
    masters = sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    shown = masters[:max_masters]
    for master, group in shown:
        lines.append('  %4d  %s' % (len(group), master))
    if len(masters) > len(shown):
        lines.append('  +%d more masters' % (len(masters) - len(shown)))
    return '\n'.join(lines)
