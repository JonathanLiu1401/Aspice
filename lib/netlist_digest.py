"""Compact digest and progressive-disclosure views over a Spectre/ADE netlist IR.

An LLM agent must not read a raw ADE netlist (tens of thousands of lines).
It reads outline() or summary(), then pulls fragments via show_*, grep,
net_report, and find_dangling.

This module is duck-typed against the fixed IR contract (Netlist, Instance,
Subckt, Analysis, Include). It does not import spectre_netlist at module
level: that module may be missing or mid-write by another agent.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict

# Output discipline
MAX_WIDTH = 100
MAX_OUTLINE_SUBCKTS = 32
MAX_SUMMARY_LINES = 148
MAX_PARAM_ROWS = 40
MAX_OPTION_ROWS = 20
MAX_SAVE_ITEMS = 24
MAX_INCLUDE_ROWS = 20
MAX_ANALYSIS_ROWS = 24

# Options ADE writes into every netlist with these exact values. They carry no
# design information, so the digest folds them into one line and lists only
# options that differ.
ADE_DEFAULT_OPTIONS = {
    "psfversion": '"1.4.0"', "reltol": "1e-3", "vabstol": "1e-6", "iabstol": "1e-12",
    "temp": "27", "tnom": "27", "scalem": "1.0", "scale": "1.0", "gmin": "1e-12",
    "rforce": "1", "maxnotes": "5", "maxwarns": "5", "digits": "5", "cols": "80",
    "pivrel": "1e-3", "checklimitdest": "psf", "save": "allpub",
}
MAX_DEVICE_ROWS = 40
MAX_NET_ROWS = 12
MAX_MASTERS_IN_OUTLINE = 6
GREP_MATCH_CAP = 40
EXPR_TEXT_CAP = 3
HIGH_FANOUT_MIN = 3

# Analysis keys worth showing in the compact view
_ANALYSIS_KEYS = (
    "param",
    "start",
    "stop",
    "step",
    "dec",
    "lin",
    "probe",
    "oprobe",
    "iprobe",
    "donoise",
)

_SUPPLY_NETS = frozenset(
    {
        "0",
        "gnd",
        "gnd!",
        "vss",
        "vdd",
        "vcc",
        "vee",
        "avdd",
        "avss",
        "dvdd",
        "dvss",
        "vddd",
        "vssd",
        "vdda",
        "vssa",
        "vpwr",
        "vgnd",
    }
)

_SKIP_NODES = frozenset({"", "*", "None"})

# Spectre scale suffixes. M (mega) and m (milli) are case-sensitive.
_SCALE = {
    "T": 1e12,
    "t": 1e12,
    "G": 1e9,
    "g": 1e9,
    "MEG": 1e6,
    "Meg": 1e6,
    "meg": 1e6,
    "X": 1e6,
    "x": 1e6,
    "M": 1e6,
    "K": 1e3,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "U": 1e-6,
    "n": 1e-9,
    "N": 1e-9,
    "p": 1e-12,
    "P": 1e-12,
    "f": 1e-15,
    "F": 1e-15,
    "a": 1e-18,
    "A": 1e-18,
}

_NUM_RE = re.compile(
    r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([A-Za-z]+)?$"
)

_SUBCKT_START_RE = re.compile(
    r"^(?:subckt|\.subckt)\s+(\S+)", re.IGNORECASE
)
_SUBCKT_END_RE = re.compile(
    r"^(?:ends|\.ends)(?:\s+(\S+))?", re.IGNORECASE
)


def spectre_number(text) -> float | None:
    """Parse a Spectre numeric literal. Return None for expressions."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    m = _NUM_RE.match(s)
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    suffix = m.group(2)
    if not suffix:
        return value
    scale = _SCALE.get(suffix)
    if scale is None:
        return None
    return value * scale


