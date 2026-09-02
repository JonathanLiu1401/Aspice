"""Inline tests for lib/spectre_netlist.py. Run:
python scripts/test_parser.py
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from spectre_netlist import (
    Analysis,
    Include,
    Instance,
    Model,
    Subckt,
    find_instances,
    parse_file,
    parse_netlist,
    set_instance_param,
    set_param,
    spectre_number,
    write_netlist,
)


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


def rt(text: str) -> None:
    """Round-trip must be byte-identical."""
    nl = parse_netlist(text)
    out = write_netlist(nl)
    check("roundtrip", out == text, "len %d vs %d" % (len(out), len(text)))


# ---------------------------------------------------------------------------
# Fixtures (all inline; no corpus dir)
# ---------------------------------------------------------------------------

MIXED = """\
// spectre header
simulator lang=spectre
parameters W=1u L=180n
I0 (net1 net2 0) nmos w=W l=L
simulator lang=spice
* spice comment
.param VDD=1.8
.include "models.l"
M1 d g 0 0 nmos w=1u l=180n
simulator lang=spectre
dc1 dc param=vds start=0 stop=1.8 step=0.01
"""

CONT = """\
// continuations
simulator lang=spectre
parameters W=1u \\
    L=180n
M0 (d g s b) nch
+ w=W
+ l=L
"""

MODEL = """\
simulator lang=spectre
model nch bsim4 {
    type=n
    version=4.5
    tox=2.5e-9
    vth0={VTH}
}
model pch bsim4 type=p tox=2.5e-9
"""

NEST = """\
simulator lang=spectre
subckt outer a b
subckt inner c d
R1 (c d) resistor r=1k
ends inner
X1 (a b) inner
ends outer
"""

INST = """\
simulator lang=spectre
I0 (net1 net2 net3 0) nmos w=1u l=180n
I1 net1 net2 nmos w=2u l=180n
"""

BLK = """\
simulator lang=spectre
/* this is a
   multi-line comment */
I0 (a b) resistor r=1k
"""

PARAMS = """\
simulator lang=spectre
parameters W=1u L=180n
I0 (a b) nmos w=W l=L
"""

ADE = """\
// Generated for: spectre
simulator lang=spectre
global 0
parameters Lmin=180n Wn=2u

include "models/tt.scs" section=tt

subckt amp (out in vdd)
parameters mult=1
    M0 (out in 0 0) nch w=Wn l=Lmin m=mult
    M1 (out bias vdd vdd) pch w=2*Wn l=Lmin
ends amp

I0 (out in vdd) amp mult=2
V0 (vdd 0) vsource dc=1.8 type=dc
V1 (in 0) vsource dc=0.7 mag=1 type=dc

simulator lang=spice
.model mymod nmos level=54 vth0=0.4
+ tox=1.8n
simulator lang=spectre

