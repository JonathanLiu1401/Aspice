"""laygrid: template-and-grid layout in the style of LAYGO (Han et al., TCAS-I 2021).

You write the layout generator; laygrid does the arithmetic that makes it DRC-clean by
construction and keeps it symmetric:

  * Templates are the PDK's own cells or pcells (MOS, cap, res, tap, via), MEASURED in
    Virtuoso: bounding box, per-layer extents and pin shapes. Nothing about a PDK is
    hard-coded; design rules come from the techfile's constraint groups.
  * Placement is relative (right_of / above / mirror about an axis) on the manufacturing
    grid. The gap between two neighbours is the smallest one that satisfies every layer's
    minimum spacing at the facing edges (wells and implants of the same kind may merge).
  * Arrays come from patterns: interdigitated (ABAB), common-centroid (ABBA, 2-D
    cross-coupled) and dummy columns at the ends of each row.
  * Routing is on tracks: pitch = min width + min spacing of the metal (or what you give),
    vias found by layer pair in the techfile.

Typical use (bridge client or batch):

    import laygrid as LG
    run = LG.bridge_runner(client)           # or LG.batch_runner(cds_lib=..., workdir=...)
    tech = LG.Tech.load(run, 'gpdk045')
    n = LG.Template('gpdk045', 'nmos1v', params={'fw': '1u', 'l': '100n', 'fingers': 2})
    d = LG.Template('gpdk045', 'nmos1v', params={'fw': '1u', 'l': '100n', 'fingers': 1})
    tech.measure(run, [n, d])
    L = LG.Layout(tech, 'mylib', 'pair')
    L.row('m', LG.with_dummies(LG.common_centroid('A', 'B', 2)), {'A': n, 'B': n, 'D': d})
    print(L.report())
    L.write(run, overwrite=True)

The generator is yours: one call per device or row, chosen positions, explicit routes. It
is not a netlist-to-layout tool.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# running SKILL: batch (virtuoso -nograph) or a live session through the bridge
# ---------------------------------------------------------------------------

_WRAP = 'let((aspOut) aspOut = nil\n%s\nbuildString(reverse(aspOut) "\\n"))'


def _rec(fmt, *args):
    """SKILL that appends one 'ASPICE|...' record to aspOut."""
    return 'aspOut = cons(sprintf(nil "ASPICE|%s" %s) aspOut)' % (fmt, ' '.join(args))


def _parse(text):
    out = []
    for ln in text.replace('\\n', '\n').splitlines():
        ln = ln.strip().strip('"')
        if ln.startswith('ASPICE|'):
            out.append(ln[len('ASPICE|'):].split('|'))
    return out


def batch_runner(cds_lib=None, workdir=None, timeout=900):
    """Runner for headless Virtuoso (aspice cadence.run_skill)."""
    import cadence as CD

    def run(code):
        r = CD.run_skill('let((s) s = %s\nforeach(l parseString(s "\\n") printf("%%s\\n" l)))'
                         % (_WRAP % code), cds_lib=cds_lib, workdir=workdir, timeout=timeout)
        if r.errors:
            raise RuntimeError('SKILL failed: %s (log %s)' % ('; '.join(r.errors[:3]), r.log_path))
        return [rec for rec in r.records]
    return run


def bridge_runner(client, timeout=300):
    """Runner for a live Virtuoso through virtuoso-bridge (client.execute_skill)."""
    def run(code):
        r = client.execute_skill(_WRAP % code, timeout=timeout)
        if not r.ok:
            raise RuntimeError('SKILL failed: %s' % ('; '.join(r.errors) or r.output[:500]))
        return _parse(r.output or '')
    return run


def _sexp(text):
    """Parse a SKILL list literal (numbers, strings, symbols, nested lists)."""
    toks = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', text)
    pos = 0

    def item():
        nonlocal pos
        tk = toks[pos]
        pos += 1
        if tk == '(':
            out = []
            while toks[pos] != ')':
                out.append(item())
            pos += 1
            return out
        if tk.startswith('"'):
            return tk[1:-1]
        try:
            return float(tk)
        except ValueError:
            return None if tk == 'nil' else tk
    return item() if toks else None


def _rule_value(text):
    """(minimum value, table) from a constraint value: a number, or a spacing table
    ((("width" "length") default) ((w l) v (w l) v ...))."""
    text = text.strip()
    try:
        return float(text), None
    except ValueError:
        pass
    try:
        v = _sexp(text)
    except (IndexError, ValueError):
        return None, None
    if isinstance(v, list) and len(v) >= 1 and isinstance(v[0], list) and len(v[0]) >= 2 \
            and isinstance(v[0][-1], float):
        default = v[0][-1]
        table = []
        if len(v) > 1 and isinstance(v[1], list):
            ent = v[1]
            for i in range(0, len(ent) - 1, 2):
                idx, val = ent[i], ent[i + 1]
                if isinstance(val, float):
                    if isinstance(idx, list) and len(idx) == 2:
                        table.append((idx[0] or 0.0, idx[1] or 0.0, val))
                    elif isinstance(idx, float):
                        table.append((idx, 0.0, val))
        return default, table
    return None, None


def _q(s):
    return '"%s"' % str(s).replace('\\', '\\\\').replace('"', '\\"')


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self):
        return self.x1 - self.x0

    @property
    def h(self):
        return self.y1 - self.y0

    @property
    def cx(self):
        return 0.5 * (self.x0 + self.x1)

    @property
    def cy(self):
        return 0.5 * (self.y0 + self.y1)

    def shift(self, dx, dy):
        return Rect(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)

    def union(self, o):
        return Rect(min(self.x0, o.x0), min(self.y0, o.y0), max(self.x1, o.x1), max(self.y1, o.y1))

    def overlaps(self, o, eps=1e-9):
        return self.x0 < o.x1 - eps and o.x0 < self.x1 - eps and self.y0 < o.y1 - eps and o.y0 < self.y1 - eps

    def skill(self):
        return 'list(%s:%s %s:%s)' % tuple(_g(v) for v in (self.x0, self.y0, self.x1, self.y1))


def _g(v):
    return ('%.6f' % v).rstrip('0').rstrip('.') if v != int(v) else '%d' % int(v)


# orient -> function mapping a point of the master to the instance frame (origin at 0)
ORIENTS = {
    'R0': lambda x, y: (x, y), 'MX': lambda x, y: (x, -y), 'MY': lambda x, y: (-x, y),
    'R180': lambda x, y: (-x, -y), 'R90': lambda x, y: (-y, x), 'R270': lambda x, y: (y, -x),
    'MXR90': lambda x, y: (y, x), 'MYR90': lambda x, y: (-y, -x),
}


def transform(r, orient, origin=(0.0, 0.0)):
    f = ORIENTS[orient]
    a, b = f(r.x0, r.y0), f(r.x1, r.y1)
    ox, oy = origin
    return Rect(min(a[0], b[0]) + ox, min(a[1], b[1]) + oy, max(a[0], b[0]) + ox, max(a[1], b[1]) + oy)


def snap_up(v, grid):
    return math.ceil(round(v / grid, 6)) * grid


def snap(v, grid):
    return round(v / grid) * grid


# ---------------------------------------------------------------------------
# technology (read from the techfile) and templates (measured)
# ---------------------------------------------------------------------------

MERGEABLE = ('nwell', 'pwell', 'nplus', 'pplus', 'nimplant', 'pimplant', 'implant', 'well',
             'recognition', 'other', 'unknown', '')


@dataclass
class Tech:
    lib: str
    grid: float = 0.005
    min_width: dict = field(default_factory=dict)
    min_spacing: dict = field(default_factory=dict)
    function: dict = field(default_factory=dict)       # layer -> techfile layer function
    min_area: dict = field(default_factory=dict)        # layer -> um2
    min_spacing2: dict = field(default_factory=dict)    # (layerA, layerB) -> min spacing
    spacing_table: dict = field(default_factory=dict)   # layer -> [(width, length, spacing)]
    vias: dict = field(default_factory=dict)            # (lower, upper) -> viaDef name
    via_size: dict = field(default_factory=dict)        # viaDef -> Rect (cut + enclosures)
    scratch_lib: str = 'aspice_laygrid'
    scratch_path: str | None = None

    @classmethod
    def load(cls, run, tech_lib, scratch_path=None):
        """Read grid, layer functions, minWidth/minSpacing and via defs from the techfile."""
        code = '\n'.join([
            'tf = techGetTechFile(ddGetObj(%s))' % _q(tech_lib),
            'unless(tf error("no techfile for %s"))' % tech_lib,
            _rec('GRID|%L', 'techGetMfgGridResolution(tf)'),
            'foreach(ly tf~>layers %s)' % _rec('LAYER|%s|%L', 'ly~>name',
                                              'techGetLayerFunction(tf ly~>name)'),
            'foreach(g tf~>constraintGroups when(member(g~>name list("foundry" "default"))'
            ' foreach(c g~>objects when(member(c~>defName list("minWidth" "minSpacing" "minArea"))'
            ' && member(length(c~>layers) list(1 2)) %s))))' % _rec(
                'RULE|%s|%s|%s|%L', 'g~>name', 'c~>defName',
                'buildString(c~>layers ",")', 'c~>value'),
            'foreach(v tf~>viaDefs when(v~>layer1 && v~>layer2 && v~>objType == "stdViaDef" %s))'
            % _rec('VIA|%s|%s|%s', 'v~>name', 'v~>layer1~>name', 'v~>layer2~>name'),
        ])
        t = cls(tech_lib, scratch_path=scratch_path)
        fnd = {}
        for r in run(code):
            if r[0] == 'GRID' and r[1] not in ('nil', ''):
                t.grid = float(r[1])
            elif r[0] == 'LAYER':
                t.function[r[1]] = r[2].strip('"') if r[2] != 'nil' else ''
            elif r[0] == 'RULE':
                layers = tuple(r[3].split(','))
                val, table = _rule_value('|'.join(r[4:]))
                if val is None:
                    continue
                key = (layers, r[2])
                if fnd.get(key) == 'foundry' and r[1] != 'foundry':
                    continue
                fnd[key] = r[1]
                if r[2] == 'minWidth' and len(layers) == 1:
                    t.min_width[layers[0]] = val
                elif r[2] == 'minArea' and len(layers) == 1:
                    t.min_area[layers[0]] = val
                elif r[2] == 'minSpacing' and len(layers) == 1:
                    t.min_spacing[layers[0]] = val
                    if table:
                        t.spacing_table[layers[0]] = table
                elif r[2] == 'minSpacing':
                    t.min_spacing2[layers] = val
                    t.min_spacing2[layers[::-1]] = val
            elif r[0] == 'VIA':
                t.vias.setdefault((r[2], r[3]), r[1])
        return t

    def spacing(self, layer, width=0.0, length=0.0, other=None):
        """Minimum spacing; with width/length, the techfile's spacing table applies
        (wide metal needs more); with ``other``, the two-layer rule."""
        if other is not None and other != layer:
            return self.min_spacing2.get((layer, other), 0.0)
        sp = self.min_spacing.get(layer, 0.0)
        for w, ln, v in self.spacing_table.get(layer, ()):
            if width >= w and length >= ln:
                sp = max(sp, v)
        return sp

    def width(self, layer):
        return self.min_width.get(layer, self.grid)

    def pad(self, layer):
        """Widest landing a via puts on this layer (vias drawn at track points)."""
        w = self.width(layer)
        for (l1, l2), vd in self.vias.items():
            if layer in (l1, l2) and vd in self.via_size:
                r = self.via_size[vd]
                w = max(w, r.w, r.h)
        return w

    def pitch(self, layer):
        """Track pitch: via landing + minimum spacing, so vias on neighbouring tracks
        never violate spacing."""
        return snap_up(self.pad(layer) + self.spacing(layer), self.grid)

    def mergeable(self, layer):
        return (self.function.get(layer, '') or '').lower() in MERGEABLE

    def via_def(self, a, b):
        return self.vias.get((a, b)) or self.vias.get((b, a))

    def via_path(self, a, b):
        """Layers from a to b through via defs (shortest chain)."""
        if a == b:
            return [a]
        adj = {}
        for (l1, l2) in self.vias:
            adj.setdefault(l1, set()).add(l2)
            adj.setdefault(l2, set()).add(l1)
        prev, todo = {a: None}, [a]
        while todo:
            nxt = []
            for x in todo:
                for y in sorted(adj.get(x, ())):
                    if y not in prev:
                        prev[y] = x
                        nxt.append(y)
            todo = nxt
        if b not in prev:
            raise KeyError('no via chain %s -> %s in %s' % (a, b, self.lib))
        path = [b]
        while path[-1] != a:
            path.append(prev[path[-1]])
        return path[::-1]

    # -- template measurement ------------------------------------------------
    def measure(self, run, templates, scratch_path=None):
        """Instantiate each template in a scratch cellview, record bbox, per-layer extents
        and pins, plus every via def's size. One SKILL round trip for all."""
        path = scratch_path or self.scratch_path or '/var/tmp/aspice_laygrid'
        code = [
            'unless(ddGetObj(%s) ddCreateLib(%s %s))' % (_q(self.scratch_lib), _q(self.scratch_lib),
                                                          _q(path + '/' + self.scratch_lib)),
            'techBindTechFile(ddGetObj(%s) %s "tech.oa" t)' % (_q(self.scratch_lib), _q(self.lib)),
            'aspCv = dbOpenCellViewByType(%s "measure" "layout" "maskLayout" "w")' % _q(self.scratch_lib),
            'unless(aspCv error("cannot open scratch cellview"))',
            # SI scale of a float pcell parameter: pcell default / CDF default
            'procedure(aspPcellParams(lib cell view uparams)',
            ' let((sm cdf out p ty dv cd sc v) sm = dbOpenCellViewByType(lib cell view nil "r")',
            '  cdf = cdfGetBaseCellCDF(ddGetObj(lib cell)) out = nil',
            '  foreach(up uparams',
            '   p = car(setof(x sm~>parameters~>value x~>name == car(up)))',
            '   ty = if(p p~>valueType "string") dv = if(p p~>value nil)',
            '   cd = if(cdf car(setof(x cdf~>parameters x~>name == car(up))))',
            '   v = cadr(up)',
            '   cond((ty == "float" sc = 1.0',
            '         when(cd && stringp(cd~>defValue) && floatp(dv) && cdfParseFloatString(cd~>defValue)'
            '              && cdfParseFloatString(cd~>defValue) != 0.0'
            '          sc = dv / cdfParseFloatString(cd~>defValue))',
            '         v = if(stringp(v) cdfParseFloatString(v) float(v)) * sc)',
            '        (ty == "int" v = if(stringp(v) atoi(v) v))',
            '        (ty == "boolean" v = if(member(v list(t "t" "TRUE" "true" 1)) "TRUE" "FALSE"))',
            '        (t v = if(stringp(v) v sprintf(nil "%L" v))))',
            '   out = cons(list(car(up) ty v) out))',
            '  reverse(out)))',
            'procedure(aspBox(bb) sprintf(nil "%g,%g,%g,%g" caar(bb) cadar(bb) caadr(bb) cadadr(bb)))',
        ]
        for k, tp in enumerate(templates):
            up = ' '.join('list(%s %s)' % (_q(n), _q(v) if isinstance(v, str) else v)
                          for n, v in tp.params.items())
            code += [
                'aspP = aspPcellParams(%s %s %s list(%s))' % (_q(tp.lib), _q(tp.cell), _q(tp.view), up),
                'foreach(p aspP %s)' % _rec('PARAM|%d|%%s|%%s|%%L' % k, 'car(p)', 'cadr(p)', 'caddr(p)'),
                'aspI = if(aspP dbCreateParamInstByMasterName(aspCv %s %s %s "T%d" 0:0 "R0" 1 aspP)'
                ' dbCreateInstByMasterName(aspCv %s %s %s "T%d" 0:0 "R0"))' % (
                    _q(tp.lib), _q(tp.cell), _q(tp.view), k, _q(tp.lib), _q(tp.cell), _q(tp.view), k),
                'unless(aspI error("cannot place %s/%s"))' % (tp.lib, tp.cell),
                _rec('BBOX|%d|%%s' % k, 'aspBox(aspI~>master~>bBox)'),
                'foreach(lp aspI~>master~>lpps let((bb mw) bb = nil mw = 0.0 foreach(s lp~>shapes '
                'mw = max(mw min(caadr(s~>bBox) - caar(s~>bBox) cadadr(s~>bBox) - cadar(s~>bBox))) '
                'bb = if(bb '
                'list(list(min(caar(bb) caar(s~>bBox)) min(cadar(bb) cadar(s~>bBox))) '
                'list(max(caadr(bb) caadr(s~>bBox)) max(cadadr(bb) cadadr(s~>bBox)))) s~>bBox)) '
                'when(bb && lp~>purpose == "drawing" %s)))' % _rec(
                    'LPP|%d|%%s|%%s|%%s|%%g' % k, 'lp~>layerName', 'lp~>purpose', 'aspBox(bb)', 'mw'),
                'foreach(tm aspI~>master~>terminals foreach(pn tm~>pins when(pn~>fig %s)))' % _rec(
                    'PIN|%d|%%s|%%s|%%s' % k, 'tm~>name', 'pn~>fig~>layerName', 'aspBox(pn~>fig~>bBox)'),
                'dbDeleteObject(aspI)',
            ]
        code += [
            'aspTf = techGetTechFile(ddGetObj(%s))' % _q(self.lib),
            'foreach(v aspTf~>viaDefs when(v~>objType == "stdViaDef" let((vi) '
            'vi = dbCreateVia(aspCv v 0:0 "R0" nil) when(vi %s dbDeleteObject(vi)))))' % _rec(
                'VSIZE|%s|%s', 'v~>name', 'aspBox(vi~>bBox)'),
            'dbClose(aspCv)',
        ]
        recs = run('\n'.join(code))
        for t in templates:
            t.pins, t.layers, t.pparams = {}, {}, []
        for r in recs:
            if r[0] == 'VSIZE':
                self.via_size[r[1]] = Rect(*map(float, r[2].split(',')))
                continue
            if r[0] not in ('PARAM', 'BBOX', 'LPP', 'PIN'):
                continue
            t = templates[int(r[1])]
            if r[0] == 'PARAM':
                t.pparams.append((r[2], r[3], r[4]))
            elif r[0] == 'BBOX':
                t.bbox = Rect(*map(float, r[2].split(',')))
            elif r[0] == 'LPP':
                box = Rect(*map(float, r[4].split(',')))
                t.layers[r[2]] = t.layers[r[2]].union(box) if r[2] in t.layers else box
                t.shape_width[r[2]] = max(t.shape_width.get(r[2], 0.0), float(r[5]))
            elif r[0] == 'PIN':
                t.pins.setdefault(r[2], []).append((r[3], Rect(*map(float, r[4].split(',')))))
        missing = [t.cell for t in templates if t.bbox is None]
        if missing:
            raise RuntimeError('templates not measured: %s' % missing)
        return templates


