"""Operating-point audit of a finished schematic, from Spectre's own oppoint.

Checks that every amplifying device sits where Razavi's design method puts it, so a
schematic that "simulates" but relies on a device in triode, at the edge of saturation or
in a mismatched mirror is caught before layout:

  region      every analog MOS is in saturation (Ch. 2-3); triode / off / breakdown fails,
              weak inversion is a warning
  headroom    |Vds| - |Vdsat| >= margin (default 100 mV): what survives PVT and signal swing
  gm/Id       5..25 1/V: below 5 is deep strong inversion (velocity saturation, no headroom),
              above 25 is weak inversion (slow); outside the range is reported, not failed
  gm*ro       intrinsic gain >= 10 for gain devices (L too close to minimum otherwise)
  pairs       differential pairs: equal Vgs and Id at the balanced operating point (Ch. 4)
  mirrors     current ratio matches the W*nf*m/L ratio within 5 %, same L, and the
              Vds difference that causes the error is named (Ch. 5)

Switches and logic (gate at a rail, triode with |Vds| ~ 0, or off with Vgs ~ 0) are
recognised and listed separately. Nothing here assumes a PDK: devices are found from the
oppoint records (anything with a region and a Vdsat) and mapped back to the netlist for
their nets and sizes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

import cadence as CD
import spectre_netlist as SN

REGION = CD.REGION_NAMES
TINFO = 'aspice_opck_tinfo'
DCINFO = 'aspice_opck_info'


@dataclass
class Rules:
    margin: float = 0.10          # V, |Vds| - |Vdsat| below this warns
    gmid_lo: float = 5.0
    gmid_hi: float = 25.0
    gain_min: float = 10.0
    mirror_tol: float = 0.05      # relative current-ratio error
    pair_dvgs: float = 0.005      # V, |Vgs1 - Vgs2| of a pair at balance
    pair_did: float = 0.05        # relative Id difference of a pair
    vdd: float | None = None      # V, auto from the vsources when None
    ignore: str | None = None     # regex on instance path: skip entirely
    switch: str | None = None     # regex on instance path: treat as switch/logic


@dataclass
class Finding:
    level: str                    # FAIL | WARN | INFO
    rule: str
    device: str
    msg: str
    point: str = 'dc'


@dataclass
class Device:
    path: str
    master: str
    d: str
    g: str
    s: str
    b: str
    params: dict
    op: dict = field(default_factory=dict)
    role: str = 'analog'

    def f(self, k, default=float('nan')):
        v = self.op.get(k)
        return v if isinstance(v, float) else default

    @property
    def region(self):
        r = self.op.get('region')
        return REGION.get(int(r), str(r)) if isinstance(r, float) else str(r)

    @property
    def pol(self):
        return 'p' if self.f('vth', 0.0) < 0 else 'n'

    @property
    def headroom(self):
        return abs(self.f('vds')) - abs(self.f('vdsat'))

    @property
    def gm_id(self):
        i = self.f('ids', 0.0)
        return abs(self.f('gm') / i) if i else float('nan')

    @property
    def gain(self):
        g = self.f('gds', 0.0)
        return abs(self.f('gm') / g) if g else float('inf')

    @property
    def diode(self):
        return self.d == self.g

    def num(self, k, default=None):
        v = self.params.get(k)
        if v is None:
            return default
        v = str(v).strip()
        while v.startswith('(') and v.endswith(')'):
            v = v[1:-1].strip()
        return SN.spectre_number(v)

    def size(self, per_finger_w=False):
        """(W_total*m/L, L, W_finger) when the sizes are plain numbers, else Nones.
        ``per_finger_w``: the PDK's w is per finger (total = w*nf), else w is total."""
        w, l = self.num('w'), self.num('l')
        if not w or not l:
            return None, None, None
        nf, m = self.num('nf', 1.0) or 1.0, self.num('m', 1.0) or 1.0
        wt = w * nf if per_finger_w else w
        return wt * m / l, l, wt / nf

    def same_size(self, other):
        keys = ('w', 'l', 'nf', 'm')
        a = [self.num(k, 1.0 if k in ('nf', 'm') else None) for k in keys]
        b = [other.num(k, 1.0 if k in ('nf', 'm') else None) for k in keys]
        if None in a or None in b:
            return all(str(self.params.get(k)) == str(other.params.get(k)) for k in keys)
        return all(abs(x - y) <= 1e-9 * max(abs(x), abs(y), 1e-30) for x, y in zip(a, b))


