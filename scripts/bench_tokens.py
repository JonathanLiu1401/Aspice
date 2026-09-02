"""Prove digest compression on a large in-memory synthetic netlist.

Generates the netlist in-memory (does not write a huge file). Token estimate
is ceil(chars/4). Targets: outline < 1% of raw, summary < 5% of raw.
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


CELL_SPECS = (
    ("amp", ["in", "out", "vdd"], 48, "nch"),
    ("bias", ["nbias", "vdd"], 36, "nch"),
    ("inv", ["a", "y", "vdd"], 8, "nch"),
    ("nand2", ["a", "b", "y", "vdd"], 12, "nch"),
    ("buf", ["a", "y", "vdd"], 16, "pch"),
    ("sw", ["in", "out", "en", "vdd"], 10, "nch"),
    ("load", ["p", "n"], 20, "res"),
    ("decap", ["vdd", "vss"], 24, "cap"),
)


def _build_subckt(name, ports, n_dev, master, line_no):
    insts = []
    for i in range(n_dev):
        w = f"{(i % 4) + 1}u"
        if master == "nch":
            params = {"w": w, "l": "180n", "m": str((i % 4) + 1)}
            other = "pch"
        elif master == "pch":
            params = {"w": "2u", "l": "180n"}
            other = "nch"
        elif master == "res":
            params = {"r": "10k"}
            other = "res"
        else:
            params = {"c": "50f"}
            other = "cap"
        use = master if i % 5 else other
        if use == "nch":
            params = {"w": w, "l": "180n", "m": str((i % 4) + 1)}
        elif use == "pch":
            params = {"w": "2u", "l": "180n"}
        elif use == "res":
            params = {"r": "10k"}
        else:
            params = {"c": "50f"}
        drain = "out" if "out" in ports else ports[0]
        gate = ports[0]
        insts.append(
            Instance(
                f"D{i}",
                [drain, gate, "0", "0"] if use in ("nch", "pch") else [drain, "0"],
                use,
                params,
                line_no=line_no + 1 + i,
            )
        )
    return Subckt(name, list(ports), instances=insts, line_no=line_no)


def build_large_netlist(n_top=400, n_raw=5000) -> Netlist:
    raw = [
        "// Generated for: spectre",
        "// Design: bench_large",
        "simulator lang=spectre",
        "global 0",
        "parameters Lmin=180n Wmin=1u Wmax=4u Ibias=10u VDD=1.8",
        'include "$PDK/models/design.scs" section=tt',
        "include \"corners/tt.scs\" section=tt",
    ]
    subckts = {}
    line_no = len(raw) + 1
    for name, ports, n_dev, master in CELL_SPECS:
        raw.append(f"subckt {name} {' '.join(ports)}")
        start = len(raw)
        for i in range(n_dev):
            w = (i % 4) + 1
            raw.append(
                f"  D{i} (n{i} n{(i + 1) % max(1, n_dev)} 0 0) nch w={w}u l=180n m={w}"
            )
        raw.append(f"ends {name}")
        subckts[name] = _build_subckt(name, ports, n_dev, master, start)
        line_no = len(raw) + 1

    top = []
    names = [c[0] for c in CELL_SPECS]
    for i in range(n_top):
        cell = names[i % len(names)]
        ports = [p for p in CELL_SPECS[i % len(CELL_SPECS)][1]]
        nets = []
        for j, _p in enumerate(ports):
            if _p in ("vdd", "vss"):
                nets.append(_p)
            elif _p == "out" or _p == "y":
                nets.append(f"n{i % 40}")
            else:
                nets.append(f"t{j}_{i % 17}")
        if "vdd" not in nets:
            nets[-1] = "vdd"
        inst = Instance(
            f"X{i}",
            nets,
            cell,
            {"m": "1"},
            line_no=len(raw) + 1,
        )
        top.append(inst)
        raw.append(
            f"X{i} ({' '.join(nets)}) {cell} m=1"
        )

    raw.append("dc1 dc param=vds start=0 stop=1.8 step=0.01")
    raw.append("ac1 ac start=1 stop=1G dec=20")
    raw.append("tran1 tran stop=100n")
    raw.append("saveOptions options save=allpub")
    raw.append("save out vin n0 n1")

    while len(raw) < n_raw:
        k = len(raw)
        raw.append(
            f"// pad {k:05d} unused comment keeping raw size representative of ADE dump"
        )

    return Netlist(
        title="bench_large",
        language="spectre",
        statements=top,
        subckts=subckts,
        parameters={
            "Lmin": "180n",
            "Wmin": "1u",
            "Wmax": "4u",
            "Ibias": "10u",
            "VDD": "1.8",
        },
        includes=[
            Include("$PDK/models/design.scs", "tt"),
            Include("corners/tt.scs", "tt"),
        ],
        analyses=[
            Analysis("dc1", "dc", {"param": "vds", "start": "0", "stop": "1.8", "step": "0.01"}),
            Analysis("ac1", "ac", {"start": "1", "stop": "1G", "dec": "20"}),
            Analysis("tran1", "tran", {"stop": "100n"}),
        ],
        options={"save": "allpub", "temp": "27"},
        saves=["out", "vin", "n0", "n1"],
        raw_lines=raw,
    )


def est_tokens(n_chars: int) -> int:
    return int(math.ceil(n_chars / 4.0)) if n_chars else 0


def main() -> int:
    nl = build_large_netlist()
    raw = "\n".join(nl.raw_lines)
    if not raw.endswith("\n"):
        raw_text = raw + "\n"
    else:
        raw_text = raw
    out_txt = nd.outline(nl)
    sum_txt = nd.summary(nl)

    rows = [
        ("raw", raw_text),
        ("outline", out_txt),
        ("summary", sum_txt),
    ]
    raw_chars = len(raw_text)
    print(f"synthetic: {len(nl.raw_lines)} raw lines, {len(nl.statements)} top inst, "
          f"{len(nl.subckts)} subckt defs")
    print(f"{'view':<12} {'chars':>10} {'est_tokens':>12} {'ratio_vs_raw':>14}")
    results = {}
    for name, text in rows:
        n = len(text)
        tok = est_tokens(n)
        ratio = n / raw_chars if raw_chars else 0.0
        results[name] = (n, tok, ratio)
        print(f"{name:<12} {n:10d} {tok:12d} {ratio:13.4f}x")

    print()
    print("outline lines:", out_txt.count("\n") + 1 if out_txt else 0)
    print("summary lines:", sum_txt.count("\n") + 1 if sum_txt else 0)
    print()
    o_ok = results["outline"][2] < 0.01
    s_ok = results["summary"][2] < 0.05
    print(
        "TARGET outline < 1% of raw:",
        "HIT" if o_ok else f"MISS ({results['outline'][2]*100:.4f}%)",
    )
    print(
        "TARGET summary < 5% of raw:",
        "HIT" if s_ok else f"MISS ({results['summary'][2]*100:.4f}%)",
    )
    print()
    print("--- outline ---")
    print(out_txt)
    print()
    print("--- summary ---")
    print(sum_txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
