#!/usr/bin/env python3
"""Operating-point audit (Razavi design paradigms) of a Spectre deck or a Virtuoso cell.

    python opcheck.py tb.scs                       # DC operating point of a Spectre deck
    python opcheck.py input.scs --at 2n,3.5n       # also at transient times (clocked circuits)
    python opcheck.py LIB CELL                     # netlist a testbench cell headless, then audit
    python opcheck.py tb.scs --sections tt,ss,ff --temps -40,27,125

Exit code 0 = no FAIL (every analog device saturated), 1 = FAIL, 2 = could not run.
See references/opcheck.md.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))

import opcheck as OP  # noqa: E402


def floats(s):
    return [float(x) for x in s.split(',') if x.strip()] if s else None


def times(s):
    import spectre_netlist as SN
    return [SN.spectre_number(x.strip()) for x in s.split(',') if x.strip()] if s else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('target', nargs='+', help='NETLIST.scs, or LIB CELL')
    ap.add_argument('--at', help='comma-separated transient times for extra oppoints (e.g. 2n,3.5n)')
    ap.add_argument('--sections', help='model sections to sweep, e.g. tt,ss,ff (rewrites include ... section=)')
    ap.add_argument('--temps', help='temperatures in C, e.g. -40,27,125')
    ap.add_argument('--vdd', type=float, help='supply voltage (default: largest vsource dc)')
    ap.add_argument('--margin', type=float, default=0.10, help='saturation headroom target in V (0.10)')
    ap.add_argument('--ignore', help='regex of instance paths to skip (e.g. testbench devices)')
    ap.add_argument('--switch', help='regex of instance paths that are switches/logic')
    ap.add_argument('--keep-analyses', action='store_true', help="keep the deck's own analyses")
    ap.add_argument('--cds-lib', help='cds.lib for LIB CELL mode')
    ap.add_argument('--section', default=None, help='model section for LIB CELL netlisting')
    ap.add_argument('--brief', action='store_true', help='findings only, no device table')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)

    if len(a.target) == 2:
        import cadence as CD
        netlist = CD.netlist_cell(a.target[0], a.target[1], section=a.section, cds_lib=a.cds_lib)
    elif len(a.target) == 1:
        netlist = str(Path(a.target[0]).expanduser().resolve())
    else:
        ap.error('give NETLIST or LIB CELL')
    rules = OP.Rules(margin=a.margin, vdd=a.vdd, ignore=a.ignore, switch=a.switch)
    secs = a.sections.split(',') if a.sections else [None]
    temps = floats(a.temps) or [None]
    try:
        rep = OP.audit(netlist, rules, sections=secs, temps=temps, at=times(a.at),
                       keep_analyses=a.keep_analyses)
    except Exception as e:  # noqa: BLE001
        print('opcheck: could not run: %s' % e, file=sys.stderr)
        return 2
    print(OP.to_json(rep) if a.json else OP.format_report(rep, show_devices=not a.brief))
    return 0 if rep['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