def size_ratio(out, ref):
    """Intended W/L ratio out:ref. A PDK's w is either total or per finger; when the
    finger counts differ, take the convention that agrees with the simulator's own
    effective-beta ratio (betaeff), so no PDK convention is assumed."""
    r_tot, r_fin = [(out.size(pf)[0], ref.size(pf)[0]) for pf in (False, True)]
    if not all(r_tot) or not all(r_fin):
        return None, None
    a, b = r_tot[0] / r_tot[1], r_fin[0] / r_fin[1]
    if abs(a - b) <= 1e-9 * a:
        return a, False
    be_o, be_r = out.f('betaeff', 0.0), ref.f('betaeff', 0.0)
    if be_o and be_r:
        import math
        e = be_o / be_r
        return (a, False) if abs(math.log(a / e)) <= abs(math.log(b / e)) else (b, True)
    return a, False


# ---------------------------------------------------------------------------
# netlist -> flat leaf instances with hierarchical net names
# ---------------------------------------------------------------------------

def flatten(nl):
    """{path: (master, [nets], params)} for every leaf instance (master not a subckt
    defined in this netlist). Net names follow Spectre's hierarchical naming."""
    out = {}
    glob = {'0', 'gnd!'}

    def walk(insts, prefix, netmap):
        for inst in insts:
            path = prefix + inst.name
            nets = [n if n in glob else netmap.get(n, (prefix + n) if prefix else n)
                    for n in inst.nodes]
            sk = nl.subckts.get(inst.master)
            if sk is not None:
                sub = dict(zip(sk.ports, nets))
                walk(sk.instances, path + '.', sub)
            else:
                out[path] = (inst.master, nets, dict(inst.params))

    top = [s for s in nl.statements if isinstance(s, SN.Instance)]
    walk(top, '', {})
    return out


def supply_voltage(nl):
    vals = []
    for path, (master, nets, params) in flatten(nl).items():
        if master == 'vsource' and 'dc' in params:
            v = SN.spectre_number(str(params['dc']))
            if v is not None:
                vals.append(abs(v))
    return max(vals) if vals else None


def match_devices(flat, structs):
    """Pair each MOS oppoint record with its netlist leaf (longest path prefix)."""
    devs = {}
    for rec_name, rec in structs.items():
        if not isinstance(rec.get('region'), float) or 'vdsat' not in rec:
            continue
        leaf = rec_name if rec_name in flat else None
        if leaf is None:
            cands = [p for p in flat if rec_name.startswith(p + '.')]
            leaf = max(cands, key=len) if cands else None
        if leaf is None:
            master, nets, params = '?', ['?'] * 4, {}
            leaf = rec_name
        else:
            master, nets, params = flat[leaf]
        nets = (list(nets) + ['?'] * 4)[:4]
        old = devs.get(leaf)
        if old and abs(old.f('ids', 0.0)) >= abs(rec.get('ids', 0.0) or 0.0):
            continue
        devs[leaf] = Device(leaf, master, nets[0], nets[1], nets[2], nets[3], params, rec)
    return devs


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------

def classify(dev, rules, vdd):
    if rules.ignore and re.search(rules.ignore, dev.path):
        return 'ignored'
    if rules.switch and re.search(rules.switch, dev.path):
        return 'switch'
    vgs, vds = abs(dev.f('vgs', 0.0)), abs(dev.f('vds', 0.0))
    reg = dev.region
    if vdd:
        if reg == 'triode' and vgs >= 0.8 * vdd and vds <= 0.05 * vdd:
            return 'switch'
        if reg in ('off', 'subthreshold') and vgs <= 0.05 * vdd:
            return 'switch'
    return 'analog'