@dataclass(eq=False)
class Template:
    lib: str
    cell: str
    view: str = 'layout'
    params: dict = field(default_factory=dict)
    name: str = ''
    bbox: Rect | None = None
    layers: dict = field(default_factory=dict)     # layer -> Rect (extent in the master)
    pins: dict = field(default_factory=dict)       # term -> [(layer, Rect)]
    pparams: list = field(default_factory=list)    # coerced pcell params [(name, type, skill value)]
    shape_width: dict = field(default_factory=dict)  # layer -> widest single shape (for spacing tables)

    def __post_init__(self):
        self.name = self.name or '%s(%s)' % (self.cell, ','.join('%s=%s' % kv for kv in self.params.items()))

    @property
    def area(self):
        return self.bbox.w * self.bbox.h


# ---------------------------------------------------------------------------
# patterns (LAYGO-style arrays)
# ---------------------------------------------------------------------------

def interdigitate(a, b, n):
    """ABAB... with n units of each."""
    return [x for _ in range(n) for x in (a, b)]


def common_centroid(a, b, n):
    """1-D common centroid with n units of each (n even): ABBA, ABBAABBA, ...; both
    centroids sit at the row centre and every unit sees the same neighbours on average.
    Odd n has no exact 1-D common centroid: use cross_coupled() (2-D) instead."""
    if n % 2:
        raise ValueError('common_centroid needs an even unit count; use cross_coupled() for n=%d' % n)
    return [a, b, b, a] * (n // 2)


def cross_coupled(a, b, cols):
    """2-D common centroid (two rows): ABAB.. over BABA..; returns [row0, row1]."""
    r0 = [a if i % 2 == 0 else b for i in range(2 * cols)]
    return [r0, [b if x == a else a for x in r0]]


def with_dummies(seq, n=1, token='D'):
    return [token] * n + list(seq) + [token] * n


def centroid(positions):
    return sum(positions) / len(positions) if positions else float('nan')


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class Inst:
    name: str
    tpl: Template
    origin: tuple
    orient: str = 'R0'

    @property
    def bbox(self):
        return transform(self.tpl.bbox, self.orient, self.origin)

    def layer_box(self, layer):
        r = self.tpl.layers.get(layer)
        return transform(r, self.orient, self.origin) if r else None

    def pin(self, term):
        """[(layer, Rect)] of a terminal, in layout coordinates."""
        if term not in self.tpl.pins:
            raise KeyError('%s has no pin %r (has %s)' % (self.tpl.cell, term, sorted(self.tpl.pins)))
        return [(ly, transform(r, self.orient, self.origin)) for ly, r in self.tpl.pins[term]]


@dataclass
class Track:
    layer: str
    dir: str        # 'h' (runs along x, sits at y=coord) or 'v'
    coord: float
    width: float


class Layout:
    def __init__(self, tech, lib, cell, view='layout'):
        self.tech, self.lib, self.cell, self.view = tech, lib, cell, view
        self.insts = {}
        self.paths = []      # (layer, [(x,y)...], width)
        self.rects = []      # (layer, purpose, Rect)
        self.vias = []       # (viaDef, (x,y), {param: value})
        self.pins = []       # (net, layer, Rect, direction)
        self.labels = []     # (text, layer, (x,y), height)

    # -- spacing ---------------------------------------------------------------
    def gap(self, left, right, axis='x'):
        """Smallest legal gap between the bbox edges of two templates placed side by side
        (axis 'x': left|right, 'y': below|above)."""
        return self.gap_fill(left, right, axis)[0]

    def gap_fill(self, left, right, axis='x'):
        """(gap, fill_layers). Same-layer and two-layer spacing rules at the facing edges set
        the gap. Wells, implants and marker layers (mergeable functions) never push devices
        apart: if they would end up closer than their spacing, they are listed in
        fill_layers and bridged with a filler rectangle, as LAYGO's boundary cells do."""
        need, fills = 0.0, []
        for la, ra in left.layers.items():
            for lb, rb in right.layers.items():
                sp = self.tech.spacing(la) if la == lb else self.tech.spacing(la, other=lb)
                if not sp:
                    continue
                if axis == 'x':
                    d0 = (left.bbox.x1 - ra.x1) + (rb.x0 - right.bbox.x0)
                    par = min(ra.y1, rb.y1) - max(ra.y0, rb.y0)
                else:
                    d0 = (left.bbox.y1 - ra.y1) + (rb.y0 - right.bbox.y0)
                    par = min(ra.x1, rb.x1) - max(ra.x0, rb.x0)
                if par <= 0 and la != lb:
                    continue
                if la == lb and self.tech.mergeable(la):
                    if d0 > 1e-9 and par > 0:
                        fills.append(la)
                    continue
                if la == lb:
                    w = max(left.shape_width.get(la, 0.0), right.shape_width.get(la, 0.0))
                    sp = self.tech.spacing(la, width=w, length=max(par, 0.0))
                need = max(need, sp - d0)
        return snap_up(max(0.0, need), self.tech.grid), sorted(set(fills))

    def _fill_between(self, a, b, layers, axis):
        for ly in layers:
            ra, rb = a.layer_box(ly), b.layer_box(ly)
            if not ra or not rb:
                continue
            if axis == 'x':
                lo, hi = max(ra.y0, rb.y0), min(ra.y1, rb.y1)
                if ra.x1 < rb.x0 and hi > lo:
                    self.rect(ly, Rect(ra.x1, lo, rb.x0, hi))
            else:
                lo, hi = max(ra.x0, rb.x0), min(ra.x1, rb.x1)
                if ra.y1 < rb.y0 and hi > lo:
                    self.rect(ly, Rect(lo, ra.y1, hi, rb.y0))

    def _gap_inst(self, a_tpl, a_orient, b_tpl, b_orient, axis):
        return self.gap_fill(_oriented(a_tpl, a_orient), _oriented(b_tpl, b_orient), axis)

    # -- placement -------------------------------------------------------------
    def place(self, name, tpl, ll=(0.0, 0.0), orient='R0'):
        """Place so the instance's bounding box lower-left lands on ``ll``."""
        if name in self.insts:
            raise ValueError('duplicate instance %s' % name)
        g = self.tech.grid
        r = transform(tpl.bbox, orient)
        origin = (snap(ll[0] - r.x0, g), snap(ll[1] - r.y0, g))
        inst = Inst(name, tpl, origin, orient)
        for o in self.insts.values():
            if inst.bbox.overlaps(o.bbox):
                raise ValueError('%s overlaps %s' % (name, o.name))
        self.insts[name] = inst
        return inst

    def right_of(self, name, tpl, ref, orient='R0', align='bottom', extra=0.0):
        gap, fills = self._gap_inst(ref.tpl, ref.orient, tpl, orient, 'x')
        r = transform(tpl.bbox, orient)
        rb = ref.bbox
        y = {'bottom': rb.y0, 'top': rb.y1 - r.h, 'center': rb.cy - r.h / 2}[align]
        inst = self.place(name, tpl, (rb.x1 + gap + extra, y), orient)
        self._fill_between(ref, inst, fills, 'x')
        return inst

    def left_of(self, name, tpl, ref, orient='R0', align='bottom', extra=0.0):
        gap, fills = self._gap_inst(tpl, orient, ref.tpl, ref.orient, 'x')
        r = transform(tpl.bbox, orient)
        rb = ref.bbox
        y = {'bottom': rb.y0, 'top': rb.y1 - r.h, 'center': rb.cy - r.h / 2}[align]
        inst = self.place(name, tpl, (rb.x0 - gap - extra - r.w, y), orient)
        self._fill_between(inst, ref, fills, 'x')
        return inst

    def above(self, name, tpl, ref, orient='R0', align='left', extra=0.0):
        gap, fills = self._gap_inst(ref.tpl, ref.orient, tpl, orient, 'y')
        r = transform(tpl.bbox, orient)
        rb = ref.bbox
        x = {'left': rb.x0, 'right': rb.x1 - r.w, 'center': rb.cx - r.w / 2}[align]
        inst = self.place(name, tpl, (x, rb.y1 + gap + extra), orient)
        self._fill_between(ref, inst, fills, 'y')
        return inst

    def below(self, name, tpl, ref, orient='R0', align='left', extra=0.0):
        gap, fills = self._gap_inst(tpl, orient, ref.tpl, ref.orient, 'y')
        r = transform(tpl.bbox, orient)
        rb = ref.bbox
        x = {'left': rb.x0, 'right': rb.x1 - r.w, 'center': rb.cx - r.w / 2}[align]
        inst = self.place(name, tpl, (x, rb.y0 - gap - extra - r.h), orient)
        self._fill_between(inst, ref, fills, 'y')
        return inst

    def row(self, prefix, seq, tpls, ll=(0.0, 0.0), orients=None, ref=None, side='right',
            dummy='D'):
        """Place a row from a pattern, e.g. with_dummies(common_centroid('A','B',2)).
        Instances are named <prefix><token><k> (k counts per token; the dummy token becomes
        'dummy', which the layout audit recognises). Returns the list."""
        out, count = [], {}
        orients = orients or {}
        prev = ref
        for tok in seq:
            count[tok] = count.get(tok, 0) + 1
            name = '%s%s%d' % (prefix, 'dummy' if tok == dummy else tok, count[tok])
            o = orients.get(tok, 'R0')
            if prev is None:
                inst = self.place(name, tpls[tok], ll, o)
            elif side == 'right':
                inst = self.right_of(name, tpls[tok], prev, o)
            else:
                inst = self.above(name, tpls[tok], prev, o)
            out.append(inst)
            prev = inst
        return out

    def mirror_x(self, name, inst, axis_x):
        """Mirror-symmetric copy of ``inst`` about the vertical line x = axis_x."""
        b = inst.bbox
        o = {'R0': 'MY', 'MY': 'R0', 'MX': 'R180', 'R180': 'MX'}.get(inst.orient, inst.orient)
        return self.place(name, inst.tpl, (2 * axis_x - b.x1, b.y0), o)

    # -- routing ---------------------------------------------------------------
    def tracks(self, layer, direction, start, count, pitch=None, width=None):
        p = pitch or self.tech.pitch(layer)
        w = width or snap_up(self.tech.width(layer), self.tech.grid)
        return [Track(layer, direction, snap(start + k * p, self.tech.grid), w) for k in range(count)]

    def wire(self, layer, pts, width=None):
        w = width or snap_up(self.tech.width(layer), self.tech.grid)
        g = self.tech.grid
        self.paths.append((layer, [(snap(x, g), snap(y, g)) for x, y in pts], w))

    def rect(self, layer, r, purpose='drawing'):
        self.rects.append((layer, purpose, r))

    def via(self, via_def, xy, **params):
        """One via (or tap: a diffusion-to-metal via def such as a substrate contact),
        with via-def parameters, e.g. cutColumns=12, cutRows=1."""
        g = self.tech.grid
        self.vias.append((via_def, (snap(xy[0], g), snap(xy[1], g)), params))

    def via_stack(self, a, b, xy, **params):
        """Vias from layer a to b at xy. Each intermediate layer gets a landing patch big
        enough for its minimum-area rule (a bare via landing usually is not)."""
        chain = self.tech.via_path(a, b)
        g = self.tech.grid
        for l1, l2 in zip(chain, chain[1:]):
            self.via(self.tech.via_def(l1, l2), xy, **params)
        for ly in chain[1:-1]:
            amin = self.tech.min_area.get(ly, 0.0)
            pad = self.tech.pad(ly)
            if amin and pad * pad < amin:
                side = snap_up(math.sqrt(amin), g)
                h = snap_up(max(pad, amin / side), g)
                self.rect(ly, Rect(xy[0] - side / 2, xy[1] - h / 2, xy[0] + side / 2, xy[1] + h / 2))

    def connect(self, pins, track, extend=0.0):
        """Run a stub from each pin rect to the track on the pin's own layer, via up to the
        track layer, and draw the track wire across all of them."""
        pts = []
        for ly, r in pins:
            if track.dir == 'h':
                x = r.cx
                y0 = r.cy
                if not (r.y0 <= track.coord <= r.y1):
                    self.wire(ly, [(x, y0), (x, track.coord)], width=min(r.w, r.h) if ly == track.layer else None)
                self.via_stack(ly, track.layer, (x, track.coord))
                pts.append(x)
            else:
                y = r.cy
                x0 = r.cx
                if not (r.x0 <= track.coord <= r.x1):
                    self.wire(ly, [(x0, y), (track.coord, y)])
                self.via_stack(ly, track.layer, (track.coord, y))
                pts.append(y)
        lo, hi = min(pts) - extend, max(pts) + extend
        half = 0.5 * max(self.tech.via_size.get(self.tech.via_def(*self.tech.via_path(pins[0][0], track.layer)[-2:]),
                                                Rect(0, 0, track.width, track.width)).w
                         if pins[0][0] != track.layer else track.width, track.width)
        if track.dir == 'h':
            self.wire(track.layer, [(lo - half, track.coord), (hi + half, track.coord)], track.width)
        else:
            self.wire(track.layer, [(track.coord, lo - half), (track.coord, hi + half)], track.width)

    def pin(self, net, layer, r, direction='inputOutput', label_height=None):
        self.pins.append((net, layer, r, direction))
        self.labels.append((net, layer, (r.cx, r.cy), label_height or max(self.tech.grid * 10, 0.05)))

    # -- report ----------------------------------------------------------------
    def bbox(self):
        boxes = [i.bbox for i in self.insts.values()] + [r for _, _, r in self.rects]
        for vd, (x, y), prm in self.vias:
            v = self.tech.via_size.get(vd)
            if v is not None:
                nx = int(prm.get('cutColumns', 1))
                ny = int(prm.get('cutRows', 1))
                # unit via size scaled by the cut array (approximate: cut pitch ~ unit size)
                boxes.append(Rect(x + v.x0 * nx, y + v.y0 * ny, x + v.x1 * nx, y + v.y1 * ny))
        for ly, pts, w in self.paths:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            boxes.append(Rect(min(xs) - w / 2, min(ys) - w / 2, max(xs) + w / 2, max(ys) + w / 2))
        if not boxes:
            return None
        b = boxes[0]
        for x in boxes[1:]:
            b = b.union(x)
        return b

    def report(self, area_budget=None):
        b = self.bbox()
        if b is None:
            return 'empty layout'
        dev = sum(i.tpl.area for i in self.insts.values())
        area = b.w * b.h
        asp = max(b.w, b.h) / max(min(b.w, b.h), 1e-9)
        rows = {}
        for i in self.insts.values():
            rows.setdefault(round(i.bbox.y0, 3), []).append(i)
        lines = ['%s/%s: %.2f x %.2f um = %.1f um2, aspect %.2f, device utilisation %.0f %%, '
                 '%d instances in %d rows, %d wires, %d vias' % (
                     self.lib, self.cell, b.w, b.h, area, asp, 100 * dev / area if area else 0,
                     len(self.insts), len(rows), len(self.paths), len(self.vias))]
        if area_budget and area > area_budget:
            lines.append('OVER BUDGET: %.1f um2 > %.1f um2' % (area, area_budget))
        if asp > 2.0:
            lines.append('aspect ratio %.2f > 2 (hard limit): fold the long rows; aim for 1:1' % asp)
        elif asp > 1.5:
            lines.append('aspect ratio %.2f: aim for 1:1 (fold a row, stack halves)' % asp)
        return '\n'.join(lines)

    def centroids(self, prefix_by_group):
        """{group: (cx, cy)} for instance-name prefixes, to confirm a common centroid."""
        out = {}
        for grp, rx in prefix_by_group.items():
            xs = [i.bbox for n, i in self.insts.items() if re.match(rx, n)]
            out[grp] = (centroid([r.cx for r in xs]), centroid([r.cy for r in xs]))
        return out

    # -- SKILL emission --------------------------------------------------------
    def skill(self, overwrite=False):
        c = []
        mode = '"w"'
        c.append('aspL = ddGetObj(%s)' % _q(self.lib))
        c.append('unless(aspL error("library %s not found"))' % self.lib)
        if not overwrite:
            c.append('when(ddGetObj(%s %s %s) error("%s/%s/%s exists: pass overwrite=True"))' % (
                _q(self.lib), _q(self.cell), _q(self.view), self.lib, self.cell, self.view))
        c.append('aspCv = dbOpenCellViewByType(%s %s %s "maskLayout" %s)' % (
            _q(self.lib), _q(self.cell), _q(self.view), mode))
        c.append('unless(aspCv error("cannot open %s/%s for write"))' % (self.lib, self.cell))
        c.append('aspTf = techGetTechFile(aspCv)')
        for i in self.insts.values():
            t = i.tpl
            xy = '%s:%s' % (_g(i.origin[0]), _g(i.origin[1]))
            if t.pparams:
                plist = ' '.join('list(%s %s %s)' % (_q(n), _q(ty), v) for n, ty, v in t.pparams)
                c.append('dbCreateParamInstByMasterName(aspCv %s %s %s %s %s %s 1 list(%s))' % (
                    _q(t.lib), _q(t.cell), _q(t.view), _q(i.name), xy, _q(i.orient), plist))
            else:
                c.append('dbCreateInstByMasterName(aspCv %s %s %s %s %s %s)' % (
                    _q(t.lib), _q(t.cell), _q(t.view), _q(i.name), xy, _q(i.orient)))
        for ly, pur, r in self.rects:
            c.append('dbCreateRect(aspCv list(%s %s) %s)' % (_q(ly), _q(pur), r.skill()))
        for ly, pts, w in self.paths:
            # "truncate" with the ends pushed out by half a width = an extended path, drawn
            # as explicit geometry (pathStyle names differ between releases)
            c.append('dbCreatePath(aspCv list(%s "drawing") list(%s) %s)' % (
                _q(ly), ' '.join('%s:%s' % (_g(x), _g(y)) for x, y in _extend_ends(pts, w / 2)),
                _g(w)))
        for vd, (x, y), prm in self.vias:
            plist = ' '.join('list(%s %s)' % (_q(k), _q(v) if isinstance(v, str) else _g(v))
                             for k, v in prm.items())
            c.append('dbCreateVia(aspCv techFindViaDefByName(aspTf %s) %s:%s "R0" %s)' % (
                _q(vd), _g(x), _g(y), 'list(%s)' % plist if prm else 'nil'))
        for net, ly, r, d in self.pins:
            c.append('let((n f) n = dbFindNetByName(aspCv %s) unless(n n = dbCreateNet(aspCv %s)) '
                     'f = dbCreateRect(aspCv list(%s "pin") %s) '
                     'unless(dbFindTermByName(aspCv %s) dbCreateTerm(n %s %s)) dbCreatePin(n f))' % (
                         _q(net), _q(net), _q(ly), r.skill(), _q(net), _q(net), _q(d)))
        for text, ly, (x, y), h in self.labels:
            c.append('dbCreateLabel(aspCv list(%s "pin") %s:%s %s "centerCenter" "R0" "stick" %s)' % (
                _q(ly), _g(x), _g(y), _q(text), _g(h)))
        c.append('dbSave(aspCv)')
        c.append(_rec('WROTE|%d|%d|%d', 'length(aspCv~>instances)', 'length(aspCv~>shapes)',
                      'length(aspCv~>vias)'))
        c.append('dbClose(aspCv)')
        return '\n'.join(c)

    def write(self, run, overwrite=False):
        recs = run(self.skill(overwrite))
        w = [r for r in recs if r[0] == 'WROTE']
        if not w:
            raise RuntimeError('layout not written (no confirmation record)')
        return {'instances': int(w[0][1]), 'shapes': int(w[0][2]), 'vias': int(w[0][3])}


def _extend_ends(pts, e):
    if len(pts) < 2 or e <= 0:
        return pts
    def push(a, b):
        dx, dy = a[0] - b[0], a[1] - b[1]
        n = math.hypot(dx, dy) or 1.0
        return (a[0] + e * dx / n, a[1] + e * dy / n)
    return [push(pts[0], pts[1])] + list(pts[1:-1]) + [push(pts[-1], pts[-2])]


def _oriented(tpl, orient):
    """A shallow template copy with bbox/layers transformed (for gap computation)."""
    if orient == 'R0':
        return tpl
    t = Template(tpl.lib, tpl.cell, tpl.view, tpl.params, tpl.name)
    t.bbox = transform(tpl.bbox, orient)
    t.layers = {k: transform(v, orient) for k, v in tpl.layers.items()}
    t.shape_width = tpl.shape_width
    return t
