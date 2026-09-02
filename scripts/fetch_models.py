"""Download the PTM transistor model cards and normalise them for aspice.

The model cards are NOT vendored into this repository. They come from the
Predictive Technology Model project (ptm.asu.edu, now offline) and are
redistributed by a third-party mirror that carries no licence, so shipping
copies inside a public repo would be redistributing files of unclear terms.
Fetching them on demand keeps that decision with the person running the tool.

    python scripts/fetch_models.py

Each downloaded card is rewritten so that its two devices are always called
`nch` and `pch`. That is what lets a circuit switch technology node by changing
only the `tech(...)` call.
"""
import re
import sys
import urllib.request
from pathlib import Path

MIRROR = ('https://raw.githubusercontent.com/'
          'SJTU-YONGFU-RESEARCH-GRP/spice_model_collections/HEAD/ptm')
DEST = Path(__file__).resolve().parent.parent / 'models'

NODES = {
    '180nm_bulk.pm': ('ptm_180nm.lib', '180 nm bulk CMOS', 'BSIM3v3 (level 49)', '1.8 V'),
    '130nm_bulk.pm': ('ptm_130nm.lib', '130 nm bulk CMOS', 'BSIM4 (level 54)', '1.3 V'),
    '90nm_bulk.pm':  ('ptm_90nm.lib',  '90 nm bulk CMOS',  'BSIM4 (level 54)', '1.2 V'),
    '65nm_bulk.pm':  ('ptm_65nm.lib',  '65 nm bulk CMOS',  'BSIM4 (level 54)', '1.1 V'),
    '45nm_HP.pm':    ('ptm_45nm_hp.lib', '45 nm bulk CMOS, high performance', 'BSIM4 (level 54)', '1.0 V'),
    '45nm_LP.pm':    ('ptm_45nm_lp.lib', '45 nm bulk CMOS, low power',        'BSIM4 (level 54)', '1.1 V'),
    '32nm_HP.pm':    ('ptm_32nm_hp.lib', '32 nm bulk CMOS, high performance', 'BSIM4 (level 54)', '0.9 V'),
    '22nm_HP.pm':    ('ptm_22nm_hp.lib', '22 nm bulk CMOS, high performance', 'BSIM4 (level 54)', '0.8 V'),
}

MODEL_RE = re.compile(r'^(\s*\.model\s+)(\S+)(\s+)(\S+)', re.IGNORECASE | re.MULTILINE)
TOKEN_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\S+)')

# Exactly the parameters ngspice reports as "unrecognized parameter (X) - ignored"
# for BSIM3 level 49. Queried from ngspice itself, not guessed. Removing them is
# electrically a no-op there (ngspice already discards them) and keeps the
# console clean. js, jsw, rsh and hdif are recognised and must be left alone.
#
# This list is valid for BSIM3 ONLY and is applied only to level-49 cards.
# BSIM4 (level 54) recognises several of these names - rd, rs and n among them -
# and stripping them from a BSIM4 card shifts the operating point by 75 to 85
# percent. That was measured, not assumed.
IGNORED_BY_BSIM3 = {'acm', 'binflag', 'cta', 'ctp', 'ldif', 'n', 'php', 'pta',
                    'ptp', 'rd', 'rdc', 'rs', 'rsc', 'tref', 'xl', 'xw'}

LEVEL_RE = re.compile(r'\blevel\s*=\s*(\d+)', re.IGNORECASE)


def rename_devices(text):
    """Rename the two model cards to nch / pch, keeping their device types."""
    def sub(match):
        head, name, gap, mtype = match.groups()
        low = mtype.lower()
        new = 'nch' if low == 'nmos' else 'pch' if low == 'pmos' else name
        return f'{head}{new}{gap}{mtype}'
    return MODEL_RE.subn(sub, text)


def drop_ignored_params(text):
    """Remove parameters ngspice discards. BSIM3 (level 49) cards only.

    Returns the text unchanged for any other model level: these names are
    BSIM3-specific, and several of them are meaningful to BSIM4.

    Line-aware on purpose: a `+` continuation whose tokens are all removed is
    dropped entirely, so no orphan `+` is left behind to be misparsed as a
    parameter name.
    """
    levels = {int(m) for m in LEVEL_RE.findall(text)}
    if levels != {49}:
        return text
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith('+') or stripped.startswith('*'):
            out.append(line)
            continue
        body = stripped[1:]
        kept = [m.group(0) for m in TOKEN_RE.finditer(body)
                if m.group(1).lower() not in IGNORED_BY_BSIM3]
        if kept:
            out.append('+' + ' '.join(kept))
        elif not TOKEN_RE.search(body):
            out.append(line)          # not a parameter line; keep verbatim
    return '\n'.join(out) + '\n'


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    failures = []
    for source_name, (dest_name, desc, level, vdd) in NODES.items():
        url = f'{MIRROR}/{source_name}'
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                body = response.read().decode('utf-8', 'replace')
        except Exception as exc:
            failures.append(f'{source_name}: {type(exc).__name__}: {exc}')
            print(f'  FAIL {dest_name}: {exc}')
            continue

        body, count = rename_devices(body)
        if count != 2:
            failures.append(f'{source_name}: expected 2 .model lines, found {count}')
            print(f'  FAIL {dest_name}: found {count} model cards, expected 2')
            continue
        body = drop_ignored_params(body)

        header = (
            f'* {desc} - Predictive Technology Model (PTM), {level}\n'
            f'* Nominal supply: {vdd}\n'
            f'* Device names normalised to `nch` (NMOS) and `pch` (PMOS) so a\n'
            f'* circuit can change technology node by changing one include.\n'
            f'* Source: PTM (ptm.asu.edu, offline); mirrored from\n'
            f'*   github.com/SJTU-YONGFU-RESEARCH-GRP/spice_model_collections\n'
            f'* Original file: {source_name}\n'
            f'*\n'
            f'* PTM cards are predictive and academic, not a foundry PDK, and each\n'
            f'* is extracted at its node\'s nominal channel length. Using a much\n'
            f'* longer L is an extrapolation: fine for coursework, worth stating.\n'
            f'*\n'
        )
        (DEST / dest_name).write_text(header + body)
        print(f'  ok   {dest_name}  ({len(body)} bytes)')

    print()
    if failures:
        print(f'{len(failures)} model(s) failed to fetch:')
        for f in failures:
            print(f'  - {f}')
        return 1
    print(f'{len(NODES)} model cards written to {DEST}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
