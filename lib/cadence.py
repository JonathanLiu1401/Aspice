"""Headless Cadence on the machine that has it: Spectre, PSF results, Virtuoso
(SKILL / OCEAN), stream in/out, and Calibre DRC/LVS.

Everything here is non-destructive by construction:

* every run happens in its own scratch directory (``workdir``), never in the
  user's library or ``~/simulation`` tree;
* user libraries are reached through a scratch ``cds.lib`` that INCLUDEs the
  user's ``cds.lib``, and cellviews are opened read-only (``"r"``);
* anything that needs a writable cellview (schematic check, stream-in) works
  on a copy in a scratch library inside ``workdir``;
* OCEAN's project directory is redirected into ``workdir`` (left alone it
  writes to ``~/simulation/<cell>``).

Tool discovery order for every tool: an explicit ``ASPICE_<TOOL>`` env var,
then ``PATH``, then the install roots in ``ASPICE_EDA_ROOTS`` (colon list)
or the built-in defaults. Licenses come from the environment, else from a
site ``cshrc/licenses.cshrc`` under an install root.

    import cadence as CD
    print(CD.doctor())                          # what is here, does it license
    r = CD.run_spectre('input.scs')             # any Spectre netlist / text / IR
    r.op()['vout'];  r.devices()['NM0']['gm']   # PSF results as numbers
    sch = CD.schematic('lab3', 'inverter')      # read a schematic headlessly
    nl  = CD.netlist_cell('lab3', 'inverter')   # schematic -> Spectre netlist
    drc = CD.calibre_drc('top.gds', 'top', rules)
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

DEFAULT_EDA_ROOTS = [
    '/home/lab.apps/vlsiapps_new',      # UW ECE lab (IC23.1, SPECTRE231)
    '/home/lab.apps/vlsiapps',
    '/opt/cadence', '/tools/cadence', '/eda/cadence',
]

# tool -> (env var, candidate paths relative to an EDA root)
_TOOLS = {
    'spectre':  ('ASPICE_SPECTRE', ['spectre/current/tools.lnx86/bin/spectre',
                                    'spectre/current/bin/spectre',
                                    'SPECTRE*/bin/spectre']),
    'virtuoso': ('ASPICE_VIRTUOSO', ['virtuoso/current/tools/dfII/bin/virtuoso',
                                     'virtuoso/current/bin/virtuoso',
                                     'IC*/tools/dfII/bin/virtuoso']),
    'ocean':    ('ASPICE_OCEAN', ['virtuoso/current/tools/dfII/bin/ocean']),
    'si':       ('ASPICE_SI', ['virtuoso/current/tools/dfII/bin/si']),
    'strmout':  ('ASPICE_STRMOUT', ['virtuoso/current/tools/dfII/bin/strmout',
                                    'virtuoso/current/bin/strmout']),
    'strmin':   ('ASPICE_STRMIN', ['virtuoso/current/tools/dfII/bin/strmin',
                                   'virtuoso/current/bin/strmin']),
    'psf':      ('ASPICE_PSF', ['virtuoso/current/tools/dfII/bin/psf']),
    'calibre':  ('ASPICE_CALIBRE', ['calibre/current/bin/calibre']),
}

_LICENSE_VARS = ('CDS_LIC_FILE', 'LM_LICENSE_FILE', 'MGLS_LICENSE_FILE')


def eda_roots():
    env = os.environ.get('ASPICE_EDA_ROOTS')
    roots = env.split(':') if env else []
    return [r for r in roots + DEFAULT_EDA_ROOTS if r and Path(r).is_dir()]


def _find_tool(tool):
    var, rels = _TOOLS[tool]
    if os.environ.get(var):
        p = Path(os.environ[var]).expanduser()
        return str(p) if p.is_file() else None
    hit = shutil.which(tool)
    if hit:
        return hit
    for root in eda_roots():
        for rel in rels:
            for p in sorted(glob.glob(os.path.join(root, rel)), reverse=True):
                if os.path.isfile(p) and os.access(p, os.X_OK):
                    return p
    return None


def _site_licenses():
    out = {}
    for root in eda_roots():
        f = Path(root) / 'cshrc' / 'licenses.cshrc'
        if f.is_file():
            for m in re.finditer(r'^\s*setenv\s+(\w+)\s+(\S+)', f.read_text(errors='replace'),
                                 re.M):
                out.setdefault(m.group(1), m.group(2))
    return out


def _home_of(path, depth):
    p = Path(path)
    for _ in range(depth):
        p = p.parent
    return str(p)


CONFIG_PATH = Path(os.environ.get('ASPICE_CONFIG', '~/.config/aspice/cadence.json')).expanduser()


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}


def configure(**settings):
    """Persist settings (e.g. ``cds_lib='~/EE332/cadence/cds.lib'``) to
    ~/.config/aspice/cadence.json. Values of None remove a key."""
    cfg = load_config()
    for k, v in settings.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = str(Path(v).expanduser()) if k == 'cds_lib' else v
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + '\n')
    environment(refresh=True)
    return cfg


def cds_lib_candidates():
    """Every cds.lib under ~ (two levels), newest first."""
    hits = glob.glob(str(Path.home() / '*' / 'cds.lib')) + \
        glob.glob(str(Path.home() / '*' / '*' / 'cds.lib'))
    hits = [h for h in hits if '/.' not in h[len(str(Path.home())):]]
    return sorted(hits, key=os.path.getmtime, reverse=True)


def find_cds_lib():
    """The user's cds.lib, in order: $ASPICE_CDS_LIB, the ``cds_lib`` saved by
    configure(), ./cds.lib, ~/cds.lib, else the only ~/*/cds.lib or
    ~/*/*/cds.lib. With several candidates and no choice made, the newest is
    used and doctor() lists the others - set one explicitly with configure().
    """
    env = os.environ.get('ASPICE_CDS_LIB') or load_config().get('cds_lib')
    if env:
        return str(Path(env).expanduser())
    for c in (Path.cwd() / 'cds.lib', Path.home() / 'cds.lib'):
        if c.is_file():
            return str(c)
    hits = cds_lib_candidates()
    return hits[0] if hits else None


@dataclass
class CadenceEnv:
    tools: dict
    env: dict
    cds_lib: str | None
    roots: list

    def has(self, *tools):
        return all(self.tools.get(t) for t in tools)

    def require(self, *tools):
        missing = [t for t in tools if not self.tools.get(t)]
        if missing:
            raise ToolMissing(
                'not found: %s. Set ASPICE_<TOOL> or ASPICE_EDA_ROOTS; see '
                'cadence.doctor().' % ', '.join(missing))


class ToolMissing(RuntimeError):
    pass


_ENV_CACHE = None


def environment(refresh=False):
    """Discover tools and build the environment Cadence tools expect."""
    global _ENV_CACHE
    if _ENV_CACHE is not None and not refresh:
        return _ENV_CACHE
    tools = {t: _find_tool(t) for t in _TOOLS}
    env = dict(os.environ)
    site = _site_licenses()
    for var in _LICENSE_VARS:
        if not env.get(var) and site.get(var):
            env[var] = site[var]
    path = env.get('PATH', '')
    if tools['virtuoso']:
        cds = _home_of(tools['virtuoso'], 4)        # <CDS>/tools/dfII/bin/virtuoso
        env.setdefault('CDS', cds)
        env.setdefault('CDSHOME', cds)
        env.setdefault('CDS_INST_DIR', cds)
        for sub in ('tools/dfII/bin', 'tools/bin', 'bin'):
            path = os.path.join(cds, sub) + ':' + path
    if tools['spectre']:
        path = str(Path(tools['spectre']).parent) + ':' + path
    if tools['calibre']:
        home = _home_of(tools['calibre'], 2)
        env.setdefault('MGC_HOME', home)
        env.setdefault('CALIBRE_HOME', home)
        env.setdefault('USE_CALIBRE_VCO', 'aoj')
        path = os.path.join(home, 'bin') + ':' + path
    env['PATH'] = path
    env.setdefault('CDS_LOAD_ENV', 'CWDElseHome')
    env.setdefault('CDS_Netlisting_Mode', 'Analog')
    env.setdefault('CDS_AUTO_64BIT', 'EXCLUDE:si:PIPO')
    env.setdefault('SKIP_OS_CHECKS', '1')
    _ENV_CACHE = CadenceEnv(tools=tools, env=env, cds_lib=find_cds_lib(),
                            roots=eda_roots())
    return _ENV_CACHE


def make_workdir(prefix='aspice_', workdir=None):
    """A fresh scratch directory. ASPICE_WORKDIR sets the parent."""
    if workdir:
        Path(workdir).mkdir(parents=True, exist_ok=True)
        return str(Path(workdir).resolve())
    parent = os.environ.get('ASPICE_WORKDIR') or None
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)
    return tempfile.mkdtemp(prefix=prefix, dir=parent)


def _run(cmd, cwd, timeout, env_extra=None):
    ce = environment()
    env = dict(ce.env)
    env['CDS_LOG_PATH'] = cwd           # keeps CDS.log out of the user's dirs
    env.update(env_extra or {})
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, text=True, errors='replace')
        return p.returncode, p.stdout, time.time() - t0
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors='replace') if isinstance(e.stdout, bytes) else (e.stdout or '')
        return -9, out + '\n[aspice] timed out after %ss' % timeout, time.time() - t0


# ---------------------------------------------------------------------------
# PSF (Spectre results)
# ---------------------------------------------------------------------------

_TOK = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+')


def _num(tok):
    try:
        return float(tok)
    except ValueError:
        return tok.strip('"') if tok.startswith('"') else tok


def _skip_parens(toks, i):
    """toks[i] is just after an opening '('; return index after its ')'."""
    depth = 1
    while i < len(toks) and depth:
        if toks[i] == '(' or toks[i].endswith('('):
            depth += 1
        elif toks[i] == ')':
            depth -= 1
        i += 1
    return i


def _parse_types(toks):
    """TYPE section -> {type_name: [field, ...]} for STRUCT types."""
    structs, i = {}, 0
    while i < len(toks):
        t = toks[i]
        if t.startswith('"') and i + 1 < len(toks) and toks[i + 1] == 'STRUCT(':
            name, fields, i = t.strip('"'), [], i + 2
            while i < len(toks) and toks[i] != ')':
                if toks[i].startswith('"'):
                    fields.append(toks[i].strip('"'))
                    i += 1
                    while i < len(toks) and not toks[i].startswith('"') and toks[i] != ')':
                        if toks[i].endswith('('):
                            i = _skip_parens(toks, i + 1)
                        else:
                            i += 1
                else:
                    i += 1
            structs[name] = fields
            i += 1
        else:
            i += 1
    return structs


def _sections(text):
    names = ('HEADER', 'TYPE', 'SWEEP', 'TRACE', 'VALUE', 'END')
    pos = {}
    for n in names:
        m = re.search(r'^%s\s*$' % n, text, re.M)
        if m:
            pos[n] = m
    out = {}
    order = sorted(pos.items(), key=lambda kv: kv[1].start())
    for k, (name, m) in enumerate(order):
        end = order[k + 1][1].start() if k + 1 < len(order) else len(text)
        out[name] = text[m.end():end]
    return out


def _tokens(s):
    # glue "NAME(" so STRUCT( / PROP( stay single tokens
    toks = _TOK.findall(s)
    out = []
    for t in toks:
        if t == '(' and out and re.fullmatch(r'[A-Z]+', out[-1]):
            out[-1] += '('
        else:
            out.append(t)
    return out


@dataclass
class PsfData:
    """One PSF file. Swept data: ``x`` plus ``traces[name]`` arrays (complex
    for AC). Unswept: ``traces[name]`` scalars, ``structs[inst]`` dicts."""
    path: str
    header: dict = field(default_factory=dict)
    sweep: str | None = None
    x: np.ndarray | None = None
    traces: dict = field(default_factory=dict)
    kinds: dict = field(default_factory=dict)
    structs: dict = field(default_factory=dict)
    note: str | None = None

    @property
    def analysis_type(self):
        return self.header.get('analysis type')

    def __getitem__(self, name):
        return self.traces[name]

    def __repr__(self):
        n = len(self.x) if self.x is not None else 0
        return '<PsfData %s type=%s sweep=%s points=%d traces=%d structs=%d>' % (
            Path(self.path).name, self.analysis_type, self.sweep, n,
            len(self.traces), len(self.structs))


def read_psf(path, psf_tool=None):
    """Parse one PSF file. Binary PSF (ADE's default) is converted with
    Cadence's ``psf`` utility first; PSF-ASCII is read directly."""
    path = str(path)
    with open(path, 'rb') as fh:
        head = fh.read(6)
    if head != b'HEADER':
        tool = psf_tool or environment().tools.get('psf')
        if not tool:
            raise ToolMissing('binary PSF needs the Cadence psf utility')
        rc, out, _ = _run([tool, '-f', '%.15e', '-i', path], cwd=str(Path(path).parent),
                          timeout=300)
        if rc != 0 or not out.lstrip().startswith('HEADER'):
            raise RuntimeError('psf conversion failed for %s: %s' % (path, out[:300]))
        text = out
    else:
        text = Path(path).read_text(errors='replace')
    return parse_psf_ascii(text, path)


def parse_psf_ascii(text, path='<psf>'):
    sec = _sections(text)
    d = PsfData(path=path)
    htoks = _TOK.findall(sec.get('HEADER', ''))
    for k in range(0, len(htoks) - 1, 2):
        d.header[htoks[k].strip('"')] = _num(htoks[k + 1])
    structs = _parse_types(_tokens(sec.get('TYPE', '')))
    if 'SWEEP' in sec:
        st = _TOK.findall(sec['SWEEP'])
        d.sweep = st[0].strip('"') if st else None
        ttoks = _TOK.findall(sec.get('TRACE', ''))
        names = []
        k = 0
        while k + 1 < len(ttoks):
            if ttoks[k].startswith('"') and ttoks[k + 1].startswith('"'):
                names.append(ttoks[k].strip('"'))
                d.kinds[names[-1]] = ttoks[k + 1].strip('"')
                k += 2
            else:
                k += 1
        xs, cols = [], {n: [] for n in names}
        toks = _tokens(sec.get('VALUE', ''))
        i = 0
        while i < len(toks):
            name = toks[i].strip('"')
            i += 1
            if i >= len(toks):
                break
            if toks[i] == '(':
                vals = []
                i += 1
                while toks[i] != ')':
                    vals.append(float(toks[i]))
                    i += 1
                i += 1
                v = complex(vals[0], vals[1]) if len(vals) == 2 else vals
            else:
                v = _num(toks[i])
                i += 1
            if name == d.sweep:
                xs.append(v)
            elif name in cols:
                cols[name].append(v)
            else:
                cols.setdefault(name, []).append(v)
        d.x = np.array(xs, dtype=float)
        for n, v in cols.items():
            if v:
                d.traces[n] = np.array(v)
        return d
    # Unswept: "name" "type" value | "name" "type" ( v ... ) PROP( ... )
    toks = _tokens(sec.get('VALUE', ''))
    i = 0
    while i < len(toks):
        if not toks[i].startswith('"'):
            i = _skip_parens(toks, i + 1) if toks[i].endswith('(') else i + 1
            continue
        name = toks[i].strip('"')
        typ = toks[i + 1].strip('"') if i + 1 < len(toks) and toks[i + 1].startswith('"') else None
        i += 2 if typ is not None else 1
        if i < len(toks) and toks[i] == '(':
            vals, i = [], i + 1
            while i < len(toks) and toks[i] != ')':
                vals.append(_num(toks[i]))
                i += 1
            i += 1
            fields = structs.get(typ)
            if fields and len(fields) == len(vals):
                rec = dict(zip(fields, vals))
                rec['_type'] = typ
                d.structs[name] = rec
            elif len(vals) == 1:
                d.traces[name] = vals[0]
            else:
                d.structs[name] = {'_type': typ, '_values': vals}
        elif i < len(toks):
            d.traces[name] = _num(toks[i])
            d.kinds[name] = typ
            i += 1
        if i < len(toks) and toks[i] == 'PROP(':
            i = _skip_parens(toks, i + 1)
    return d


def read_psf_dir(raw_dir, psf_tool=None):
    """Every analysis result in a raw directory, keyed by analysis name."""
    out = {}
    for f in sorted(Path(raw_dir).iterdir()):
        if not f.is_file() or f.name.startswith('.') or \
                f.suffix in ('.psfxl', '.sig', '.log', '.out', '.dpl') or \
                f.name in ('logFile', 'artistLogFile', 'runObjFile', 'simRunData',
                           'variables_file', 'logStatus', 'spectre.out') or \
                '.' not in f.name:
            continue
        try:
            d = read_psf(f, psf_tool)
        except Exception as e:  # keep going; report per file
            out[f.name] = e
            continue
        key = d.header.get('analysis name') or f.name.split('.')[0]
        if d.sweep and (d.x is None or len(d.x) == 0) and \
                Path(str(f) + '.psfxl').is_file():
            d.note = ('waveform is in %s.psfxl (ADE PSF XL); read it with '
                      'read_ade_waveform(%r, %r, [...])' % (f.name, str(raw_dir), str(key)))
        out[str(key)] = d
    return out


def read_ade_waveform(psf_dir, analysis, signals, workdir=None, timeout=600):
    """Waveforms from any results directory OCEAN can open, including the
    PSF XL transients ADE writes (which the psf converters cannot read).

    Returns {'x': array, '<signal>': array, '_signals': [all signal names]}.
    Signals are OCEAN names: node voltages as ``"VOUT"``, terminal currents
    as ``"NM0:d"``. 7 significant figures (ocnPrint).
    """
    wd = make_workdir('aspice_ocnread_', workdir)
    files = {sig: str(Path(wd) / ('sig%d.txt' % k)) for k, sig in enumerate(signals)}
    prints = ''.join(
        'ocnPrint(%s(%s) ?output %s ?numberNotation \'scientific ?precision 15)\n' % (
            'i' if ':' in sig else 'v', _q(sig), _q(path)) for sig, path in files.items())
    code = ('openResults(%s)\nselectResult(\'%s)\n'
            'printf("%sSIGS|%%s\\n" buildString(outputs() ","))\n%s' % (
                _q(psf_dir), analysis, _MARK, prints))
    r = run_skill(code, workdir=wd, ocean=True, timeout=timeout)
    out = {'_signals': next((rec[1].split(',') for rec in r.records if rec[0] == 'SIGS'), [])}
    for sig, path in files.items():
        rows = []
        for line in _read_safe(path).splitlines():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    rows.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    pass
        if not rows:
            raise RuntimeError('read_ade_waveform: no data for %s (%s)' % (
                sig, '; '.join(r.errors) or 'see %s' % r.log_path))
        arr = np.array(rows)
        out.setdefault('x', arr[:, 0])
        out[sig] = arr[:, 1]
    return out


# ---------------------------------------------------------------------------
# Spectre
# ---------------------------------------------------------------------------

_COMPLETE_RE = re.compile(
    r'spectre completes with (\d+) errors?, (\d+) warnings?, and (\d+) notices?')


def parse_spectre_log(text):
    """Errors, warnings and the completion line from a spectre log/spectre.out."""
    errors = [m.group(0).strip() for m in
              re.finditer(r'^ERROR[^\n]*(?:\n(?![A-Z]{4,}|\s*$)[^\n]*)*', text, re.M)]
    warnings = [m.group(0).strip() for m in
                re.finditer(r'^WARNING[^\n]*(?:\n(?![A-Z]{4,}|\s*$)[^\n]*)*', text, re.M)]
    m = _COMPLETE_RE.search(text)
    fatal = 'terminated prematurely' in text or 'Fatal error' in text
    return {
        'completed': bool(m) and not fatal,
        'n_errors': int(m.group(1)) if m else len(errors),
        'n_warnings': int(m.group(2)) if m else len(warnings),
        'n_notices': int(m.group(3)) if m else None,
        'errors': errors, 'warnings': warnings, 'fatal': fatal,
        'version': (re.search(r'Version (\S+)', text) or [None, None])[1],
    }


@dataclass
class SpectreResult:
    ok: bool
    workdir: str
    netlist_path: str
    raw_dir: str
    log_path: str
    returncode: int
    seconds: float
    log: dict
    analyses: dict = field(default_factory=dict)

    @property
    def errors(self):
        return self.log.get('errors', [])

    @property
    def warnings(self):
        return self.log.get('warnings', [])

    def analysis(self, name_or_type):
        if name_or_type in self.analyses:
            return self.analyses[name_or_type]
        for d in self.analyses.values():
            if isinstance(d, PsfData) and d.analysis_type == name_or_type:
                return d
        raise KeyError('%s not in %s' % (name_or_type, sorted(self.analyses)))

    def op(self, name=None):
        """Node voltages / terminal currents of the (first) DC operating point."""
        if name:
            return dict(self.analyses[name].traces)
        for d in self.analyses.values():
            if isinstance(d, PsfData) and d.analysis_type == 'dc' and d.sweep is None:
                return dict(d.traces)
        raise KeyError('no unswept dc analysis in %s' % sorted(self.analyses))

    def devices(self, name=None):
        """Per-instance operating-point records (gm, gds, vth, vdsat, region,
        cgg, ...) from an ``info what=oppoint`` analysis."""
        for k, d in self.analyses.items():
            if not isinstance(d, PsfData) or d.analysis_type != 'info':
                continue
            if name and k != name:
                continue
            if d.structs and any('gm' in s or 'ids' in s or 'i' in s for s in d.structs.values()):
                return d.structs
        return {}

    def device_table(self):
        """Compact MOS oppoint table: region, Id, gm, gds, gm/Id, gm*ro, Vth, Vdsat,
        Vds - Vdsat headroom. The saturation check, from the simulator."""
        rows = ['%-10s %-12s %10s %10s %10s %7s %7s %8s %8s %8s' % (
            'device', 'region', 'Id(uA)', 'gm(uS)', 'gds(uS)', 'gm/Id', 'gm*ro',
            'Vth', 'Vdsat', 'Vds-Vdsat')]
        for name, d in sorted(self.devices().items()):
            if 'gm' not in d or 'ids' not in d:
                continue
            reg = d.get('region')
            reg = REGION_NAMES.get(int(reg), str(reg)) if isinstance(reg, float) else str(reg)
            ids, gm, gds = d['ids'], d['gm'], d.get('gds') or 0.0
            vds, vdsat = d.get('vds'), d.get('vdsat')
            head = abs(vds) - abs(vdsat) if isinstance(vds, float) and isinstance(vdsat, float) else None
            rows.append('%-10s %-12s %10.3f %10.2f %10.3f %7.2f %7.1f %8.3f %8.3f %8s' % (
                name, reg, ids * 1e6, gm * 1e6, gds * 1e6,
                abs(gm / ids) if ids else 0, abs(gm / gds) if gds else float('inf'),
                d.get('vth', float('nan')), vdsat if vdsat is not None else float('nan'),
                '%.3f' % head if head is not None else '-'))
        return '\n'.join(rows)

    def wave(self, analysis, signal):
        d = self.analysis(analysis)
        return d.x, d.traces[signal]

    def summary(self):
        lines = ['spectre %s in %.1fs: %s  (%d errors, %d warnings)' % (
            self.log.get('version'), self.seconds, 'OK' if self.ok else 'FAILED',
            self.log.get('n_errors', 0), self.log.get('n_warnings', 0))]
        for e in self.errors[:5]:
            lines.append('  ' + e.replace('\n', ' ')[:200])
        infos = []
        for k, d in self.analyses.items():
            if isinstance(d, PsfData) and str(d.analysis_type).startswith('info'):
                infos.append('%s(%d)' % (k, len(d.structs)))
            elif isinstance(d, PsfData):
                n = len(d.x) if d.x is not None else 0
                lines.append('  %-10s %-5s %s' % (k, d.analysis_type, '%d pts x %d traces' % (
                    n, len(d.traces)) if d.sweep else '%d values' % len(d.traces)))
            else:
                lines.append('  %-10s unreadable: %s' % (k, str(d)[:80]))
        if infos:
            lines.append('  info: ' + ' '.join(infos))
        lines.append('  workdir: %s' % self.workdir)
        return '\n'.join(lines)


def _netlist_text(netlist):
    """Accept a path, raw text, or a spectre_netlist IR."""
    if hasattr(netlist, 'statements'):
        import spectre_netlist as SN
        return SN.write_netlist(netlist), None
    s = str(netlist)
    if '\n' not in s and len(s) < 4096 and Path(s).expanduser().is_file():
        p = Path(s).expanduser()
        return p.read_text(errors='surrogateescape'), p
    return s, None


def run_spectre(netlist, workdir=None, timeout=900, args=(), copy_siblings=True,
                fmt='psfascii'):
    """Run Spectre on a netlist (path, text or IR) in a scratch directory.

    The deck is written to ``<workdir>/netlist/input.scs`` and results go to
    ``<workdir>/psf``: the same layout ADE uses, so relative paths inside an
    ADE netlist (``include "ade_e.scs"``, ``sensfile="../psf/..."``) resolve.
    For a path input, small sibling files (ade_e.scs, .scs includes) are
    copied too. The original files are never written.
    """
    ce = environment()
    ce.require('spectre')
    text, src = _netlist_text(netlist)
    wd = make_workdir('aspice_spectre_', workdir)
    nd, raw = Path(wd) / 'netlist', Path(wd) / 'psf'
    nd.mkdir(exist_ok=True)
    raw.mkdir(exist_ok=True)
    if src is not None and copy_siblings:
        for f in src.parent.iterdir():
            if f.is_file() and f.stat().st_size < 5_000_000 and f.name != src.name:
                shutil.copy2(f, nd / f.name)
    deck = nd / 'input.scs'
    deck.write_text(text, errors='surrogateescape')
    log = raw / 'spectre.out'
    cmd = [ce.tools['spectre'], 'input.scs', '+escchars', '=log', str(log),
           '-format', fmt, '-raw', str(raw)] + list(args)
    rc, out, secs = _run(cmd, cwd=str(nd), timeout=timeout)
    ltxt = log.read_text(errors='replace') if log.is_file() else out
    info = parse_spectre_log(ltxt)
    if not log.is_file():
        info['errors'].append(out[-2000:])
    res = SpectreResult(ok=(rc == 0 and info['completed'] and info['n_errors'] == 0),
                        workdir=wd, netlist_path=str(deck), raw_dir=str(raw),
                        log_path=str(log), returncode=rc, seconds=secs, log=info)
    if raw.is_dir():
        res.analyses = read_psf_dir(raw)
    return res


def cross_check(netlist, nodes=None, rtol=1e-3, atol=1e-6, models=None):
    """Same netlist through Spectre and through the ngspice translation.

    Returns {'rows': [(node, spectre, ngspice, abs_err, ok)], 'ok': bool,
    'spectre': SpectreResult, 'ngspice': dict}. Meaningful only when both
    simulators can read the models (e.g. a PTM card included with
    ``simulator lang=spice``); a licensed Spectre-only PDK cannot be read by
    ngspice and shows up as an ngspice failure, not a numeric match.
    """
    import spectre_netlist as SN
    import spectre_to_ngspice as TR
    text, _ = _netlist_text(netlist)
    sp = run_spectre(text)
    ng = TR.simulate(SN.parse_netlist(text), models=models)
    rows, ok = [], sp.ok and ng.get('ok')
    if ok:
        s_op = sp.op()
        n_op = {k.lower(): v for k, v in (ng.get('nodes') or {}).items()}
        tr = TR.translate(SN.parse_netlist(text))
        names = nodes or [k for k, v in s_op.items()
                          if isinstance(v, float) and ':' not in k and '.' not in k]
        for n in names:
            key = tr.node_map.get(n, n).lower()
            a, b = s_op.get(n), n_op.get(key)
            if a is None or b is None:
                rows.append((n, a, b, None, False))
                ok = False
                continue
            err = abs(a - b)
            good = err <= atol + rtol * abs(a)
            ok = ok and good
            rows.append((n, a, b, err, good))
    return {'rows': rows, 'ok': bool(ok), 'spectre': sp, 'ngspice': ng}


def format_cross_check(cc):
    lines = ['%-14s %16s %16s %12s' % ('node', 'spectre', 'ngspice', '|err|')]
    for n, a, b, e, good in cc['rows']:
        lines.append('%-14s %16s %16s %12s %s' % (
            n, '%.9g' % a if a is not None else '-', '%.9g' % b if b is not None else '-',
            '%.3g' % e if e is not None else '-', 'ok' if good else 'MISMATCH'))
    if not cc['rows']:
        lines.append('  spectre ok=%s  ngspice ok=%s %s' % (
            cc['spectre'].ok, cc['ngspice'].get('ok'), cc['ngspice'].get('error') or ''))
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# PDK characterisation through Spectre (reads Spectre-dialect kits directly)
# ---------------------------------------------------------------------------

# Spectre's oppoint `region` code for MOS devices.
REGION_NAMES = {0: 'off', 1: 'triode', 2: 'saturation', 3: 'subthreshold', 4: 'breakdown'}


def spectre_probe(model_file, device, section='tt', polarity='n', w=1e-6, l=None,
                  vgs=0.6, vds=0.6, vbs=0.0, nf=1, sweep_vgs=None, extra='',
                  workdir=None):
    """Bias one PDK device in Spectre and return its oppoint record.

    ``device`` is the name a schematic instantiates (g45n1svt, nch, ...).
    With ``sweep_vgs=(start, stop, step)`` also returns Id(Vgs) and gm/Id
    arrays from a DC sweep at the same Vds.
    """
    s = 1 if polarity == 'n' else -1
    l = l or 100e-9
    sec = ' section=%s' % section if section else ''
    deck = '\n'.join([
        'simulator lang=spectre', 'global 0',
        'include "%s"%s' % (model_file, sec),
        'parameters vgs=%g vds=%g vbs=%g' % (s * vgs, s * vds, s * vbs),
        'VD (d 0) vsource dc=vds', 'VG (g 0) vsource dc=vgs',
        'VB (b 0) vsource dc=vbs',
        'DUT (d g 0 b) %s w=%g l=%g nf=%d %s' % (device, w, l, nf, extra),
        'dcOp dc', 'dcOpInfo info what=oppoint where=rawfile',
    ])
    if sweep_vgs:
        a, b, st = sweep_vgs
        deck += '\nswp dc param=vgs start=%g stop=%g step=%g' % (s * a, s * b, s * st)
    r = run_spectre(deck + '\n', workdir=workdir, timeout=300)
    out = {'ok': r.ok, 'device': device, 'section': section, 'w': w, 'l': l,
           'vgs': vgs, 'vds': vds, 'result': r}
    if not r.ok:
        out['error'] = '; '.join(e.replace('\n', ' ')[:200] for e in r.errors[:3]) or \
            'spectre failed (see %s)' % r.log_path
        return out
    devs = r.devices()
    rec = devs.get('DUT') or next((v for k, v in devs.items() if k.startswith('DUT')), None)
    if rec:
        out['op'] = rec
        for k in ('ids', 'gm', 'gds', 'gmbs', 'vth', 'vdsat', 'cgg', 'cgs', 'cgd',
                  'ft', 'region', 'gmoverid', 'self_gain'):
            if k in rec:
                out[k] = rec[k]
        if isinstance(out.get('region'), float):
            out['region_name'] = REGION_NAMES.get(int(out['region']), str(out['region']))
        if out.get('gm') and out.get('ids'):
            out['gm_id'] = abs(out['gm'] / out['ids'])
        if out.get('gm') and out.get('gds'):
            out['intrinsic_gain'] = abs(out['gm'] / out['gds'])
    if sweep_vgs and 'swp' in r.analyses:
        d = r.analyses['swp']
        idd = -d.traces['VD:p'] * s
        out['sweep'] = {'vgs': np.abs(d.x), 'id': idd,
                        'gm': np.gradient(idd, np.abs(d.x))}
        with np.errstate(divide='ignore', invalid='ignore'):
            out['sweep']['gm_id'] = out['sweep']['gm'] / idd
    return out


# ---------------------------------------------------------------------------
# Library files (pure Python; no Cadence needed)
# ---------------------------------------------------------------------------

def _expand(text, env):
    """$VAR / ${VAR} / ~ against ``env`` (the Cadence env, not just os.environ)."""
    text = os.path.expanduser(text)
    return re.sub(r'\$\{?(\w+)\}?', lambda m: env.get(m.group(1), m.group(0)), text)


def read_cds_lib(path=None, _seen=None):
    """Parse a cds.lib (DEFINE / UNDEFINE / INCLUDE / SOFTINCLUDE, $VAR and
    relative paths resolved against the file that names them).

    Returns {lib: {'path': str, 'exists': bool, 'defined_in': str}}. A library
    whose directory is missing is exactly what makes Virtuoso say "library
    not found" at open time; this finds it without starting Virtuoso.
    """
    path = Path(path or environment().cds_lib or 'cds.lib').expanduser().resolve()
    _seen = _seen if _seen is not None else set()
    libs = {}
    if path in _seen or not path.is_file():
        return libs
    _seen.add(path)
    env = dict(environment().env)
    for raw in path.read_text(errors='replace').splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        key = parts[0].upper()
        if key in ('INCLUDE', 'SOFTINCLUDE') and len(parts) > 1:
            inc = Path(_expand(parts[1], env))
            if not inc.is_absolute():
                inc = path.parent / inc
            libs.update(read_cds_lib(inc, _seen))
        elif key == 'DEFINE' and len(parts) > 2:
            lp = Path(_expand(parts[2], env))
            if not lp.is_absolute():
                lp = path.parent / lp
            libs[parts[1]] = {'path': str(lp), 'exists': lp.is_dir(), 'defined_in': str(path)}
        elif key == 'UNDEFINE' and len(parts) > 1:
            libs.pop(parts[1], None)
    return libs


def library_cells(lib_path):
    """{cell: [views]} straight from a library directory on disk (a view is a
    subdirectory holding a master file, e.g. sch.oa / layout.oa)."""
    out = {}
    root = Path(lib_path)
    if not root.is_dir():
        return out
    for c in sorted(root.iterdir()):
        if not c.is_dir() or c.name.startswith('.'):
            continue
        views = sorted(v.name for v in c.iterdir() if v.is_dir() and any(
            f.suffix == '.oa' or f.name.endswith('.state') or f.name == 'maestro.sdb'
            for f in v.iterdir()))
        if views:
            # OA escapes characters in directory names: closed-5ota -> closed#2d5ota
            out[re.sub(r'#([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), c.name)] = views
    return out


def layermap_from_calibre(layer_inc, out_path, purposes=('drawing',)):
    """Write a Virtuoso stream layer map from a Calibre ``layer NAME NUM``
    file (e.g. FreePDK45's calibre/layer.inc, which ships no .layermap)."""
    rows = []
    for m in re.finditer(r'^\s*layer\s+(\w+)\s+(\d+)\s*$', Path(layer_inc).read_text(), re.M | re.I):
        for pur in purposes:
            rows.append('%-12s %-10s %s 0' % (m.group(1), pur, m.group(2)))
    Path(out_path).write_text('\n'.join(rows) + '\n')
    return str(out_path)


def find_layer_map(tech_lib, workdir=None, cds_lib=None):
    """A stream layer map for a technology library: a *.layermap shipped in
    or near the library, else one generated from a Calibre layer.inc in the
    same kit. None when neither exists."""
    info = read_cds_lib(cds_lib).get(tech_lib)
    if not info:
        return None
    base = Path(info['path'])
    for anc in [base, base.parent, base.parent.parent]:
        hits = sorted(anc.glob('*.layermap')) + sorted(anc.glob('*/*.layermap'))
        if hits:
            return str(hits[0])
    for anc in [base.parent, base.parent.parent, base.parent.parent.parent]:
        hits = sorted(anc.glob('**/calibre/layer.inc'))
        if hits:
            wd = make_workdir('aspice_layermap_', workdir)
            return layermap_from_calibre(hits[0], Path(wd) / ('%s.layermap' % tech_lib))
    return None


# ---------------------------------------------------------------------------
# Virtuoso (SKILL, OCEAN) - read-only access to the user's libraries
# ---------------------------------------------------------------------------

_MARK = 'ASPICE|'


def _scratch_cds_lib(wd, cds_lib=None, scratch_libs=()):
    cds_lib = cds_lib or environment().cds_lib
    lines = []
    if cds_lib:
        lines.append('INCLUDE %s' % Path(cds_lib).expanduser().resolve())
    for name in scratch_libs:
        lines.append('DEFINE %s %s' % (name, Path(wd) / name))
    (Path(wd) / 'cds.lib').write_text('\n'.join(lines) + '\n')


def _skill_records(log_text):
    recs = []
    for line in log_text.splitlines():
        if line.startswith('\\o ' + _MARK):
            recs.append(line[3 + len(_MARK):].split('|'))
    return recs


@dataclass
class SkillResult:
    ok: bool
    records: list
    errors: list
    log_path: str
    workdir: str
    seconds: float


def run_skill(code, workdir=None, cds_lib=None, timeout=600, scratch_libs=(),
              ocean=False):
    """Run SKILL in a headless Virtuoso (``-nograph``) inside a scratch dir.

    Print results as ``printf("ASPICE|kind|field|...\\n" ...)``; they come
    back as ``records`` (lists of fields). The code runs inside ``errset`` so
    a SKILL error is reported, not hung on. ``ocean=True`` uses the OCEAN
    executable (needed for simulator/design/createNetlist).
    """
    ce = environment()
    ce.require('ocean' if ocean else 'virtuoso')
    wd = make_workdir('aspice_skill_', workdir)
    _scratch_cds_lib(wd, cds_lib, scratch_libs)
    for name in scratch_libs:
        (Path(wd) / name).mkdir(exist_ok=True)
    script = Path(wd) / ('run.ocn' if ocean else 'run.il')
    script.write_text(
        'let((res) res = errset(progn(\n%s\n) t)\n'
        '  unless(res printf("%sERROR|%%L\\n" errset.errset)))\nexit()\n' % (code, _MARK))
    log = Path(wd) / 'CDS.log'
    if ocean:
        cmd = [ce.tools['ocean'], '-nograph', '-replay', str(script), '-log', str(log)]
    else:
        cmd = [ce.tools['virtuoso'], '-nograph', '-nocdsinit', '-replay', str(script),
               '-log', str(log)]
    rc, out, secs = _run(cmd, cwd=wd, timeout=timeout)
    text = log.read_text(errors='replace') if log.is_file() else out
    recs = _skill_records(text)
    errs = [r[1] for r in recs if r and r[0] == 'ERROR']
    errs += [ln[3:].strip() for ln in text.splitlines() if ln.startswith('\\e ') or
             (ln.startswith('\\w ') and '*Error*' in ln)]
    if rc == -9:
        errs.append('timed out after %ss' % timeout)
    return SkillResult(ok=(rc == 0 and not errs), records=[r for r in recs if r[0] != 'ERROR'],
                       errors=errs, log_path=str(log), workdir=wd, seconds=secs)


def _read_safe(p):
    try:
        return Path(p).read_text(errors='replace')
    except OSError:
        return ''


def _q(s):
    return '"%s"' % str(s).replace('\\', '\\\\').replace('"', '\\"')


def libraries(cds_lib=None):
    """{lib: path} for every library the user's cds.lib defines."""
    r = run_skill('foreach(l ddGetLibList() printf("%sLIB|%%s|%%s\\n" l~>name l~>readPath))'
                  % _MARK, cds_lib=cds_lib)
    return {rec[1]: rec[2] for rec in r.records if rec[0] == 'LIB'}


def cells(lib, cds_lib=None):
    """{cell: [views]} for one library."""
    r = run_skill(
        'let((l) l = ddGetObj(%s) unless(l error("no library %s"))\n'
        'foreach(c l~>cells printf("%sCELL|%%s|%%s\\n" c~>name '
        'buildString(c~>views~>name ","))))' % (_q(lib), lib, _MARK), cds_lib=cds_lib)
    if r.errors:
        raise RuntimeError('; '.join(r.errors))
    return {rec[1]: [v for v in rec[2].split(',') if v] for rec in r.records if rec[0] == 'CELL'}


_SCH_DUMP = r'''
cv = dbOpenCellViewByType(%(lib)s %(cell)s %(view)s nil "r")
unless(cv error("cannot open %(lib_raw)s/%(cell_raw)s/%(view_raw)s"))
printf("ASPICE|CV|%%s|%%s|%%s|%%s\n" cv~>libName cv~>cellName cv~>viewName cv~>cellViewType)
foreach(tm cv~>terminals printf("ASPICE|PIN|%%s|%%s\n" tm~>name tm~>direction))
foreach(i cv~>instances
  printf("ASPICE|INST|%%s|%%s|%%s|%%s\n" i~>name i~>libName i~>cellName i~>viewName)
  foreach(it i~>instTerms
    printf("ASPICE|CONN|%%s|%%s|%%s\n" i~>name it~>name if(it~>net it~>net~>name "")))
  foreach(p i~>prop
    printf("ASPICE|PROP|%%s|%%s|%%L\n" i~>name p~>name p~>value)))
foreach(n cv~>nets printf("ASPICE|NET|%%s|%%d|%%L\n" n~>name length(n~>instTerms)
  if(n~>term t nil)))
dbClose(cv)
'''


def _unquote(v):
    v = v.strip()
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v


@dataclass
class Schematic:
    lib: str
    cell: str
    view: str
    pins: dict = field(default_factory=dict)        # name -> direction
    instances: dict = field(default_factory=dict)   # name -> {master, conns, params}
    nets: dict = field(default_factory=dict)        # name -> {fanout, is_pin}

    def floating_nets(self):
        """Nets touching fewer than two terminals that are not cell pins."""
        return sorted(n for n, d in self.nets.items()
                      if d['fanout'] < 2 and not d['is_pin'])

    def unconnected_terms(self):
        return sorted('%s.%s' % (i, t) for i, d in self.instances.items()
                      for t, n in d['conns'].items() if not n)

    def summary(self):
        lines = ['%s/%s/%s: %d instances, %d nets, %d pins' % (
            self.lib, self.cell, self.view, len(self.instances), len(self.nets),
            len(self.pins))]
        if self.pins:
            lines.append('  pins: ' + ', '.join('%s(%s)' % kv for kv in sorted(self.pins.items())))
        for name, d in sorted(self.instances.items()):
            conns = ' '.join('%s=%s' % kv for kv in d['conns'].items())
            keys = ('w', 'l', 'nf', 'fingers', 'm', 'r', 'c', 'vdc', 'idc', 'model')
            ps = ' '.join('%s=%s' % (k, d['params'][k]) for k in keys if k in d['params'])
            lines.append('  %-8s %-24s %s  %s' % (name, d['master'], conns, ps))
        fl, uc = self.floating_nets(), self.unconnected_terms()
        if fl:
            lines.append('  floating nets: ' + ', '.join(fl))
        if uc:
            lines.append('  unconnected terminals: ' + ', '.join(uc))
        return '\n'.join(lines)


def schematic(lib, cell, view='schematic', cds_lib=None):
    """Read a schematic (read-only): pins, instances with connectivity and
    CDF parameters, nets with fanout."""
    code = _SCH_DUMP % dict(lib=_q(lib), cell=_q(cell), view=_q(view),
                            lib_raw=lib, cell_raw=cell, view_raw=view)
    r = run_skill(code, cds_lib=cds_lib)
    if r.errors or not any(rec[0] == 'CV' for rec in r.records):
        raise RuntimeError('schematic(%s/%s/%s): %s' % (lib, cell, view,
                                                       '; '.join(r.errors) or 'no output'))
    s = Schematic(lib, cell, view)
    for rec in r.records:
        k = rec[0]
        if k == 'PIN':
            s.pins[rec[1]] = rec[2]
        elif k == 'INST':
            s.instances[rec[1]] = {'master': '%s/%s' % (rec[2], rec[3]), 'conns': {},
                                   'params': {}}
        elif k == 'CONN':
            s.instances[rec[1]]['conns'][rec[2]] = rec[3]
        elif k == 'PROP':
            s.instances[rec[1]]['params'][rec[2]] = _unquote('|'.join(rec[3:]))
        elif k == 'NET':
            s.nets[rec[1]] = {'fanout': int(rec[2]), 'is_pin': rec[3].strip() == 't'}
    return s


def check_schematic(lib, cell, view='schematic', cds_lib=None):
    """Run Virtuoso's schematic check (schCheck) on a scratch COPY of the
    cellview, so the user's view is never opened for writing. Returns
    {'errors': n, 'warnings': n, 'markers': [...]}."""
    code = r'''
src = dbOpenCellViewByType(%(lib)s %(cell)s %(view)s nil "r")
unless(src error("cannot open source cellview"))
ddCreateLib("aspice_scratch" "%(wd)s/aspice_scratch")
dst = dbCopyCellView(src "aspice_scratch" %(cell)s %(view)s)
unless(dst error("copy failed"))
dbClose(src)
dst = dbOpenCellViewByType("aspice_scratch" %(cell)s %(view)s nil "a")
res = schCheck(dst)
printf("ASPICE|CHECK|%%d|%%d\n" car(res) cadr(res))
foreach(m dst~>markers printf("ASPICE|MARKER|%%s|%%s\n" m~>severity m~>msg))
dbClose(dst)
'''
    wd = make_workdir('aspice_schcheck_')
    r = run_skill(code % dict(lib=_q(lib), cell=_q(cell), view=_q(view), wd=wd),
                  workdir=wd, cds_lib=cds_lib)
    chk = [rec for rec in r.records if rec[0] == 'CHECK']
    if r.errors or not chk:
        raise RuntimeError('check_schematic: %s' % ('; '.join(r.errors) or 'no output'))
    return {'errors': int(chk[0][1]), 'warnings': int(chk[0][2]),
            'markers': [(rec[1], '|'.join(rec[2:])) for rec in r.records if rec[0] == 'MARKER'],
            'workdir': wd}


SCRATCH_LIB = 'aspice_scratch'


def copy_cell(lib, cell, views=('schematic', 'symbol'), dst_lib=SCRATCH_LIB, workdir=None,
              cds_lib=None):
    """Copy a cell into a scratch library inside a new workdir. Returns the
    scratch cds.lib path: pass it as ``cds_lib=`` to edit_instance_params,
    netlist_cell, simulate_cell, schematic, check_schematic."""
    wd = make_workdir('aspice_copy_', workdir)
    code = 'ddCreateLib(%s %s)\n' % (_q(dst_lib), _q(Path(wd) / dst_lib))
    for v in views:
        code += ('when(src = dbOpenCellViewByType(%s %s %s nil "r")\n'
                 '  dbCopyCellView(src %s %s %s) dbClose(src)\n'
                 '  printf("%sCOPIED|%s\\n"))\n' % (_q(lib), _q(cell), _q(v), _q(dst_lib),
                                                      _q(cell), _q(v), _MARK, v))
    r = run_skill(code, workdir=wd, cds_lib=cds_lib, scratch_libs=())
    copied = [rec[1] for rec in r.records if rec[0] == 'COPIED']
    if r.errors or not copied:
        raise RuntimeError('copy_cell(%s/%s): %s' % (lib, cell, '; '.join(r.errors) or 'nothing copied'))
    return str(Path(wd) / 'cds.lib')


def edit_instance_params(lib, cell, edits, view='schematic', cds_lib=None, allow_write=False):
    """Set CDF parameters on schematic instances and save, then schCheck.

    ``edits`` = {'NM0': {'w': '2u', 'l': '90n'}, ...}. Refuses to write any
    library other than the scratch library unless ``allow_write=True``: the
    normal flow is copy_cell -> edit -> netlist/simulate the copy.
    """
    if lib != SCRATCH_LIB and not allow_write:
        raise PermissionError(
            'refusing to modify %s/%s in place; use copy_cell() first or pass '
            'allow_write=True' % (lib, cell))
    sets = ''
    for inst, params in edits.items():
        sets += 'inst = car(setof(i cv~>instances i~>name == %s))\n' % _q(inst)
        sets += 'unless(inst error("no instance %s"))\n' % inst
        for k, v in params.items():
            sets += ('let((p) p = cdfFindParamByName(cdfGetInstCDF(inst) %s)\n'
                     '  if(p then p~>value = %s else dbReplaceProp(inst %s "string" %s))\n'
                     '  printf("%sSET|%s|%s|%%L\\n" if(p p~>value dbGetPropByName(inst %s)~>value)))\n'
                     % (_q(k), _q(v), _q(k), _q(v), _MARK, inst, k, _q(k)))
    code = ('cv = dbOpenCellViewByType(%s %s %s nil "a")\n'
            'unless(cv error("cannot open for edit"))\n%s'
            'res = schCheck(cv)\nprintf("%sCHECK|%%d|%%d\\n" car(res) cadr(res))\n'
            'dbSave(cv)\ndbClose(cv)\n' % (_q(lib), _q(cell), _q(view), sets, _MARK))
    r = run_skill(code, cds_lib=cds_lib)
    if r.errors:
        raise RuntimeError('edit_instance_params: %s' % '; '.join(r.errors))
    chk = next((rec for rec in r.records if rec[0] == 'CHECK'), None)
    return {'set': [(rec[1], rec[2], _unquote(rec[3])) for rec in r.records if rec[0] == 'SET'],
            'check_errors': int(chk[1]) if chk else None,
            'check_warnings': int(chk[2]) if chk else None}


_BUILD_SKILL = r"""
procedure(aspiceStub(cv inst termName net)
  let((tm fig box cx cy ib icx icy dx dy p2 w)
    ; a new instance has no instTerms until connected; use the master's terminal
    tm = car(setof(x inst~>master~>terminals x~>name == termName))
    unless(tm error(sprintf(nil "%s has no terminal %s (has %L)" inst~>name termName
                            inst~>master~>terminals~>name)))
    fig = car(car(tm~>pins)~>figs)
    box = dbTransformBBox(fig~>bBox inst~>transform)
    cx = (caar(box) + caadr(box)) / 2.0  cy = (cadar(box) + cadadr(box)) / 2.0
    ib = inst~>bBox
    icx = (caar(ib) + caadr(ib)) / 2.0  icy = (cadar(ib) + cadadr(ib)) / 2.0
    dx = cx - icx  dy = cy - icy
    if(abs(dx) > abs(dy) then p2 = list(cx + if(dx > 0 0.25 -0.25) cy)
                         else p2 = list(cx cy + if(dy > 0 0.25 -0.25)))
    w = car(schCreateWire(inst~>cellView "draw" "full" list(list(cx cy) p2) 0.0625 0.0625 0.0))
    schCreateWireLabel(inst~>cellView w p2 net "lowerLeft" "R0" "stick" 0.0625 nil)))
"""


def build_schematic(cell, devices, pins=(), lib=SCRATCH_LIB, workdir=None, cds_lib=None):
    """Create a schematic in a scratch library from a device list. Returns
    the scratch cds.lib path (use as ``cds_lib=`` for netlist_cell etc.).

    ``devices``: [(name, master_lib, master_cell, {cdf_param: value},
    {terminal: net}), ...] e.g. ``('R0', 'analogLib', 'res', {'r': '1k'},
    {'PLUS': 'a', 'MINUS': 'gnd!'})``. Connectivity is made with a short wire
    stub and a net label on every terminal, then schCheck runs. ``pins``:
    [(net, 'input'|'output'|'inputOutput'), ...].

    This replaces the export -> copy -> spiceIn round trip when Virtuoso is
    on the same machine: the circuit is built, checked and netlisted here.
    """
    wd = make_workdir('aspice_build_', workdir)
    code = [_BUILD_SKILL,
            'unless(ddGetObj(%s) ddCreateLib(%s %s))' % (_q(lib), _q(lib), _q(Path(wd) / lib)),
            'cv = dbOpenCellViewByType(%s %s "schematic" "schematic" "w")' % (_q(lib), _q(cell)),
            'unless(cv error("cannot create schematic"))']
    for k, (name, mlib, mcell, params, conns) in enumerate(devices):
        code.append('master = dbOpenCellViewByType(%s %s "symbol" nil "r")' % (_q(mlib), _q(mcell)))
        code.append('unless(master error("no symbol %s/%s"))' % (mlib, mcell))
        code.append('inst = dbCreateInst(cv master %s list(%g 0.0) "R0")' % (_q(name), 3.0 * k))
        for pk, pv in (params or {}).items():
            code.append('let((p) p = cdfFindParamByName(cdfGetInstCDF(inst) %s) '
                        'if(p then p~>value = %s else dbReplaceProp(inst %s "string" %s)))'
                        % (_q(pk), _q(pv), _q(pk), _q(pv)))
        for term, net in conns.items():
            code.append('aspiceStub(cv inst %s %s)' % (_q(term), _q(net)))
    dirs = {'input': 'ipin', 'output': 'opin', 'inputOutput': 'iopin'}
    for k, (net, direction) in enumerate(pins):
        code.append('pm = dbOpenCellViewByType("basic" %s "symbol" nil "r")' % _q(dirs[direction]))
        code.append('pin = schCreatePin(cv pm %s %s nil list(%g -4.0) "R0")'
                    % (_q(net), _q(direction), 1.5 * k))
        code.append('aspiceStub(cv pin car(pin~>master~>terminals)~>name %s)' % _q(net))
    code += ['res = schCheck(cv)',
             'printf("%sCHECK|%%d|%%d\\n" car(res) cadr(res))' % _MARK,
             'dbSave(cv) dbClose(cv)']
    sym = None
    r = run_skill('\n'.join(code), workdir=wd, cds_lib=cds_lib)
    chk = next((rec for rec in r.records if rec[0] == 'CHECK'), None)
    if r.errors or not chk:
        raise RuntimeError('build_schematic(%s): %s' % (cell, '; '.join(r.errors) or 'no check'))
    if int(chk[1]):
        raise RuntimeError('build_schematic(%s): schCheck reported %s errors' % (cell, chk[1]))
    return str(Path(wd) / 'cds.lib')


def create_layout(cell, shapes, tech_lib, labels=(), lib=SCRATCH_LIB, workdir=None,
                  cds_lib=None):
    """Draw rectangles (and labels) into a scratch-library layout attached to
    ``tech_lib``. ``shapes``: [(layer, purpose, x0, y0, x1, y1), ...] in um.
    Returns the scratch cds.lib path. For DRC/LVS experiments and tests."""
    wd = make_workdir('aspice_layout_', workdir)
    code = ['unless(ddGetObj(%s) ddCreateLib(%s %s))' % (_q(lib), _q(lib), _q(Path(wd) / lib)),
            'techBindTechFile(ddGetObj(%s) %s "tech.oa" t)' % (_q(lib), _q(tech_lib)),
            'cv = dbOpenCellViewByType(%s %s "layout" "maskLayout" "w")' % (_q(lib), _q(cell)),
            'unless(cv error("cannot create layout"))']
    for layer, purpose, x0, y0, x1, y1 in shapes:
        code.append('dbCreateRect(cv list(%s %s) list(list(%g %g) list(%g %g)))'
                    % (_q(layer), _q(purpose), x0, y0, x1, y1))
    for text, layer, x, y in labels:
        code.append('dbCreateLabel(cv list(%s "drawing") list(%g %g) %s "centerCenter" "R0" '
                    '"stick" 0.05)' % (_q(layer), x, y, _q(text)))
    code += ['printf("%sSHAPES|%%d\\n" length(cv~>shapes))' % _MARK, 'dbSave(cv) dbClose(cv)']
    r = run_skill('\n'.join(code), workdir=wd, cds_lib=cds_lib)
    if r.errors or not any(rec[0] == 'SHAPES' for rec in r.records):
        raise RuntimeError('create_layout(%s): %s' % (cell, '; '.join(r.errors) or 'no output'))
    return str(Path(wd) / 'cds.lib')


_LAY_DUMP = r'''
cv = dbOpenCellViewByType(%(lib)s %(cell)s %(view)s nil "r")
unless(cv error("cannot open layout"))
b = cv~>bBox
printf("ASPICE|BBOX|%%g|%%g|%%g|%%g\n" caar(b) cadar(b) caadr(b) cadadr(b))
printf("ASPICE|TECH|%%s\n" if(techGetTechFile(cv) techGetTechFile(cv)~>libName ""))
foreach(i cv~>instances printf("ASPICE|INST|%%s|%%s|%%s|%%g|%%g|%%s\n"
  i~>name i~>libName i~>cellName car(i~>xy) cadr(i~>xy) i~>orient))
foreach(l cv~>lpps printf("ASPICE|LPP|%%s|%%s|%%d\n" l~>layerName l~>purpose length(l~>shapes)))
foreach(s cv~>shapes when(s~>objType == "label"
  printf("ASPICE|LABEL|%%s|%%s|%%g|%%g\n" s~>theLabel s~>layerName car(s~>xy) cadr(s~>xy))))
foreach(tm cv~>terminals printf("ASPICE|PIN|%%s|%%s|%%d\n" tm~>name tm~>direction length(tm~>pins)))
dbClose(cv)
'''


def layout(lib, cell, view='layout', cds_lib=None):
    """Read a layout (read-only): bbox, tech library, instances, shape counts
    per layer/purpose, labels and pins."""
    r = run_skill(_LAY_DUMP % dict(lib=_q(lib), cell=_q(cell), view=_q(view)),
                  cds_lib=cds_lib)
    if r.errors:
        raise RuntimeError('layout(%s/%s/%s): %s' % (lib, cell, view, '; '.join(r.errors)))
    out = {'lib': lib, 'cell': cell, 'view': view, 'instances': [], 'lpps': {},
           'labels': [], 'pins': {}, 'bbox': None, 'tech': None}
    for rec in r.records:
        k = rec[0]
        if k == 'BBOX':
            out['bbox'] = tuple(float(x) for x in rec[1:5])
        elif k == 'TECH':
            out['tech'] = rec[1] or None
        elif k == 'INST':
            out['instances'].append({'name': rec[1], 'master': '%s/%s' % (rec[2], rec[3]),
                                     'xy': (float(rec[4]), float(rec[5])), 'orient': rec[6]})
        elif k == 'LPP':
            out['lpps']['%s/%s' % (rec[1], rec[2])] = int(rec[3])
        elif k == 'LABEL':
            out['labels'].append({'text': rec[1], 'layer': rec[2],
                                  'xy': (float(rec[3]), float(rec[4]))})
        elif k == 'PIN':
            out['pins'][rec[1]] = {'direction': rec[2], 'figs': int(rec[3])}
    return out


def netlist_cell(lib, cell, view='schematic', design_vars=None, section=None,
                 analyses='', workdir=None, cds_lib=None):
    """Netlist a schematic for Spectre with OCEAN, headlessly.

    OCEAN's project directory is pointed into the scratch workdir. Returns
    the path of the generated ``input.scs``. ``section`` rewrites the model
    include's ``section=`` (ADE defaults gpdk045 to ``mc``); ``analyses`` is
    Spectre text appended to the netlist (e.g. ``"dcOp dc\\n"``).
    """
    wd = make_workdir('aspice_netlist_', workdir)
    dv = ''.join('desVar(%s %s)\n' % (_q(k), _q(v) if isinstance(v, str) else v)
                 for k, v in (design_vars or {}).items())
    code = '''
envSetVal("asimenv.startup" "projectDir" 'string %(proj)s)
simulator('spectre)
design(%(lib)s %(cell)s %(view)s)
resultsDir(%(res)s)
%(dv)s
printf("ASPICE|NETLIST|%%s\\n" createNetlist(?recreateAll t ?display nil))
''' % dict(proj=_q(Path(wd) / 'proj'), res=_q(Path(wd) / 'proj' / 'res'), lib=_q(lib),
           cell=_q(cell), view=_q(view), dv=dv)
    r = run_skill(code, workdir=wd, cds_lib=cds_lib, ocean=True)
    paths = [rec[1] for rec in r.records if rec[0] == 'NETLIST' and rec[1] not in ('nil', '')]
    if not paths or not Path(paths[0]).is_file():
        raise RuntimeError('netlist_cell(%s/%s): %s' % (lib, cell,
                                                        '; '.join(r.errors) or 'no netlist'))
    p = Path(paths[0])
    if section or analyses or design_vars:
        text = p.read_text()
        if section:
            text = re.sub(r'(^include\s+"[^"]+"\s+section=)\w+', r'\g<1>%s' % section,
                          text, flags=re.M)
        if design_vars:
            params = ' '.join('%s=%s' % kv for kv in design_vars.items())
            text = re.sub(r'^(global 0\s*\n)', r'\1parameters %s\n' % params, text,
                          count=1, flags=re.M)
        if analyses:
            text = text.rstrip('\n') + '\n' + analyses.strip('\n') + '\n'
        p.write_text(text)
    return str(p)


def simulate_cell(lib, cell, analyses='dcOp dc\ndcOpInfo info what=oppoint where=rawfile\n',
                  section='tt', design_vars=None, view='schematic', cds_lib=None):
    """Schematic -> netlist -> Spectre -> parsed results, all headless."""
    path = netlist_cell(lib, cell, view=view, design_vars=design_vars, section=section,
                        analyses=analyses, cds_lib=cds_lib)
    return run_spectre(path)


def stream_out(lib, cell, gds_path=None, view='layout', layer_map=None, workdir=None,
               cds_lib=None, timeout=600):
    """Export a layout to GDS with strmout (reads the library only)."""
    ce = environment()
    ce.require('strmout')
    wd = make_workdir('aspice_strmout_', workdir)
    _scratch_cds_lib(wd, cds_lib)
    gds = str(Path(gds_path).resolve()) if gds_path else str(Path(wd) / ('%s.gds' % cell))
    cmd = [ce.tools['strmout'], '-library', lib, '-strmFile', gds, '-topCell', cell,
           '-view', view, '-logFile', str(Path(wd) / 'strmOut.log')]
    if layer_map:
        cmd += ['-layerMap', str(layer_map)]
    rc, out, _ = _run(cmd, cwd=wd, timeout=timeout)
    if rc != 0 and not layer_map and 'XSTRM' in out + _read_safe(Path(wd) / 'strmOut.log'):
        tech = layout(lib, cell, view=view, cds_lib=str(Path(wd) / 'cds.lib')).get('tech')
        layer_map = find_layer_map(tech, workdir=wd, cds_lib=cds_lib) if tech else None
        if layer_map:
            rc, out, _ = _run(cmd + ['-layerMap', layer_map], cwd=wd, timeout=timeout)
    if rc != 0 or not Path(gds).is_file():
        log = Path(wd) / 'strmOut.log'
        raise RuntimeError('strmout failed (rc=%s): %s' % (
            rc, (log.read_text(errors='replace') if log.is_file() else out)[-1500:]))
    return gds


def stream_in(gds, lib='aspice_scratch', tech_lib=None, workdir=None, cds_lib=None,
              layer_map=None, timeout=600):
    """Import a GDS into a scratch library inside the workdir (never into a
    user library). Returns (workdir, lib); pass ``workdir`` on to layout()
    via ``cds_lib=<workdir>/cds.lib``."""
    ce = environment()
    ce.require('strmin')
    wd = make_workdir('aspice_strmin_', workdir)
    _scratch_cds_lib(wd, cds_lib)
    cmd = [ce.tools['strmin'], '-library', lib, '-strmFile', str(Path(gds).resolve()),
           '-logFile', str(Path(wd) / 'strmIn.log')]
    if tech_lib:
        cmd += ['-attachTechFileOfLib', tech_lib]
        layer_map = layer_map or find_layer_map(tech_lib, workdir=wd, cds_lib=cds_lib)
    if layer_map:
        cmd += ['-layerMap', str(layer_map)]
    rc, out, _ = _run(cmd, cwd=wd, timeout=timeout)
    log = Path(wd) / 'strmIn.log'
    text = log.read_text(errors='replace') if log.is_file() else out
    if rc != 0 or not (Path(wd) / lib).is_dir():
        raise RuntimeError('strmin failed (rc=%s): %s' % (rc, text[-1500:]))
    return wd, lib


# ---------------------------------------------------------------------------
# Calibre DRC / LVS
# ---------------------------------------------------------------------------

def parse_drc_summary(path_or_text):
    """Calibre DRC summary -> {'total': n, 'checks': n, 'violations': {rule: n}}."""
    t = Path(path_or_text).read_text(errors='replace') if '\n' not in str(path_or_text) \
        else str(path_or_text)
    tot = re.search(r'TOTAL DRC Results Generated:\s+(\d+)', t)
    chk = re.search(r'TOTAL DRC RuleChecks Executed:\s+(\d+)', t)
    viol = {m.group(1): int(m.group(2)) for m in re.finditer(
        r'^RULECHECK\s+(\S+)\s+\.+\s+TOTAL Result Count\s*=\s*(\d+)', t, re.M)
        if int(m.group(2)) > 0}
    return {'total': int(tot.group(1)) if tot else None,
            'checks': int(chk.group(1)) if chk else None, 'violations': viol}


def parse_lvs_report(path_or_text):
    """Calibre LVS report -> {'status': 'CORRECT'|'INCORRECT'|..., 'match': bool,
    'cells': [(result, layout_cell, source_cell)], 'counts': {'ports'|'nets'|
    'instances': (layout, source)}, 'warnings': [...]}."""
    t = Path(path_or_text).read_text(errors='replace') if '\n' not in str(path_or_text) \
        else str(path_or_text)
    # The OVERALL COMPARISON RESULTS banner spells the verdict in ASCII art;
    # the per-cell table carries it in plain words.
    status = None
    m = re.search(r'OVERALL COMPARISON RESULTS(.*?)(?:\n\s*\*{5,}|CELL\s+SUMMARY)', t, re.S)
    block = m.group(1) if m else t
    for word in ('INCORRECT', 'NOT COMPARED', 'CORRECT'):
        if re.search(r'\b%s\b' % word, block):
            status = word
            break
    if status is None:
        m2 = re.search(r'^\s*(CORRECT|INCORRECT|NOT COMPARED)\s', t, re.M)
        status = m2.group(1) if m2 else 'UNKNOWN'
    cs = re.search(r'CELL\s+SUMMARY\s*\n\*+\n(.*?)\n\*{10,}', t, re.S)
    cells = re.findall(r'^\s*(CORRECT|INCORRECT|NOT COMPARED)\s+(\S+)\s+(\S+)\s*$',
                       cs.group(1) if cs else '', re.M)
    counts = {}
    m = re.search(r'INITIAL NUMBERS OF OBJECTS(.*?)Total Inst:\s+(\d+)\s+(\d+)', t, re.S)
    if m:
        for kind in ('Ports', 'Nets'):
            mm = re.search(r'%s:\s+(\d+)\s+(\d+)' % kind, m.group(1))
            if mm:
                counts[kind.lower()] = (int(mm.group(1)), int(mm.group(2)))
        counts['instances'] = (int(m.group(2)), int(m.group(3)))
    warns = re.findall(r'^\s*(?:Warning|WARNING)[^\n]*', t, re.M)
    return {'status': status, 'match': status == 'CORRECT', 'cells': cells,
            'counts': counts, 'warnings': warns[:50]}


def rule_env(rules):
    """Environment variables a Calibre deck needs, inferred from the deck.

    Kit decks say ``include $PDK_DIR/ncsu_basekit/...``. When the variable is
    unset, or set to a directory that does not hold the referenced file (a
    stale site setting), the ancestor of the deck that does hold it is used.
    """
    rules = Path(rules).resolve()
    env = environment().env
    out = {}
    for m in re.finditer(r'\$\{?(\w+)\}?(/[^\s"\']+)', _read_safe(rules)):
        var, rel = m.group(1), m.group(2).lstrip('/')
        cur = out.get(var) or env.get(var)
        if cur and (Path(cur) / rel).exists():
            continue
        for anc in rules.parents:
            if (anc / rel).exists():
                out[var] = str(anc)
                break
    return out


def _calibre(args, runset, wd, timeout, rules=None):
    ce = environment()
    ce.require('calibre')
    extra = rule_env(rules) if rules else {}
    rc, out, secs = _run([ce.tools['calibre']] + args + [runset], cwd=wd, timeout=timeout,
                         env_extra=extra)
    (Path(wd) / 'calibre.log').write_text(out)
    return rc, out, secs


# Runset statement -> its keyword. A kit deck that already sets one (FreePDK45's
# LVS deck carries its own LVS REPORT) makes Calibre reject ours as
# "superfluous specification statement", so ours is dropped and the deck's
# file name is used instead.
_SPEC_KEY = re.compile(r'^\s*((?:LVS|DRC|MASK|ERC)\s+[A-Z]+(?:\s+(?:REPORT|DATABASE|DIRECTORY|'
                       r'MAXIMUM|OPTION|RESULTS|GATES|SUPPLY))?)', re.I)


def _deck_value(deck_text, key):
    m = re.search(r'^\s*%s\s+(?!OPTION\b|MAXIMUM\b)"?([^\s"]+)' % key.replace(' ', r'\s+'),
                  deck_text, re.M | re.I)
    return m.group(1) if m else None


def _runset(statements, rules):
    deck = _read_safe(rules)
    keep = []
    for st in statements:
        m = _SPEC_KEY.match(st)
        if m and re.search(r'^\s*%s\b' % m.group(1).replace(' ', r'\s+'), deck, re.M | re.I):
            continue
        keep.append(st)
    keep.append('INCLUDE "%s"' % Path(rules).resolve())
    return '\n'.join(keep) + '\n', deck


def calibre_drc(gds, top, rules, workdir=None, timeout=1800, extra=''):
    """Calibre DRC on a COPY of ``gds`` with the PDK deck ``rules`` (INCLUDEd)."""
    wd = make_workdir('aspice_drc_', workdir)
    shutil.copy2(gds, Path(wd) / 'layout.gds')
    runset = Path(wd) / 'drc.rul'
    text, deck = _runset([
        'LAYOUT PATH "layout.gds"', 'LAYOUT PRIMARY "%s"' % top, 'LAYOUT SYSTEM GDSII',
        'DRC RESULTS DATABASE "drc.results" ASCII', 'DRC MAXIMUM RESULTS 1000',
        'DRC SUMMARY REPORT "drc.summary" REPLACE HIER', extra], rules)
    runset.write_text(text)
    rc, out, secs = _calibre(['-drc', '-hier'], str(runset), wd, timeout, rules)
    summ = Path(wd) / (_deck_value(deck, 'DRC SUMMARY REPORT') or 'drc.summary')
    res = parse_drc_summary(summ) if summ.is_file() else {'total': None, 'violations': {}}
    res.update(ok=(rc == 0 and res.get('total') is not None), clean=res.get('total') == 0,
               workdir=wd, summary_path=str(summ), results_path=str(Path(wd) / 'drc.results'),
               seconds=secs, returncode=rc)
    if not res['ok']:
        res['error'] = out[-1500:]
    return res


def calibre_lvs(gds, top, source_netlist, rules, source_top=None, workdir=None,
                timeout=1800, extra=''):
    """Calibre LVS of a COPY of ``gds`` against a SPICE/CDL source netlist."""
    wd = make_workdir('aspice_lvs_', workdir)
    shutil.copy2(gds, Path(wd) / 'layout.gds')
    shutil.copy2(source_netlist, Path(wd) / 'source.net')
    runset = Path(wd) / 'lvs.rul'
    text, deck = _runset([
        'LAYOUT PATH "layout.gds"', 'LAYOUT PRIMARY "%s"' % top, 'LAYOUT SYSTEM GDSII',
        'SOURCE PATH "source.net"', 'SOURCE PRIMARY "%s"' % (source_top or top),
        'SOURCE SYSTEM SPICE', 'MASK SVDB DIRECTORY "svdb" QUERY',
        'LVS REPORT "lvs.report"', 'LVS REPORT OPTION NONE', 'LVS REPORT MAXIMUM 50',
        'LVS RECOGNIZE GATES ALL', 'LVS ABORT ON SUPPLY ERROR YES', extra], rules)
    runset.write_text(text)
    rc, out, secs = _calibre(['-lvs', '-hier', '-spice', 'layout.sp'], str(runset), wd,
                             timeout, rules)
    rep = Path(wd) / (_deck_value(deck, 'LVS REPORT') or 'lvs.report')
    res = parse_lvs_report(rep) if rep.is_file() else {'status': 'NOT RUN', 'match': False}
    res.update(ok=(rc == 0 and rep.is_file()), workdir=wd, report_path=str(rep),
               extracted_netlist=str(Path(wd) / 'layout.sp'), seconds=secs, returncode=rc)
    if not res['ok']:
        res['error'] = out[-1500:]
    return res


# ---------------------------------------------------------------------------
# ADE / Maestro results already on disk
# ---------------------------------------------------------------------------

def ade_runs(root='~/simulation'):
    """Index ADE/Maestro runs: one dict per point with its netlist, PSF dir
    and spectre.out status. Read-only."""
    root = Path(root).expanduser()
    out = []
    for scs in sorted(root.glob('**/netlist/input.scs')):
        point = scs.parent.parent
        rel = scs.relative_to(root).parts
        psf = point / 'psf'
        so = psf / 'spectre.out'
        st = parse_spectre_log(so.read_text(errors='replace')) if so.is_file() else None
        out.append({
            'lib': rel[0] if len(rel) > 0 else None,
            'cell': rel[1] if len(rel) > 1 else None,
            'history': next((p for p in rel if re.match(
                r'(ExplorerRun|Interactive|GlobalOpt|LocalOpt|MonteCarlo|Sweep)\.', p)), None),
            'point': point.name, 'netlist': str(scs),
            'psf': str(psf) if psf.is_dir() else None,
            'status': None if st is None else (
                'ok' if st['completed'] and st['n_errors'] == 0 else 'failed'),
            'n_errors': st['n_errors'] if st else None,
            'n_warnings': st['n_warnings'] if st else None,
            'mtime': scs.stat().st_mtime,
        })
    return out


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

def doctor(live=True):
    """What is installed, and (``live=True``) whether each tool actually runs
    and licenses: a 2-resistor Spectre run, a SKILL printf, a Calibre version
    call. Returns a printable report."""
    ce = environment(refresh=True)
    lines = ['aspice cadence doctor']
    lines.append('  EDA roots: %s' % (', '.join(ce.roots) or 'none'))
    for t, p in ce.tools.items():
        lines.append('  %-9s %s' % (t, p or 'NOT FOUND'))
    for v in _LICENSE_VARS:
        lines.append('  %-17s %s' % (v, ce.env.get(v) or 'unset'))
    chosen = bool(os.environ.get('ASPICE_CDS_LIB') or load_config().get('cds_lib'))
    lines.append('  cds.lib   %s%s' % (ce.cds_lib or 'not found (cadence.configure(cds_lib=...))',
                                      '' if chosen or not ce.cds_lib else '  (auto-picked)'))
    others = [c for c in cds_lib_candidates() if c != ce.cds_lib]
    if others and not chosen:
        lines.append('  WARNING: several cds.lib files; pin one with cadence.configure(cds_lib=...):')
        lines += ['            %s' % c for c in others[:6]]
    if ce.cds_lib:
        missing = {k: v['path'] for k, v in read_cds_lib(ce.cds_lib).items() if not v['exists']}
        for lib, path in sorted(missing.items()):
            lines.append('  WARNING: library %s -> %s does not exist%s' % (
                lib, path, ' (unset $VAR)' if '$' in path else ''))
    if not live:
        return '\n'.join(lines)
    if ce.tools['spectre']:
        r = run_spectre('simulator lang=spectre\nV1 (a 0) vsource dc=1\n'
                        'R1 (a b) resistor r=1k\nR2 (b 0) resistor r=1k\nop dc\n', timeout=180)
        v = r.op().get('b') if r.ok else None
        lines.append('  spectre run: %s (V(b)=%s, version %s)' % (
            'OK' if r.ok and v and abs(v - 0.5) < 1e-9 else 'FAILED', v, r.log.get('version')))
    if ce.tools['virtuoso']:
        r = run_skill('printf("ASPICE|PING|%s\\n" getVersion())', timeout=300)
        ping = [rec for rec in r.records if rec[0] == 'PING']
        lines.append('  virtuoso SKILL: %s %s' % ('OK' if ping else 'FAILED',
                                                  ping[0][1] if ping else '; '.join(r.errors)[:200]))
    if ce.tools['calibre']:
        rc, out, _ = _run([ce.tools['calibre'], '-version'], cwd=make_workdir('aspice_cal_'),
                          timeout=120)
        lines.append('  calibre: %s' % (out.strip().splitlines()[0][:100] if out.strip()
                                        else 'rc=%s' % rc))
    return '\n'.join(lines)
