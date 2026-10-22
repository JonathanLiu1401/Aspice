"""Layout sign-off steps run in batch on the verification host.

    export_gds   strmout of LIB/CELL/layout
    export_cdl   si/auCdl netlist of LIB/CELL/schematic (the LVS source)
    run_drc      PVS DRC
    run_lvs      PVS LVS; with ``rc_data`` it also writes the Quantus input (svdb/)
    run_pex      Quantus RC extraction of one corner -> a SPICE netlist Spectre can include

Batch tools read the saved cellviews, so save in Virtuoso first. Nothing here opens a
form or dialog in the user's session.

The parsers at the bottom are pure functions; the unit tests cover them.
"""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath

from aspverify.config import VerifyConfig
from aspverify.host import VerifyHost


@dataclass
class StepResult:
    step: str
    ok: bool
    summary: str
    run_dir: str = ""
    seconds: float = 0.0
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _q(s: str) -> str:
    return shlex.quote(str(s))


def _fresh_dir(host: VerifyHost, path: str) -> None:
    host.run(f"rm -rf {_q(path)} && mkdir -p {_q(path)}")


def _tail(text: str | None, n: int = 15) -> str:
    return "\n".join((text or "").splitlines()[-n:])


def _cdshome_prefix(cfg: VerifyConfig) -> str:
    """cds.lib files name base libraries via $CDSHOME; derive it from an absolute si path."""
    si = PurePosixPath(cfg.si)
    if si.is_absolute() and len(si.parents) >= 4 and si.parents[1].name == "dfII":
        return f'CDSHOME="${{CDSHOME:-{si.parents[3]}}}"; export CDSHOME; '
    return ""


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def export_gds(host: VerifyHost, cfg: VerifyConfig, lib: str, cell: str, run_dir: str,
               view: str = "layout") -> StepResult:
    t0 = time.time()
    _fresh_dir(host, run_dir)
    gds = f"{run_dir}/{cell}.gds"
    # strmout only reads ./cds.lib
    host.write_text(f"{run_dir}/cds.lib", f"INCLUDE {cfg.cds_lib}\n")
    host.run(f"{_cdshome_prefix(cfg)}{_q(cfg.strmout)} -library {_q(lib)} -strmFile {_q(gds)} "
             f"-topCell {_q(cell)} -view {_q(view)} -logFile strmout.log > strmout.out 2>&1",
             cwd=run_dir, timeout=1800)
    log = host.read_text(f"{run_dir}/strmout.log") or host.read_text(f"{run_dir}/strmout.out")
    errors, warnings = parse_strmout_counts(log or "")
    ok = errors == 0 and host.exists(gds)
    summary = (f"GDS written ({warnings} warnings)" if ok
               else f"stream-out failed: {_tail(log, 6) or 'no log'}")
    return StepResult("gds", ok, summary, run_dir, round(time.time() - t0, 1),
                      {"gds": gds, "errors": errors, "warnings": warnings})


SI_ENV = """simLibName = "{lib}"
simCellName = "{cell}"
simViewName = "{view}"
simSimulator = "auCdl"
simNotIncremental = 't
simReNetlistAll = 't
simViewList = '("auCdl" "{view}")
simStopList = '("auCdl")
simNetlistHier = 't
hnlNetlistFileName = "netlist"
"""


def _schematic_nf(host: VerifyHost, cfg: VerifyConfig, lib: str, cell: str, view: str,
                 run_dir: str) -> dict[str, str]:
    """Instance name -> nf from the schematic CDF. Empty if Virtuoso cannot be started.

    auCdl drops nf. The value is read back so the schematic simulation is the device
    that was drawn, not one finger of the total width. Failure here does not fail CDL.
    """
    virt = str(PurePosixPath(cfg.si).parent / "virtuoso") if cfg.si else ""
    if not virt or not host.exists(virt):
        return {}
    il = f"{run_dir}/nf.il"
    code = (
        'cv = dbOpenCellViewByType(%s %s %s nil "r")\n'
        'unless(cv error("cannot open schematic"))\n'
        'foreach(i cv~>instances\n'
        ' let((cdf p) cdf = cdfGetInstCDF(i)\n'
        '  when(cdf p = cdfFindParamByName(cdf "nf")\n'
        '   when(p && p~>value != nil printf("NF|%%s|%%L\\n" i~>name p~>value)))))\n'
        'dbClose(cv)\n'
    ) % (_q(lib), _q(cell), _q(view))
    host.write_text(il, 'let((res) res = errset(progn(\n%s\n) t)\n'
                    ' unless(res printf("NFERR|%%L\\n" errset.errset)))\nexit()\n' % code)
    host.run(f"cd {_q(run_dir)} && {_q(virt)} -nograph -nocdsinit -cdslib {_q(cfg.cds_lib)} "
             f"-replay {_q(il)} -log nf.log > nf.out 2>&1", cwd=run_dir, timeout=240)
    from aspverify.postlayout import parse_nf_print
    return parse_nf_print(host.read_text(f"{run_dir}/nf.out") or "")


