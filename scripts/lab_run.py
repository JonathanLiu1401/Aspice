#!/usr/bin/env python3
"""Run a Spectre netlist on a UW ECE lab Linux host from any computer.

The client half (hosts, pdks, run, status, fetch) needs only Python 3.8+ and the
OpenSSH client (built into Windows 10/11, macOS and Linux) - no Cadence, no
numpy, no virtuoso-bridge. The server half (serve) runs on the lab host with
aspice's own lib/cadence.py, launched detached so a dropped connection or a
closed laptop does not kill a long simulation.

    python lab_run.py hosts                       # rank lab hosts by NFS latency + load
    python lab_run.py pdks                        # model include lines on the lab
    python lab_run.py run tb.scs --accuracy high --verify
    python lab_run.py status aspice_runs/tb-<id>/job.json
    python lab_run.py fetch  aspice_runs/tb-<id>/job.json

Results land in ./aspice_runs/<deck>-<id>/: result.json (pass/fail, errors,
warnings, operating point, device table, accuracy check), spectre.out, and the
PSF-ASCII results (psf/) unless --no-psf.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

DOMAIN = '.ece.uw.edu'
DEFAULT_HOSTS = ['linux-lab-%d' % n for n in range(136, 146)]
REMOTE_PY = '~/.claude/skills-venv/bin/python'
REMOTE_ASPICE = '~/.claude/skills/aspice'
REMOTE_ROOT = '/var/tmp/$USER/aspice-remote'   # host-local disk, never NFS
TEXT_EXT = {'.scs', '.sp', '.spi', '.spice', '.cir', '.inc', '.lib', '.va', '.vams',
            '.txt', '.net', '.cdl', '.ocn', '.il'}

KITS = '/home/lab.apps/vlsiapps_new/kits'
FREEPDK45 = '/home/projects/ee476/common/freepdk45_opencelllibrarypdk45/FreePDK45'
PDKS = [
    # name, include line, corners, core devices
    ('gpdk045', f'include "{KITS}/gpdk045_v_6_0/models/spectre/gpdk045.scs" section=tt',
     'tt ff ss fs sf mc (+ _hvt/_lvt/_18 variants)', 'nch pch nch_hvt pch_lvt nch18 ... (1.0 V core)'),
    ('gpdk090', f'include "{KITS}/gpdk090_v4.6/models/spectre/gpdk090.scs" section=NN',
     'NN SS SF FS FF (+ _highperf), stat_mis', 'gpdk090_nmos1v gpdk090_pmos1v gpdk090_nmos2v ...'),
    ('gpdk180', f'include "{KITS}/gpdk180_v3.3/models/spectre/gpdk.scs" section=NN',
     'NN SS SF FS FF stat', 'nmos1 pmos1 (1.8 V), nmos25 pmos25 (3.3 V)'),
    ('sky130', f'include "{KITS}/sky130_release_0.0.1/models/sky130.lib.spice" section=tt',
     'tt ss ff sf fs', 'sky130_fd_pr__nfet_01v8 / __pfet_01v8 (subckts; instance them with X)'),
    # hspice_nom.include itself is unusable in Spectre (unterminated quotes):
    # include the per-device cards; corners are directories models_nom/_ss/_ff.
    ('freepdk45', f'include "{FREEPDK45}/ncsu_basekit/models/hspice/tran_models/models_nom/NMOS_VTG.inc"',
     'directories models_nom / models_ss / models_ff; include one file per device (PMOS_VTG.inc too)',
     'NMOS_VTG PMOS_VTG NMOS_VTL NMOS_VTH NMOS_THKOX (SPICE syntax: wrap in simulator lang=spice)'),
]

# Tolerances injected as an options statement before the first analysis.
ACCURACY = {
    'netlist': ([], None),
    'high': (['+errpreset=conservative'],
             'reltol=1e-5 vabstol=1e-7 iabstol=1e-13 gmin=1e-14'),
    'max': (['+errpreset=conservative'],
            'reltol=1e-6 vabstol=1e-8 iabstol=1e-14 gmin=1e-15'),
}
TIGHTER = {'netlist': 'high', 'high': 'max', 'max': None}
INCLUDE_RE = re.compile(r'^(\s*(?:include|ahdl_include|\.include|\.inc|\.lib|\.hdl)\s+)'
                        r'(["\']?)([^"\'\s]+)\2', re.I | re.M)


# --------------------------------------------------------------------- ssh plumbing

class Remote:
    def __init__(self, host, user=None, jump=None, timeout=30):
        self.host = host if '.' in host or host == 'localhost' else host + DOMAIN
        self.user, self.jump, self.timeout = user, jump, timeout

    @property
    def target(self):
        return f'{self.user}@{self.host}' if self.user else self.host

    def _opts(self):
        o = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
             '-o', 'ServerAliveInterval=30', '-o', 'StrictHostKeyChecking=accept-new']
        if self.jump:
            o += ['-J', self.jump]
        return o

    def bash(self, script, timeout=None, check=True):
        """Run a bash script on the host (sent on stdin: no quoting across shells)."""
        # Bytes, not text=True: on Windows text mode turns \n into \r\n and
        # bash would see a stray \r at the end of every command.
        p = subprocess.run(['ssh'] + self._opts() + [self.target, 'bash', '-s'],
                           input=script.replace('\r\n', '\n').encode(), capture_output=True,
                           timeout=timeout or self.timeout)
        p.stdout = p.stdout.decode(errors='replace')
        p.stderr = p.stderr.decode(errors='replace')
        if check and p.returncode != 0:
            raise RuntimeError(f'ssh {self.host} failed ({p.returncode}): '
                               f'{(p.stderr or p.stdout).strip()[-800:]}')
        return p

    def put_dir(self, local_dir, remote_dir):
        # cwd = the local dir and relative sources: a Windows "C:\..." path never
        # reaches scp, which would read "C:" as a host name.
        names = sorted(os.listdir(local_dir))
        subprocess.run(['scp', '-q', '-r'] + self._opts() + names +
                       [f'{self.target}:{remote_dir}/'], cwd=local_dir, check=True,
                       timeout=600)

    def get(self, remote_path, local_dir):
        p = subprocess.run(['scp', '-q'] + self._opts() + [f'{self.target}:{remote_path}', '.'],
                           cwd=local_dir, capture_output=True, text=True, timeout=3600)
        return p.returncode == 0


# --------------------------------------------------------------------- host ranking

PROBE = r'''
python3 - <<'EOF'
import os, time, json
p = os.path.expanduser('~/.aspice_nfs_probe_%s' % os.uname()[1])
t = time.time()
for _ in range(20):
    open(p, 'w').write('x'); os.stat(p)
os.remove(p)
nfs = time.time() - t
load = float(open('/proc/loadavg').read().split()[0])
sp = sum(1 for d in os.listdir('/proc') if d.isdigit() and
         os.path.exists('/proc/%s/comm' % d) and
         open('/proc/%s/comm' % d).read().strip() in ('spectre', 'spectrex', 'virtuoso'))
print(json.dumps({'nfs_s': round(nfs, 3), 'load': load, 'cpus': os.cpu_count(),
                  'cadence_procs': sp}))
EOF
'''


def probe(host, user, jump):
    r = Remote(host, user, jump, timeout=40)
    t = time.time()
    try:
        out = r.bash(PROBE).stdout.strip().splitlines()[-1]
        d = json.loads(out)
    except Exception as e:
        return {'host': host, 'ok': False, 'error': str(e).splitlines()[0][:120]}
    d.update(host=host, ok=d['nfs_s'] < 0.3, ssh_s=round(time.time() - t, 2))
    d['score'] = d['nfs_s'] * 10 + d['load'] / max(d['cpus'], 1) + d['cadence_procs'] * 0.5
    return d


def rank_hosts(hosts, user, jump):
    with cf.ThreadPoolExecutor(len(hosts)) as ex:
        rows = list(ex.map(lambda h: probe(h, user, jump), hosts))
    return sorted(rows, key=lambda d: (not d['ok'], d.get('score', 1e9)))


def print_hosts(rows):
    print('%-16s %-5s %7s %6s %5s %6s  %s' % ('host', 'ok', 'nfs20', 'load', 'cpus', 'cad', 'note'))
    for d in rows:
        if 'nfs_s' in d:
            print('%-16s %-5s %6.2fs %6.2f %5d %6d  %s' % (
                d['host'], d['ok'], d['nfs_s'], d['load'], d['cpus'], d['cadence_procs'],
                '' if d['ok'] else 'NFS slow - do not run Cadence here'))
        else:
            print('%-16s %-5s %s' % (d['host'], False, d['error']))


# --------------------------------------------------------------------- client: run

def stage(deck, extra, staging):
    """Copy the deck and every relative include it names (recursively) into one
    flat staging directory under unique names, rewriting the include lines to
    match, and normalise line endings (a CRLF deck breaks Spectre tokens).
    Absolute lab paths (/home/lab.apps/..., $PDK_DIR/...) are left alone."""
    deck = Path(deck).resolve()
    names, warn = {}, []

    def name_for(src):
        if src not in names:
            base, n = src.name, 1
            while base in names.values():
                base, n = f'{src.stem}_{n}{src.suffix}', n + 1
            names[src] = base
            copy(src)
        return names[src]

    def copy(src):
        data = src.read_bytes()
        if src.suffix.lower() in TEXT_EXT or src == deck:
            text = data.replace(b'\r\n', b'\n').decode('utf-8', 'surrogateescape')

            def fix(m):
                p = m.group(3)
                if p.startswith(('/', '$', '~')):
                    return m.group(0)
                if re.match(r'^[A-Za-z]:[\\/]', p):
                    warn.append(f'{src.name}: local path {p!r} cannot exist on the lab host')
                    return m.group(0)
                inc = (src.parent / p).resolve()
                if not inc.is_file():
                    warn.append(f'{src.name}: include {p!r} not found locally')
                    return m.group(0)
                q = m.group(2) or ''
                return f'{m.group(1)}{q}{name_for(inc)}{q}'

            data = INCLUDE_RE.sub(fix, text).encode('utf-8', 'surrogateescape')
        (Path(staging) / names[src]).write_bytes(data)

    first = name_for(deck)
    for e in extra or []:
        name_for(Path(e).resolve())
    return first, warn


def cmd_run(a):
    deck = Path(a.deck)
    if not deck.is_file():
        sys.exit(f'no such netlist: {deck}')
    if a.host in (None, 'auto'):
        rows = rank_hosts(a.hosts or DEFAULT_HOSTS, a.user, a.jump)
        good = [d for d in rows if d['ok']]
        if not good:
            print_hosts(rows)
            sys.exit('no healthy lab host reachable (off campus? connect Husky OnNet '
                     'VPN or pass --jump)')
        host = good[0]['host']
        print(f'host: {host} (nfs20 {good[0]["nfs_s"]}s, load {good[0]["load"]})')
    else:
        host = a.host
    r = Remote(host, a.user, a.jump)
    jid = time.strftime('%Y%m%d-%H%M%S-') + '%04x' % random.getrandbits(16)
    out = Path(a.out or Path('aspice_runs') / f'{deck.stem}-{jid}').resolve()
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        st = Path(tmp) / 'deck'
        st.mkdir()
        deck_name, warn = stage(deck, a.include, st)
        for w in warn:
            print('warning:', w)
        shutil.copy2(__file__, Path(tmp) / 'lab_run.py')
        rdir = r.bash(f'd={REMOTE_ROOT}/{jid}; mkdir -p "$d" && echo "$d"').stdout.strip()
        r.put_dir(tmp, rdir)

    serve = [REMOTE_PY, f'{rdir}/lab_run.py', 'serve', rdir, '--deck', deck_name,
             '--accuracy', a.accuracy, '--timeout', str(a.timeout),
             '--aspice', REMOTE_ASPICE]
    if a.verify:
        serve.append('--verify')
    for s in a.spectre_arg or []:
        serve += ['--spectre-arg', s]
    # ~ must expand remotely, so the python/aspice paths are left unquoted.
    cmd = ' '.join(x if x.startswith('~') else shlex.quote(x) for x in serve)
    r.bash(f'cd {shlex.quote(rdir)} && (setsid nohup {cmd} > serve.log 2>&1 < /dev/null & echo $! > pid)')
    job = {'host': r.host, 'user': a.user, 'jump': a.jump, 'remote_dir': rdir, 'id': jid,
           'deck': str(deck), 'accuracy': a.accuracy, 'verify': a.verify,
           'out': str(out), 'started': time.strftime('%Y-%m-%d %H:%M:%S')}
    (out / 'job.json').write_text(json.dumps(job, indent=2))
    print(f'job {jid} running on {r.host}:{rdir}\n  record: {out / "job.json"}')
    if a.no_wait:
        print('  later: python lab_run.py status|fetch', out / 'job.json')
        return
    wait_and_fetch(job, psf=not a.no_psf)


def load_job(path):
    return json.loads(Path(path).read_text())


def remote_status(job):
    r = Remote(job['host'], job.get('user'), job.get('jump'))
    p = r.bash(f'cat {shlex.quote(job["remote_dir"])}/status.json 2>/dev/null || printf "{{}}"; echo; '
               f'tail -c 400 {shlex.quote(job["remote_dir"])}/serve.log 2>/dev/null; echo; '
               f'kill -0 "$(cat {shlex.quote(job["remote_dir"])}/pid 2>/dev/null)" 2>/dev/null '
               f'&& echo ALIVE || echo GONE',
               check=False)
    lines = p.stdout.splitlines()
    try:
        st = json.loads(lines[0]) if lines else {}
    except ValueError:
        st = {}
    st['alive'] = bool(lines) and lines[-1] == 'ALIVE'
    st['log_tail'] = '\n'.join(lines[1:-1]).strip()
    return st


def wait_and_fetch(job, psf=True):
    t0, delay, last, gone = time.time(), 5, None, 0
    while True:
        try:
            st = remote_status(job)
        except Exception as e:                       # transient network drop: keep waiting
            print('  status check failed, retrying:', str(e)[:120])
            st = {'alive': True}
        phase = st.get('phase')
        if phase != last:
            print(f'  [{time.time() - t0:7.0f}s] {phase or "starting"}')
            last = phase
        if phase == 'done':
            break
        gone = gone + 1 if not st['alive'] else 0
        if gone >= 2:                                # two polls in a row: really gone
            print('  remote process is gone without finishing; serve.log tail:\n' +
                  st.get('log_tail', ''))
            break
        time.sleep(delay)
        delay = min(delay * 1.5, 60)
    fetch(job, psf)


def fetch(job, psf=True):
    out = Path(job['out'])
    out.mkdir(parents=True, exist_ok=True)
    r = Remote(job['host'], job.get('user'), job.get('jump'))
    rd = job['remote_dir']
    for f in ('result.json', 'spectre.out', 'serve.log'):
        r.get(f'{rd}/{f}', str(out))
    if psf and r.get(f'{rd}/psf.tgz', str(out)):
        with tarfile.open(out / 'psf.tgz') as t:
            if hasattr(tarfile, 'data_filter'):
                t.extractall(out, filter='data')
            else:
                t.extractall(out)
        (out / 'psf.tgz').unlink()
    res = out / 'result.json'
    if not res.is_file():
        print('no result.json yet; see', out / 'serve.log')
        return None
    summarize(json.loads(res.read_text()), out)
    return out


def summarize(res, out):
    print(f'\n== {"PASS" if res["ok"] else "FAIL"}  {res.get("seconds", 0):.1f}s on {res["host"]}'
          f'  accuracy={res["accuracy"]}  {res.get("spectre_version", "")}')
    for e in res.get('errors', [])[:10]:
        print('ERROR', e[:400])
    if res.get('warnings'):
        print(f'{len(res["warnings"])} warnings (result.json has them all); first:',
              res['warnings'][0][:200])
    for name, an in res.get('analyses', {}).items():
        print(f'  {name:<16} {an.get("type") or "":<8} {an.get("points", "")} pts, '
              f'{an.get("signals", "")} signals')
    if res.get('device_table'):
        print(res['device_table'])
    v = res.get('verify')
    if v:
        print(f'\naccuracy check vs {v["against"]}: worst deviation {v["worst_rel"]:.2e} of signal '
              f'range ({v["worst_signal"]}) -> {v["verdict"]}')
        if v.get('worst_current'):
            print(f'  terminal currents: worst {v["worst_current_rel"]:.2e} ({v["worst_current"]}); '
                  'edge spikes are timestep-limited - compare integrated charge if they matter')
    print('results:', out)


# --------------------------------------------------------------------- server half

def inject_accuracy(text, opts):
    """Insert an options statement before the first top-level analysis, in
    Spectre language, restoring SPICE mode if the deck was in it."""
    if not opts:
        return text
    lines = text.split('\n')
    analysis = re.compile(r'^\s*\w+\s+(dc|ac|tran|noise|stb|xf|pz|sp|pss|pac|pnoise|hb|'
                          r'montecarlo|sweep|info)\b', re.I)
    spice_an = re.compile(r'^\s*\.(op|dc|ac|tran|noise)\b', re.I)
    depth, lang, at = 0, 'spectre', len(lines)
    for i, ln in enumerate(lines):
        s = ln.strip()
        m = re.match(r'simulator\s+lang\s*=\s*(\w+)', s, re.I)
        if m:
            lang = m.group(1).lower()
        if re.match(r'^(inline\s+)?subckt\b|^\.subckt\b', s, re.I):
            depth += 1
        elif re.match(r'^ends\b|^\.ends\b', s, re.I):
            depth = max(0, depth - 1)
        elif depth == 0 and (analysis.match(ln) or spice_an.match(ln)):
            at = i
            break
    block = ['simulator lang=spectre',
             f'aspiceAccuracy options {opts}  // injected by lab_run.py']
    if lang != 'spectre':
        block.append(f'simulator lang={lang}')
    return '\n'.join(lines[:at] + block + lines[at:])


def absolutize_includes(text, base):
    def fix(m):
        p = m.group(3)
        if p.startswith(('/', '$', '~')):
            return m.group(0)
        q = m.group(2) or ''
        return f'{m.group(1)}{q}{(Path(base) / p).resolve()}{q}'
    return INCLUDE_RE.sub(fix, text)


def _set_status(d, **kw):
    p = Path(d) / 'status.json'
    st = json.loads(p.read_text()) if p.is_file() else {}
    st.update(kw, t=time.time())
    p.write_text(json.dumps(st))


def _numeric_traces(analyses):
    import numpy as np
    out = {}
    for an, d in analyses.items():
        if isinstance(d, Exception) or not hasattr(d, 'traces'):
            continue
        x = getattr(d, 'x', None)
        for sig, v in d.traces.items():
            arr = np.asarray(v)
            if arr.dtype.kind in 'fc' or (arr.dtype.kind == 'i'):
                out[(an, sig)] = (x, arr)
    return out


def compare(base, tight):
    """Worst deviation between two runs, relative to each signal's range."""
    import numpy as np
    A, B = _numeric_traces(base), _numeric_traces(tight)
    rows = []
    for key, (xa, ya) in A.items():
        if key not in B:
            continue
        xb, yb = B[key]
        if ya.ndim == 0 or ya.size == 1:
            a_, b_ = complex(ya.ravel()[0]), complex(yb.ravel()[0])
            rng = max(abs(a_), abs(b_), 1e-12)
            rows.append((abs(a_ - b_) / rng, key))
            continue
        if xa is not None and xb is not None and len(xa) == len(ya) and len(xb) == len(yb) \
                and not (len(xa) == len(xb) and np.allclose(xa, xb)):
            if np.iscomplexobj(yb):
                yb = np.interp(xa, xb, yb.real) + 1j * np.interp(xa, xb, yb.imag)
            else:
                yb = np.interp(xa, xb, yb)
        if ya.shape != np.shape(yb):
            continue
        rng = max(float(np.ptp(np.abs(ya))), float(np.max(np.abs(ya))) * 1e-3, 1e-12)
        rows.append((float(np.max(np.abs(ya - yb))) / rng, key))
    rows.sort(reverse=True)
    return rows


