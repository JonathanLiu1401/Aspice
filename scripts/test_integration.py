"""Integration test: real parser IR -> real digest, over the whole corpus.

The parser, the digest and the corpus were each built independently against a
written IR contract. This is the test that they actually fit together, and that
the parser's output matches the corpus's declared ground truth.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib'))

import spectre_netlist as SN
import netlist_digest as ND

FAIL = []


def check(name, cond, detail=''):
    print(f'  [{"PASS" if cond else "FAIL"}] {name}{"  " + detail if detail else ""}')
    if not cond:
        FAIL.append(name)


corpus = sorted((ROOT / 'corpus').glob('*.scs'))
print(f'{len(corpus)} corpus files\n')

for scs in corpus:
    text = scs.read_text()
    label = scs.name
    try:
        nl = SN.parse_netlist(text)
    except Exception as exc:
        check(f'{label}: parses', False, f'{type(exc).__name__}: {exc}')
        continue

    # 1. Round-trip must be byte-identical.
    check(f'{label}: round-trip byte-identical',
          SN.write_netlist(nl) == text)

    # 2. Every digest view must run on the real IR without raising.
    for fn_name in ('outline', 'summary', 'device_rollup'):
        try:
            out = getattr(ND, fn_name)(nl)
            ok = isinstance(out, str)
        except Exception as exc:
            ok, out = False, f'{type(exc).__name__}: {exc}'
        check(f'{label}: digest.{fn_name}', ok, '' if ok else str(out)[:90])

    # 3. Ground truth from the corpus's own .expected.json.
    expected_path = scs.with_suffix('').with_suffix('.expected.json')
    if not expected_path.exists():
        expected_path = Path(str(scs)[:-4] + '.expected.json')
    if expected_path.exists():
        exp = json.loads(expected_path.read_text())
        if 'n_subckts' in exp:
            check(f'{label}: subckt count', len(nl.subckts) == exp['n_subckts'],
                  f'got {len(nl.subckts)} want {exp["n_subckts"]} '
                  f'({sorted(nl.subckts)})')
        if 'language_at_start' in exp:
            check(f'{label}: start language',
                  nl.language == exp['language_at_start'],
                  f'got {nl.language!r} want {exp["language_at_start"]!r}')
        if 'parameters' in exp and exp['parameters']:
            missing = [k for k in exp['parameters'] if k not in nl.parameters]
            check(f'{label}: global parameters', not missing,
                  f'missing {missing}' if missing else '')
        if 'analyses' in exp and exp['analyses']:
            got = {a.type for a in nl.analyses}
            want = {a['type'] for a in exp['analyses']}
            check(f'{label}: analysis types', want <= got,
                  f'got {sorted(got)} want superset of {sorted(want)}')

# 4. Editing must change exactly one line.
sample = next(p for p in corpus if p.name.startswith('01_'))
nl = SN.parse_netlist(sample.read_text())
if nl.parameters:
    key = sorted(nl.parameters)[0]
    before = SN.write_netlist(nl).splitlines()
    SN.set_param(nl, key, '999n')
    after = SN.write_netlist(nl).splitlines()
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    check('set_param changes exactly one line', len(changed) == 1,
          f'changed lines: {changed}')
    check('set_param actually took effect', '999n' in after[changed[0]]
          if changed else False)

# 5. find_dangling must run on the real IR.
try:
    dangling = ND.find_dangling(nl)
    check('find_dangling runs on real IR', isinstance(dangling, list),
          f'{len(dangling)} single-terminal nets')
except Exception as exc:
    check('find_dangling runs on real IR', False, f'{type(exc).__name__}: {exc}')

print('\n' + '=' * 60)
if FAIL:
    print(f'{len(FAIL)} FAILED:')
    for f in FAIL:
        print(f'  - {f}')
    sys.exit(1)
print('INTEGRATION OK')