def outline(nl) -> str:
    """Cheapest view: subckt hierarchy rollup plus one TOP line."""
    recs = []
    for name, sub in _subckt_map(nl).items():
        insts = _subckt_instances(sub)
        n_ports = len(_as_list(getattr(sub, "ports", None)))
        mix = _format_master_mix(_master_counts(insts))
        recs.append((len(insts), str(name), n_ports, mix))
    recs.sort(key=lambda t: (-t[0], t[1]))
    hidden = 0
    if len(recs) > MAX_OUTLINE_SUBCKTS:
        hidden = len(recs) - MAX_OUTLINE_SUBCKTS
        recs = recs[:MAX_OUTLINE_SUBCKTS]
    name_w = min(24, max((len(r[1]) for r in recs), default=1))
    lines = []
    for n_inst, name, n_ports, mix in recs:
        body = f"{name:<{name_w}} ({n_ports} ports): {n_inst} inst"
        if mix:
            body += f"  [{mix}]"
        lines.append(_fit(body))
    if hidden:
        lines.append(_fit(f"+{hidden} more subckts"))
    top_n = len(_top_instances(nl))
    n_an = len(_as_list(getattr(nl, "analyses", None)))
    n_inc = len(_as_list(getattr(nl, "includes", None)))
    n_par = len(_as_dict(getattr(nl, "parameters", None)))
    lines.append(
        _fit(
            f"TOP: {top_n} inst, {n_an} analyses, {n_inc} include, {n_par} parameters"
        )
    )
    return "\n".join(lines)


