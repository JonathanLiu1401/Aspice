import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
import pytest

from aspverify.config import VerifyConfig, parse_corners, parse_models
from aspverify.flow import (
    device_models_to_instances,
    net_parasitics,
    parse_subckt_terminals,
    parse_nxf,
    lvs_discrepancies,
    parse_drc_ascii,
    parse_lvs_status,
    parse_qrc_stats,
    parse_strmout_counts,
    qrc_command_file,
)
from aspverify.postlayout import (
    PostLayoutReport,
    Variant,
    align_ports,
    parse_measure_file,
    render_testbench,
    subckt_ports,
)


def test_parse_corners_and_models():
    assert parse_corners("typical=/k/qrc/typical, rcworst=/k/qrc/rcworst") == {
        "typical": "/k/qrc/typical", "rcworst": "/k/qrc/rcworst"}
    with pytest.raises(ValueError):
        parse_corners("typical")
    assert parse_models("/m/a.scs@tt; /m/b.scs") == (("/m/a.scs", "tt"), ("/m/b.scs", None))


def test_missing_settings_named_per_step():
    cfg = VerifyConfig()
    assert cfg.missing(("gds", "drc")) == ["VB_PV_CDS_LIB (or --cds-lib)", "VB_PV_DRC_RULES"]
    assert "VB_QRC_CORNERS" in cfg.missing(("pex",))
    assert VerifyConfig(lvs_rules="x").missing(("lvs",)) == []


def test_parse_drc_ascii_counts_rules():
    text = "\n".join([
        "comp 1000",
        "M1.S.1",
        "3 3 2 Oct 20 16:39:01 2026",
        "Rule File Pathname: deck",
        "p 1 4",
        "0 0",
        "M1.W.1",
        "0 0 2 Oct 20 16:39:01 2026",
        "PO.S.2",
        "1 1 2 Oct 20 16:39:01 2026",
    ])
    assert parse_drc_ascii(text) == {"M1.S.1": 3, "PO.S.2": 1}


def test_lvs_status_and_discrepancies():
    rep = "#####  Run Result   :   MATCH\n"
    assert parse_lvs_status(rep) == "MATCH"
    assert parse_lvs_status("nothing") is None
    bad = "Run Result : MISMATCH\nUnmatched nets | 2 |\nUnmatched devices | 0 |\n"
    assert lvs_discrepancies(bad) == ["Unmatched nets | 2 |"]


def test_strmout_and_qrc_logs():
    assert parse_strmout_counts("INFO (XSTRM-234): Translation completed. '0' error(s) and '2' warning(s) found.") == (0, 2)
    assert parse_strmout_counts("INFO (XSTRM-273): Translation failed. '1' error(s) and '0' warning(s) found.") == (1, 0)
    assert parse_qrc_stats(" Output statistics:\n    R: 160\n    C: 709\n") == {"R": 160, "C": 709}


def test_qrc_command_file_names_inputs():
    cmd = qrc_command_file(cell="comp", svdb="/r/svdb", techdir="/k/typical", out_file="/r/comp.pex.typical.sp")
    assert "input_db -type pvs -directory_name /r/svdb -run_name comp" in cmd
    assert "-technology_directory /k/typical" in cmd
    assert "-subtype" not in cmd   # Quantus 25.1 rejects "-subtype spectre"
    assert "-include_cap_model true" in cmd   # else PDK caps come out as ~1e-21 F primitives


PEX = """.SUBCKT comp CLK INN INP OUTN OUTP VDD VSS
MX0/M0 OUTP CLK VSS VSS g45n1svt L=4.5e-08 W=1.2e-07
.ENDS comp
"""


def test_align_ports_same_order_is_untouched():
    assert align_ports(PEX, "comp", ["CLK", "INN", "INP", "OUTN", "OUTP", "VDD", "VSS"]) == (PEX, "")


def test_align_ports_wraps_reordered_pins():
    sch = ["INP", "INN", "CLK", "OUTP", "OUTN", "VDD", "VSS"]
    text, note = align_ports(PEX, "comp", sch)
    assert ".SUBCKT comp__pex CLK INN INP OUTN OUTP VDD VSS" in text
    assert ".SUBCKT comp INP INN CLK OUTP OUTN VDD VSS" in text
    assert "X0 CLK INN INP OUTN OUTP VDD VSS comp__pex" in text
    assert subckt_ports(text, "comp") == sch
    assert note


