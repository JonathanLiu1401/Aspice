#!/usr/bin/env python3
"""Layout sign-off of a saved cell: DRC, LVS, Quantus PEX and pre- vs post-layout simulation.

    python <aspice>/scripts/verify.py LIB CELL --steps drc,lvs,pex,post --corners typical,rcworst \
        --testbench tb_CELL.sp [--cds-lib ./cds.lib] [--env ./.env] [--json]

Runs with the Python that has virtuoso_bridge (bridge-lite) installed; settings (VB_PV_*, VB_QRC_CORNERS) come from
the bridge's .env. See references/verify.md.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from aspverify.cli import add_parser, cli_verify  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="verify.py")
    add_parser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["verify", *(sys.argv[1:] if argv is None else argv)])
    if args.env:
        from dotenv import load_dotenv
        load_dotenv(args.env, override=True)
    try:
        return cli_verify(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
