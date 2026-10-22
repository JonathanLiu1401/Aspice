"""Runtime path policy for local logs, state, cache, tmp, and artifacts.

Helpers return paths only.  Callers create directories at the write site so a
plain ``import virtuoso_bridge`` stays side-effect free.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_APP_NAME = "virtuoso_bridge"


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return Path(raw).expanduser().resolve()


def _home_path(*parts: str) -> Path | None:
    root = _env_path("VB_HOME")
    if root is None:
        return None
    return root.joinpath(*parts)


def _windows_base(name: str, fallback: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else fallback


def _xdg_base(name: str, fallback: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else fallback


def config_dir() -> Path:
    if (path := _env_path("VB_CONFIG_DIR")) is not None:
        return path
    if (path := _home_path("config")) is not None:
        return path
    if os.name == "nt":
        return _windows_base("APPDATA", Path.home() / "AppData" / "Roaming") / _APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_NAME
    return _xdg_base("XDG_CONFIG_HOME", Path.home() / ".config") / _APP_NAME


def state_dir() -> Path:
    if (path := _env_path("VB_STATE_DIR")) is not None:
        return path
    if (path := _home_path("state")) is not None:
        return path
    if os.name == "nt":
        return _windows_base("LOCALAPPDATA", Path.home() / "AppData" / "Local") / _APP_NAME / "state"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / _APP_NAME / "state"
    return _xdg_base("XDG_STATE_HOME", Path.home() / ".local" / "state") / _APP_NAME


def cache_dir(*parts: str) -> Path:
    if (path := _env_path("VB_CACHE_DIR")) is not None:
        return path.joinpath(*parts)
    if (path := _home_path("cache")) is not None:
        return path.joinpath(*parts)
    if os.name == "nt":
        base = _windows_base("LOCALAPPDATA", Path.home() / "AppData" / "Local") / _APP_NAME / "cache"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / _APP_NAME
    else:
        base = _xdg_base("XDG_CACHE_HOME", Path.home() / ".cache") / _APP_NAME
    return base.joinpath(*parts)


def log_dir() -> Path:
    if (path := _env_path("VB_LOG_DIR")) is not None:
        return path
    if (path := _home_path("logs")) is not None:
        return path
    if os.name == "nt":
        return _windows_base("LOCALAPPDATA", Path.home() / "AppData" / "Local") / _APP_NAME / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / _APP_NAME
    return state_dir() / "logs"


def tmp_dir(*parts: str) -> Path:
    if (path := _env_path("VB_TMP_DIR")) is not None:
        return path.joinpath(*parts)
    if (path := _home_path("tmp")) is not None:
        return path.joinpath(*parts)
    if os.name == "nt":
        base = _windows_base("LOCALAPPDATA", Path.home() / "AppData" / "Local") / _APP_NAME / "tmp"
    else:
        base = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / _APP_NAME
    return base.joinpath(*parts)


def artifact_dir(*parts: str) -> Path:
    if (path := _env_path("VB_OUTPUT_DIR")) is not None:
        return path.joinpath(*parts)
    if (path := _home_path("artifacts")) is not None:
        return path.joinpath(*parts)
    if os.name == "nt":
        base = _windows_base("LOCALAPPDATA", Path.home() / "AppData" / "Local") / _APP_NAME / "artifacts"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / _APP_NAME / "artifacts"
    else:
        base = state_dir() / "artifacts"
    return base.joinpath(*parts)


def command_log_file() -> Path:
    return log_dir() / "commands.log"


def legacy_cache_state_file(profile: str | None = None) -> Path:
    name = f"state_{profile}.json" if profile else "state.json"
    return Path.home() / ".cache" / _APP_NAME / name
