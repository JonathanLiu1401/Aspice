"""Tests for lib/cadence.py - the headless Cadence backend.

Runnable:
  python scripts/test_cadence.py            # offline + every live tool found
  python scripts/test_cadence.py --offline  # parsers only, no Cadence needed

Offline checks (always): PSF-ASCII parsing, spectre log / Calibre report
parsing, cds.lib resolution, rule-deck $VAR inference, runset merging, the
write guard. Live checks run against whatever tools cadence.environment()
finds and are reported SKIP (not PASS) when a tool is missing, so a green run
on a machine without Cadence does not pretend to have verified Spectre.

Every live run happens in a scratch directory and only ever writes the
scratch library; user libraries are opened read-only.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))

import numpy as np  # noqa: E402

import cadence as CD  # noqa: E402

OFFLINE = '--offline' in sys.argv
PASSES, FAILURES, SKIPS = 0, [], []


def check(name, cond, detail=''):
    global PASSES
    if cond:
        PASSES += 1
        print('  [PASS] %s' % name)
    else:
        FAILURES.append(name)
        print('  [FAIL] %s  %s' % (name, detail))


def skip(name, why):
    SKIPS.append(name)
    print('  [SKIP] %s  (%s)' % (name, why))


# ---------------------------------------------------------------------------
print('\n1. PSF-ASCII parsing (formats copied from Spectre 23.1 output)')
OP = '''HEADER
"PSFversion" "1.00"
"analysis type" "dc"
"analysis name" "dcOp"
TYPE
"V" FLOAT DOUBLE PROP(
"units" "V"
)
VALUE
"a" "V" 1.000000000000000e+00
"b" "V" 4.990009990009990e-01
"V1:p" "I" -5.0e-04
END
'''
d = CD.parse_psf_ascii(OP)
check('unswept dc values', d.sweep is None and d.traces['b'] == 0.499000999000999
      and d.traces['V1:p'] == -5e-4, d.traces)
check('header read', d.analysis_type == 'dc' and d.header['analysis name'] == 'dcOp')

AC = '''HEADER
"analysis type" "ac"
SWEEP
"freq" "sweep" PROP(
"units" "Hz"
)
TRACE
"out" "V"
"V1:p" "I"
VALUE
"freq" 1.0e+03
"out" (2.5e+00 -1.0e-03)
"V1:p" (1.0e-6 0.0e+00)
"freq" 1.0e+04
"out" (2.4e+00 -1.0e-02)
"V1:p" (2.0e-6 0.0e+00)
END
'''
d = CD.parse_psf_ascii(AC)
check('swept AC is complex', d.sweep == 'freq' and list(d.x) == [1e3, 1e4]
      and d.traces['out'][1] == complex(2.4, -0.01), d.traces.get('out'))
check('trace kinds recorded', d.kinds == {'out': 'V', 'V1:p': 'I'}, d.kinds)

INFO = '''HEADER
"analysis type" "info"
TYPE
"bsim4" STRUCT(
"ids" FLOAT DOUBLE PROP(
"units" "A"
"description" "Resistive drain-to-source current"
)
"gm" FLOAT DOUBLE PROP(
"units" "S"
)
"region" FLOAT DOUBLE PROP(
"units" ""
)
)
VALUE
"NM0" "bsim4" (
1.7e-05
2.7e-04
2.0e+00
) PROP(
"model" "nch"
)
END
'''
d = CD.parse_psf_ascii(INFO)
rec = d.structs.get('NM0', {})
check('oppoint struct fields mapped by name', rec.get('gm') == 2.7e-4 and rec.get('ids') == 1.7e-5
      and rec.get('_type') == 'bsim4', rec)

# ---------------------------------------------------------------------------
print('\n2. Log and report parsing')
log = CD.parse_spectre_log('''Version 23.1.0.403.isr6 64bit
ERROR (SFE-23): "input.scs" 5: The instance `M1' is referencing an undefined model `nfet'.
    Either include the file containing the definition, or define it.

spectre terminated prematurely due to fatal error.
''')
check('spectre fatal error parsed', not log['completed'] and log['fatal']
      and 'SFE-23' in log['errors'][0] and log['version'] == '23.1.0.403.isr6', log)
ok = CD.parse_spectre_log('spectre completes with 0 errors, 2 warnings, and 5 notices.')
check('spectre completion line parsed', ok['completed'] and ok['n_warnings'] == 2)

drc = CD.parse_drc_summary('''RULECHECK Metal1.1 ................................ TOTAL Result Count = 1    (1)
RULECHECK Metal1.2 ................................ TOTAL Result Count = 0    (0)
TOTAL DRC RuleChecks Executed:   156
TOTAL DRC Results Generated:     1 (1)
''')
check('DRC summary: totals and only nonzero rules', drc == {
    'total': 1, 'checks': 156, 'violations': {'Metal1.1': 1}}, drc)
LVS = '''
                               OVERALL COMPARISON RESULTS

                   #   #         #    INCORRECT    #
**************************************************************************************************************
                                      CELL  SUMMARY
**************************************************************************************************************

  Result         Layout                        Source
  -----------    -----------                   --------------
  INCORRECT      inv                           inv

**************************************************************************************************************
INITIAL NUMBERS OF OBJECTS
 Ports:              4         4
 Nets:               4         5
 Total Inst:         2         2
'''
lv = CD.parse_lvs_report(LVS)
check('LVS verdict, cell table and counts', lv['status'] == 'INCORRECT' and not lv['match']
      and lv['cells'] == [('INCORRECT', 'inv', 'inv')] and lv['counts']['nets'] == (4, 5), lv)

# ---------------------------------------------------------------------------
print('\n3. Library files, rule decks, safety')
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    (t / 'libA').mkdir()
    (t / 'sub').mkdir()
    (t / 'sub' / 'libB').mkdir()
    (t / 'sub' / 'cds.lib').write_text('DEFINE libB libB\nDEFINE gone /no/such/dir\n')
    os.environ['ASPICE_TEST_DIR'] = str(t)
    CD.environment(refresh=True)
    (t / 'cds.lib').write_text('# top\nDEFINE libA $ASPICE_TEST_DIR/libA\n'
                               'INCLUDE sub/cds.lib\nUNDEFINE gone2\n')
    libs = CD.read_cds_lib(t / 'cds.lib')
    check('DEFINE with $VAR', libs['libA']['exists'] and libs['libA']['path'] == str(t / 'libA'))
    check('relative DEFINE resolves against the INCLUDEd file',
          libs['libB']['path'] == str(t / 'sub' / 'libB') and libs['libB']['exists'], libs['libB'])
    check('missing library directory flagged', libs['gone']['exists'] is False)

    # A kit deck that includes $PDK_DIR/... relative to its own install.
    kit = t / 'kit' / 'tech' / 'calibre'
    kit.mkdir(parents=True)
    (kit / 'layer.inc').write_text('layer active 1\nlayer metal1 11\n')
    (kit / 'lvs.rul').write_text('LVS REPORT mask.lvs.rep\ninclude $PDK_DIR/tech/calibre/layer.inc\n')
    os.environ.pop('PDK_DIR', None)
    CD.environment(refresh=True)
    check('rule deck $PDK_DIR inferred from its own location',
          CD.rule_env(kit / 'lvs.rul') == {'PDK_DIR': str(t / 'kit')}, CD.rule_env(kit / 'lvs.rul'))
    text, _ = CD._runset(['LVS REPORT "lvs.report"', 'LVS REPORT OPTION NONE',
                          'SOURCE PATH "s.net"'], kit / 'lvs.rul')
    check('runset drops statements the deck already sets',
          'lvs.report' not in text and 'SOURCE PATH' in text and 'OPTION NONE' in text, text)
    check('report name taken from the deck',
          CD._deck_value((kit / 'lvs.rul').read_text(), 'LVS REPORT') == 'mask.lvs.rep')
    lm = CD.layermap_from_calibre(kit / 'layer.inc', t / 'x.layermap')
    check('layer map generated from Calibre layer.inc',
          Path(lm).read_text().split() == ['active', 'drawing', '1', '0',
                                           'metal1', 'drawing', '11', '0'])
CD.environment(refresh=True)

# Projects: each carries its own cds.lib and env (e.g. PDK_DIR). Uses a temp
# config so the user's ~/.config/aspice/cadence.json is not touched.
with tempfile.TemporaryDirectory() as tmp:
    t = Path(tmp)
    saved = (CD.CONFIG_PATH, os.environ.pop('ASPICE_PROJECT', None),
             os.environ.pop('ASPICE_CDS_LIB', None), Path.cwd())
    CD.CONFIG_PATH = t / 'cfg.json'
    try:
        for n in ('A', 'B'):
            (t / n).mkdir()
            (t / n / 'cds.lib').write_text('DEFINE lib%s $KIT_%s/lib\n' % (n, n))
            (t / ('kit' + n) / 'lib').mkdir(parents=True)
        CD.add_project('A', t / 'A' / 'cds.lib', env={'KIT_A': str(t / 'kitA')})
        CD.add_project('B', t / 'B' / 'cds.lib', env={'KIT_B': str(t / 'kitB')})
        check('first project becomes active', CD.current_project()[0] == 'A')
        check('project env resolves its cds.lib', CD.read_cds_lib()['libA']['exists'])
        os.chdir(t / 'B')
        check('working inside a project directory selects it',
              CD.current_project()[0] == 'B' and CD.environment(refresh=True).cds_lib
              == str(t / 'B' / 'cds.lib'))
        os.chdir(t)
        os.environ['ASPICE_PROJECT'] = 'B'
        check('$ASPICE_PROJECT overrides the active project', CD.current_project()[0] == 'B')
        del os.environ['ASPICE_PROJECT']
        CD.use_project('B')
        check('use_project switches the default', CD.current_project()[0] == 'B'
              and CD.environment(refresh=True).env.get('KIT_B') == str(t / 'kitB'))
    finally:
        CD.CONFIG_PATH = saved[0]
        os.chdir(saved[3])
        for k, v in (('ASPICE_PROJECT', saved[1]), ('ASPICE_CDS_LIB', saved[2])):
            if v is not None:
                os.environ[k] = v
        CD.environment(refresh=True)
try:
    CD.edit_instance_params('mylib', 'amp', {'M0': {'w': '1u'}})
    check('edits to a user library refused without allow_write', False, 'no exception')
except PermissionError:
    check('edits to a user library refused without allow_write', True)

# ---------------------------------------------------------------------------
ce = CD.environment()
print('\n4. Live Spectre')
if OFFLINE or not ce.has('spectre'):
    skip('spectre', 'offline' if OFFLINE else 'spectre not found')
else:
    r = CD.run_spectre('''simulator lang=spectre
V1 (a 0) vsource dc=1
R1 (a b) resistor r=1k
R2 (b gnd) resistor r=1k
R3 (b 0) resistor r=(1M)
C1 (b 0) capacitor c=(1n)
op dc
ac1 ac start=1k stop=100M dec=20
''')
    check('spectre runs and licenses', r.ok, r.summary())
    if r.ok:
        vb = r.op()['b']
        # gnd is an ordinary net in Spectre (floats); (1M) is a megohm.
        check('Spectre: V(b) = 1M/(1k+1M), gnd floats', abs(vb - 1e6 / 1.001e6) < 1e-9
              and abs(r.op()['gnd'] - vb) < 1e-9, 'V(b)=%r' % vb)
    r2 = CD.run_spectre('''simulator lang=spectre
V1 (a 0) vsource dc=0 mag=1
R1 (a b) resistor r=1k
C1 (b 0) capacitor c=1n
ac1 ac start=1k stop=100M dec=50
''')
    if r2.ok:
        f, h = r2.wave('ac1', 'b')
        f3 = float(np.interp(1 / math.sqrt(2), np.abs(h)[::-1], f[::-1]))
        check('AC: RC f_3dB = 1/(2 pi R C)', abs(f3 / (1 / (2 * math.pi * 1e3 * 1e-9)) - 1) < 2e-3,
              '%.1f Hz vs %.1f Hz' % (f3, 1 / (2 * math.pi * 1e-6)))
    ptm = ROOT / 'models' / 'ptm_45nm_hp.lib'
    if ptm.is_file():
        cc = CD.cross_check('''simulator lang=spectre
global 0
simulator lang=spice
.include "%s"
simulator lang=spectre
VDD (vdd 0) vsource dc=1
VG (g 0) vsource dc=0.55
RD (vdd d) resistor r=5k
M1 (d g s 0) nch w=2u l=45n
RS (s 0) resistor r=200
op dc
''' % ptm)
        check('same BSIM4 card: Spectre and ngspice agree to 0.1%', cc['ok'],
              '\n' + CD.format_cross_check(cc))
    else:
        skip('cross_check', 'models/ptm_45nm_hp.lib missing (run fetch_models.py)')

    kits = [Path(r) / 'kits/GPDK045/gpdk045_v_6_0/models/spectre/gpdk045.scs' for r in ce.roots]
    gpdk = next((k for k in kits if k.is_file()), None)
    if gpdk:
        p = CD.spectre_probe(str(gpdk), 'g45n1svt', w=1e-6, l=100e-9, vgs=0.6, vds=0.6)
        check('PDK probe through Spectre (gpdk045 g45n1svt)', p['ok'] and p.get('gm', 0) > 0
              and p.get('region_name') in ('saturation', 'subthreshold'),
              {k: p.get(k) for k in ('ids', 'gm', 'vth', 'region_name')})
    else:
        skip('spectre_probe', 'gpdk045 not found under EDA roots')

print('\n5. Live Virtuoso: build, read, check, edit, netlist, simulate')
if OFFLINE or not ce.has('virtuoso', 'ocean'):
    skip('virtuoso', 'offline' if OFFLINE else 'virtuoso/ocean not found')
else:
    try:
        cl = CD.build_schematic('aspice_div', [
            ('V0', 'analogLib', 'vdc', {'vdc': '1'}, {'PLUS': 'vin', 'MINUS': 'gnd!'}),
            ('R0', 'analogLib', 'res', {'r': '1k'}, {'PLUS': 'vin', 'MINUS': 'mid'}),
            ('R1', 'analogLib', 'res', {'r': '3k'}, {'PLUS': 'mid', 'MINUS': 'gnd!'}),
        ], pins=[('mid', 'output')])
        check('schematic built in scratch library', Path(cl).is_file())
        s = CD.schematic(CD.SCRATCH_LIB, 'aspice_div', cds_lib=cl)
        check('schematic read back: connectivity', s.instances['R1']['conns'] ==
              {'PLUS': 'mid', 'MINUS': 'gnd!'} and s.pins == {'mid': 'output'},
              s.summary())
        chk = CD.check_schematic(CD.SCRATCH_LIB, 'aspice_div', cds_lib=cl)
        check('schCheck clean (run on a copy)', chk['errors'] == 0, chk)
        r = CD.simulate_cell(CD.SCRATCH_LIB, 'aspice_div', analyses='dcOp dc\n',
                             section=None, cds_lib=cl)
        check('schematic -> OCEAN netlist -> Spectre: V(mid) = 0.75',
              r.ok and abs(r.op()['mid'] - 0.75) < 1e-9, r.summary())
        e = CD.edit_instance_params(CD.SCRATCH_LIB, 'aspice_div', {'R1': {'r': '1k'}}, cds_lib=cl)
        r = CD.simulate_cell(CD.SCRATCH_LIB, 'aspice_div', analyses='dcOp dc\n',
                             section=None, cds_lib=cl)
        check('edit R1 -> 1k, re-simulate: V(mid) = 0.5',
              e['check_errors'] == 0 and r.ok and abs(r.op()['mid'] - 0.5) < 1e-9,
              (e, r.op().get('mid') if r.ok else r.errors))
        libs = CD.libraries()
        check('libraries() matches the pure-Python cds.lib reader',
              set(libs) == set(CD.read_cds_lib()), sorted(set(libs) ^ set(CD.read_cds_lib())))
    except Exception as exc:  # report, do not crash the suite
        check('virtuoso flow', False, repr(exc)[:600])

print('\n6. Live layout: draw, stream out, Calibre DRC')
techs = CD.read_cds_lib()
deck = None
for r in ce.roots:
    cand = Path(r) / 'kits/FreePDK45/ncsu_basekit/techfile/calibre/calibreDRC.rul'
    if cand.is_file():
        deck = cand
if OFFLINE or not ce.has('virtuoso', 'strmout', 'calibre'):
    skip('layout/DRC', 'offline' if OFFLINE else 'virtuoso/strmout/calibre not found')
elif 'NCSU_TechLib_FreePDK45' not in techs or deck is None:
    skip('layout/DRC', 'FreePDK45 tech library / Calibre deck not available')
else:
    try:
        res = {}
        for name, shapes in [('aspice_ok', [('metal1', 'drawing', 0, 0, 0.5, 0.5)]),
                             ('aspice_bad', [('metal1', 'drawing', 0, 0, 0.03, 0.5),
                                             ('metal1', 'drawing', 0.06, 0, 0.5, 0.5)])]:
            cl = CD.create_layout(name, shapes, 'NCSU_TechLib_FreePDK45')
            lay = CD.layout(CD.SCRATCH_LIB, name, cds_lib=cl)
            gds = CD.stream_out(CD.SCRATCH_LIB, name, cds_lib=cl)
            res[name] = (lay, CD.calibre_drc(gds, name, deck))
        check('layout read back', res['aspice_ok'][0]['bbox'] == (0.0, 0.0, 0.5, 0.5),
              res['aspice_ok'][0]['bbox'])
        check('DRC clean shape: 0 results', res['aspice_ok'][1]['total'] == 0, res['aspice_ok'][1])
        bad = res['aspice_bad'][1]
        check('DRC catches width and spacing', bad['total'] and 'Metal1.1' in bad['violations']
              and 'Metal1.2' in bad['violations'], bad['violations'])
    except Exception as exc:
        check('layout flow', False, repr(exc)[:600])

print('\n' + '=' * 64)
print('%d passed, %d failed, %d skipped' % (PASSES, len(FAILURES), len(SKIPS)))
for f in FAILURES:
    print('  FAILED: %s' % f)
sys.exit(1 if FAILURES else 0)