def summary(nl) -> str:
    """outline plus params, analyses, includes, options, saves, devices, nets."""
    sections = [outline(nl)]

    params = _as_dict(getattr(nl, "parameters", None))
    sections.append("PARAMETERS")
    if not params:
        sections.append("  (none)")
    else:
        items = sorted(params.items(), key=lambda kv: kv[0])
        extra = 0
        if len(items) > MAX_PARAM_ROWS:
            extra = len(items) - MAX_PARAM_ROWS
            items = items[:MAX_PARAM_ROWS]
        name_w = min(24, max((len(k) for k, _ in items), default=1))
        for key, val in items:
            sections.append(_fit(f"  {key:<{name_w}} = {_short(val, 70)}"))
        if extra:
            sections.append(_fit(f"  +{extra} more parameters"))

    analyses = _as_list(getattr(nl, "analyses", None))
    sections.append("ANALYSES")
    if not analyses:
        sections.append("  (none)")
    else:
        # ADE emits up to eight output-only `info` statements; one line covers them.
        infos = [an for an in analyses if str(getattr(an, "type", "")).lower() == "info"]
        analyses = [an for an in analyses if an not in infos]
        shown = analyses[:MAX_ANALYSIS_ROWS]
        extra = max(0, len(analyses) - len(shown))
        if infos:
            whats = [str(_as_dict(getattr(an, "params", None)).get("what", "?")) for an in infos]
            sections.append(_fit("  info x%d  what=%s" % (len(infos), ",".join(whats))))
        for an in shown:
            name = str(getattr(an, "name", "?"))
            typ = str(getattr(an, "type", "?"))
            kv = _as_dict(getattr(an, "params", None))
            bits = []
            for key in _ANALYSIS_KEYS:
                if key in kv:
                    bits.append(f"{key}={_short(kv[key], 16)}")
            leftover = [k for k in sorted(kv) if k not in _ANALYSIS_KEYS]
            for key in leftover[:3]:
                bits.append(f"{key}={_short(kv[key], 12)}")
            extra_kv = max(0, len(leftover) - 3)
            if extra_kv:
                bits.append(f"+{extra_kv} more")
            tail = " ".join(bits)
            sections.append(_fit(f"  {name}  {typ}  {tail}".rstrip()))
        if extra:
            sections.append(_fit(f"  +{extra} more analyses"))

    includes = _as_list(getattr(nl, "includes", None))
    sections.append("INCLUDES")
    if not includes:
        sections.append("  (none)")
    else:
        extra = 0
        shown = includes[:MAX_INCLUDE_ROWS]
        extra = max(0, len(includes) - len(shown))
        for inc in shown:
            path = str(getattr(inc, "path", ""))
            section = getattr(inc, "section", None)
            kind = str(getattr(inc, "kind", "include") or "include")
            tail = f" section={section}" if section else ""
            room = MAX_WIDTH - len(kind) - len(tail) - 3
            if len(path) > room > 10:   # keep the file name and the corner
                path = "..." + path[-(room - 3):]
            sections.append(_fit(f"  {kind} {path}{tail}"))
        if extra:
            sections.append(_fit(f"  +{extra} more includes"))

    options = _as_dict(getattr(nl, "options", None))
    sections.append("OPTIONS")
    if not options:
        sections.append("  (none)")
    else:
        items = sorted(options.items(), key=lambda kv: kv[0])
        n_default = sum(1 for k, v in items if ADE_DEFAULT_OPTIONS.get(k) == str(v))
        items = [(k, v) for k, v in items if ADE_DEFAULT_OPTIONS.get(k) != str(v)]
        if n_default:
            sections.append(f"  ({n_default} ADE defaults)")
        extra = 0
        if len(items) > MAX_OPTION_ROWS:
            extra = len(items) - MAX_OPTION_ROWS
            items = items[:MAX_OPTION_ROWS]
        for key, val in items:
            sections.append(_fit(f"  {key}={_short(val, 70)}"))
        if extra:
            sections.append(_fit(f"  +{extra} more options"))

    saves = [str(s) for s in _as_list(getattr(nl, "saves", None))]
    sections.append("SAVES")
    if not saves:
        sections.append("  (none)")
    else:
        shown = saves[:MAX_SAVE_ITEMS]
        extra = max(0, len(saves) - len(shown))
        line = " "
        for item in shown:
            add = " " + item
            if len(line) + len(add) > MAX_WIDTH:
                sections.append(_fit(line))
                line = "  " + item
            else:
                line += add
        if line.strip():
            sections.append(_fit(line))
        if extra:
            sections.append(_fit(f"  +{extra} more saves"))

    sections.append("DEVICES")
    roll = device_rollup(nl)
    if not roll.strip():
        sections.append("  (none)")
    else:
        sections.extend(roll.splitlines())

    sections.append("NETS")
    net_block = _net_rollup(nl)
    if not net_block.strip():
        sections.append("  (none)")
    else:
        sections.extend(net_block.splitlines())

    lines = []
    for raw in sections:
        for ln in str(raw).splitlines() or [""]:
            lines.append(_fit(ln))
    extra = 0
    if len(lines) > MAX_SUMMARY_LINES:
        extra = len(lines) - MAX_SUMMARY_LINES
        lines = lines[:MAX_SUMMARY_LINES]
        lines.append(_fit(f"+{extra} more summary lines suppressed"))
    return "\n".join(lines)


def device_rollup(nl, scope=None) -> str:
    """Group instances by (master, param-name signature). Collapse counts and ranges."""
    groups = defaultdict(list)
    for _scope, inst in _iter_instances(nl, scope):
        master = str(getattr(inst, "master", "") or "")
        params = _as_dict(getattr(inst, "params", None))
        sig = tuple(sorted(str(k) for k in params))
        groups[(master, sig)].append(params)

    keys = sorted(groups, key=lambda k: (-len(groups[k]), k[0], k[1]))
    extra = 0
    if len(keys) > MAX_DEVICE_ROWS:
        extra = len(keys) - MAX_DEVICE_ROWS
        keys = keys[:MAX_DEVICE_ROWS]

    rows = []
    for master, sig in keys:
        plist = groups[(master, sig)]
        count = len(plist)
        bits = []
        for pname in sig:
            values = [str(p.get(pname, "")) for p in plist]
            bits.append(f"{pname}={_format_param_span(values)}")
        rows.append((master, count, " ".join(bits)))

    if not rows:
        return ""

    m_w = min(20, max(len(r[0]) for r in rows))
    c_w = max(len(str(r[1])) for r in rows)
    lines = []
    for master, count, rest in rows:
        line = f"{master:<{m_w}}  x{count:<{c_w}}  {rest}".rstrip()
        lines.append(_fit(line))
    if extra:
        lines.append(_fit(f"+{extra} more device groups"))
    return "\n".join(lines)