def cmd_serve(a):
    d = Path(a.dir).resolve()
    sys.path.insert(0, str(Path(os.path.expanduser(a.aspice)) / 'lib'))
    os.environ.setdefault('ASPICE_WORKDIR', str(d / 'work'))
    os.environ.setdefault('PDK_DIR', FREEPDK45)      # FreePDK45 model files use $PDK_DIR
    import cadence as CD
    _set_status(d, phase='preparing')
    deck_dir = d / 'deck'
    text = (deck_dir / a.deck).read_text(errors='surrogateescape')
    text = absolutize_includes(text, deck_dir)

    def one(level, tag):
        args, opts = ACCURACY[level]
        deck = inject_accuracy(text, opts)
        _set_status(d, phase=f'simulating ({tag}, accuracy={level})')
        return CD.run_spectre(deck, workdir=str(d / 'work' / tag), timeout=a.timeout,
                              args=args + list(a.spectre_arg or []), copy_siblings=False)

    t0 = time.time()
    res = one(a.accuracy, 'main')
    ver = ''
    try:
        rc = subprocess.run([CD.environment().tools['spectre'], '-V'], capture_output=True,
                            text=True, timeout=60)
        ver = next((l for l in (rc.stdout + rc.stderr).splitlines() if 'version' in l), '')
        ver = re.sub(r'.*spectre\s+', 'spectre ', ver).strip(' $')
    except Exception:
        pass
    out = {'ok': res.ok, 'host': os.uname()[1], 'accuracy': a.accuracy,
           'spectre_args': ACCURACY[a.accuracy][0] + list(a.spectre_arg or []),
           'options_injected': ACCURACY[a.accuracy][1], 'spectre_version': ver,
           'seconds': res.seconds, 'errors': res.errors, 'warnings': res.warnings,
           'analyses': {}}
    for name, an in res.analyses.items():
        if isinstance(an, Exception):
            out['analyses'][name] = {'error': str(an)[:300]}
            continue
        n = len(an.x) if getattr(an, 'x', None) is not None else 1
        out['analyses'][name] = {'type': an.analysis_type, 'sweep': an.sweep, 'points': n,
                                 'signals': len(an.traces) + len(an.structs)}
    try:
        out['op'] = {k: v for k, v in res.op().items() if isinstance(v, float)}
    except Exception:
        pass
    try:
        dt = res.device_table()
        if dt.count('\n') > 0:
            out['device_table'] = dt
    except Exception:
        pass

    tighter = TIGHTER.get(a.accuracy)
    if a.verify and res.ok and tighter:
        res2 = one(tighter, 'verify')
        if res2.ok:
            rows = compare(res.analyses, res2.analyses)
            # Terminal currents ("vin:p") spike at switching edges, where two
            # adaptive time grids interpolate differently: judge on node
            # voltages and report currents on their own.
            volts = [r for r in rows if ':' not in r[1][1]]
            amps = [r for r in rows if ':' in r[1][1]]
            worst = volts[0] if volts else (rows[0] if rows else (0.0, ('', '')))
            out['verify'] = {
                'against': tighter, 'seconds': res2.seconds, 'worst_rel': worst[0],
                'worst_signal': '/'.join(worst[1]),
                'worst_current_rel': amps[0][0] if amps else None,
                'worst_current': '/'.join(amps[0][1]) if amps else None,
                'top': [{'signal': '/'.join(k), 'rel': r} for r, k in rows[:15]],
                'verdict': ('converged: tolerances are not limiting accuracy'
                            if worst[0] < 1e-3 else
                            'NOT converged: rerun with a tighter --accuracy or finer maxstep'),
            }
        else:
            out['verify'] = {'against': tighter, 'worst_rel': float('nan'), 'worst_signal': '',
                             'verdict': 'tighter run failed: ' + '; '.join(res2.errors)[:300]}
    elif a.verify and not tighter:
        out['verify'] = {'against': 'none', 'worst_rel': float('nan'), 'worst_signal': '',
                         'verdict': 'already at max accuracy; nothing tighter to compare'}

    out['wall_seconds'] = time.time() - t0
    (d / 'result.json').write_text(json.dumps(out, indent=2, default=str))
    raw = Path(res.raw_dir)
    if (raw / 'spectre.out').is_file():
        shutil.copy2(raw / 'spectre.out', d / 'spectre.out')
    if raw.is_dir():
        with tarfile.open(d / 'psf.tgz', 'w:gz') as t:
            t.add(raw, arcname='psf')
    _set_status(d, phase='done', ok=res.ok)