def export_cdl(host: VerifyHost, cfg: VerifyConfig, lib: str, cell: str, run_dir: str,
               view: str = "schematic") -> StepResult:
    t0 = time.time()
    _fresh_dir(host, run_dir)
    host.write_text(f"{run_dir}/si.env", SI_ENV.format(lib=lib, cell=cell, view=view))
    host.run(f"{_cdshome_prefix(cfg)}CDS_Netlisting_Mode=Analog; export CDS_Netlisting_Mode; "
             f"{_q(cfg.si)} {_q(run_dir)} -batch -command netlist -cdslib {_q(cfg.cds_lib)} "
             f"> si.out 2>&1", cwd=run_dir, timeout=1800)
    netlist = f"{run_dir}/netlist"
    text = host.read_text(netlist)
    ok = bool(text) and re.search(rf"^\.SUBCKT\s+{re.escape(cell)}\b", text, re.M | re.I) is not None
    if ok:
        summary = f"CDL netlist of {lib}/{cell}/{view}"
        nf = _schematic_nf(host, cfg, lib, cell, view, run_dir)
        if nf:
            from aspverify.postlayout import splice_nf
            host.write_text(netlist + ".spectre", splice_nf(text, nf))
            summary += f"; nf copied onto {len(nf)} devices for the schematic sim"
    else:
        log = host.read_text(f"{run_dir}/si.out") or ""
        errs = [ln for ln in log.splitlines() if "*Error*" in ln or "ERROR" in ln]
        summary = "CDL export failed: " + ("; ".join(errs[:3]) or _tail(log, 6))
    return StepResult("cdl", ok, summary, run_dir, round(time.time() - t0, 1), {"cdl": netlist})


# ---------------------------------------------------------------------------
# PVS
# ---------------------------------------------------------------------------

def _copy_run_files(host: VerifyHost, cfg: VerifyConfig, run_dir: str) -> None:
    for f in cfg.run_files:
        host.run(f"cp {_q(f)} {_q(run_dir)}/")


def run_drc(host: VerifyHost, cfg: VerifyConfig, gds: str, cell: str, run_dir: str) -> StepResult:
    t0 = time.time()
    _fresh_dir(host, run_dir)
    _copy_run_files(host, cfg, run_dir)
    rdb = f"{run_dir}/{cell}.drc.ascii"
    host.run(f"{_q(cfg.pvs)} -drc -gds {_q(gds)} -top_cell {_q(cell)} -run_dir {_q(run_dir)} "
             f"-ascrdb {_q(rdb)} -log pvs_drc.log -license_timeout {cfg.license_timeout} "
             f"{_q(cfg.drc_rules)} > pvs_drc.out 2>&1", cwd=run_dir, timeout=3600)
    log = host.read_text(f"{run_dir}/pvs_drc.log") or ""
    finished = "Design Rule Check Finished Normally" in log
    viol = parse_drc_ascii(host.read_text(rdb) or "") if finished else {}
    total = sum(viol.values())
    if not finished:
        summary = "DRC did not finish: " + (_license_hint(log) or _tail(log, 6))
    elif total == 0:
        summary = "DRC clean"
    else:
        worst = ", ".join(f"{r} x{n}" for r, n in sorted(viol.items(), key=lambda kv: -kv[1])[:6])
        summary = f"{total} DRC violations in {len(viol)} rules: {worst}"
    return StepResult("drc", finished and total == 0, summary, run_dir, round(time.time() - t0, 1),
                      {"violations": viol, "total": total, "rdb": rdb, "finished": finished})