def net_report(nl, net) -> str:
    """Every instance terminal attached to one net, with terminal index."""
    query = str(net)
    want_scope = None
    want_net = query
    if "." in query:
        left, _, right = query.partition(".")
        if right:
            want_scope, want_net = left, right

    hits = []
    for scope, inst in _iter_instances(nl, None):
        nodes = _as_list(getattr(inst, "nodes", None))
        for idx, node in enumerate(nodes):
            node_s = str(node)
            if want_scope is not None:
                if scope != want_scope or node_s != want_net:
                    continue
            elif node_s != query:
                continue
            hits.append(
                (
                    scope,
                    str(getattr(inst, "name", "?")),
                    idx,
                    str(getattr(inst, "master", "")),
                    getattr(inst, "line_no", ""),
                )
            )

    title = f"net {query}  ({len(hits)} terminals)"
    if not hits:
        return _fit(title + "  (none)")
    s_w = min(16, max(len(h[0]) for h in hits))
    n_w = min(20, max(len(h[1]) for h in hits))
    lines = [_fit(title)]
    for scope, name, idx, master, line_no in hits:
        loc = f"L{line_no}" if line_no != "" else ""
        lines.append(
            _fit(f"  {scope:<{s_w}}  {name:<{n_w}}  term {idx}  {master}  {loc}".rstrip())
        )
    return "\n".join(lines)


def show_subckt(nl, name) -> str:
    """Raw text of one subckt, with original line numbers."""
    raw = _raw_lines(nl)
    want = str(name)
    sub = _subckt_map(nl).get(want)
    start = None
    if sub is not None:
        ln = getattr(sub, "line_no", None)
        if isinstance(ln, int) and ln >= 1 and ln <= len(raw):
            start = ln
    if start is None:
        start = _find_subckt_start(raw, want)
    if start is None:
        return _fit(f"subckt {want} not found")
    end = _find_subckt_end(raw, start, want)
    return show_lines(nl, start, end)


def show_lines(nl, start, end) -> str:
    """Raw slice with 1-based line numbers. Bounds are clamped."""
    raw = _raw_lines(nl)
    n = len(raw)
    if n == 0:
        return ""
    try:
        a = int(start)
    except (TypeError, ValueError):
        a = 1
    try:
        b = int(end)
    except (TypeError, ValueError):
        b = n
    a = max(1, min(a, n))
    b = max(1, min(b, n))
    if a > b:
        return ""
    width = len(str(n))
    lines = []
    for i in range(a, b + 1):
        text = _line_text(raw[i - 1])
        lines.append(_fit(f"{i:>{width}} {text}"))
    return "\n".join(lines)


def grep(nl, pattern, context=0) -> str:
    """Regex over raw_lines. Numbered matches, capped, with suppress count."""
    raw = _raw_lines(nl)
    if str(pattern) == "":
        return _fit("0 matches (empty pattern refused)")
    try:
        rx = re.compile(str(pattern))
    except re.error as exc:
        return _fit(f"invalid regex: {exc}")
    try:
        ctx = max(0, int(context))
    except (TypeError, ValueError):
        ctx = 0

    match_idxs = []
    for i, line in enumerate(raw):
        if rx.search(_line_text(line)):
            match_idxs.append(i)

    total = len(match_idxs)
    shown_idxs = match_idxs[:GREP_MATCH_CAP]
    suppressed = total - len(shown_idxs)

    if total == 0:
        return _fit("0 matches")

    emit = []
    last_printed = -10**9
    n = len(raw)
    width = len(str(max(n, 1)))
    for mi in shown_idxs:
        lo = max(0, mi - ctx)
        hi = min(n - 1, mi + ctx)
        if emit and lo > last_printed + 1:
            emit.append("--")
        for j in range(lo, hi + 1):
            if j <= last_printed:
                continue
            mark = ":" if j == mi else "-"
            emit.append(_fit(f"{j + 1:>{width}}{mark}{_line_text(raw[j])}"))
            last_printed = j

    header = f"{total} matches"
    if suppressed:
        header += f", {suppressed} suppressed (cap {GREP_MATCH_CAP})"
    return "\n".join([_fit(header)] + emit)


