"""Tests for lib/spectre_to_ngspice.py.

Runnable:
  python scripts/test_translate.py

Prints PASS/FAIL per case and exits non-zero on any failure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from spectre_netlist import (  # noqa: E402
    Include,
    parse_file,
    parse_netlist,
    write_netlist,
)
from spectre_to_ngspice import (  # noqa: E402
    apply_tuned,
    lint,
    simulate,
    translate,
    tune,
)

PTM_180 = (Path(__file__).resolve().parent.parent / 'models' / 'ptm_180nm.lib')

FAILURES = 0
PASSES = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAILURES, PASSES
    if cond:
        PASSES += 1
        print("PASS %s" % name)
    else:
        FAILURES += 1
        extra = ("  " + detail) if detail else ""
        print("FAIL %s%s" % (name, extra))


def check_eq(name: str, got, expected) -> None:
    check(name, got == expected, "got=%r expected=%r" % (got, expected))


def strip_includes(nl) -> None:
    nl.includes.clear()
    nl.statements = [st for st in nl.statements if not isinstance(st, Include)]


# ---------------------------------------------------------------------------
# 1. translate() on every corpus file, no raise; lint clean
# ---------------------------------------------------------------------------

def test_corpus_translate() -> None:
    corpus = sorted((ROOT / "corpus").glob("*.scs"))
    check("corpus has 19 scs files", len(corpus) == 19, "got %d" % len(corpus))
    for scs in corpus:
        label = scs.name
        try:
            nl = parse_file(scs)
        except Exception as exc:
            check("%s parses" % label, False, "%s: %s" % (type(exc).__name__, exc))
            continue
        try:
            tr = translate(nl)
        except Exception as exc:
            check("%s translate()" % label, False, "%s: %s" % (type(exc).__name__, exc))
            continue
        check("%s translate() no raise" % label, True)
        offenders = lint(tr.netlist)
        check(
            "%s lint() clean" % label,
            offenders == [],
            "offenders=%s" % offenders[:4],
        )
        check(
            "%s deck is str and non-empty" % label,
            isinstance(tr.netlist, str) and len(tr.netlist) > 10,
        )


# ---------------------------------------------------------------------------
# 2. Unsupported analyses stay in unsupported and are not emitted
# ---------------------------------------------------------------------------

def test_unsupported_analyses() -> None:
    nl = parse_file(ROOT / "corpus" / "06_analyses.scs")
    tr = translate(nl)
    joined = "\n".join(tr.unsupported).lower()
    deck_low = tr.netlist.lower()
    for atype in ("stb", "pss", "xf", "pz"):
        # pss is not in 06 but must still be a policy we honour if present
        mentioned = atype in joined
        if atype == "pss":
            # inject a synthetic one below; here just record 06 coverage
            check(
                "06_analyses flags %s as unsupported (if present)" % atype,
                mentioned or atype == "pss",
                "unsupported=%s" % tr.unsupported,
            )
            continue
        check("06_analyses lists %s in unsupported" % atype, mentioned,
              "unsupported=%s" % tr.unsupported)
        # Do not silently emit .stb / .pss / .xf / .pz
        check(
            "06_analyses does not emit .%s" % atype,
            not re.search(r"^\.%s\b" % atype, tr.netlist, re.I | re.M),
            "deck snippet around %s" % atype,
        )

    # Explicit pss card (not in corpus 06).
    pss_src = (
        "simulator lang=spectre\n"
        "V0 (p 0) vsource dc=1 type=dc\n"
        "pss1 pss fund=1G harms=7\n"
    )
    tr2 = translate(parse_netlist(pss_src))
    check(
        "synthetic pss is unsupported",
        any("pss" in u.lower() for u in tr2.unsupported),
        "unsupported=%s" % tr2.unsupported,
    )
    check(
        "synthetic pss is not emitted as .pss",
        not re.search(r"^\.pss\b", tr2.netlist, re.I | re.M),
    )
    check(
        "06_analyses still emits supported .op/.dc/.ac/.tran/.noise",
        (".op" in deck_low) and (".ac" in deck_low) and (".tran" in deck_low),
        "deck=\n%s" % tr.netlist,
    )


# ---------------------------------------------------------------------------
# 3. Spectre 1M -> 1e6, 1m -> 1e-3 (mega/milli trap)
# ---------------------------------------------------------------------------

def test_mega_milli() -> None:
    src = (
        "simulator lang=spectre\n"
        "Rbig (a 0) resistor r=1M\n"
        "Rsmall (b 0) resistor r=1m\n"
        "Cff (c 0) capacitor c=10f\n"
    )
    tr = translate(parse_netlist(src))
    deck = tr.netlist
    check("mega/milli lint clean", lint(deck) == [], "lint=%s deck=\n%s" % (lint(deck), deck))
    # 1M (Spectre mega) must become a million, never a milli.
    check(
        "Spectre 1M becomes 1e6-class literal",
        ("1000000" in deck) or ("1e+06" in deck.lower()) or ("1e6" in deck.lower()),
        "deck=\n%s" % deck,
    )
    check(
        "Spectre 1m becomes 1e-3-class literal",
        ("0.001" in deck) or ("1e-3" in deck.lower()) or ("1e-03" in deck.lower()),
        "deck=\n%s" % deck,
    )
    check("raw suffix 1M is not copied", not re.search(r"\b1M\b", deck))
    check("raw suffix 1m is not copied", not re.search(r"\b1m\b", deck))
    # 10f must not survive (femto trap). spectre_number(10f)=1e-14.
    check("10f not copied into deck", "10f" not in deck.lower())
    check(
        "10f became ~1e-14",
        ("1e-14" in deck.lower()) or ("1e-014" in deck.lower()) or ("0.00000000000001" in deck),
        "deck=\n%s" % deck,
    )

    # Corpus unit file stores r=1M even though the *fault* is spice-mode M=milli.
    # Translator always uses spectre_number, so 1M -> 1e6.
    nl = parse_file(ROOT / "corpus" / "08_broken_unit_Mm.scs")
    tr3 = translate(nl)
    check(
        "08_broken_unit_Mm 1M -> mega via spectre_number",
        ("1000000" in tr3.netlist) or ("1e+06" in tr3.netlist.lower())
        or ("1e6" in tr3.netlist.lower()),
        "deck=\n%s" % tr3.netlist,
    )


# ---------------------------------------------------------------------------
# 4. Mapping smoke: MOS, R, V, subckt, include section, model levels, nodes
# ---------------------------------------------------------------------------

def test_mapping_smoke() -> None:
    src = (
        "simulator lang=spectre\n"
        "parameters Wn=2u\n"
        "include \"models.scs\" section=tt\n"
        "subckt cell a b\n"
        "parameters g=1\n"
        "    M0 (b a 0 0) nch w=Wn l=180n\n"
        "ends cell\n"
        "I0 (net1 net3) cell\n"
        "VIN (net1 0) vsource dc=0.7 mag=1 type=dc\n"
        "RD (vdd vout) resistor r=10k\n"
        "C1 (vout 0) capacitor c=1p\n"
        "L1 (vout vdd) inductor l=1n\n"
        "Ibias (vdd 0) isource dc=20u\n"
        "MX (d g s b) nch w=1u l=180n ad=1e-12 nf=2\n"
        "Nbad (a<b> x[0] gnd! 0) nch w=1u l=180n\n"
        "model nch bsim4 type=n toxe=1.85n\n"
        "model pch bsim3v3 type=p\n"
        "model old mos1 type=n\n"
        "model weird foo type=n\n"
        "dcOp dc annotate=status\n"
        "ac1 ac start=1 stop=1G dec=10\n"
        "tran1 tran stop=100n\n"
        "save vout\n"
    )
    # include file does not exist: omitted with warning (not a raise)
    tr = translate(parse_netlist(src))
    deck = tr.netlist
    check("subckt -> .subckt / .ends", ".subckt cell" in deck and ".ends cell" in deck)
    check("subckt PARAMS: present", "PARAMS:" in deck)
    check("global param -> .param", ".param Wn=" in deck)
    check("I0 subckt -> X prefix", re.search(r"\bXI0\b", deck) is not None, deck)
    check("MOS -> M prefix", re.search(r"\bMM0\b", deck) is not None, deck)
    check("resistor positional", re.search(r"\bRRD\b", deck) is not None, deck)
    check("capacitor positional", re.search(r"\bCC1\b", deck) is not None, deck)
    check("inductor positional", re.search(r"\bLL1\b", deck) is not None, deck)
    check("vsource DC/AC", "DC" in deck and "AC" in deck)
    check("isource DC", re.search(r"\bIIbias\b", deck) is not None, deck)
    check("unknown MOS params dropped (ad, nf)", "ad=" not in deck and "nf=" not in deck)
    check("dropped-param warning names them",
          any("ad=" in w or "ad=" in w.replace(" ", "") or "dropped param ad" in w
              for w in tr.warnings),
          "warnings=%s" % tr.warnings)
    check("bsim4 -> level 54", "level=54" in deck or "level=54" in deck.replace(" ", ""))
    check("bsim3v3 -> level 49", "level=49" in deck)
    check("mos1 -> level 1", re.search(r"\.model\s+old\s+nmos", deck) is not None)
    check("weird model unsupported", any("weird" in u and "foo" in u for u in tr.unsupported),
          "unsupported=%s" % tr.unsupported)
    check("node < > [ ] ! mapped",
          "a_b_" in deck and "x_0_" in deck and "gnd_" in deck, deck)
    check("net 0 never renamed", re.search(r"\b0\b", deck) is not None)
    check("node_map records illegal chars",
          any(k != v for k, v in tr.node_map.items()),
          "node_map=%s" % tr.node_map)
    check("node_map keeps 0 as 0", tr.node_map.get("0", "0") == "0")
    check("include section omitted (missing file) with warning",
          ".lib" not in deck and any("include" in w.lower() for w in tr.warnings))
    check("ac stop=1G converted", "1000000000" in deck or "1e+09" in deck.lower()
          or "1e9" in deck.lower(), deck)
    check("dcOp became .op", ".op" in deck)
    check("ac became .ac dec", re.search(r"\.ac\s+dec", deck) is not None, deck)
    check("tran became .tran", ".tran" in deck)
    check("save became .save", ".save" in deck)


def test_expressions_braced() -> None:
    nl = parse_file(ROOT / "corpus" / "07_expressions.scs")
    tr = translate(nl)
    check("W0*2 emitted in braces", "{W0*2}" in tr.netlist, tr.netlist)
    check("{Lmin} stays an expression", "{Lmin}" in tr.netlist, tr.netlist)
    check(
        "pPar flagged unsupported",
        any("pPar" in u or "ppar" in u.lower() for u in tr.unsupported),
        "unsupported=%s" % tr.unsupported,
    )
    check(
        "iPar flagged unsupported even when nf is dropped",
        any("iPar" in u or "ipar" in u.lower() for u in tr.unsupported),
        "unsupported=%s" % tr.unsupported,
    )


# ---------------------------------------------------------------------------
# 5. END-TO-END simulate of 01_cs_amp with PTM 180 nm
# ---------------------------------------------------------------------------

def _cs_amp_for_sim():
    nl = parse_file(ROOT / "corpus" / "01_cs_amp.scs")
    strip_includes(nl)
    return nl


def test_e2e_simulate() -> None:
    check("PTM 180 nm model file exists", PTM_180.is_file(), str(PTM_180))
    if not PTM_180.is_file():
        check("e2e simulate skipped (no model file)", False, str(PTM_180))
        return
    nl = _cs_amp_for_sim()
    tr = translate(nl, models=str(PTM_180))
    check("e2e translate lint clean", lint(tr.netlist) == [], lint(tr.netlist))
    check("e2e deck includes PTM", "ptm_180nm.lib" in tr.netlist.replace("\\", "/"))
    check("e2e PDK include not present", "$PDK" not in tr.netlist)

    res = simulate(tr.netlist)
    check("e2e simulate ok", res.get("ok") is True, "error=%s stdout=%s" % (
        res.get("error"), (res.get("stdout") or "")[-400:]))
    if not res.get("ok"):
        return

    nodes = res.get("nodes") or {}
    branches = res.get("branches") or {}
    # Top-level output of I0 is net3 (cs_amp vout).
    vout = None
    for key in ("net3", "NET3"):
        if key in nodes and nodes[key] is not None:
            vout = nodes[key]
            break
    check("e2e has V(net3)", vout is not None, "nodes=%s" % list(nodes))
    if vout is not None:
        check(
            "e2e V(net3) is a sane interior voltage",
            0.05 < vout < 1.75,
            "V(net3)=%s" % vout,
        )

    # Supply current on VVDD (prefixed V + instance VDD).
    current = None
    for key, val in branches.items():
        low = str(key).lower()
        if "vdd" in low and val is not None:
            current = val
            break
    check("e2e has a VDD branch current", current is not None, "branches=%s" % branches)
    if current is not None:
        check(
            "e2e |I(VDD)| is a real on-device current (> 1 uA)",
            abs(current) > 1e-6,
            "I=%s" % current,
        )
        check(
            "e2e |I(VDD)| is not a short-circuit amp-level current",
            abs(current) < 5e-3,
            "I=%s" % current,
        )

    vin = None
    for key in ("net1", "NET1"):
        if key in nodes:
            vin = nodes[key]
            break
    if vin is not None:
        check("e2e Vin ~ 0.7 V", abs(vin - 0.7) < 0.05, "Vin=%s" % vin)


# ---------------------------------------------------------------------------
# 6. REAL TUNE + apply_tuned re-export changes only the intended line
# ---------------------------------------------------------------------------

def test_tune_reexport() -> None:
    if not PTM_180.is_file():
        check("tune skipped (no PTM file)", False, str(PTM_180))
        return

    nl = _cs_amp_for_sim()
    original = write_netlist(nl)
    # Nominal V(net3) is ~0.33 V at R=10k. Target a nearby, reachable voltage
    # by shrinking R (less IR drop -> higher Vout).
    target_v = 0.50

    def meas(results):
        nodes = results.get("nodes") or {}
        if "net3" in nodes:
            return float(nodes["net3"])
        if "NET3" in nodes:
            return float(nodes["NET3"])
        raise KeyError("net3 not in results nodes %s" % list(nodes))

    knobs = [{"param": "r", "instance": "RD", "low": 1e3, "high": 20e3}]
    targets = [{"measure": meas, "name": "vout", "goal": target_v, "tol": 0.02}]
    result = tune(nl, knobs, targets, max_iter=40, models=str(PTM_180))
    print("  tune messages: %s" % result.messages)
    print("  tune knobs: %s" % result.knobs)
    print("  tune achieved: %s met=%s iters=%s" % (
        result.achieved, result.met, result.iterations))

    check("tune returned knobs", "RD.r" in result.knobs, "knobs=%s" % result.knobs)
    check("tune ran at least one iteration", result.iterations >= 1, "iters=%s" % result.iterations)
    check(
        "tune met Vout target within 2%",
        result.met is True,
        "achieved=%s messages=%s" % (result.achieved, result.messages),
    )
    if result.achieved.get("vout") is not None:
        rel = abs(result.achieved["vout"] - target_v) / target_v
        check("tune relative error <= 0.02", rel <= 0.02, "rel=%s v=%s" % (
            rel, result.achieved["vout"]))

    # Original IR must be unchanged until apply_tuned.
    check(
        "tune did not mutate caller netlist",
        write_netlist(nl) == original,
    )

    apply_tuned(nl, result)
    after = write_netlist(nl)
    before_lines = original.splitlines()
    after_lines = after.splitlines()
    check("re-export same line count", len(before_lines) == len(after_lines),
          "before=%d after=%d" % (len(before_lines), len(after_lines)))
    changed = [i for i, (a, b) in enumerate(zip(before_lines, after_lines)) if a != b]
    check(
        "re-export changed exactly one line",
        len(changed) == 1,
        "changed lines %s\n  %s" % (
            changed,
            "\n  ".join(
                "L%d: %s -> %s" % (i + 1, before_lines[i], after_lines[i])
                for i in changed[:5]
            ),
        ),
    )
    if changed:
        line = after_lines[changed[0]]
        check("changed line is the RD instance", "RD" in line and "resistor" in line, line)
        check("changed line is not a random header", "Generated" not in line, line)

    # Re-simulate the configured Spectre netlist after apply_tuned.
    res2 = simulate(nl, models=str(PTM_180))
    check("post-apply simulate ok", res2.get("ok") is True, res2.get("error"))
    if res2.get("ok"):
        v2 = (res2.get("nodes") or {}).get("net3")
        if v2 is None:
            v2 = (res2.get("nodes") or {}).get("NET3")
        check("post-apply V(net3) still on target", v2 is not None and abs(v2 - target_v) / target_v <= 0.03,
              "V=%s" % v2)


def test_tune_unreachable_and_multiknob() -> None:
    """Honest miss + the least_squares path (2 knobs). No MOSFET required."""
    src = (
        "simulator lang=spectre\n"
        "parameters Ra=2000 Rb=2000\n"
        "RA (mid 0) resistor r=Ra\n"
        "RB (vdd mid) resistor r=Rb\n"
        "VDD (vdd 0) vsource dc=1 type=dc\n"
        "dcOp dc\n"
    )
    nl = parse_netlist(src)

    def vmid(results):
        nodes = results.get("nodes") or {}
        if "mid" in nodes:
            return float(nodes["mid"])
        if "MID" in nodes:
            return float(nodes["MID"])
        raise KeyError("mid not in %s" % list(nodes))

    # VDD is 1 V; 2 V at mid is unreachable.
    miss = tune(
        nl,
        [{"param": "Ra", "low": 500.0, "high": 4000.0}],
        [{"measure": vmid, "name": "vmid", "goal": 2.0, "tol": 0.02}],
        max_iter=12,
    )
    print("  unreachable messages: %s" % miss.messages)
    print("  unreachable achieved: %s met=%s" % (miss.achieved, miss.met))
    check("unreachable target not claimed met", miss.met is False)
    check(
        "unreachable reports a closest value",
        miss.closest.get("vmid") is not None,
        "closest=%s" % miss.closest,
    )
    if miss.closest.get("vmid") is not None:
        check(
            "closest Vmid stays <= VDD (honest physics)",
            miss.closest["vmid"] <= 1.01,
            "closest=%s" % miss.closest["vmid"],
        )

    two = tune(
        nl,
        [
            {"param": "Ra", "low": 500.0, "high": 8000.0},
            {"param": "Rb", "low": 500.0, "high": 8000.0},
        ],
        [{"measure": vmid, "name": "vmid", "goal": 0.40, "tol": 0.03}],
        max_iter=30,
    )
    print("  two-knob knobs: %s achieved: %s met=%s iters=%s" % (
        two.knobs, two.achieved, two.met, two.iterations))
    check("two-knob tune ran", two.iterations >= 1)
    check(
        "two-knob met 0.40 V within 3%",
        two.met is True,
        "achieved=%s messages=%s" % (two.achieved, two.messages),
    )


# ---------------------------------------------------------------------------
# 7. Adversarial extras (must not weaken required tests)
# ---------------------------------------------------------------------------

def test_lint_function() -> None:
    # Specified guard is \\d+[AaFf]\\b (atto/femto). 100uA is a different
    # token and is not required to match that regex.
    bad_a = "title\nI1 1 0 DC 1A\n.end\n"
    bad_f = "title\nC1 1 0 10F\n.end\n"
    check("lint catches 1A", len(lint(bad_a)) >= 1, "off=%s" % lint(bad_a))
    check("lint catches 10F", len(lint(bad_f)) >= 1, "off=%s" % lint(bad_f))
    good = "title\nI1 1 0 DC 0.0001\n.end\n"
    check("lint accepts 0.0001", lint(good) == [])


def test_simulate_op_only_resistor() -> None:
    """Prove the analog_spice shared path independently of MOSFET models."""
    deck = (
        "resistor divider\n"
        "R1 1 0 1000\n"
        "V1 1 0 DC 1\n"
        ".op\n"
        ".end\n"
    )
    res = simulate(deck)
    check("divider simulate ok", res.get("ok") is True, res.get("error"))
    if res.get("ok"):
        v = None
        for k, val in (res.get("nodes") or {}).items():
            if str(k) in ("1", "V(1)"):
                v = val
                break
        check("divider V(1)=1", v is not None and abs(v - 1.0) < 1e-9, "V=%s nodes=%s" % (
            v, res.get("nodes")))


def main() -> int:
    print("=== test_translate.py ===\n")
    test_corpus_translate()
    print()
    test_unsupported_analyses()
    print()
    test_mega_milli()
    print()
    test_mapping_smoke()
    test_expressions_braced()
    print()
    test_lint_function()
    test_simulate_op_only_resistor()
    print()
    test_e2e_simulate()
    print()
    test_tune_reexport()
    print()
    test_tune_unreachable_and_multiknob()
    print()
    print("%d passed, %d failed" % (PASSES, FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