def run_lvs(host: VerifyHost, cfg: VerifyConfig, gds: str, cell: str, cdl: str, run_dir: str,
            source_cell: str | None = None, rc_data: bool = True) -> StepResult:
    t0 = time.time()
    _fresh_dir(host, run_dir)
    _copy_run_files(host, cfg, run_dir)
    rc = "-rc_data " if rc_data else ""
    host.run(f"{_q(cfg.pvs)} -lvs {rc}-gds {_q(gds)} -top_cell {_q(cell)} "
             f"-source_cdl {_q(cdl)} -source_top_cell {_q(source_cell or cell)} "
             f"-spice {_q(cell)}.layout.spi -run_dir {_q(run_dir)} -log pvs_lvs.log "
             f"-license_timeout {cfg.license_timeout} {_q(cfg.lvs_rules)} > pvs_lvs.out 2>&1",
             cwd=run_dir, timeout=3600)
    log = host.read_text(f"{run_dir}/pvs_lvs.log") or ""
    report = host.read_text(f"{run_dir}/{cell}.lvsrpt.cls") or host.read_text(f"{run_dir}/{cell}.rep") or ""
    status = parse_lvs_status(log + "\n" + report)
    match = bool(status) and status.upper().startswith("MATCH")
    svdb = f"{run_dir}/svdb"
    has_rc = rc_data and host.exists(f"{svdb}/{cell}.lvsfile")
    if status is None:
        summary = "LVS did not finish: " + (_license_hint(log) or _tail(log, 6))
    elif match:
        summary = "LVS MATCH" + (" (Quantus data written)" if has_rc else "")
    else:
        summary = f"LVS {status}: " + "; ".join(lvs_discrepancies(report)[:6])
    return StepResult("lvs", match, summary, run_dir, round(time.time() - t0, 1),
                      {"status": status, "svdb": svdb if has_rc else None,
                       "report": f"{run_dir}/{cell}.lvsrpt.cls",
                       "layout_netlist": f"{run_dir}/{cell}.layout.spi"})


def _license_hint(log: str) -> str:
    m = re.search(r"(LMC-\d+|License (?:initialization|call) failed[^\n]*)", log)
    return f"license error ({m.group(1)}); is CDS_LIC_FILE set in the Cadence env script?" if m else ""


# ---------------------------------------------------------------------------
# Quantus
# ---------------------------------------------------------------------------

def qrc_command_file(*, cell: str, svdb: str, techdir: str, out_file: str,
                     temperature: float = 27.0, extract_type: str = "rc_coupled",
                     coupling_abs_ff: float = 0.01) -> str:
    """Quantus command file for a PVS-LVS input run, written as an HSPICE-format netlist.

    The netlist keeps the LVS device names (the PDK's model subcircuits), so Spectre can
    include it directly in place of the schematic's CDL.
    """
    return "\n".join([
        f"process_technology -technology_directory {techdir} -temperature {temperature:g}",
        f"input_db -type pvs -directory_name {svdb} -run_name {cell}",
        f"extract -selection all -type {extract_type}",
        "extraction_setup -net_name_space schematic",
        f"filter_coupling_cap -coupling_cap_threshold_absolute {coupling_abs_ff:g} "
        "-coupling_cap_threshold_relative 0.001",
        "output_db -type spice -include_cap_model true -include_res_model true",
        f"output_setup -file_name {out_file} -temporary_directory_name qrc_tmp",
        "log_file -file_name qrc.log",
        "",
    ])


