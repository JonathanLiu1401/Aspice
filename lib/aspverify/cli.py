"""``virtuoso-bridge verify LIB CELL`` command."""

from __future__ import annotations

import argparse
import dataclasses
import json

from aspverify import ALL_STEPS, run_signoff
from aspverify.config import VerifyConfig


def add_parser(subparsers) -> None:
    sp = subparsers.add_parser(
        "verify",
        help="Batch layout sign-off of a saved cell: DRC, LVS, Quantus PEX, post-layout sim",
        description=(
            "Export GDS and CDL of LIB/CELL in batch (save in Virtuoso first), then run the "
            "requested steps on the Virtuoso host. Rule decks, Quantus corners and models come "
            "from VB_PV_* / VB_QRC_CORNERS in the profile .env."),
    )
    sp.add_argument("lib")
    sp.add_argument("cell")
    sp.add_argument("--steps", default="drc,lvs",
                    help=f"comma-separated subset of {','.join(ALL_STEPS)} (default drc,lvs)")
    sp.add_argument("--corners", default="",
                    help="Quantus corners from VB_QRC_CORNERS (default: the first one)")
    sp.add_argument("--testbench", default=None,
                    help="SPICE testbench with {{DUT}} (and optional {{MODELS}}) for the post step")
    sp.add_argument("--temperature", type=float, default=27.0, help="extraction temperature, C")
    sp.add_argument("--cds-lib", default=None, help="cds.lib defining LIB (overrides VB_PV_CDS_LIB)")
    sp.add_argument("--layout-view", default="layout")
    sp.add_argument("--schematic-view", default="schematic")
    sp.add_argument("--json", action="store_true", help="print the full result as JSON")
    sp.add_argument("-p", "--profile", default=None, help="Connection profile")
    sp.add_argument("--env", default=None, help="Explicit .env file path (highest priority)")


def cli_verify(args: argparse.Namespace) -> int:
    cfg = VerifyConfig.from_env(args.profile)
    if args.cds_lib:
        cfg = dataclasses.replace(cfg, cds_lib=args.cds_lib)
    steps = tuple(s.strip() for s in args.steps.split(",") if s.strip())
    bad = [s for s in steps if s not in ALL_STEPS]
    if bad:
        raise ValueError(f"unknown step(s) {bad}; choose from {list(ALL_STEPS)}")
    out = run_signoff(
        args.lib, args.cell, steps=steps,
        corners=tuple(c.strip() for c in args.corners.split(",") if c.strip()),
        testbench=args.testbench, temperature=args.temperature,
        layout_view=args.layout_view, schematic_view=args.schematic_view,
        cfg=cfg, profile=args.profile,
    )
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        for s in out["steps"]:
            mark = "PASS" if s["ok"] else "FAIL"
            first, *rest = s["summary"].splitlines() or [""]
            print(f"[{mark}] {s['step']:<4} {first}  ({s['seconds']} s)")
            for line in rest:
                print(f"       {line}")
        print(f"work dir: {out['work_dir']}")
    return 0 if out["ok"] else 1