dc1 dc param=temp start=-40 stop=125 step=5
ac1 ac start=1 stop=1G dec=20
saveOptions options save=allpub
"""

SLASH = """\
simulator lang=spectre
subckt inv a y / parameters Wp=2u / MP (y a vdd vdd) pch w=Wp l=180n / MN (y a 0 0) nch w=1u l=180n / ends inv
"""

EXPR = """\
simulator lang=spectre
parameters Lmin=180n W0=1u
M0 (out in 0 0) nch w=W0*2 l={Lmin} m=nf
"""

CRLF = "// crlf title\r\nsimulator lang=spectre\r\nparameters W=1u\r\n"


def test_spectre_number() -> None:
    def close(name: str, got, expected) -> None:
        check(name, got is not None and math.isclose(got, expected, rel_tol=0, abs_tol=1e-18),
              "got=%r expected=%r" % (got, expected))

    close("spectre_number 1u", spectre_number("1u"), 1e-6)
    close("spectre_number 1G", spectre_number("1G"), 1e9)
    close("spectre_number 1M", spectre_number("1M"), 1e6)
    check("spectre_number 2*W is None", spectre_number("2*W") is None)
    close("spectre_number 1m milli", spectre_number("1m"), 1e-3)
    close("spectre_number 180n", spectre_number("180n"), 180e-9)
    close("spectre_number 1.8", spectre_number("1.8"), 1.8)
    close("spectre_number 1e-6", spectre_number("1e-6"), 1e-6)
    check("spectre_number {Lmin} is None", spectre_number("{Lmin}") is None)
    check("spectre_number empty is None", spectre_number("") is None)


def test_mixed() -> None:
    nl = parse_netlist(MIXED)
    check_eq("mixed language at start", nl.language, "spectre")
    check_eq("mixed title", nl.title, "spectre header")
    check_eq("mixed param W", nl.parameters.get("W"), "1u")
    check_eq("mixed param L", nl.parameters.get("L"), "180n")
    check_eq("mixed spice .param VDD", nl.parameters.get("VDD"), "1.8")
    insts = find_instances(nl)
    names = [i.name for i in insts]
    check("mixed has I0", "I0" in names)
    check("mixed has M1", "M1" in names)
    i0 = find_instances(nl, name_re=r"^I0$")[0]
    check_eq("mixed I0 nodes", i0.nodes, ["net1", "net2", "0"])
    check_eq("mixed I0 master", i0.master, "nmos")
    m1 = find_instances(nl, name_re=r"^M1$")[0]
    check_eq("mixed M1 master", m1.master, "nmos")
    check_eq("mixed M1 nodes", m1.nodes, ["d", "g", "0", "0"])
    check("mixed include models.l", any(inc.path == "models.l" for inc in nl.includes))
    check("mixed has dc1", any(a.name == "dc1" and a.type == "dc" for a in nl.analyses))
    rt(MIXED)


def test_cont() -> None:
    nl = parse_netlist(CONT)
    check_eq("cont W", nl.parameters.get("W"), "1u")
    check_eq("cont L", nl.parameters.get("L"), "180n")
    m0 = find_instances(nl, name_re=r"^M0$")[0]
    check_eq("cont M0 master", m0.master, "nch")
    check_eq("cont M0 w", m0.params.get("w"), "W")
    check_eq("cont M0 l", m0.params.get("l"), "L")
    check_eq("cont M0 nodes", m0.nodes, ["d", "g", "s", "b"])
    rt(CONT)


def test_model() -> None:
    nl = parse_netlist(MODEL)
    models = [s for s in nl.statements if isinstance(s, Model)]
    check_eq("model count", len(models), 2)
    nch = models[0]
    check_eq("model nch name", nch.name, "nch")
    check_eq("model nch type", nch.mtype, "bsim4")
    check_eq("model nch type=n", nch.params.get("type"), "n")
    check_eq("model nch vth0", nch.params.get("vth0"), "{VTH}")
    check_eq("model nch tox", nch.params.get("tox"), "2.5e-9")
    pch = models[1]
    check_eq("model pch inline", pch.params.get("type"), "p")
    rt(MODEL)


def test_nest() -> None:
    nl = parse_netlist(NEST)
    check("nest outer in subckts", "outer" in nl.subckts)
    outer = nl.subckts["outer"]
    check_eq("nest outer ports", outer.ports, ["a", "b"])
    inners = [s for s in outer.statements if isinstance(s, Subckt)]
    check_eq("nest inner count", len(inners), 1)
    inner = inners[0]
    check_eq("nest inner name", inner.name, "inner")
    check_eq("nest inner ports", inner.ports, ["c", "d"])
    check_eq("nest R1 master", inner.instances[0].master, "resistor")
    check_eq("nest X1 master", outer.instances[0].master, "inner")
    found = find_instances(nl, master="resistor")
    check_eq("nest find resistor", len(found), 1)
    rt(NEST)


def test_inst() -> None:
    nl = parse_netlist(INST)
    i0 = find_instances(nl, name_re=r"^I0$")[0]
    i1 = find_instances(nl, name_re=r"^I1$")[0]
    check_eq("inst I0 parens nodes", i0.nodes, ["net1", "net2", "net3", "0"])
    check_eq("inst I0 master", i0.master, "nmos")
    check_eq("inst I1 no-paren nodes", i1.nodes, ["net1", "net2"])
    check_eq("inst I1 master", i1.master, "nmos")
    check_eq("inst I1 w", i1.params.get("w"), "2u")
    rt(INST)


def test_block_comment() -> None:
    nl = parse_netlist(BLK)
    insts = find_instances(nl)
    check_eq("block comment instance count", len(insts), 1)
    check_eq("block comment I0 master", insts[0].master, "resistor")
    rt(BLK)


def test_set_param() -> None:
    nl = parse_netlist(PARAMS)
    before = PARAMS.splitlines(keepends=True)
    set_param(nl, "W", "2u")
    after = write_netlist(nl).splitlines(keepends=True)
    check_eq("set_param line count", len(after), len(before))
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    check_eq("set_param exactly one line", changed, [1])
    check_eq("set_param W value", nl.parameters["W"], "2u")
    check("set_param W rewritten", "W=2u" in after[1] and "L=180n" in after[1])
    check_eq("set_param other lines identical", after[0], before[0])
    check_eq("set_param last line identical", after[2], before[2])


def test_set_param_cont_second_line() -> None:
    nl = parse_netlist(CONT)
    before = CONT.splitlines(keepends=True)
    set_param(nl, "L", "90n")
    after = write_netlist(nl).splitlines(keepends=True)
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    check_eq("set_param cont L one line", len(changed), 1)
    check("set_param cont L on cont line", "L=90n" in after[changed[0]])
    check("set_param cont W untouched", "W=1u" in "".join(after))


def test_set_instance_param() -> None:
    nl = parse_netlist(INST)
    before = INST.splitlines(keepends=True)
    set_instance_param(nl, "I0", "w", "5u")
    after = write_netlist(nl).splitlines(keepends=True)
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    check_eq("set_inst one line", len(changed), 1)
    check("set_inst I0 w", "w=5u" in after[changed[0]])
    check("set_inst I0 l kept", "l=180n" in after[changed[0]])
    i1_line = [ln for ln in after if ln.startswith("I1 ")][0]
    check("set_inst I1 untouched", "w=2u" in i1_line)


def test_ade_sample() -> None:
    nl = parse_netlist(ADE)
    check_eq("ade language", nl.language, "spectre")
    check_eq("ade title", nl.title, "Generated for: spectre")
    check_eq("ade Lmin", nl.parameters.get("Lmin"), "180n")
    check_eq("ade Wn", nl.parameters.get("Wn"), "2u")
    check("ade include section", any(i.path == "models/tt.scs" and i.section == "tt" for i in nl.includes))
    check("ade subckt amp", "amp" in nl.subckts)
    amp = nl.subckts["amp"]
    check_eq("ade amp ports", amp.ports, ["out", "in", "vdd"])
    check_eq("ade amp param mult", amp.params.get("mult"), "1")
    check_eq("ade amp inst count", len(amp.instances), 2)
    m1 = [i for i in amp.instances if i.name == "M1"][0]
    check_eq("ade M1 w expr", m1.params.get("w"), "2*Wn")
    tops = [s for s in nl.statements if isinstance(s, Instance)]
    names = [i.name for i in tops]
    check("ade top I0", "I0" in names)
    check("ade top V0", "V0" in names)
    models = [s for s in nl.statements if isinstance(s, Model)]
    check_eq("ade spice model name", models[0].name, "mymod")
    check_eq("ade spice model tox via +", models[0].params.get("tox"), "1.8n")
    check("ade dc1", any(a.name == "dc1" and a.type == "dc" for a in nl.analyses))
    check("ade ac1", any(a.name == "ac1" and a.type == "ac" for a in nl.analyses))
    check_eq("ade options save", nl.options.get("save"), "allpub")
    rt(ADE)


def test_slash_subckt() -> None:
    nl = parse_netlist(SLASH)
    check("slash inv present", "inv" in nl.subckts)
    inv = nl.subckts["inv"]
    check_eq("slash ports", inv.ports, ["a", "y"])
    check_eq("slash Wp", inv.params.get("Wp"), "2u")
    check_eq("slash inst count", len(inv.instances), 2)
    rt(SLASH)


def test_expr() -> None:
    nl = parse_netlist(EXPR)
    m0 = find_instances(nl, name_re=r"^M0$")[0]
    check_eq("expr w", m0.params.get("w"), "W0*2")
    check_eq("expr l", m0.params.get("l"), "{Lmin}")
    check_eq("expr m", m0.params.get("m"), "nf")
    check("expr w not evaluated", spectre_number(m0.params["w"]) is None)
    rt(EXPR)


def test_crlf_roundtrip() -> None:
    nl = parse_netlist(CRLF)
    out = write_netlist(nl)
    check("crlf byte-identical", out == CRLF, "repr %r" % (out,))
    check_eq("crlf param W", nl.parameters.get("W"), "1u")


def test_parse_file() -> None:
    fd, path = tempfile.mkstemp(suffix=".scs")
    try:
        os.close(fd)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(PARAMS)
        nl = parse_file(path)
        check_eq("parse_file W", nl.parameters.get("W"), "1u")
        check("parse_file roundtrip", write_netlist(nl) == PARAMS)
    finally:
        os.unlink(path)


def test_empty_and_comments_only() -> None:
    check("empty roundtrip", write_netlist(parse_netlist("")) == "")
    only = "// just a comment\n"
    check("comment-only roundtrip", write_netlist(parse_netlist(only)) == only)
    check_eq("comment-only title", parse_netlist(only).title, "just a comment")


def test_ahdl_and_lib() -> None:
    text = """\
