"""Point aspice at a PDK you already have, and find out what is actually in it.

Licensing, stated plainly. Cadence's GPDK kits (gpdk045, gpdk090, ...) are
proprietary and marked confidential; they are licensed to universities and
companies, not redistributable. This module therefore never downloads or ships a
PDK. It reads a directory *you* supply, on a machine where you are licensed to
have it. Genuinely open PDKs exist and work the same way here: FreePDK45
(Apache 2.0), SkyWater sky130, GlobalFoundries GF180MCU, IHP SG13G2.

What it does. A PDK is a pile of model files with corner sections and device
names that differ between processes. Before simulating anything you need to know
which files hold models, which corners exist, what the devices are called, and
which parameter names the cards use. ``scan`` answers that by reading the files,
and ``probe`` answers the only question that really matters - does this model
actually bias up - by running it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ['PdkModel', 'PdkFile', 'Pdk', 'scan', 'OPEN_PDKS']

# Genuinely open PDKs, for when someone needs one they are allowed to have.
OPEN_PDKS = {
    'freepdk45': ('FreePDK45, 45 nm, Apache 2.0 - the closest open analogue to '
                  'Cadence gpdk045', 'https://eda.ncsu.edu/freepdk/'),
    'sky130': ('SkyWater sky130, 130 nm, Apache 2.0, silicon-proven',
               'https://github.com/google/skywater-pdk'),
    'gf180mcu': ('GlobalFoundries GF180MCU, 180 nm, Apache 2.0',
                 'https://github.com/google/gf180mcu-pdk'),
    'ihp-sg13g2': ('IHP SG13G2, 130 nm SiGe BiCMOS, open',
                   'https://github.com/IHP-GmbH/IHP-Open-PDK'),
}

MODEL_SUFFIXES = ('.lib', '.scs', '.mod', '.m', '.pm', '.spi', '.sp', '.cir',
                  '.model', '.models', '.l', '.inc', '.txt')

# `.model name type ...` (SPICE) and `model name type ...` (Spectre).
_MODEL_RE = re.compile(r'^\s*\.?model\s+([^\s(]+)\s+([A-Za-z][\w.]*)', re.I | re.M)
# `.lib <section>` opens a corner block; `.endl` closes it.
_LIB_SECTION_RE = re.compile(r'^\s*\.lib\s+(\S+)\s*$', re.I | re.M)
# Spectre `section <name>` / `endsection`.
_SECTION_RE = re.compile(r'^\s*section\s+(\S+)', re.I | re.M)
# `.include "x"` / `include "x"` / `.lib "file" section`.
_INCLUDE_RE = re.compile(
    r'^\s*\.?(?:include|inc|lib)\s+"?([^"\s]+)"?(?:\s+(\S+))?', re.I | re.M)
_LEVEL_RE = re.compile(r'\blevel\s*=\s*(\d+)', re.I)

# Model type -> the kind of device it is, for grouping.
_KIND = {'nmos': 'nmos', 'pmos': 'pmos', 'nch': 'nmos', 'pch': 'pmos',
         'npn': 'npn', 'pnp': 'pnp', 'd': 'diode', 'r': 'resistor',
         'c': 'capacitor', 'res': 'resistor', 'cap': 'capacitor'}

_BSIM_LEVEL = {1: 'MOS level 1 (square law)', 2: 'MOS level 2',
               3: 'MOS level 3', 49: 'BSIM3v3', 53: 'BSIM3',
               54: 'BSIM4', 68: 'BSIM-CMG (FinFET)', 72: 'BSIM-BULK'}


@dataclass
class PdkModel:
    """One model card found in the PDK."""
    name: str
    mtype: str
    file: str
    line_no: int
    section: str | None = None
    level: int | None = None

    @property
    def kind(self):
        return _KIND.get(self.mtype.lower(), self.mtype.lower())

    @property
    def model_family(self):
        return _BSIM_LEVEL.get(self.level, f'level {self.level}'
                               if self.level else 'unspecified')

    def __repr__(self):
        loc = f'{Path(self.file).name}:{self.line_no}'
        sec = f' [{self.section}]' if self.section else ''
        return f'<{self.name} {self.mtype}{sec} {self.model_family} {loc}>'


@dataclass
class PdkFile:
    """One file in the PDK that contains models or includes."""
    path: str
    models: list = field(default_factory=list)
    sections: list = field(default_factory=list)
    includes: list = field(default_factory=list)
    size: int = 0


@dataclass
class Pdk:
    """Everything discovered about a PDK directory."""
    root: str
    files: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def models(self):
        return [m for f in self.files for m in f.models]

    def corners(self):
        """Every corner/section name seen, e.g. ['tt', 'ss', 'ff', 'sf', 'fs']."""
        seen = []
        for f in self.files:
            for s in f.sections:
                if s not in seen:
                    seen.append(s)
        return seen

    def by_kind(self, kind):
        return [m for m in self.models if m.kind == kind]

    def device_names(self):
        """Model names grouped by device kind."""
        out = {}
        for m in self.models:
            out.setdefault(m.kind, [])
            if m.name not in out[m.kind]:
                out[m.kind].append(m.name)
        return out

    def find(self, pattern):
        """Models whose name matches a regex, case-insensitively."""
        rx = re.compile(pattern, re.I)
        return [m for m in self.models if rx.search(m.name)]

    def summary(self):
        """A compact report: what is here, which corners, which devices.

        Deliberately short. A PDK can be thousands of files, and the point is to
        decide what to include and which model to use, not to read it.
        """
        lines = [f'PDK: {self.root}',
                 f'  files with models: {len([f for f in self.files if f.models])}'
                 f'  (scanned {len(self.files)})',
                 f'  model cards: {len(self.models)}']
        corners = self.corners()
        lines.append('  corners/sections: ' + (', '.join(corners) if corners
                                               else 'none found'))
        devices = self.device_names()
        if devices:
            lines.append('  devices:')
            for kind in sorted(devices):
                names = devices[kind]
                shown = ', '.join(names[:8]) + (f' +{len(names) - 8} more'
                                                if len(names) > 8 else '')
                lines.append(f'    {kind:<10} {len(names):>4}  {shown}')
        families = sorted({m.model_family for m in self.models
                           if m.kind in ('nmos', 'pmos')})
        if families:
            lines.append('  MOS model families: ' + ', '.join(families))
        top = sorted((f for f in self.files if f.models),
                     key=lambda f: -len(f.models))[:5]
        if top:
            lines.append('  largest model files:')
            for f in top:
                lines.append(f'    {len(f.models):>4} models  {f.path}')
        for w in self.warnings[:5]:
            lines.append(f'  warning: {w}')
        return '\n'.join(lines)

    def techmap_skeleton(self, nmos=None, pmos=None):
        """A techmap draft for netlist_export, to be checked against the PDK.

        The cell names are filled in from what was found. The CDF parameter
        names cannot be discovered from model cards - they live in the PDK's CDF
        data - so they are left at the common defaults and flagged.
        """
        n = nmos or (self.by_kind('nmos')[0].name if self.by_kind('nmos') else 'nmos')
        p = pmos or (self.by_kind('pmos')[0].name if self.by_kind('pmos') else 'pmos')
        return {
            '_check_me': ('Cell names come from the model cards found in this PDK. '
                          'The params block below is a GUESS at the CDF parameter '
                          'names: confirm each against the PDK (in Virtuoso, '
                          'cdlgenDumpCDF) before exporting. A name that reads like '
                          'a total width in one process is a finger width in '
                          'another.'),
            'lib': Path(self.root).name,
            'cells': {'nmos': n, 'pmos': p},
            'params': {'l': 'l', 'w_f': 'w', 'n_f': 'm'},
            'defaults': {'l': '1u', 'w_f': '1u', 'n_f': 1},
        }


def _read(path, limit=8_000_000):
    try:
        if path.stat().st_size > limit:
            return None
        return path.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return None


def scan(root, max_files=4000, follow_includes=True):
    """Walk a PDK directory and report what is in it.

    Only files with a plausible model suffix are read, and very large files are
    skipped, so pointing this at a full PDK tree is cheap.
    """
    root_path = Path(root).expanduser()
    pdk = Pdk(root=str(root_path))
    if not root_path.exists():
        pdk.warnings.append(f'{root_path} does not exist')
        return pdk
    if root_path.is_file():
        candidates = [root_path]
    else:
        candidates = [p for p in sorted(root_path.rglob('*'))
                      if p.is_file() and p.suffix.lower() in MODEL_SUFFIXES]
    if len(candidates) > max_files:
        pdk.warnings.append(
            f'{len(candidates)} candidate files; scanning the first {max_files}. '
            f'Point scan() at a subdirectory to narrow it.')
        candidates = candidates[:max_files]

    for path in candidates:
        text = _read(path)
        if text is None:
            continue
        entry = PdkFile(path=str(path), size=len(text))

        # Corner sections, both dialects.
        for m in _LIB_SECTION_RE.finditer(text):
            if m.group(1).lower() not in entry.sections:
                entry.sections.append(m.group(1).lower())
        for m in _SECTION_RE.finditer(text):
            if m.group(1).lower() not in entry.sections:
                entry.sections.append(m.group(1).lower())

        for m in _MODEL_RE.finditer(text):
            name, mtype = m.group(1), m.group(2)
            if mtype.lower() in ('lang', 'spectre', 'spice'):
                continue
            line_no = text.count('\n', 0, m.start()) + 1
            tail = text[m.end():m.end() + 4000]
            level = _LEVEL_RE.search(tail)
            section = None
            for sec_match in _LIB_SECTION_RE.finditer(text, 0, m.start()):
                section = sec_match.group(1).lower()
            entry.models.append(PdkModel(
                name=name, mtype=mtype, file=str(path), line_no=line_no,
                section=section, level=int(level.group(1)) if level else None))

        if follow_includes:
            for m in _INCLUDE_RE.finditer(text):
                entry.includes.append((m.group(1), m.group(2)))

        if entry.models or entry.sections or entry.includes:
            pdk.files.append(entry)

    if not pdk.models:
        pdk.warnings.append(
            'no model cards found. Point scan() at the directory holding the '
            'model libraries (often models/, spectre/, or a corner directory), '
            'or pass a single model file directly.')
    return pdk


def probe(model_file, model_name, kind='nmos', length=None, width=10e-6,
          vdd=1.8, vgs=None, section=None, temperature=27):
    """Bias one device from a PDK and report whether it actually works.

    Reading a model card tells you it exists; only simulating tells you it
    biases. Returns a dict with the operating point and small-signal parameters,
    or ``{'ok': False, 'error': ...}``.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from analog_spice import Circuit, u_V, operating_point, _spice_path

    length = length or 0.18e-6
    vgs = vdd * 0.7 if vgs is None else vgs
    c = Circuit('pdk_probe')
    if section:
        c.raw_spice += f'.lib {_spice_path(model_file)} {section}\n'
    else:
        c.include(_spice_path(model_file))
    c.V('dd', 'd', c.gnd, vdd @ u_V)
    c.V('g', 'g', c.gnd, vgs @ u_V)
    if kind == 'nmos':
        c.MOSFET(1, 'd', 'g', c.gnd, c.gnd, model=model_name, w=width, l=length)
    else:
        # PMOS: source and bulk at the supply, gate driven below it.
        c.V('gp', 'gp', c.gnd, (vdd - vgs) @ u_V)
        c.R(1, 'dp', c.gnd, 1e-3)
        c.MOSFET(1, 'dp', 'gp', 'd', 'd', model=model_name, w=width, l=length)
    try:
        op = operating_point(c, temperature=temperature,
                             nominal_temperature=temperature)
    except Exception as exc:
        return {'ok': False, 'error': f'{type(exc).__name__}: {exc}',
                'model': model_name}
    d = op.devices.get('m1')
    if d is None or d.id is None:
        return {'ok': False, 'error': 'device did not appear in the operating point',
                'model': model_name}
    return {'ok': True, 'model': model_name, 'kind': kind,
            'w': width, 'l': length, 'vgs': vgs,
            'id': d.id, 'gm': d.gm, 'gds': d.gds, 'ro': d.ro,
            'vth': d.vth, 'vdsat': d.vdsat, 'gm_id': d.gm_id,
            'intrinsic_gain': d.intrinsic_gain, 'region': d.region,
            'has_capacitances': d.cgs is not None}
