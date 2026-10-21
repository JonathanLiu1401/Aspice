"""Pre- vs post-layout simulation of one testbench.

The testbench is SPICE-syntax (Spectre reads it in spice mode) and names the design
under test with a placeholder:

    {{DUT}}      replaced by an include of the schematic CDL (pre) or a Quantus
                 netlist (one run per corner)
    {{MODELS}}   optional; replaced by ``.lib``/``.include`` lines from VB_PV_MODELS

and reports results with ``.measure`` statements. Each variant runs as a separate
Spectre job; the ``.mt0``/``.ms0``/``.ma0`` measure files are compared side by side.

Quantus writes the top subcircuit with its ports in its own order. When that differs
from the schematic's order, a wrapper with the schematic's order is added, so an
``X`` line written for the schematic connects the same nets after extraction.
"""

from __future__ import annotations

import math
import re
import shlex
import time
from dataclasses import asdict, dataclass, field

from aspverify.config import VerifyConfig
from aspverify.host import VerifyHost

MEASURE_EXTS = ("mt0", "ms0", "ma0")


@dataclass
class Variant:
    name: str                 # "schematic" or a Quantus corner
    netlist: str              # host path of the DUT netlist to include
    ok: bool = False
    measures: dict[str, float | None] = field(default_factory=dict)
    error: str = ""
    run_dir: str = ""


@dataclass
class PostLayoutReport:
    cell: str
    variants: list[Variant]
    seconds: float = 0.0

    def table(self) -> list[dict]:
        """One row per measure: value per variant and change vs the schematic."""
        names: list[str] = []
        for v in self.variants:
            names += [n for n in v.measures if n not in names]
        base = next((v for v in self.variants if v.name == "schematic"), None)
        rows = []
        for n in names:
            row: dict = {"measure": n}
            b = base.measures.get(n) if base else None
            for v in self.variants:
                x = v.measures.get(n)
                row[v.name] = x
                if v is not base and b not in (None, 0) and x is not None:
                    row[f"{v.name}_change_pct"] = round(100.0 * (x - b) / abs(b), 2)
            rows.append(row)
        return rows

    def to_dict(self) -> dict:
        return {"cell": self.cell, "seconds": self.seconds, "variants": [asdict(v) for v in self.variants], "table": self.table()}

    def text(self) -> str:
        cols = [v.name for v in self.variants]
        out = [f"post-layout comparison of {self.cell}", "measure".ljust(18) + "".join(c.rjust(22) for c in cols)]
        for row in self.table():
            cells = []
            for c in cols:
                x = row.get(c)
                s = "failed" if x is None else f"{x:.4g}"
                if f"{c}_change_pct" in row:
                    s += f" ({row[f'{c}_change_pct']:+.0f}%)"
                cells.append(s.rjust(22))
            out.append(row["measure"][:18].ljust(18) + "".join(cells))
        for v in self.variants:
            if not v.ok:
                out.append(f"[{v.name}] {v.error}")
        return "\n".join(out)


# ---------------------------------------------------------------------------
# netlist handling (pure)
# ---------------------------------------------------------------------------

def subckt_ports(netlist: str, cell: str) -> list[str] | None:
    """Ports of ``.SUBCKT cell`` (continuation lines joined), or None if absent."""
    text = re.sub(r"\n\+", " ", netlist)
    m = re.search(rf"^\.SUBCKT\s+{re.escape(cell)}\s+([^\n]*)$", text, re.M | re.I)
    if not m:
        return None
    return [p for p in m.group(1).split() if "=" not in p and p != "/"]


def align_ports(pex: str, cell: str, schematic_ports: list[str]) -> tuple[str, str]:
    """Make the extracted top cell answer to the schematic's port order.

    Returns (netlist, note). Raises ValueError when the port *sets* differ: then the
    layout and schematic disagree on pins and no wrapper can be correct.
    """
    ports = subckt_ports(pex, cell)
    if ports is None:
        raise ValueError(f"extracted netlist has no .SUBCKT {cell}")
    if ports == schematic_ports:
        return pex, ""
    if sorted(p.upper() for p in ports) != sorted(p.upper() for p in schematic_ports):
        raise ValueError(f"pins differ: layout {ports} vs schematic {schematic_ports}")
    inner = f"{cell}__pex"
    body = re.sub(rf"^(\.SUBCKT\s+){re.escape(cell)}\b", rf"\g<1>{inner}", pex, count=1, flags=re.M | re.I)
    body = re.sub(rf"^(\.ENDS\s+){re.escape(cell)}\b", rf"\g<1>{inner}", body, flags=re.M | re.I)
    by_upper = {p.upper(): p for p in schematic_ports}
    wrapper = (f"\n.SUBCKT {cell} {' '.join(schematic_ports)}\n"
               f"X0 {' '.join(by_upper[p.upper()] for p in ports)} {inner}\n.ENDS {cell}\n")
    return body + wrapper, f"ports reordered by a wrapper (layout order {' '.join(ports)})"


