"""Self-contained tests for lib/netlist_digest.py.

Uses local stub IR classes matching the fixed duck-typed contract so this
file passes with or without lib/spectre_netlist.py.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import netlist_digest as nd  # noqa: E402


@dataclass
class Instance:
    name: str
    nodes: list
    master: str
    params: dict
    line_no: int = 1
    raw: str = ""


@dataclass
class Subckt:
    name: str
    ports: list
    params: dict = field(default_factory=dict)
    instances: list = field(default_factory=list)
    statements: list = field(default_factory=list)
    line_no: int = 1


@dataclass
class Analysis:
    name: str
    type: str
    params: dict = field(default_factory=dict)
    line_no: int = 1
    raw: str = ""


@dataclass
class Include:
    path: str
    section: str | None = None
    kind: str = "include"


@dataclass
class Netlist:
    title: str = ""
    language: str = "spectre"
    statements: list = field(default_factory=list)
    subckts: dict = field(default_factory=dict)
    parameters: dict = field(default_factory=dict)
    includes: list = field(default_factory=list)
    analyses: list = field(default_factory=list)
    options: dict = field(default_factory=dict)
    saves: list = field(default_factory=list)
    raw_lines: list = field(default_factory=list)


FAILED = 0
PASSED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILED, PASSED
    if cond:
        PASSED += 1
        print(f"PASS  {name}")
    else:
        FAILED += 1
        extra = f"  {detail}" if detail else ""
        print(f"FAIL  {name}{extra}")


def test_rollup_collapses_identical() -> None:
    insts = [
        Instance(f"M{i}", ["d", "g", "s", "b"], "nch", {"w": "1u", "l": "180n"})
        for i in range(50)
    ]
    nl = Netlist(statements=insts)
    text = nd.device_rollup(nl)
    nch_rows = [
        ln for ln in text.splitlines() if ln.split() and ln.split()[0] == "nch"
    ]
    check(
        "rollup collapses N identical devices to one row",
        len(nch_rows) == 1 and "x50" in nch_rows[0],
        repr(text),
    )


def test_numeric_ranges() -> None:
    widths = ["1u", "2u", "4u", "1u", "4u"]
    insts = [
        Instance(f"M{i}", ["d", "g", "0", "0"], "nch", {"w": w, "l": "180n", "m": str((i % 4) + 1)})
        for i, w in enumerate(widths)
    ]
    text = nd.device_rollup(Netlist(statements=insts))
    has_w = "[1u..4u]" in text
    has_l = "l=180n" in text
    has_m = "[1..4]" in text
    check(
        "numeric ranges are correct",
        has_w and has_l and has_m,
        repr(text),
    )


def test_expression_params_listed() -> None:
    exprs = ["W0*2", "W0*4", "{Wmin}", "W0*8", "nf"]
    insts = [
        Instance(f"M{i}", ["d", "g", "0", "0"], "nch", {"w": e, "l": "180n"})
        for i, e in enumerate(exprs)
    ]
    text = nd.device_rollup(Netlist(statements=insts))
    ranged = any(
        part.startswith("w=[") and ".." in part for part in text.replace("  ", " ").split()
    )
    listed = "W0*2" in text and "W0*4" in text and "{Wmin}" in text
    capped = "+2 more" in text
    check(
        "expression params are listed not ranged",
        (not ranged) and listed and capped,
        repr(text),
    )


def test_net_report_all_terminals() -> None:
    insts = [
        Instance("M0", ["out", "in", "0", "0"], "nch", {"w": "1u"}, line_no=10),
        Instance("M1", ["vdd", "out", "0", "0"], "pch", {"w": "2u"}, line_no=11),
        Instance("R0", ["out", "mid"], "res", {"r": "10k"}, line_no=12),
    ]
    amp = Subckt(
        "amp",
        ["in", "out"],
        instances=[
            Instance("M2", ["out", "bias", "0", "0"], "nch", {"w": "1u"}, line_no=20)
        ],
        line_no=18,
    )
    nl = Netlist(statements=insts, subckts={"amp": amp})
    text = nd.net_report(nl, "out")
    check(
        "net_report finds all terminals",
        (
            "term 0" in text
            and "term 1" in text
            and "M0" in text
            and "M1" in text
            and "R0" in text
            and "M2" in text
            and "(4 terminals)" in text
        ),
        repr(text),
    )


def test_find_dangling() -> None:
    insts = [
        Instance("M0", ["out", "in", "0", "0"], "nch", {"w": "1u"}),
        Instance("M1", ["vdd", "out", "0", "0"], "pch", {"w": "2u"}),
        Instance("C0", ["float_n", "0"], "cap", {"c": "10f"}),
    ]
    amp = Subckt(
        "amp",
        ["vin", "vout"],
        instances=[
            Instance("M2", ["vout", "vin", "0", "0"], "nch", {"w": "1u"}),
            Instance("M3", ["local_float", "0"], "cap", {"c": "1f"}),
        ],
    )
    nl = Netlist(statements=insts, subckts={"amp": amp})
    dangling = nd.find_dangling(nl)
    check(
        "find_dangling catches a single-terminal net",
        "float_n" in dangling,
        repr(dangling),
    )
    check(
        "find_dangling does NOT flag a 2-terminal net",
        "out" not in dangling and "vout" not in dangling,
        repr(dangling),
    )
    check(
        "find_dangling does NOT flag subckt ports",
        "amp.vin" not in dangling and "vin" not in dangling,
        repr(dangling),
    )
    check(
        "find_dangling flags scoped single-terminal net",
        "amp.local_float" in dangling,
        repr(dangling),
    )


def test_grep_caps() -> None:
    raw = [f"noise line {i}" for i in range(10)]
    raw += [f"HIT token {i}" for i in range(50)]
    raw += ["tail"]
    nl = Netlist(raw_lines=raw)
    text = nd.grep(nl, r"HIT token")
    shown_hits = sum(1 for ln in text.splitlines() if "HIT token" in ln)
    check(
        "grep caps output and says how many were suppressed",
        (
            "50 matches" in text
            and "suppressed" in text
            and str(50 - nd.GREP_MATCH_CAP) in text
            and shown_hits == nd.GREP_MATCH_CAP
        ),
        repr(text[:400]),
    )


def test_show_lines_bounds() -> None:
    nl = Netlist(raw_lines=[f"L{i}" for i in range(1, 11)])
    mid = nd.show_lines(nl, 2, 4)
    mid_lines = [ln for ln in mid.splitlines() if ln.strip()]
    check(
        "show_lines respects the requested window",
        len(mid_lines) == 3 and "L2" in mid and "L4" in mid and "L1" not in mid and "L5" not in mid,
        repr(mid),
    )
    clamped = nd.show_lines(nl, -20, 999)
    check(
        "show_lines clamps out-of-range bounds",
        "L1" in clamped and "L10" in clamped and len(clamped.splitlines()) == 10,
        repr(clamped),
    )
    empty = nd.show_lines(nl, 8, 3)
    check("show_lines start>end is empty", empty == "", repr(empty))


def test_diff_digest_param() -> None:
    a = Netlist(parameters={"Lmin": "180n", "Wmin": "1u"})
    b = Netlist(parameters={"Lmin": "200n", "Wmin": "1u"})
    text = nd.diff_digest(a, b)
    check(
        "diff_digest reports a changed parameter",
        "Lmin" in text and "180n" in text and "200n" in text and "->" in text,
        repr(text),
    )


def test_spectre_number() -> None:
    check("spectre_number 1u", math.isclose(nd.spectre_number("1u"), 1e-6, rel_tol=0.0, abs_tol=1e-18))
    check("spectre_number 180n", math.isclose(nd.spectre_number("180n"), 180e-9, rel_tol=0.0, abs_tol=1e-20))
    check("spectre_number 1G", math.isclose(nd.spectre_number("1G"), 1e9, rel_tol=0.0, abs_tol=1e-6))
    check("spectre_number 1M mega", math.isclose(nd.spectre_number("1M"), 1e6, rel_tol=0.0, abs_tol=1e-6))
    check("spectre_number 1m milli", math.isclose(nd.spectre_number("1m"), 1e-3, rel_tol=0.0, abs_tol=1e-15))
    check("spectre_number 2*W is None", nd.spectre_number("2*W") is None)
    check("spectre_number {Lmin} is None", nd.spectre_number("{Lmin}") is None)


def test_outline_and_width() -> None:
    amp = Subckt(
        "amp",
        ["in", "out", "vdd"],
        instances=[
            Instance("M0", ["out", "in", "0", "0"], "nch", {"w": "1u"}),
            Instance("M1", ["vdd", "out", "0", "0"], "pch", {"w": "2u"}),
        ],
    )
    bias = Subckt(
        "bias",
        ["nbias", "vdd"],
        instances=[
            Instance("M2", ["nbias", "nbias", "0", "0"], "nch", {"w": "1u"}),
            Instance("M3", ["nbias", "nbias", "0", "0"], "nch", {"w": "1u"}),
            Instance("M4", ["nbias", "nbias", "0", "0"], "nch", {"w": "1u"}),
            Instance("R0", ["vdd", "nbias"], "res", {"r": "10k"}),
        ],
    )
    nl = Netlist(
        statements=[Instance("X0", ["in", "out", "vdd"], "amp", {})],
        subckts={"amp": amp, "bias": bias},
        parameters={"Lmin": "180n", "Wmin": "1u", "Wmax": "4u", "Ibias": "10u"},
        includes=[Include("models/tt.scs", "tt")],
        analyses=[Analysis("dc1", "dc", {"start": "0", "stop": "1.8"})],
        options={"save": "allpub"},
        saves=["out"],
        raw_lines=["// header"],
    )
    text = nd.outline(nl)
    check(
        "outline names subckts with port and inst counts",
        "amp" in text and "3 ports" in text and "bias" in text and "TOP:" in text,
        repr(text),
    )
    summ = nd.summary(nl)
    too_wide = [ln for ln in summ.splitlines() if len(ln) > 100]
    check("summary lines stay within 100 chars", not too_wide, repr(too_wide[:3]))
    check("summary includes device rollup", "nch" in summ and "DEVICES" in summ, summ[:400])


def test_show_subckt_and_grep_invalid() -> None:
    raw = [
        "simulator lang=spectre",
        "subckt amp in out vdd",
        "M0 (out in 0 0) nch w=1u l=180n",
        "ends amp",
        "X0 (vin vout vdd) amp",
    ]
    amp = Subckt("amp", ["in", "out", "vdd"], instances=[], line_no=2)
    nl = Netlist(subckts={"amp": amp}, raw_lines=raw)
    text = nd.show_subckt(nl, "amp")
    check(
        "show_subckt returns numbered raw slice",
        "subckt amp" in text and "ends amp" in text and "M0" in text,
        repr(text),
    )
    missing = nd.show_subckt(nl, "nope")
    check("show_subckt missing name", "not found" in missing, repr(missing))
    bad = nd.grep(nl, "[unterminated")
    check("grep invalid regex is safe", "invalid regex" in bad, repr(bad))
    empty = nd.grep(nl, "")
    check(
        "grep empty pattern is refused",
        "empty pattern refused" in empty and "subckt" not in empty,
        repr(empty),
    )


def test_scope_and_mixed_and_outline_cap() -> None:
    amp_insts = [
        Instance(f"A{i}", ["o", "i", "0", "0"], "nch", {"w": "1u"}) for i in range(5)
    ]
    top_insts = [
        Instance(f"T{i}", ["x", "y", "0", "0"], "pch", {"w": "2u"}) for i in range(3)
    ]
    nl = Netlist(
        statements=top_insts,
        subckts={"amp": Subckt("amp", ["i", "o"], instances=amp_insts)},
    )
    scoped = nd.device_rollup(nl, scope="amp")
    check(
        "device_rollup scope=amp excludes top instances",
        "nch" in scoped and "pch" not in scoped and "x5" in scoped,
        repr(scoped),
    )
    mixed = [
        Instance("M0", ["a", "b"], "nch", {"w": "1u"}),
        Instance("M1", ["a", "b"], "nch", {"w": "W0*2"}),
    ]
    mx = nd.device_rollup(Netlist(statements=mixed))
    check(
        "mixed numeric and expression is listed not ranged",
        "1u" in mx and "W0*2" in mx and "[1u.." not in mx,
        repr(mx),
    )
    many = {}
    for i in range(60):
        many[f"cell{i:02d}"] = Subckt(
            f"cell{i:02d}",
            ["a"],
            instances=[Instance("M0", ["a", "0"], "nch", {"w": "1u"})],
        )
    big = Netlist(subckts=many)
    ot = nd.outline(big)
    nlines = ot.count("\n") + 1
    check(
        "outline caps subckt rows under 40 lines",
        nlines <= 40 and "+28 more subckts" in ot,
        f"lines={nlines} text={ot!r}",
    )
    raw = [f"alpha {i}" for i in range(5)]
    raw[2] = "MATCH here"
    ctx = nd.grep(Netlist(raw_lines=raw), "MATCH", context=1)
    check(
        "grep context includes neighbors",
        "alpha 1" in ctx and "MATCH here" in ctx and "alpha 3" in ctx,
        repr(ctx),
    )


def test_empty_netlist() -> None:
    nl = Netlist()
    try:
        o = nd.outline(nl)
        s = nd.summary(nl)
        r = nd.device_rollup(nl)
        d = nd.find_dangling(nl)
        g = nd.grep(nl, "x")
        sl = nd.show_lines(nl, 1, 10)
        ok = True
    except Exception as exc:  # noqa: BLE001
        o = s = r = g = sl = ""
        d = []
        ok = False
        print(f"  empty-netlist exception: {exc}")
    check("empty netlist does not crash", ok and "TOP:" in o and "0 matches" in g)
    check("empty rollup and dangling", r == "" and d == [] and sl == "" and "PARAMETERS" in s)


def main() -> int:
    test_rollup_collapses_identical()
    test_numeric_ranges()
    test_expression_params_listed()
    test_net_report_all_terminals()
    test_find_dangling()
    test_grep_caps()
    test_show_lines_bounds()
    test_diff_digest_param()
    test_spectre_number()
    test_outline_and_width()
    test_show_subckt_and_grep_invalid()
    test_scope_and_mixed_and_outline_cap()
    test_empty_netlist()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
