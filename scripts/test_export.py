"""Tests for lib/netlist_export.py.

The claim being tested is that one description renders two consistent views: a
CDL/Spectre netlist carrying PDK CDF names for Cadence, and a SPICE deck that
actually simulates here. So the SPICE half is really simulated, and the two
halves are checked against each other rather than just eyeballed.

    python scripts/test_export.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib'))

import numpy as np
from netlist_export import (Design, Techmap, emit_cdl, emit_spectre, emit_spice,
                            export_package, parse_value, format_value)
import spectre_netlist as SN

FAILURES = []


def check(name, cond, detail=''):
    print(f'  [{"PASS" if cond else "FAIL"}] {name}{"  " + str(detail) if detail else ""}')
    if not cond:
        FAILURES.append(name)
    return cond


def build_ota():
    """The 5T OTA, described once."""
    d = Design(name='ota_5t')
    s = d.subckt('ota_5t', ['VDD', 'VSS', 'VINP', 'VINN', 'VBIAS', 'VOUT'])
    nin = dict(l='1u', w_f='2u', n_f=2)
    pld = dict(l='1u', w_f='4u', n_f=2)
    ntl = dict(l='1u', w_f='4u', n_f=4)
    s.device('M1', 'nmos', ['DIODE', 'VINP', 'TAIL', 'TAIL'], **nin)
    s.device('M2', 'nmos', ['VOUT', 'VINN', 'TAIL', 'TAIL'], **nin)
    s.device('M3', 'pmos', ['DIODE', 'DIODE', 'VDD', 'VDD'], **pld)
    s.device('M4', 'pmos', ['VOUT', 'DIODE', 'VDD', 'VDD'], **pld)
    s.device('M5', 'nmos', ['TAIL', 'VBIAS', 'VSS', 'VSS'], **ntl)
    return d


# ---------------------------------------------------------------------------
print('\n1. Value parsing (the unit traps)')
def approx(a, b, tol=1e-12):
    return a is not None and abs(a - b) <= tol * max(abs(a), abs(b), 1e-300)


check('2u', approx(parse_value('2u'), 2e-6))
# 180 * 1e-9 is not bit-identical to 180e-9 in binary floating point, so this is
# a tolerance comparison on purpose; format_value renders it back to 1.8e-07.
check('180n', approx(parse_value('180n'), 180e-9))
check('180n formats cleanly', format_value(parse_value('180n')) == '1.8e-07',
      format_value(parse_value('180n')))
check('1meg is mega', parse_value('1meg') == 1e6)
check('1m is milli', parse_value('1m') == 1e-3)
check('bare float', parse_value('0.0001') == 1e-4)
check('numeric passthrough', parse_value(4e-6) == 4e-6)
check('expression -> None', parse_value('W0*2') is None)
check('format emits no unit suffix',
      re.fullmatch(r'[-+0-9.e]+', format_value(2e-6)) is not None,
      format_value(2e-6))

print('\n2. CDL carries PDK CDF names')
d = build_ota()
cdl = emit_cdl(d)
check('.SUBCKT / .ENDS present', '.SUBCKT ota_5t' in cdl and '.ENDS' in cdl)
check('PDK cell names used', 'nmos1v' in cdl and 'pmos1v' in cdl)
check('CDF names used (fw, fingers)', 'fw=' in cdl and 'fingers=' in cdl)
check('w_tot derived, not assumed', 'w=4e-06' in cdl or 'w=4u' in cdl,
      [ln for ln in cdl.splitlines() if ln.startswith('M1')])
check('all 5 devices', sum(ln.startswith('M') for ln in cdl.splitlines()) == 5)

print('\n3. SPICE view carries SPICE names')
sp = emit_spice(d)
m1 = next(ln for ln in sp.splitlines() if ln.strip().startswith('M1'))
check('per-finger width as w', ' w=2e-06' in m1, m1.strip())
check('finger count as m', ' m=2' in m1, m1.strip())
check('no CDF names leak in', 'fw=' not in sp and 'fingers=' not in sp)
check('model names are the local ones', ' nch' in sp and ' pch' in sp)

print('\n4. The two views describe the same circuit')
cdl_nets = {}
for line in cdl.splitlines():
    if line.startswith('M'):
        parts = line.split()
        cdl_nets[parts[0]] = parts[1:5]
sp_nets = {}
for line in sp.splitlines():
    if line.strip().startswith('M'):
        parts = line.split()
        sp_nets[parts[0]] = parts[1:5]
check('same instances', set(cdl_nets) == set(sp_nets), sorted(set(cdl_nets) ^ set(sp_nets)))
check('same connectivity', cdl_nets == sp_nets)

print('\n5. Spectre view parses with aspice\'s own parser')
scs = emit_spectre(d)
nl = SN.parse_netlist(scs)
check('parses', nl is not None)
check('round-trips byte-identically', SN.write_netlist(nl) == scs)
check('subckt found', 'ota_5t' in nl.subckts, sorted(nl.subckts))
sub = nl.subckts.get('ota_5t')
check('6 ports, 5 instances', sub and len(sub.ports) == 6 and len(sub.instances) == 5,
      f'{len(sub.ports) if sub else 0} ports, {len(sub.instances) if sub else 0} inst')

print('\n6. Structural checks catch real mistakes')
bad = Design(name='bad')
b = bad.subckt('bad', ['A', 'B'])
b.device('M1', 'nmos', ['A', 'B', 'ORPHAN', 'B'], l='1u', w_f='1u', n_f=1)
problems = bad.check()
check('floating net reported', any('ORPHAN' in p and 'floating' in p for p in problems),
      problems)
dup = Design(name='dup')
dsub = dup.subckt('dup', ['A'])
dsub.device('M1', 'nmos', ['A', 'A', 'A', 'A'], l='1u', w_f='1u', n_f=1)
try:
    dsub.device('M1', 'nmos', ['A', 'A', 'A', 'A'], l='1u', w_f='1u', n_f=1)
    check('duplicate instance name rejected', False, 'no error raised')
except ValueError:
    check('duplicate instance name rejected', True)
missing = Design(name='missing')
msub = missing.subckt('m', ['A', 'B'])
msub.device('M1', 'nmos', ['A', 'B', 'A', 'B'])
check('missing sizing reported', any('no l' in p for p in missing.check()),
      missing.check())

print('\n7. It actually simulates (the point of the SPICE view)')
models = ROOT / 'models' / 'ptm_180nm.lib'
if not models.is_file():
    check('model file present (run scripts/fetch_models.py)', False, str(models))
else:
    testbench = """XOTA VDD 0 VINP VINN VBIAS VOUT ota_5t