def render_testbench(template: str, dut_include: str, models: tuple[tuple[str, str | None], ...]) -> str:
    if "{{DUT}}" not in template:
        raise ValueError("testbench has no {{DUT}} placeholder")
    model_lines = "\n".join(f'.lib "{f}" {s}' if s else f'.include "{f}"' for f, s in models)
    return template.replace("{{MODELS}}", model_lines).replace("{{DUT}}", f'.include "{dut_include}"')


def parse_measure_file(text: str) -> dict[str, float | None]:
    """Spectre/HSPICE ``.mt0``: ``$``/``.title`` header, then names, then values.

    ``failed`` values become None. With several rows (alters/sweeps) the first row is kept.
    """
    toks: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("$") or s.lower().startswith(".title"):
            continue
        toks += s.split()

    def is_val(t: str) -> bool:
        if t.lower() == "failed":
            return True
        try:
            float(t)
            return True
        except ValueError:
            return False

    n = next((i for i, t in enumerate(toks) if is_val(t)), len(toks))
    names, vals = toks[:n], toks[n:2 * n]
    out: dict[str, float | None] = {}
    for name, v in zip(names, vals):
        if name in ("temper", "alter#"):
            continue
        x = None if v.lower() == "failed" else float(v)
        out[name] = None if x is not None and not math.isfinite(x) else x
    return out


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

def _run_variant(host: VerifyHost, cfg: VerifyConfig, template: str, v: Variant, run_dir: str) -> None:
    q = shlex.quote
    v.run_dir = run_dir
    host.run(f"rm -rf {q(run_dir)} && mkdir -p {q(run_dir)}")
    try:
        tb = render_testbench(template, v.netlist, cfg.models)
    except ValueError as exc:
        v.error = str(exc)
        return
    host.write_text(f"{run_dir}/tb.sp", tb)
    r = host.run(f"{q(cfg.spectre)} tb.sp -format psfascii -raw raw =log spectre.log > /dev/null 2>&1",
                 cwd=run_dir, timeout=7200)
    log = host.read_text(f"{run_dir}/spectre.log") or ""
    errs = re.findall(r"ERROR \([A-Z0-9-]+\):[^\n]*", log)
    for ext in MEASURE_EXTS:
        txt = host.read_text(f"{run_dir}/tb.{ext}")
        if txt:
            v.measures.update(parse_measure_file(txt))
    v.ok = r.ok and not errs and bool(v.measures)
    if not v.ok:
        v.error = "; ".join(errs[:3]) or ("no .measure results" if r.ok else f"spectre exit {r.returncode}")


def compare(host: VerifyHost, cfg: VerifyConfig, *, cell: str, template: str, cdl: str,
            pex_netlists: dict[str, str], run_dir: str) -> PostLayoutReport:
    """Run *template* with the schematic CDL and with every extracted corner."""
    variants = [Variant("schematic", cdl)]
    sch_ports = subckt_ports(host.read_text(cdl) or "", cell)
    for corner, path in pex_netlists.items():
        v = Variant(corner, path)
        text = host.read_text(path)
        if text is None:
            v.error = f"missing {path}"
        elif sch_ports is None:
            v.error = f"schematic CDL has no .SUBCKT {cell}"
        else:
            try:
                aligned, _note = align_ports(text, cell, sch_ports)
            except ValueError as exc:
                v.error = str(exc)
            else:
                if aligned is not text:
                    v.netlist = path[:-3] + ".aligned.sp"
                    host.write_text(v.netlist, aligned)
        variants.append(v)
    t0 = time.time()
    for v in variants:
        if not v.error:
            _run_variant(host, cfg, template, v, f"{run_dir}/{v.name}")
    return PostLayoutReport(cell, variants, round(time.time() - t0, 1))