def find_dangling(nl) -> list[str]:
    """Nets that appear on exactly one terminal (floating-node candidates)."""
    conn = _connectivity(nl)
    ports_by_scope = _ports_by_scope(nl)
    dangling = []
    for scope, net in sorted(conn):
        if net in _SKIP_NODES:
            continue
        if net == "0":
            continue
        if net in ports_by_scope.get(scope, set()):
            continue
        if len(conn[(scope, net)]) == 1:
            dangling.append(_qualify(scope, net))
    return dangling


def diff_digest(nl_a, nl_b) -> str:
    """Digest-level diff: parameters, device counts, analyses."""
    lines = []

    pa = _as_dict(getattr(nl_a, "parameters", None))
    pb = _as_dict(getattr(nl_b, "parameters", None))
    keys = sorted(set(pa) | set(pb))
    p_lines = []
    for key in keys:
        va = pa.get(key)
        vb = pb.get(key)
        if va is None:
            p_lines.append(f"  + {key}={_short(vb, 60)}")
        elif vb is None:
            p_lines.append(f"  - {key}={_short(va, 60)}")
        elif str(va) != str(vb):
            p_lines.append(f"  {key}: {_short(va, 32)} -> {_short(vb, 32)}")
    lines.append("PARAMETERS")
    lines.extend(p_lines or ["  (unchanged)"])

    ga = _device_counts(nl_a)
    gb = _device_counts(nl_b)
    dkeys = sorted(set(ga) | set(gb), key=lambda k: (k[0], k[1]))
    d_lines = []
    for key in dkeys:
        ca = ga.get(key, 0)
        cb = gb.get(key, 0)
        if ca == cb:
            continue
        label = _group_label(key)
        d_lines.append(f"  {label}: x{ca} -> x{cb}")
    lines.append("DEVICES")
    lines.extend(d_lines or ["  (unchanged)"])

    aa = _analysis_map(nl_a)
    ab = _analysis_map(nl_b)
    a_lines = []
    for name in sorted(set(aa) | set(ab)):
        va = aa.get(name)
        vb = ab.get(name)
        if va is None:
            a_lines.append(f"  + {name} {vb[0]}")
        elif vb is None:
            a_lines.append(f"  - {name} {va[0]}")
        elif va != vb:
            a_lines.append(f"  {name}: {va[0]} -> {vb[0]}")
            if va[1] != vb[1]:
                a_lines.append(f"    params {va[1]} -> {vb[1]}")
    lines.append("ANALYSES")
    lines.extend(a_lines or ["  (unchanged)"])

    return "\n".join(_fit(x) for x in lines)


# --- internals -------------------------------------------------------------


def _as_list(val):
    if val is None:
        return []
    if isinstance(val, (list, tuple)):
        return list(val)
    return [val]


def _as_dict(val):
    if not val:
        return {}
    try:
        return dict(val)
    except (TypeError, ValueError):
        return {}


def _line_text(line) -> str:
    if line is None:
        return ""
    return str(line).rstrip("\r\n")


def _fit(s: str, width: int = MAX_WIDTH) -> str:
    s = str(s).replace("\t", " ")
    if len(s) <= width:
        return s
    if width <= 3:
        return s[:width]
    return s[: width - 3] + "..."


def _short(val, n: int) -> str:
    s = str(val)
    if len(s) <= n:
        return s
    if n <= 3:
        return s[:n]
    return s[: n - 3] + "..."