simulator lang=spectre
ahdl_include "foo.va"
include "bar.scs" section=ss
simulator lang=spice
.lib "models.lib" tt
"""
    nl = parse_netlist(text)
    kinds = [i.kind for i in nl.includes]
    check("ahdl kind", "ahdl_include" in kinds)
    check("include kind", "include" in kinds)
    check("lib kind", "lib" in kinds)
    inc = [i for i in nl.includes if i.kind == "include"][0]
    check_eq("include section ss", inc.section, "ss")
    lib = [i for i in nl.includes if i.kind == "lib"][0]
    check_eq("lib section tt", lib.section, "tt")
    rt(text)


def test_find_instances_filters() -> None:
    nl = parse_netlist(INST)
    check_eq("find master nmos", len(find_instances(nl, master="nmos")), 2)
    check_eq("find master missing", len(find_instances(nl, master="pch")), 0)
    check_eq("find name_re I0", [i.name for i in find_instances(nl, name_re=r"I0")], ["I0"])


def test_set_param_missing() -> None:
    nl = parse_netlist(PARAMS)
    try:
        set_param(nl, "nope", "1")
        check("set_param missing raises", False, "no exception")
    except KeyError:
        check("set_param missing raises", True)


def test_spice_start() -> None:
    text = """\
* spice title line
.param foo=1
.subckt inv a y
M1 y a 0 0 nmos w=1u l=180n
.ends
"""
    nl = parse_netlist(text)
    check_eq("spice-start language", nl.language, "spice")
    check_eq("spice-start title", nl.title, "spice title line")
    check_eq("spice-start foo", nl.parameters.get("foo"), "1")
    check("spice-start inv", "inv" in nl.subckts)
    rt(text)


def test_spice_star_comment_with_slash() -> None:
    text = """\
