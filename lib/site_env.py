"""Site settings. Nothing here names a person or a home directory.

A site is a KEY=VALUE file in references/sites/<name>.env. ASPICE_SITE selects it
(a name, or a path to an env file). When ASPICE_SITE is unset and the UW tool tree
is installed, references/sites/uw.env is used. Environment variables already set
win over the file.
"""
from __future__ import annotations

import os
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
_UW_MARKER = Path('/home/lab.apps/vlsiapps_new')


def site_file() -> Path | None:
    chosen = os.environ.get('ASPICE_SITE', '').strip()
    if chosen:
        path = Path(chosen).expanduser()
        if path.is_file():
            return path
        named = SKILL_ROOT / 'references' / 'sites' / ('%s.env' % chosen)
        return named if named.is_file() else None
    uw = SKILL_ROOT / 'references' / 'sites' / 'uw.env'
    if _UW_MARKER.is_dir() and uw.is_file():
        return uw
    return None


def load() -> dict[str, str]:
    path = site_file()
    out: dict[str, str] = {}
    if path is not None:
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, val = line.split('=', 1)
            out[key.strip()] = val.strip()
    for key in list(out):
        if os.environ.get(key):
            out[key] = os.environ[key]
    return out


def get(key: str, default: str = '') -> str:
    if os.environ.get(key):
        return os.environ[key]
    return load().get(key, default)
