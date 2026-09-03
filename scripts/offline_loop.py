"""The whole aspice loop with no Cadence, no Spectre, and no real PDK.

Exercises the exact round trip the Linux workflow needs: a netlist exported
from Virtuoso comes in, gets understood, retuned to hit a spec, and goes back
out as a configured ADE netlist that still points at the real PDK.

    python scripts/offline_loop.py
"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib'))

import netlist_digest as D
import spectre_netlist as SN
from spectre_to_ngspice import apply_tuned, lint, simulate, translate, tune

SRC = ROOT / 'corpus' / '03_diffpair_ade.scs'
PTM = ROOT / 'models' / 'ptm_180nm.lib'
FAIL = []


def check(name, cond, detail=''):
    print(f'  [{"PASS" if cond else "FAIL"}] {name}{"  " + str(detail) if detail else ""}')
    if not cond:
        FAIL.append(name)
    return cond


def fwd(text):
    return text.replace('\\', '/')


# newline='' matters: the corpus files are CRLF, and parse_file preserves them.
# Reading with read_text() would translate them and fake a round-trip failure.
with open(SRC, 'r', encoding='utf-8', newline='') as fh:
    raw = fh.read()

print('\n=== STEP 1: ingest the netlist the Linux box would hand you ===')
nl = SN.parse_file(SRC)
check('parsed', nl is not None)
check('round-trips byte-identically', SN.write_netlist(nl) == raw)
inc_paths = [getattr(i, 'path', '') or '' for i in nl.includes]
check('the proprietary PDK include is understood, not needed',
      any('gpdk045' in p for p in inc_paths), inc_paths)

print('\n=== STEP 2: read it without spending the tokens ===')
print(D.outline(nl))
print(f'  raw netlist  ~{D.est_tokens(raw)} tokens')
print(f'  outline      ~{D.est_tokens(D.outline(nl))} tokens')
print(f'  summary      ~{D.est_tokens(D.summary(nl))} tokens')
check('digest is cheaper than the raw file',
      D.est_tokens(D.summary(nl)) < D.est_tokens(raw))

print('\n=== STEP 3: substitute an open model card for the licensed PDK ===')
sim_nl = SN.parse_file(SRC)      # throwaway copy; the pristine one is exported later
tr = translate(sim_nl, models=str(PTM))
check('it SAYS the licensed PDK is unavailable rather than pretending',
      any('gpdk045' in w and 'omitted' in w for w in tr.warnings), tr.warnings)
check('translation lints clean', lint(tr.netlist) == [], lint(tr.netlist))
check('no $PDK reference survives into the simulated deck', '$PDK' not in tr.netlist)
check('the open card is what actually gets included',
      'ptm_180nm.lib' in fwd(tr.netlist))
if tr.unsupported:
    print(f'  unsupported constructs reported honestly: {tr.unsupported}')
print(f'  analyses recognised: {tr.analyses}')

print('\n=== STEP 4: it really biases (ngspice is an independent tool) ===')
res = simulate(tr.netlist)
check('op converged', res.get('ok') is True, res.get('error'))


def outs(results):
    low = {k.lower(): v for k, v in (results.get('nodes') or {}).items()
           if v is not None}
    got = {}
    for want in ('outp', 'outn'):
        for cand in (want, f'i0.{want}', f'x_i0.{want}', f'xi0.{want}'):
            if cand in low:
                got[want] = float(low[cand])
                break
    return got, low


got, low = outs(res)
print(f'  differential outputs: {got}')
check('both outputs found', set(got) == {'outp', 'outn'}, sorted(low)[:14])
if len(got) == 2:
    check('the pair is balanced (it is a symmetric diff pair)',
          abs(got['outp'] - got['outn']) < 5e-3,
          f"delta = {abs(got['outp'] - got['outn']) * 1e3:.3f} mV")
    # This netlist was drawn for gpdk045. Biased instead against a 180 nm card,
    # Vbias=0.55 barely turns the tail on, so both outputs sit near VDD. That is
    # not a defect: it is the honest consequence of substituting an open model
    # for a licensed one, and it is exactly what step 5 has to fix. The point
    # worth asserting is that the substitution is visible, not that it is free.
    starved = all(v > 1.7 for v in got.values())
    print(f'  note: outputs at {got["outp"]:.3f} V with VDD=1.8 -> tail is '
          f'starved by the model swap, needs re-biasing')
    check('the model swap shifts the bias point, and it shows', starved, got)

print('\n=== STEP 5: re-bias to hit a spec by tuning, not by guessing ===')
GOAL = 1.30      # V output common mode


def meas(results):
    g, low_ = outs(results)
    if len(g) != 2:
        raise KeyError(f'outputs missing from {list(low_)}')
    return (g['outp'] + g['outn']) / 2.0


before = meas(res)
print(f'  starting output common mode: {before:.5f} V, goal {GOAL} V')
pristine_text = SN.write_netlist(sim_nl)
# Rload alone cannot get there: the tail is starved, so even 60k only drops
# 0.26 V. The tail gate bias is the knob that actually sets the current, so
# tune both, which is also what exercises the multi-knob least-squares path.
t = tune(sim_nl,
         knobs=[{'param': 'dc', 'instance': 'VBIAS', 'low': 0.5, 'high': 1.2},
                {'param': 'Rload', 'low': 1e3, 'high': 60e3}],
         targets=[{'measure': meas, 'name': 'vocm', 'goal': GOAL, 'tol': 0.01}],
         models=str(PTM))
print(f'  knobs: {t.knobs}')
print(f'  achieved: {t.achieved}  met={t.met}  iterations={t.iterations}')
check('target met within 1%', t.met is True, t.messages)
check('tune did not mutate the netlist it was given',
      SN.write_netlist(sim_nl) == pristine_text)

print('\n=== STEP 6: write the configured ADE netlist back out ===')
out = SN.parse_file(SRC)         # pristine: keeps the real $PDK include
apply_tuned(out, t.knobs)
new_text = SN.write_netlist(out)
check('exported netlist still points at the real PDK',
      'gpdk045.scs' in new_text and 'section=tt' in new_text)
check('exported netlist re-parses', 'diffpair' in SN.parse_netlist(new_text).subckts)
check('and re-emits to the same bytes (writer/parser fixed point)',
      SN.write_netlist(SN.parse_netlist(new_text)) == new_text)

print('\n  raw text diff against what came in:')
diff = [l for l in difflib.unified_diff(raw.splitlines(), new_text.splitlines(),
                                        lineterm='', n=0)
        if l[:1] in '+-' and not l.startswith(('+++', '---'))]
for line in diff:
    print(f'    {line}')
changed = {l[1:] for l in diff}
touched = [l for l in changed if 'Rload' in l or 'VBIAS' in l or 'vbias' in l]
check('only the tuned lines changed, nothing else drifted',
      len(touched) == len(changed), sorted(changed - set(touched)))
check('the edit is surgical (a handful of lines, not a rewrite)',
      len(diff) <= 6, f'{len(diff)} diff lines')

print('\n=== STEP 7: confirm the exported file behaves as promised ===')
verify = SN.parse_netlist(new_text)
vres = simulate(translate(verify, models=str(PTM)).netlist)
check('the exported netlist simulates', vres.get('ok') is True, vres.get('error'))
if vres.get('ok'):
    final = meas(vres)
    print(f'  re-simulated from the exported file: {final:.5f} V (goal {GOAL})')
    check('the exported file independently reproduces the target',
          abs(final - GOAL) / GOAL <= 0.01, f'{final:.5f} V')

print('\n' + '=' * 66)
if FAIL:
    print(f'{len(FAIL)} check(s) failed:')
    for f in FAIL:
        print(f'  - {f}')
    sys.exit(1)
print('OFFLINE LOOP OK - no Cadence, no Spectre, no proprietary PDK')
