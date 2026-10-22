#!/usr/bin/env python3
"""Search the layout technique cards.

    python scripts/layout_search.py "common centroid capacitor"
    python scripts/layout_search.py --build          # embed with local ollama

Keyword search always works. If references/layout-kb/index.json exists and ollama is
running with the same embedding model, semantic rank is added. The cards are original
notes citing the textbooks and the LAYGO / BAG3++ papers; they are not the books.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / 'references' / 'layout-kb'
INDEX = KB / 'index.json'
OLLAMA = 'http://127.0.0.1:11434'
MODEL = 'qwen3-embedding:8b'


def cards():
    out = []
    for path in sorted(KB.glob('*.md')):
        if path.name == 'SOURCES.md':
            continue
        text = path.read_text(errors='replace')
        title = text.splitlines()[0].lstrip('# ').strip() if text.startswith('#') else path.stem
        out.append({'id': path.stem, 'title': title, 'path': str(path.relative_to(ROOT)), 'text': text})
    return out


def _tokens(text):
    return re.findall(r'[a-z0-9]+', text.lower())


def keyword_scores(query, rows):
    q = _tokens(query)
    scored = []
    for row in rows:
        title = _tokens(row['title'])
        body = _tokens(row['text'])
        score = sum(3 if t in title else 1 if t in body else 0 for t in q)
        scored.append(score)
    return scored


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _norm(a):
    return math.sqrt(_dot(a, a)) or 1.0


def embed(text, model=MODEL):
    body = json.dumps({'model': model, 'input': text}).encode()
    req = urllib.request.Request(OLLAMA + '/api/embed', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    vecs = data.get('embeddings') or data.get('embedding')
    if isinstance(vecs, list) and vecs and isinstance(vecs[0], list):
        return vecs[0]
    if isinstance(vecs, list):
        return vecs
    raise RuntimeError('ollama returned no embedding')


def build(model=MODEL):
    rows = []
    for card in cards():
        vec = embed(card['title'] + '\n' + card['text'], model=model)
        rows.append({**card, 'vector': vec})
    _write_index({'model': model, 'cards': rows})
    print('wrote %s (%d cards, model %s)' % (INDEX, len(rows), model))


def _write_index(payload):
    """Replace the index atomically so a reader never sees a truncated file."""
    tmp = INDEX.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(payload))
    os.replace(tmp, INDEX)


def _load_index():
    """The parsed index, or None if it is missing or mid-write. Keyword search still runs."""
    try:
        return json.loads(INDEX.read_text())
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None


def search(query, k=4):
    rows = cards()
    if not rows:
        print('no technique cards found')
        return 1
    kw = keyword_scores(query, rows)
    sem = [0.0] * len(rows)
    how = 'keyword'
    idx = _load_index()
    # A foreign or corrupt index must not outrank the keyword hit or abort the search.
    if isinstance(idx, dict) and idx.get('model') == MODEL and idx.get('cards'):
        try:
            by_id = {c['id']: c.get('vector') for c in idx['cards'] if isinstance(c, dict) and 'id' in c}
            qv = embed(query, model=MODEL)
            qn = _norm(qv)
            for i, row in enumerate(rows):
                vec = by_id.get(row['id'])
                if vec and len(vec) == len(qv) and all(isinstance(x, (int, float)) for x in vec):
                    sem[i] = _dot(qv, vec) / (qn * _norm(vec))
            how = 'keyword+embedding (%s)' % MODEL
        except (urllib.error.URLError, TimeoutError, RuntimeError, OSError, TypeError, KeyError, ValueError):
            how = 'keyword (ollama unavailable)'
            sem = [0.0] * len(rows)
    kw_max = max(kw) or 1
    ranked = sorted(range(len(rows)), key=lambda i: -(kw[i] / kw_max + sem[i]), )
    print('search: %s' % how)
    shown = 0
    for i in ranked:
        if shown >= k:
            break
        if kw[i] == 0 and sem[i] <= 0:
            continue
        row = rows[i]
        print('\n## %s\n(%s, keyword %d, semantic %.2f)\n' % (row['title'], row['path'], kw[i], sem[i]))
        print(row['text'].strip())
        shown += 1
    if not shown:
        print('no card matched')
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('query', nargs='?')
    ap.add_argument('--build', action='store_true')
    ap.add_argument('-k', type=int, default=4)
    ap.add_argument('--model', default=MODEL)
    a = ap.parse_args(argv)
    if a.build:
        build(a.model)
        return 0
    if not a.query:
        ap.error('give a query, or --build')
    return search(a.query, a.k)


if __name__ == '__main__':
    sys.exit(main())