# --------------------------------------------------------------------- CLI

def cmd_pdks(a):
    for name, inc, corners, devs in PDKS:
        print(f'{name}\n  {inc}\n  corners: {corners}\n  devices: {devs}')
    if a.check:
        r = Remote(a.check, a.user, a.jump)
        paths = [re.search(r'"([^"]+)"', p[1]).group(1) for p in PDKS]
        p = r.bash('\n'.join(f'test -f {shlex.quote(x)} && echo OK {x} || echo MISSING {x}'
                             for x in paths))
        print(p.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)

    def conn(p):
        p.add_argument('--user', help='lab login (default: your ssh config / local name)')
        p.add_argument('--jump', help='ssh jump host, user@host (off-campus without VPN)')

    p = sub.add_parser('hosts', help='rank lab hosts')
    conn(p)
    p.add_argument('--hosts', nargs='+')
    p = sub.add_parser('pdks', help='list PDK model include lines')
    conn(p)
    p.add_argument('--check', metavar='HOST', help='verify the model files exist on HOST')
    p = sub.add_parser('run', help='simulate a netlist on a lab host')
    conn(p)
    p.add_argument('deck')
    p.add_argument('--host', default='auto')
    p.add_argument('--hosts', nargs='+', help='candidates for --host auto')
    p.add_argument('--accuracy', choices=list(ACCURACY), default='high')
    p.add_argument('--verify', action='store_true',
                   help='rerun with tighter tolerances and report the deviation')
    p.add_argument('--include', nargs='+', help='extra files to upload next to the deck')
    p.add_argument('--spectre-arg', action='append', help='extra Spectre argument, e.g. +aps')
    p.add_argument('--timeout', type=int, default=7 * 86400)
    p.add_argument('--out')
    p.add_argument('--no-wait', action='store_true')
    p.add_argument('--no-psf', action='store_true', help='fetch result.json only')
    for name in ('status', 'fetch'):
        p = sub.add_parser(name)
        p.add_argument('job', help='job.json written by run')
        p.add_argument('--no-psf', action='store_true')
    p = sub.add_parser('serve', help=argparse.SUPPRESS)
    p.add_argument('dir')
    p.add_argument('--deck', required=True)
    p.add_argument('--accuracy', default='high')
    p.add_argument('--verify', action='store_true')
    p.add_argument('--spectre-arg', action='append')
    p.add_argument('--timeout', type=int, default=7 * 86400)
    p.add_argument('--aspice', default=REMOTE_ASPICE)
    a = ap.parse_args(argv)

    if a.cmd == 'hosts':
        print_hosts(rank_hosts(a.hosts or DEFAULT_HOSTS, a.user, a.jump))
    elif a.cmd == 'pdks':
        cmd_pdks(a)
    elif a.cmd == 'run':
        cmd_run(a)
    elif a.cmd == 'status':
        st = remote_status(load_job(a.job))
        print(json.dumps({k: v for k, v in st.items() if k != 'log_tail'}), st['log_tail'], sep='\n')
    elif a.cmd == 'fetch':
        fetch(load_job(a.job), psf=not a.no_psf)
    elif a.cmd == 'serve':
        try:
            cmd_serve(a)
        except Exception as e:
            import traceback
            traceback.print_exc()
            _set_status(Path(a.dir), phase='done', ok=False, error=str(e)[:500])
            Path(a.dir, 'result.json').write_text(json.dumps(
                {'ok': False, 'host': os.uname()[1], 'accuracy': a.accuracy,
                 'errors': ['lab_run serve crashed: ' + str(e)[:500]]}, indent=2))


if __name__ == '__main__':
    main()
