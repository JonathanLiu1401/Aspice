"""Compact views of schematic and extracted netlists.

Print these. Do not open the file. An extracted netlist is mostly parasitic R and C;
a schematic netlist from ADE is mostly hierarchy the digest already knows how to fold.
"""
from __future__ import annotations


def pex_brief(text: str, top: int = 12) -> str:
    """Size, device counts, and the nets with the most parasitic C. Not the netlist.

    Sub-nodes ``NET#3`` count toward ``NET``. Series R is the largest segment on that
    net, not the sum of every segment in the mesh (a supply mesh of 1000 segments is not
    a 40 kohm resistor). Kept here so a brief does not import the Cadence host.
    """
    cap: dict[str, float] = {}
    res_n: dict[str, int] = {}
    res_max: dict[str, float] = {}
    n_r = n_c = n_m = n_x = 0
    mos: dict[str, int] = {}
    inst: dict[str, int] = {}
    subckts: list[str] = []
    n_lines = text.count('\n') + (0 if text.endswith('\n') or not text else 1)
    # Quantus continuation lines start with '+'.
    for raw in text.replace('\n+', ' ').splitlines():
        line = raw.strip()
        if not line or line[0] in '*#/' or line.startswith('//'):
            continue
        low = line.lower()
        if low.startswith('subckt ') or low.startswith('.subckt '):
            parts = line.split()
            if len(parts) > 1:
                subckts.append(parts[1])
        kind = line[0]
        toks = line.split()
        if kind in 'Rr':
            n_r += 1
        elif kind in 'Cc':
            n_c += 1
        elif kind in 'Mm':
            n_m += 1
            model = _model_before_params(toks)
            if model:
                mos[model] = mos.get(model, 0) + 1
        elif kind in 'Xx':
            n_x += 1
            model = _model_before_params(toks)
            if model:
                inst[model] = inst.get(model, 0) + 1
        if len(toks) < 4 or toks[0][0] not in 'RrCc':
            continue
        try:
            v = float(toks[3])
        except ValueError:
            continue
        a, b = (n.split('#')[0] for n in toks[1:3])
        if toks[0][0] in 'Cc':
            for n in {a, b}:
                cap[n] = cap.get(n, 0.0) + v * 1e15
        elif a == b:
            res_n[a] = res_n.get(a, 0) + 1
            if v > res_max.get(a, 0.0):
                res_max[a] = v
    rows = sorted(
        ({'net': n, 'cap_ff': cap.get(n, 0.0), 'segs': res_n.get(n, 0), 'max_r': res_max.get(n, 0.0)}
         for n in set(cap) | set(res_n)),
        key=lambda r: -r['cap_ff'])
    lines = [
        'pex: %d lines, %d bytes' % (n_lines, len(text.encode('utf-8', 'replace'))),
        'counts: %d R, %d C, %d MOS, %d instances' % (n_r, n_c, n_m, n_x),
    ]
    if mos:
        lines.append('mos: ' + _mix(mos))
    if inst:
        lines.append('devices: ' + _mix(inst))
    if subckts:
        shown = ', '.join(subckts[:8])
        extra = '' if len(subckts) <= 8 else ' (+%d)' % (len(subckts) - 8)
        lines.append('subckts: %s%s' % (shown, extra))
    lines.append('worst nets (parasitic C; R is the largest segment, not the mesh sum):')
    if not rows:
        lines.append('  (none)')
    for r in rows[:top]:
        lines.append('  %-24s %8.2f fF   %5d segs  max %8.1f ohm' % (
            r['net'], r['cap_ff'], r['segs'], r['max_r']))
    hidden = len(rows) - min(top, len(rows))
    if hidden > 0:
        lines.append('  +%d more nets' % hidden)
    return '\n'.join(lines)


def _model_before_params(toks: list[str]) -> str | None:
    """SPICE puts the model immediately before the first param=value (else the last token)."""
    for i, t in enumerate(toks):
        if '=' in t and i:
            prev = toks[i - 1]
            if '#' not in prev and not prev[0].isdigit():
                return prev
            return None
    last = toks[-1] if toks else ''
    if last and '#' not in last and '=' not in last and not last[0].isdigit():
        return last
    return None


def _mix(counts: dict[str, int], limit: int = 8) -> str:
    items = sorted(counts.items(), key=lambda kv: -kv[1])
    shown = ', '.join('%s x%d' % kv for kv in items[:limit])
    extra = '' if len(items) <= limit else ' (+%d)' % (len(items) - limit)
    return shown + extra


def scs_brief(text: str) -> str:
    """Outline and device rollup of a Spectre deck. Not the deck."""
    import spectre_netlist as SN
    import netlist_digest as ND
    nl = SN.parse_netlist(text)
    n_lines = text.count('\n') + (0 if text.endswith('\n') or not text else 1)
    body = ND.outline(nl) + '\n' + ND.device_rollup(nl)
    return 'scs: %d lines, %d bytes\n%s' % (n_lines, len(text.encode('utf-8', 'replace')), body)
