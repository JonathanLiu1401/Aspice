import json

import pytest

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'lib'))
import classmode as C
import classmode_patch as P
from virtuoso_bridge.virtuoso.basic.bridge import VirtuosoClient


@pytest.mark.parametrize("skill,cat", [
    ('cv = dbOpenCellViewByType("lib" "ota" "schematic" "schematic" "a")\n'
     'schCreateInst(cv dbOpenCellViewByType("gpdk045" "nmos1v" "symbol") "M1" 0:0 "R0")', "topology"),
    ('schCreateWire(cv "draw" "full" list(0:0 1:0) 0.0625 0.0625 0.0)', "topology"),
    ('cv = dbOpenCellViewByType("lib" "ota" "schematic" "schematic" "a")\ndbCreateInst(cv m "M1" 0:0 "R0")',
     "topology"),
    ('inst = dbFindAnyInstByName(dbOpenCellViewByType("lib" "ota" "schematic" "schematic" "a") "M1")\n'
     'inst~>w = "2u"', "sizing"),
    ('dbReplaceProp(inst "nf" "int" 4)', "sizing"),
    ('inst~>l = "90n"', "sizing"),          # cellview opened in an earlier request: refused by default
    ('maeRunOptimization("ota_tb")', "optimize"),
])
def test_design_work_is_refused(skill, cat):
    v = C.check(skill, allow=set())
    assert not v.allowed and v.category == cat and v.hint


@pytest.mark.parametrize("skill", [
    'cv = dbOpenCellViewByType("lib" "ota" "layout" "maskLayout" "a")\ndbCreateInst(cv m "M1" 0:0 "R0")',
    'cv = dbOpenCellViewByType("lib" "ota" "layout" "maskLayout" "a")\ninst~>w = 2e-6',   # layout pcell param
    'dbCreateRect(cv list("Metal1" "drawing") list(0:0 1:1))',
    'schViewToView("lib" "ota" "lib" "ota" "schematic" "symbol" "schSchemToPinList" "schPinListToSymbol")',
    'ddGetObj("lib")~>cells~>name',
    'cv = dbOpenCellViewByType("lib" "ota" "schematic" "schematic" "r")\ncv~>instances~>w',  # reading sizes
    '; schCreateInst(cv m "M1" 0:0 "R0") in a comment\n1+1',
])
def test_chores_and_reading_are_allowed(skill):
    assert C.check(skill, allow=set()).allowed


def test_course_staff_can_unlock_a_category():
    skill = 'inst~>w = "2u"'
    assert not C.check(skill, allow=set()).allowed
    assert C.check(skill, allow={"sizing"}).allowed


def test_patched_client_refuses_without_sending(monkeypatch, tmp_path):
    monkeypatch.delenv("VB_CLASS_MODE", raising=False)          # on by default in the student version
    monkeypatch.delenv("VB_CLASS_ALLOW", raising=False)
    monkeypatch.setenv("VB_CLASS_LOG", str(tmp_path / "log.jsonl"))
    sent = []
    monkeypatch.setattr(VirtuosoClient, "execute_skill", lambda self, code, *a, **k: sent.append(code) or "SENT")
    monkeypatch.setattr(VirtuosoClient, "_aspice_class_mode", False, raising=False)
    assert P.install()
    c = VirtuosoClient.__new__(VirtuosoClient)
    r = c.execute_skill('schCreateInst(cv m "M1" 0:0 "R0")')
    assert r.status.name == "ERROR" and r.metadata["class_mode"]["category"] == "topology" and not sent
    assert c.execute_skill("1+1") == "SENT" and sent == ["1+1"]
    rec = json.loads((tmp_path / "log.jsonl").read_text().splitlines()[0])
    assert rec["category"] == "topology"
    monkeypatch.setenv("VB_CLASS_MODE", "0")
    assert c.execute_skill('schCreateInst(cv m "M1" 0:0 "R0")') == "SENT"


def test_load_il_checks_file_contents(tmp_path):
    f = tmp_path / "draw.il"
    f.write_text('cv = dbOpenCellViewByType("l" "c" "schematic" "schematic" "a")\nschCreateInst(cv m "M1" 0:0 "R0")\n')
    assert not C.check_load(f, allow=set()).allowed
    f.write_text('dbCreateRect(cv list("Metal1" "drawing") list(0:0 1:1))\n')
    assert C.check_load(f, allow=set()).allowed