def check_devices(devs, rules, vdd, point='dc'):
    out = []
    for d in sorted(devs.values(), key=lambda x: x.path):
        d.role = classify(d, rules, vdd)
        if d.role == 'ignored':
            continue
        if d.role == 'switch':
            state = 'on' if d.region == 'triode' else 'off'
            out.append(Finding('INFO', 'switch', d.path,
                               'switch/logic %s (|Vgs|=%.3f V, |Vds|=%.3f V)' % (
                                   state, abs(d.f('vgs')), abs(d.f('vds'))), point))
            continue
        vds, vdsat = abs(d.f('vds')), abs(d.f('vdsat'))
        if d.region in ('triode', 'off', 'breakdown'):
            out.append(Finding('FAIL', 'region', d.path,
                               '%s, not saturated: |Vds|=%.3f V vs |Vdsat|=%.3f V '
                               '(|Vgs|=%.3f, |Vth|=%.3f)' % (d.region, vds, vdsat,
                                                             abs(d.f('vgs')), abs(d.f('vth'))),
                               point))
            continue
        if d.region == 'subthreshold':
            out.append(Finding('WARN', 'region', d.path,
                               'weak inversion: gm/Id=%.1f /V, |Vgs|-|Vth|=%.0f mV; fine for a '
                               'low-power bias device, slow for a signal-path device' % (
                                   d.gm_id, 1e3 * (abs(d.f('vgs')) - abs(d.f('vth')))), point))
        elif d.headroom < rules.margin:
            out.append(Finding('WARN', 'headroom', d.path,
                               'saturated by only %.0f mV (|Vds|=%.3f, |Vdsat|=%.3f): goes '
                               'triode under PVT or signal swing; target >= %.0f mV' % (
                                   1e3 * d.headroom, vds, vdsat, 1e3 * rules.margin), point))
        g = d.gm_id
        if g == g and d.region == 'saturation':
            if g < rules.gmid_lo:
                out.append(Finding('WARN', 'gm/Id', d.path,
                                   'gm/Id=%.1f /V: deep strong inversion (large Vov, poor '
                                   'efficiency, little headroom)' % g, point))
            elif g > rules.gmid_hi:
                out.append(Finding('WARN', 'gm/Id', d.path,
                                   'gm/Id=%.1f /V: near weak inversion (low fT)' % g, point))
        if not d.diode and d.gain < rules.gain_min:
            out.append(Finding('WARN', 'gm*ro', d.path,
                               'intrinsic gain gm*ro=%.1f < %.0f: L too short for a gain or '
                               'current-source device' % (d.gain, rules.gain_min), point))
    return out


def find_groups(devs):
    """Differential pairs and current mirrors among analog devices."""
    analog = [d for d in devs.values() if d.role == 'analog']
    rails = {'0', 'gnd!'}
    pairs, mirrors = [], []
    by_src = {}
    for d in analog:
        by_src.setdefault((d.pol, d.s), []).append(d)
    for (pol, s), grp in by_src.items():
        # mirrors: same gate and source; reference = diode-connected
        by_gate = {}
        for d in grp:
            by_gate.setdefault(d.g, []).append(d)
        for g, ms in by_gate.items():
            refs = [m for m in ms if m.diode]
            if refs and len(ms) > 1:
                mirrors.append((refs[0], [m for m in ms if m is not refs[0]]))
        # pairs: same source (not a rail), different gates, same master and sizes
        if s in rails:
            continue
        cand = [d for d in grp if not d.diode and d.g not in rails]
        for i, a in enumerate(cand):
            for b in cand[i + 1:]:
                if a.g != b.g and a.master == b.master and a.num('l') == b.num('l'):
                    pairs.append((a, b))
    return pairs, mirrors