def run_pex(host: VerifyHost, cfg: VerifyConfig, cell: str, svdb: str, corner: str,
            run_dir: str, temperature: float = 27.0, extract_type: str = "rc_coupled") -> StepResult:
    t0 = time.time()
    techdir = cfg.qrc_corners.get(corner)
    if not techdir:
        return StepResult("pex", False, f"unknown Quantus corner {corner!r}; configured: "
                          f"{', '.join(cfg.qrc_corners) or 'none (set VB_QRC_CORNERS)'}", run_dir)
    _fresh_dir(host, run_dir)
    # .sp, not .scs: Spectre reads a .scs include as Spectre syntax and this is SPICE.
    out = f"{run_dir}/{cell}.pex.{corner}.sp"
    host.write_text(f"{run_dir}/qrc.cmd", qrc_command_file(
        cell=cell, svdb=svdb, techdir=techdir, out_file=out,
        temperature=temperature, extract_type=extract_type))
    host.run(f"{_q(cfg.qrc)} -cmd qrc.cmd > qrc.out 2>&1", cwd=run_dir, timeout=7200)
    log = host.read_text(f"{run_dir}/qrc.out") or ""
    ok = "Quantus terminated normally" in log and host.exists(out)
    if ok:
        # PDK capacitors/resistors come out as `C1 a b <model> W= L= $SUB=x`; the PDK defines them as
        # subcircuits, so rewrite them as instances Spectre can run
        terms = model_terminals(host, cfg)
        raw = host.read_text(out) or ""
        fixed = device_models_to_instances(raw, terms)
        if fixed != raw:
            host.write_text(out, fixed)
    stats = parse_qrc_stats(log)
    worst: list[dict] = []
    if ok:
        names = parse_nxf(host.read_text(f"{svdb}/{cell}.nxf") or "", cell)
        worst = net_parasitics(host.read_text(out) or "", names)[:8]
        top = ", ".join(
            f"{w['net']} {w['cap_ff']:.2f} fF/max {w['res_ohm']:.0f} ohm ({w['res_segs']} segs)"
            for w in worst[:5])
        summary = (f"{corner}: {stats.get('R', 0)} R, {stats.get('C', 0)} C extracted; "
                   f"largest nets: {top}")
    else:
        errs = re.findall(r"ERROR \([A-Z0-9-]+\)[^\n]*", log)
        summary = f"{corner}: Quantus failed: " + ("; ".join(errs[:3]) or _license_hint(log) or _tail(log, 6))
    return StepResult("pex", ok, summary, run_dir, round(time.time() - t0, 1),
                      {"corner": corner, "netlist": out if ok else None, "stats": stats,
                       "worst_nets": worst})


def model_terminals(host: VerifyHost, cfg: VerifyConfig) -> dict[str, int]:
    """Terminal count of every (inline) subckt in the configured model files' directories."""
    dirs = sorted({str(PurePosixPath(f).parent) for f, _ in cfg.models})
    if not dirs:
        return {}
    r = host.run("grep -h -E '^[[:space:]]*(inline[[:space:]]+)?subckt[[:space:]]' "
                 + " ".join(f"{_q(d)}/*.scs" for d in dirs) + " 2>/dev/null")
    return parse_subckt_terminals(r.stdout)