VDD  VDD 0 DC 1.8
VB   VBIAS 0 DC 0.75
VIP  VINP 0 DC 0.9
VIN  VINN 0 DC 0.9"""
    deck = emit_spice(d, models=models, testbench=testbench)
    import analog_spice as A
    ng = A._PatchedNgSpiceShared.new_instance()
    ng.load_circuit(deck)
    ng.exec_command('op')
    plot = ng.plot(None, ng.plot_names[0])
    vals = {k.lower(): float(np.asarray(v._data).ravel()[0].real)
            for k, v in plot.items() if getattr(v, '_data', None) is not None
            and len(v._data)}
    check('operating point obtained', bool(vals), f'{len(vals)} vectors')
    idd = next((abs(v) for k, v in vals.items() if 'vdd' in k and 'branch' in k), None)
    check('supply current is a real bias current', idd is not None and 1e-7 < idd < 1e-2,
          f'I(VDD) = {idd * 1e6:.2f} uA' if idd else 'no VDD branch')
    vout, diode = vals.get('vout'), vals.get('xota.diode')
    check('VOUT inside the rails', vout is not None and 0 < vout < 1.8,
          f'V(VOUT) = {vout:.4f} V' if vout is not None else 'missing')
    # With both inputs at the same voltage the pair is balanced, so the output
    # must sit at the mirror's diode voltage. This is a correctness check on the
    # emitted connectivity, not just on the simulator running.
    check('balanced pair puts VOUT at the diode node',
          vout is not None and diode is not None and abs(vout - diode) < 1e-6,
          f'VOUT={vout:.6f} DIODE={diode:.6f}' if vout and diode else 'missing')

    # Changing the tail finger count must change the current: proves m is real.
    # n_f must reach the simulator as a real device multiplier. Isolate it on a
    # single biased device: inside the OTA the tail cannot simply double, because
    # the pair pushes the tail node up and takes M5 out of saturation, so the
    # loop gain of the circuit would mask whether m worked at all.
    def one_device_current(n_f):
        probe = Design(name='probe')
        p = probe.subckt('probe', ['D', 'G', 'S'])
        p.device('M1', 'nmos', ['D', 'G', 'S', 'S'], l='1u', w_f='2u', n_f=n_f)
        tb = ('XP D G 0 probe\nVD D 0 DC 1.8\nVG G 0 DC 0.9')
        # Reuse the same shared instance: cffi's cdef() is global, so building a
        # second NgSpiceShared raises "duplicate declaration of struct ngcomplex".
        ng.load_circuit(emit_spice(probe, models=models, testbench=tb))
        ng.exec_command('op')
        pl = ng.plot(None, ng.plot_names[0])
        vv = {k.lower(): float(np.asarray(v._data).ravel()[0].real)
              for k, v in pl.items()
              if getattr(v, '_data', None) is not None and len(v._data)}
        return next((abs(v) for k, v in vv.items() if 'vd' in k and 'branch' in k), None)

    i1, i2 = one_device_current(1), one_device_current(2)
    ratio = i2 / i1 if i1 and i2 else 0
    check('n_f reaches the simulator as a device multiplier',
          1.98 < ratio < 2.02,
          f'm=1 -> {i1 * 1e6:.3f} uA, m=2 -> {i2 * 1e6:.3f} uA (x{ratio:.4f})')

print('\n8. export_package writes a complete, self-describing bundle')
with tempfile.TemporaryDirectory() as tmp:
    result = export_package(build_ota(), Path(tmp) / 'ota_5t',
                            models=models if models.is_file() else None,
                            notes='exported by the test suite')
    out = Path(result['dir'])
    for f in ('ota_5t.cdl', 'ota_5t.scs', 'ota_5t.sp', 'techmap.json',
              'manifest.json', 'README.md'):
        check(f'wrote {f}', (out / f).is_file())
    manifest = json.loads((out / 'manifest.json').read_text())
    check('manifest records the PDK library', manifest['pdk_library'] == 'gpdk045')
    check('manifest records the CDF map', manifest['cdf_parameter_map']['w_f'] == 'fw')
    check('manifest lists nets', 'TAIL' in manifest['subckts'][0]['nets'])
    check('clean design has no structural warnings',
          manifest['structural_warnings'] == [], manifest['structural_warnings'])
    check('manifest does not claim local verification',
          manifest['verified_locally'] is False)
    # The exported techmap must reload to the same mapping.
    tm = Techmap.load(out / 'techmap.json')
    check('techmap round-trips', tm.params['w_f'] == 'fw' and tm.cells['nmos'] == 'nmos1v')
    # And the exported Spectre file must parse.
    reparsed = SN.parse_netlist((out / 'ota_5t.scs').read_text())
    check('exported .scs re-parses', 'ota_5t' in reparsed.subckts)

print('\n' + '=' * 64)
if FAILURES:
    print(f'{len(FAILURES)} check(s) failed:')
    for f in FAILURES:
        print(f'  - {f}')
    sys.exit(1)
print('ALL CHECKS PASSED')