def check_groups(devs, rules, point='dc'):
    out = []
    pairs, mirrors = find_groups(devs)
    for a, b in pairs:
        if not a.same_size(b):
            out.append(Finding('WARN', 'pair', '%s/%s' % (a.path, b.path),
                               'differential pair with unequal sizes (%s vs %s): the two halves '
                               'of a pair must be identical devices' % (_sz(a), _sz(b)), point))
            continue
        dv = abs(abs(a.f('vgs')) - abs(b.f('vgs')))
        ia, ib = abs(a.f('ids', 0.0)), abs(b.f('ids', 0.0))
        di = abs(ia - ib) / max(ia, ib) if max(ia, ib) else 0.0
        lvl = 'WARN' if dv > rules.pair_dvgs or di > rules.pair_did else 'INFO'
        out.append(Finding(lvl, 'pair', '%s/%s' % (a.path, b.path),
                           'differential pair: dVgs=%.1f mV, dId=%.1f %% (want ~0 at the balanced '
                           'input; a gap is systematic offset or an unbalanced load)' % (
                               1e3 * dv, 100 * di), point))
    for ref, outs in mirrors:
        iref = abs(ref.f('ids', 0.0))
        for m in outs:
            rl, ml = ref.num('l'), m.num('l')
            if rl and ml and abs(rl - ml) > 1e-12:
                out.append(Finding('WARN', 'mirror', m.path,
                                   'mirror from %s uses a different L (%.3g vs %.3g um): scale '
                                   'W or fingers, keep L equal' % (ref.path, ml * 1e6, rl * 1e6),
                                   point))
            want, per_finger = size_ratio(m, ref)
            if not (want and iref):
                continue
            wf_m, wf_r = m.size(per_finger)[2], ref.size(per_finger)[2]
            if wf_m and wf_r and abs(wf_m / wf_r - 1) > 0.01:
                out.append(Finding('WARN', 'mirror', m.path,
                                   'mirror from %s uses a different finger width (%.3g vs %.3g '
                                   'um): ratio mirrors from identical unit fingers' % (
                                       ref.path, wf_m * 1e6, wf_r * 1e6), point))
            got = abs(m.f('ids', 0.0)) / iref
            err = got / want - 1
            dvds = abs(m.f('vds')) - abs(ref.f('vds'))
            lvl = 'WARN' if abs(err) > rules.mirror_tol else 'INFO'
            out.append(Finding(lvl, 'mirror', m.path,
                               'mirror of %s: Iout/Iref=%.3f vs size ratio %.3f (%+.1f %%), '
                               'dVds=%+.0f mV%s' % (
                                   ref.path, got, want, 100 * err, 1e3 * dvds,
                                   ' -> equalise Vds (cascode), lengthen L, or use unit '
                                   'fingers' if lvl == 'WARN' else ''), point))
    return out


# ---------------------------------------------------------------------------
# deck preparation and running
# ---------------------------------------------------------------------------

_SPICE_ANALYSES = ('.tran', '.ac', '.dc', '.op', '.noise', '.tf', '.pz', '.meas', '.measure',
                   '.four', '.sens', '.step', '.mc')