def parse_subckt_terminals(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in re.finditer(r"^\s*(?:inline\s+)?subckt\s+(\S+)\s*\(([^)]*)\)", text, re.M | re.I):
        out[m.group(1)] = len(m.group(2).split())
    return out


def device_models_to_instances(netlist: str, terminals: dict[str, int]) -> str:
    """Rewrite ``C/R<name> n1 n2 <model> W=.. L=.. $SUB=b`` (a PDK device Quantus kept by model name) as
    ``X<name> n1 n2 [b] <model> w=.. l=..`` when *model* is a subcircuit of the PDK models.

    Only w and l are passed: the extractor's area/pj are not parameters of the PDK subcircuits. A third terminal
    (body) is taken from $SUB when the subcircuit has one, else node 0.
    """
    lines = netlist.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        j = i + 1
        while j < len(lines) and lines[j].startswith("+"):
            j += 1
        t = line.split()
        if len(t) >= 4 and t[0][0] in "CcRr" and t[3] in terminals:
            full = " ".join([line] + [x[1:] for x in lines[i + 1:j]])
            params = dict(re.findall(r"\b([WwLl])=(\S+)", full))
            sub = re.search(r"\$SUB=(\S+)", full)
            nodes = [t[1], t[2]]
            if terminals[t[3]] >= 3:
                nodes.append(sub.group(1) if sub else "0")
            kv = " ".join(f"{k.lower()}={v}" for k, v in params.items())
            out.append(f"X{t[0]} {' '.join(nodes)} {t[3]} {kv}".rstrip())
        else:
            out.extend(lines[i:j])
        i = j
    return "\n".join(out)


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------

def parse_strmout_counts(log: str) -> tuple[int, int]:
    m = re.search(r"Translation (?:completed|failed)\.\s*'?(\d+)'?\s+error\(s\)\s+and\s+'?(\d+)'?\s+warning",
                  log)
    if m:
        return int(m.group(1)), int(m.group(2))
    return (0 if "Translation completed" in log else 1), 0


def parse_drc_ascii(text: str) -> dict[str, int]:
    """Violation count per rule from a PVS ASCII results database.

    After the ``<top> <precision>`` line, each rule block is a rule-name line followed by
    ``<count> <count> <n> <date>``.
    """
    hdr = re.compile(r"^(\d+) (\d+) (\d+) \w{3}\s+\d+\s+\d+:\d+:\d+\s+\d{4}")
    viol: dict[str, int] = {}
    lines = text.splitlines()
    for i in range(1, len(lines) - 1):
        name = lines[i].strip()
        m = hdr.match(lines[i + 1])
        if m and name and " " not in name and not hdr.match(lines[i]):
            n = int(m.group(1))
            if n:
                viol[name] = viol.get(name, 0) + n
    return viol


def parse_lvs_status(text: str) -> str | None:
    m = re.search(r"Run Result\s*:\s*([^\n]+)", text)
    return m.group(1).replace("#", "").strip() if m else None


def lvs_discrepancies(report: str) -> list[str]:
    """Short lines describing what did not match, for the agent to act on."""
    keep = []
    for line in report.splitlines():
        s = line.strip("# ").strip()
        if re.search(r"(unmatched|mismatch|missing|extra|short|open|discrepanc|parameter)", s, re.I) \
                and not s.lower().startswith("run result") \
                and not re.search(r"\|\s*0\s*\|?\s*$", s):
            keep.append(re.sub(r"\s+", " ", s)[:160])
    return keep


def parse_qrc_stats(log: str) -> dict[str, int]:
    m = re.search(r"Output statistics:\s*\n\s*R:\s*(\d+)\s*\n\s*C:\s*(\d+)", log)
    return {"R": int(m.group(1)), "C": int(m.group(2))} if m else {}


def parse_nxf(text: str, cell: str) -> dict[str, str]:
    """Layout net -> schematic net of *cell* from a PVS net cross-reference (svdb/<cell>.nxf)."""
    names: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        if line.startswith("%"):
            parts = line.split()
            inside = len(parts) > 1 and parts[1] == cell
            continue
        t = line.split()
        if inside and len(t) == 4 and not line.startswith("#"):
            names[t[1]] = t[3]
    return names


def net_parasitics(netlist: str, names: dict[str, str] | None = None) -> list[dict]:
    """Parasitic C (fF, ground + coupling) and the largest series-R segment per net.

    The resistor field is the max segment, not the sum of the mesh. Sub-nodes ``NET#3``
    count toward ``NET``. *names* maps layout net names to schematic ones. Sorted by
    capacitance, largest first.
    """
    cap: dict[str, float] = {}
    res_n: dict[str, int] = {}
    res_max: dict[str, float] = {}
    for line in netlist.replace("\n+", " ").splitlines():
        t = line.split()
        if len(t) < 4 or t[0][0] not in "RrCc" or t[0].startswith("*"):
            continue
        try:
            v = float(t[3])
        except ValueError:
            continue
        a, b = (n.split("#")[0] for n in t[1:3])
        if t[0][0] in "Cc":
            for n in {a, b}:
                cap[n] = cap.get(n, 0.0) + v * 1e15
        elif a == b:
            res_n[a] = res_n.get(a, 0) + 1
            if v > res_max.get(a, 0.0):
                res_max[a] = v
    nets = set(cap) | set(res_n)
    names = names or {}
    rows = [{"net": names.get(n, n), "cap_ff": round(cap.get(n, 0.0), 3),
             "res_ohm": round(res_max.get(n, 0.0), 1), "res_segs": res_n.get(n, 0)} for n in nets]
    return sorted(rows, key=lambda r: -r["cap_ff"])