def _raw_lines(nl) -> list[str]:
    return [str(x) for x in _as_list(getattr(nl, "raw_lines", None))]


def _is_instance(obj) -> bool:
    return (
        obj is not None
        and hasattr(obj, "master")
        and hasattr(obj, "nodes")
        and hasattr(obj, "name")
        and hasattr(obj, "params")
        and not hasattr(obj, "ports")
    )


def _is_subckt(obj) -> bool:
    return (
        obj is not None
        and hasattr(obj, "ports")
        and hasattr(obj, "instances")
        and hasattr(obj, "name")
    )


def _subckt_map(nl) -> dict:
    raw = getattr(nl, "subckts", None)
    if not raw:
        return {}
    try:
        return dict(raw)
    except (TypeError, ValueError):
        return {}


def _unique_insts(primary, extra) -> list:
    seen = set()
    out = []
    for src in (primary, extra):
        for obj in _as_list(src):
            if not _is_instance(obj):
                continue
            key = id(obj)
            if key in seen:
                continue
            seen.add(key)
            out.append(obj)
    return out


def _subckt_instances(sub) -> list:
    return _unique_insts(
        getattr(sub, "instances", None), getattr(sub, "statements", None)
    )


def _top_instances(nl) -> list:
    return _unique_insts(getattr(nl, "statements", None), None)


def _iter_instances(nl, scope=None):
    """Yield (scope_name, instance). scope=None means all scopes."""
    if scope is None:
        for inst in _top_instances(nl):
            yield "TOP", inst
        mapped = _subckt_map(nl)
        for name in sorted(mapped):
            for inst in _subckt_instances(mapped[name]):
                yield str(name), inst
        return
    if scope in ("TOP", ""):
        for inst in _top_instances(nl):
            yield "TOP", inst
        return
    sub = _subckt_map(nl).get(scope)
    if sub is None:
        return
    for inst in _subckt_instances(sub):
        yield str(scope), inst


def _master_counts(insts) -> dict[str, int]:
    counts = defaultdict(int)
    for inst in insts:
        counts[str(getattr(inst, "master", "") or "?")] += 1
    return dict(counts)


def _format_master_mix(mix: dict[str, int]) -> str:
    if not mix:
        return ""
    items = sorted(mix.items(), key=lambda kv: (-kv[1], kv[0]))
    extra = 0
    if len(items) > MAX_MASTERS_IN_OUTLINE:
        extra = len(items) - MAX_MASTERS_IN_OUTLINE
        items = items[:MAX_MASTERS_IN_OUTLINE]
    txt = ", ".join(f"{m} x{c}" for m, c in items)
    if extra:
        txt += f", +{extra} more"
    return txt


def _format_param_span(values: list[str]) -> str:
    parsed = [(v, spectre_number(v)) for v in values]
    nums = [(v, n) for v, n in parsed if n is not None]
    exprs = [v for v, n in parsed if n is None]
    if nums and not exprs:
        lo = min(nums, key=lambda t: t[1])
        hi = max(nums, key=lambda t: t[1])
        if lo[1] == hi[1]:
            texts = [v for v, n in nums if n == lo[1]]
            return texts[0]
        return f"[{lo[0]}..{hi[0]}]"
    uniq = []
    seen = set()
    for v in values:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return _cap_texts(uniq)


def _cap_texts(texts: list[str]) -> str:
    extra = 0
    shown = texts
    if len(shown) > EXPR_TEXT_CAP:
        extra = len(shown) - EXPR_TEXT_CAP
        shown = shown[:EXPR_TEXT_CAP]
    body = ",".join(_short(t, 24) for t in shown)
    if extra:
        body += f" +{extra} more"
    return body