simulator lang=spice
* nch / pch compact models (SPICE dot-cards)
.model nch nmos level=54 vth0=0.32
M0 vout vin 0 0 nch w=1u l=180n
"""
    nl = parse_netlist(text)
    names = [i.name for i in find_instances(nl)]
    check("spice * comment not instance", "pch" not in names, "names=%r" % (names,))
    check("spice * comment keeps M0", "M0" in names)
    models = [s for s in nl.statements if isinstance(s, Model)]
    check_eq("spice * comment model nch", models[0].name if models else "", "nch")
    rt(text)


def test_nested_subckt_in_top_dict() -> None:
    nl = parse_netlist(NEST)
    check("nested inner in netlist.subckts", "inner" in nl.subckts)
    check("nested outer in netlist.subckts", "outer" in nl.subckts)


def test_spice_positional_resistor() -> None:
    text = """\
simulator lang=spice
Rleak vdd 0 1g
+ tc1=0 tc2=0
RD vdd vout 1M
"""
    nl = parse_netlist(text)
    rleak = find_instances(nl, name_re=r"^Rleak$")[0]
    check_eq("spice Rleak master", rleak.master, "resistor")
    check_eq("spice Rleak nodes", rleak.nodes, ["vdd", "0"])
    check_eq("spice Rleak r", rleak.params.get("r"), "1g")
    check_eq("spice Rleak tc1", rleak.params.get("tc1"), "0")
    rd = find_instances(nl, name_re=r"^RD$")[0]
    check_eq("spice RD master", rd.master, "resistor")
    check_eq("spice RD r", rd.params.get("r"), "1M")
    rt(text)


def test_sweep_values_not_eaten() -> None:
    text = """\
