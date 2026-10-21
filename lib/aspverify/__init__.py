"""Batch layout sign-off for one cell: GDS + CDL export, DRC, LVS, Quantus PEX, and a
pre- vs post-layout Spectre comparison.

    from aspverify import run_signoff
    out = run_signoff("sarlib", "comp", steps=("drc", "lvs", "pex", "post"),
                      corners=("typical", "rcworst"), testbench="tb_comp.sp")

Each step stops the chain when it fails, since the later ones would only repeat the
failure (no PEX without LVS MATCH, no post-layout run without PEX).
"""

from __future__ import annotations

import time
from pathlib import Path

from aspverify.config import VerifyConfig
from aspverify.flow import (
    StepResult,
    export_cdl,
    export_gds,
    run_drc,
    run_lvs,
    run_pex,
)
from aspverify.host import VerifyHost
from aspverify.postlayout import PostLayoutReport, compare

ALL_STEPS = ("drc", "lvs", "pex", "post")

__all__ = ["ALL_STEPS", "PostLayoutReport", "StepResult", "VerifyConfig", "VerifyHost", "run_signoff"]


def run_signoff(lib: str, cell: str, *, steps: tuple[str, ...] = ALL_STEPS,
                corners: tuple[str, ...] = (), testbench: str | None = None,
                temperature: float = 27.0, layout_view: str = "layout",
                schematic_view: str = "schematic", cfg: VerifyConfig | None = None,
                host: VerifyHost | None = None, profile: str | None = None) -> dict:
    cfg = cfg or VerifyConfig.from_env(profile)
    host = host or VerifyHost.from_env(profile)
    steps = tuple(s for s in ALL_STEPS if s in steps)
    if "post" in steps and not testbench:
        raise ValueError("the post step needs a testbench with a {{DUT}} placeholder")
    need_export = ["gds", "cdl"] if {"lvs", "pex", "post"} & set(steps) else (["gds"] if "drc" in steps else [])
    missing = cfg.missing(tuple(need_export) + steps)
    if missing:
        raise ValueError("verification settings missing: " + ", ".join(missing))
    corners = corners or tuple(cfg.qrc_corners)[:1]
    root = f"{cfg.work_dir}/{lib}__{cell}"
    out: dict = {"lib": lib, "cell": cell, "work_dir": root, "steps": [], "ok": True}
    t0 = time.time()

    def record(r: StepResult) -> bool:
        out["steps"].append(r.to_dict())
        out["ok"] = out["ok"] and r.ok
        return r.ok

    gds = cdl = None
    if "gds" in need_export:
        r = export_gds(host, cfg, lib, cell, f"{root}/gds", view=layout_view)
        if not record(r):
            return _done(out, t0)
        gds = r.details["gds"]
    if "cdl" in need_export:
        r = export_cdl(host, cfg, lib, cell, f"{root}/cdl", view=schematic_view)
        if not record(r):
            return _done(out, t0)
        cdl = r.details["cdl"]
    if "drc" in steps:
        record(run_drc(host, cfg, gds, cell, f"{root}/drc"))   # a dirty DRC does not block extraction
    svdb = None
    if {"lvs", "pex", "post"} & set(steps):
        r = run_lvs(host, cfg, gds, cell, cdl, f"{root}/lvs", rc_data={"pex", "post"} & set(steps) != set())
        if not record(r):
            return _done(out, t0)
        svdb = r.details["svdb"]
    pex: dict[str, str] = {}
    if {"pex", "post"} & set(steps):
        if not svdb:
            record(StepResult("pex", False, "LVS ran without Quantus data (svdb/<cell>.lvsfile missing); "
                              "check the LVS deck allows -rc_data"))
            return _done(out, t0)
        for c in corners:
            r = run_pex(host, cfg, cell, svdb, c, f"{root}/pex_{c}", temperature=temperature)
            if record(r):
                pex[c] = r.details["netlist"]
        out["pex_netlists"] = pex
    if "post" in steps and pex:
        template = Path(testbench).read_text() if Path(testbench).is_file() else (host.read_text(testbench) or "")
        rep: PostLayoutReport = compare(host, cfg, cell=cell, template=template, cdl=cdl,
                                        pex_netlists=pex, run_dir=f"{root}/post")
        ok = all(v.ok for v in rep.variants)
        record(StepResult("post", ok, rep.text(), f"{root}/post", rep.seconds, rep.to_dict()))
    return _done(out, t0)


def _done(out: dict, t0: float) -> dict:
    out["seconds"] = round(time.time() - t0, 1)
    return out