def _comment_analyses(text, nl=None):
    """Comment out every analysis statement, including wrapper blocks (montecarlo {...},
    sweep {...}) and their bodies, in Spectre and in `simulator lang=spice` sections.
    Text-based: continuation lines and braces are followed, options/saves are kept."""
    lines = text.splitlines(keepends=True)
    out, i, lang = [], 0, 'spectre'
    if lines and lines[0].lstrip().startswith(('*', '.')) and 'simulator lang' not in text[:2000]:
        lang = 'spice'
    while i < len(lines):
        j, logical = i, lines[i]
        cont = '\\' if lang == 'spectre' else None
        while j + 1 < len(lines) and (
                (cont and logical.rstrip('\r\n').endswith(cont)) or
                (lang == 'spice' and lines[j + 1].lstrip().startswith('+'))):
            j += 1
            logical += lines[j]
        s = logical.strip()
        m = re.match(r'simulator\s+lang\s*=\s*(\w+)', s, re.I)
        if m:
            lang = m.group(1).lower()
        toks = s.split()
        kill = False
        if lang == 'spectre' and len(toks) >= 2 and not s.startswith('//'):
            kill = toks[1] in SN.ANALYSIS_TYPES
        elif lang == 'spice' and toks:
            kill = toks[0].lower() in _SPICE_ANALYSES
        if not kill:
            out.extend(lines[i:j + 1])
            i = j + 1
            continue
        cmt = '// ' if lang == 'spectre' else '* '
        depth = logical.count('{') - logical.count('}')
        out.extend(cmt + ln for ln in lines[i:j + 1])
        i = j + 1
        while depth > 0 and i < len(lines):
            depth += lines[i].count('{') - lines[i].count('}')
            out.append(cmt + lines[i])
            i += 1
    return ''.join(out)


def _absolute_includes(text, base):
    def fix(m):
        p = m.group(2)
        if not p.startswith('/') and base is not None:
            p = str((base / p).resolve())
        return '%s"%s"' % (m.group(1), p)
    return re.sub(r'^(\s*(?:ahdl_)?include\s+)"([^"]+)"', fix, text, flags=re.M)


def prepare_deck(text, base=None, section=None, temp=None, at=None, keep_analyses=False):
    nl = SN.parse_netlist(text)
    body = text if keep_analyses else _comment_analyses(text, nl)
    body = _absolute_includes(body, base)
    if section:
        body = re.sub(r'^(\s*include\s+"[^"]+"\s+section\s*=\s*)\w+', r'\g<1>' + section,
                      body, flags=re.M)
    extra = ['', '// aspice opcheck']
    if temp is not None:
        extra.append('aspice_opck_temp options temp=%g' % temp)
    extra += ['aspice_opck_dc dc', '%s info what=oppoint where=rawfile' % DCINFO]
    if at:
        stop = max(at) * 1.001
        extra += ['%s info what=oppoint where=rawfile' % TINFO,
                  'aspice_opck_tran tran stop=%g infotimes=[%s] infoname=%s' % (
                      stop, ' '.join('%g' % t for t in at), TINFO)]
    langs = re.findall(r'^\s*simulator\s+lang\s*=\s*(\w+)', body, flags=re.M | re.I)
    if langs and langs[-1].lower() == 'spice':
        extra.insert(1, 'simulator lang=spectre')
    return body.rstrip('\n') + '\n' + '\n'.join(extra) + '\n', nl


def corner_name(sec, temp, text):
    """Label for one opcheck point. A deck that already says section=fs is not 'nominal'."""
    if not sec:
        m = re.search(r'^\s*include\s+"[^"]+"\s+section\s*=\s*(\w+)', text, re.M | re.I)
        sec = m.group(1) if m else None
    return '/'.join(x for x in (sec, None if temp is None else '%gC' % temp) if x) or 'nominal'