simulator lang=spectre
swp1 sweep param=temp values=[-40 27 125] {
    dc2 dc start=0 stop=1
}
"""
    nl = parse_netlist(text)
    sw = [a for a in nl.analyses if a.name == "swp1"][0]
    check_eq("sweep values", sw.params.get("values"), "[-40 27 125]")
    check("sweep inner dc2", any(a.name == "dc2" and a.type == "dc" for a in nl.analyses))
    rt(text)


def test_named_node_in_parens() -> None:
    text = """\
simulator lang=spectre
Csniff (vout=fb 0) capacitor c=10f
I0 (d=out g=in s=0 b=0) nch w=1u
"""
    nl = parse_netlist(text)
    c = find_instances(nl, name_re=r"^Csniff$")[0]
    check_eq("named node Csniff", c.nodes, ["vout=fb", "0"])
    check_eq("named node Csniff master", c.master, "capacitor")
    i0 = find_instances(nl, name_re=r"^I0$")[0]
    check_eq("named ports I0", i0.nodes, ["d=out", "g=in", "s=0", "b=0"])
    rt(text)


def test_all_roundtrips() -> None:
    for label, text in (
        ("MIXED", MIXED),
        ("CONT", CONT),
        ("MODEL", MODEL),
        ("NEST", NEST),
        ("INST", INST),
        ("BLK", BLK),
        ("PARAMS", PARAMS),
        ("ADE", ADE),
        ("SLASH", SLASH),
        ("EXPR", EXPR),
        ("CRLF", CRLF),
    ):
        out = write_netlist(parse_netlist(text))
        check("roundtrip %s" % label, out == text, "mismatch")


def main() -> int:
    test_spectre_number()
    test_mixed()
    test_cont()
    test_model()
    test_nest()
    test_inst()
    test_block_comment()
    test_set_param()
    test_set_param_cont_second_line()
    test_set_instance_param()
    test_ade_sample()
    test_slash_subckt()
    test_expr()
    test_crlf_roundtrip()
    test_parse_file()
    test_empty_and_comments_only()
    test_ahdl_and_lib()
    test_find_instances_filters()
    test_set_param_missing()
    test_spice_start()
    test_spice_star_comment_with_slash()
    test_nested_subckt_in_top_dict()
    test_spice_positional_resistor()
    test_named_node_in_parens()
    test_sweep_values_not_eaten()
    test_all_roundtrips()
    print("---")
    print("%d passed, %d failed" % (PASSES, FAILURES))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