def _connectivity(nl) -> dict:
    conn = defaultdict(list)
    for scope, inst in _iter_instances(nl, None):
        name = str(getattr(inst, "name", "?"))
        master = str(getattr(inst, "master", ""))
        nodes = _as_list(getattr(inst, "nodes", None))
        for idx, node in enumerate(nodes):
            node_s = str(node)
            if node_s in _SKIP_NODES:
                continue
            conn[(scope, node_s)].append((name, idx, master))
    return conn


def _ports_by_scope(nl) -> dict[str, set]:
    out = {"TOP": set()}
    for name, sub in _subckt_map(nl).items():
        out[str(name)] = {str(p) for p in _as_list(getattr(sub, "ports", None))}
    return out


def _qualify(scope: str, net: str) -> str:
    if scope == "TOP":
        return net
    return f"{scope}.{net}"


def _is_supply(net: str) -> bool:
    return net.lower() in _SUPPLY_NETS or net == "0"


def _net_rollup(nl) -> str:
    """Top-level high-fanout nets plus supplies."""
    conn = _connectivity(nl)
    top = {}
    supplies = {}
    for (scope, net), hits in conn.items():
        if scope != "TOP":
            continue
        fan = len(hits)
        if _is_supply(net):
            supplies[net] = fan
        else:
            top[net] = fan

    rows = []
    for net, fan in sorted(supplies.items(), key=lambda kv: (-kv[1], kv[0])):
        rows.append((net, fan, "supply"))
    hot = [(n, f) for n, f in top.items() if f >= HIGH_FANOUT_MIN]
    hot.sort(key=lambda t: (-t[1], t[0]))
    room = max(0, MAX_NET_ROWS - len(rows))
    extra = max(0, len(hot) - room)
    for net, fan in hot[:room]:
        rows.append((net, fan, ""))
    if not rows:
        return ""
    n_w = min(20, max(len(r[0]) for r in rows))
    lines = []
    for net, fan, tag in rows:
        extra_tag = f"  {tag}" if tag else ""
        lines.append(_fit(f"  {net:<{n_w}}  fanout={fan}{extra_tag}"))
    if extra:
        lines.append(_fit(f"  +{extra} more high-fanout nets"))
    return "\n".join(lines)


def _device_counts(nl) -> dict:
    counts = defaultdict(int)
    for _scope, inst in _iter_instances(nl, None):
        master = str(getattr(inst, "master", "") or "")
        params = _as_dict(getattr(inst, "params", None))
        sig = tuple(sorted(str(k) for k in params))
        counts[(master, sig)] += 1
    return dict(counts)


def _group_label(key) -> str:
    master, sig = key
    if sig:
        return f"{master}({','.join(sig)})"
    return master


def _analysis_map(nl) -> dict:
    out = {}
    for an in _as_list(getattr(nl, "analyses", None)):
        name = str(getattr(an, "name", ""))
        typ = str(getattr(an, "type", ""))
        params = _as_dict(getattr(an, "params", None))
        frozen = tuple(sorted((str(k), str(v)) for k, v in params.items()))
        out[name] = (typ, frozen)
    return out


def _find_subckt_start(raw: list[str], name: str) -> int | None:
    want = name.lower()
    for i, line in enumerate(raw):
        m = _SUBCKT_START_RE.match(_line_text(line).lstrip())
        if m and m.group(1).lower() == want:
            return i + 1
    return None


def _find_subckt_end(raw: list[str], start_1: int, name: str) -> int:
    depth = 0
    want = name.lower()
    for i in range(start_1 - 1, len(raw)):
        body = _line_text(raw[i]).lstrip()
        if _SUBCKT_START_RE.match(body):
            depth += 1
            continue
        em = _SUBCKT_END_RE.match(body)
        if em:
            depth = max(0, depth - 1)
            end_name = em.group(1)
            if depth == 0:
                if end_name is None or end_name.lower() == want:
                    return i + 1
    return min(len(raw), start_1)


def est_tokens(text: str) -> int:
    """ceil(chars/4) token estimate used by the benchmark."""
    return int(math.ceil(len(text) / 4.0)) if text else 0