def test_align_ports_rejects_pin_mismatch():
    with pytest.raises(ValueError, match="pins differ"):
        align_ports(PEX, "comp", ["CLK", "INN", "INP", "OUT", "VDD", "VSS"])


def test_subckt_ports_joins_continuations_and_drops_params():
    assert subckt_ports(".SUBCKT inv A VDD\n+ VSS Y wn=1u\n.ENDS\n", "inv") == ["A", "VDD", "VSS", "Y"]


def test_render_testbench():
    tb = render_testbench("{{MODELS}}\n{{DUT}}\nX1 a b comp\n", "/r/comp.sp", (("/m/g.scs", "tt"),))
    assert '.lib "/m/g.scs" tt' in tb and '.include "/r/comp.sp"' in tb
    with pytest.raises(ValueError):
        render_testbench("no placeholder", "/r/x", ())


def test_parse_measure_file_spectre_mt0():
    text = ("$ Simulator: spectre\n.title '* comparator'\n"
            "tdp tdn iavg temper alter# \n1.89017e-10 failed 2.71781e-09 27 1 \n")
    assert parse_measure_file(text) == {"tdp": 1.89017e-10, "tdn": None, "iavg": 2.71781e-09}


def test_report_table_changes_vs_schematic():
    rep = PostLayoutReport("comp", [
        Variant("schematic", "a", True, {"tdp": 1.0e-10, "tdn": None}),
        Variant("typical", "b", True, {"tdp": 5.0e-10, "tdn": None}),
    ])
    row = rep.table()[0]
    assert row["typical_change_pct"] == 400.0
    assert "failed" in rep.text() and "+400%" in rep.text()


def test_net_parasitics_groups_subnodes():
    net = ("Rk1 OUTP#3 OUTP#4 75.0 $metal1_conn\nRk2 VSS#1 VSS#2 10\n"
           "C1 OUTP OUTN 8e-16\nC2 OUTP#6 VSS 2e-16\n")
    rows = {r["net"]: r for r in net_parasitics(net)}
    assert rows["OUTP"]["cap_ff"] == 1.0 and rows["OUTP"]["res_ohm"] == 75.0
    assert rows["OUTN"]["cap_ff"] == 0.8 and rows["VSS"]["cap_ff"] == 0.2
    assert net_parasitics(net)[0]["net"] == "OUTP"


def test_parse_nxf_maps_layout_to_schematic_names():
    nxf = ("# XXXX: Net Cross Reference (nxf)\n% MP 4 MP 4 \n0 D 0 D\n"
           "% comp 7 comp 7 \n0 2 0 AN\n0 3 0 AP\n0 CLK 0 CLK\n")
    names = parse_nxf(nxf, "comp")
    assert names == {"2": "AN", "3": "AP", "CLK": "CLK"}
    rows = net_parasitics("C1 2 3 1e-15\n", names)
    assert {r["net"] for r in rows} == {"AN", "AP"}


def test_device_models_become_subckt_instances():
    terms = parse_subckt_terminals("inline subckt g45cmim (n1 n2 body)\nsubckt ressnpoly ( n1 n2 )\n")
    assert terms == {"g45cmim": 3, "ressnpoly": 2}
    net = ("CX409/X23/C0\t212#151\t225#41\tg45cmim \n"
           "+ L=3.05e-06 W=4.01e-06 area=1.22305e-11 pj=1.412e-05  \t$SUB=VSS\n"
           "RX1/R0 a b ressnpoly L=2e-06 W=4e-07 $SUB=VSS\n"
           "C5 OUTN OUTP 8.2e-16\n")
    out = device_models_to_instances(net, terms).splitlines()
    assert out[0] == "XCX409/X23/C0 212#151 225#41 VSS g45cmim l=3.05e-06 w=4.01e-06"
    assert out[1] == "XRX1/R0 a b ressnpoly l=2e-06 w=4e-07"
    assert out[2] == "C5 OUTN OUTP 8.2e-16"