def audit(netlist, rules=None, sections=(None,), temps=(None,), at=None, workdir=None,
          keep_analyses=False):
    """Run the oppoint audit. ``netlist``: path or text of a Spectre deck (an ADE
    input.scs works). Returns a dict: points -> devices + findings, and ok."""
    rules = rules or Rules()
    text, src = CD._netlist_text(netlist)
    base = src.parent if src is not None else None
    report = {'points': [], 'findings': [], 'ok': True}
    for sec in sections:
        for t in temps:
            deck, nl = prepare_deck(text, base, sec, t, at, keep_analyses)
            vdd = rules.vdd or supply_voltage(nl)
            r = CD.run_spectre(deck, workdir=workdir, timeout=3600)
            corner = corner_name(sec, t, text)
            if not r.ok:
                report['ok'] = False
                report['findings'].append(asdict(Finding(
                    'FAIL', 'spectre', '-', '; '.join(e.replace('\n', ' ')[:300]
                                                      for e in r.errors[:3]) or r.log_path,
                    corner)))
                continue
            flat = flatten(nl)
            infos = [(DCINFO, 'dc')]
            if at:
                for k, tt in enumerate(at):
                    infos.append(('aspice_opck_tran-%03d_%s' % (k, TINFO), 't=%g' % tt))
            for name, label in infos:
                d = r.analyses.get(name)
                if d is None or not getattr(d, 'structs', None):
                    continue
                point = corner + ' ' + label
                devs = match_devices(flat, d.structs)
                vdd_eff = vdd or max([abs(x.f('vgs', 0.0)) for x in devs.values()] + [0]) or None
                fnd = check_devices(devs, rules, vdd_eff, point) + check_groups(devs, rules, point)
                report['points'].append({'point': point, 'vdd': vdd_eff, 'workdir': r.workdir,
                                         'devices': [_row(x) for x in sorted(
                                             devs.values(), key=lambda x: x.path)]})
                report['findings'] += [asdict(f) for f in fnd]
    report['ok'] = report['ok'] and not any(f['level'] == 'FAIL' for f in report['findings'])
    return report


def _sz(d):
    return 'w=%s l=%s nf=%s m=%s' % tuple(d.params.get(k, '1' if k in ('nf', 'm') else '?')
                                         for k in ('w', 'l', 'nf', 'm'))


def _row(d):
    return {'device': d.path, 'master': d.master, 'role': d.role, 'region': d.region,
            'pol': d.pol, 'id_uA': 1e6 * abs(d.f('ids', 0.0)), 'vgs': d.f('vgs'),
            'vth': d.f('vth'), 'vov': abs(d.f('vgs')) - abs(d.f('vth')), 'vds': d.f('vds'),
            'vdsat': d.f('vdsat'), 'headroom': d.headroom, 'gm_id': d.gm_id, 'gm_ro': d.gain,
            'nets': {'d': d.d, 'g': d.g, 's': d.s, 'b': d.b}}


def format_report(rep, show_devices=True):
    lines = []
    for p in rep['points']:
        lines.append('== %s  (VDD=%s V)' % (p['point'], '%.3g' % p['vdd'] if p['vdd'] else '?'))
        if show_devices:
            lines.append('%-22s %-6s %-12s %9s %8s %8s %8s %9s %6s %6s' % (
                'device', 'role', 'region', 'Id(uA)', 'Vov', 'Vds', 'Vdsat', 'headroom',
                'gm/Id', 'gm*ro'))
            for r in p['devices']:
                lines.append('%-22s %-6s %-12s %9.2f %8.3f %8.3f %8.3f %9.3f %6.1f %6.1f' % (
                    r['device'][-22:], r['role'][:6], r['region'], r['id_uA'], r['vov'],
                    r['vds'], r['vdsat'], r['headroom'], r['gm_id'],
                    min(r['gm_ro'], 9999)))
    order = {'FAIL': 0, 'WARN': 1, 'INFO': 2}
    fs = sorted(rep['findings'], key=lambda f: (order[f['level']], f['point'], f['device']))
    n = {k: sum(f['level'] == k for f in fs) for k in order}
    lines.append('')
    for f in fs:
        if f['level'] == 'INFO' and f['rule'] == 'switch':
            continue
        lines.append('%-4s %-8s %-28s %s  [%s]' % (f['level'], f['rule'], f['device'][-28:],
                                                    f['msg'], f['point']))
    lines.append('%s: %d FAIL, %d WARN, %d switches/logic' % (
        'PASS' if rep['ok'] else 'FAIL', n['FAIL'], n['WARN'],
        sum(f['rule'] == 'switch' for f in fs)))
    return '\n'.join(lines)


def to_json(rep):
    return json.dumps(rep, indent=1, default=lambda o: None if o != o else str(o))
