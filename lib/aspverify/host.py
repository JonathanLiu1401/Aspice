"""Where verification runs: the Virtuoso/Spectre host, locally or over the bridge's SSH.

PVS, Quantus, si and strmout need the site's Cadence environment, the licenses and the
design libraries, which all live on the host Virtuoso runs on. Commands therefore run
there, wrapped in the profile's ``VB_CADENCE_CSHRC`` like Spectre runs; only reports
and small text files come back.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from virtuoso_bridge.cadence_env import CadenceEnvironment
from virtuoso_bridge.env import load_vb_env
from virtuoso_bridge.profile import resolve_profile
from virtuoso_bridge.transport.remote_roles import remote_host_roles_from_os
from virtuoso_bridge.transport.ssh import (
    SSHRunner,
    ssh_backend_env_from_os,
    ssh_proxy_url_from_os,
)
from virtuoso_bridge.transport.tunnel import _is_localhost


@dataclass
class HostResult:
    returncode: int
    stdout: str
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class VerifyHost:
    """Run shell commands and move small text files on the verification host."""

    def __init__(self, runner: SSHRunner | None = None,
                 cadence_env: CadenceEnvironment | None = None) -> None:
        self._runner = runner
        self._env = cadence_env or CadenceEnvironment()

    @property
    def is_local(self) -> bool:
        return self._runner is None

    @classmethod
    def from_env(cls, profile: str | None = None) -> VerifyHost:
        profile = resolve_profile(profile)
        load_vb_env()
        roles = remote_host_roles_from_os(profile, load=False)
        env = CadenceEnvironment.from_env(profile)
        host = roles.spectre_host or ""
        if not host or _is_localhost(host):
            return cls(None, env)
        backend = ssh_backend_env_from_os(profile)
        runner = SSHRunner(
            host=host,
            user=roles.remote_user,
            jump_host=roles.jump_for(host),
            jump_user=roles.jump_user,
            persistent_shell=True,
            backend=backend.backend,
            max_sessions=backend.max_sessions,
            proxy_url=ssh_proxy_url_from_os(profile),
        )
        return cls(runner, env)

    def run(self, command: str, cwd: str | None = None, timeout: float = 3600) -> HostResult:
        """Run *command* (POSIX sh) in the Cadence environment, optionally inside *cwd*."""
        if cwd:
            command = f"cd {shlex.quote(cwd)} && {command}"
        wrapped = self._env.wrap(command)
        if self._runner is None:
            p = subprocess.run(wrapped, shell=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, timeout=timeout)
            return HostResult(p.returncode, p.stdout, p.stderr)
        r = self._runner.run_command(wrapped, timeout=timeout)
        return HostResult(r.returncode, r.stdout, r.stderr)

    def write_text(self, path: str, text: str) -> None:
        if self._runner is None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(text)
            return
        self._runner.run_command(f"mkdir -p {shlex.quote(str(Path(path).parent))}")
        r = self._runner.upload_text(text, path)
        if r.returncode != 0:
            raise RuntimeError(f"cannot write {path} on the verification host: {r.stderr.strip()}")

    def read_text(self, path: str, max_bytes: int = 8_000_000) -> str | None:
        """Contents of *path*, or None when it does not exist."""
        if self._runner is None:
            p = Path(path)
            return p.read_text(errors="replace")[:max_bytes] if p.is_file() else None
        r = self._runner.run_command(
            f"test -f {shlex.quote(path)} && head -c {max_bytes} {shlex.quote(path)}")
        return r.stdout if r.returncode == 0 else None

    def exists(self, path: str) -> bool:
        return self.run(f"test -e {shlex.quote(path)}").ok

    def download(self, remote: str, local: Path) -> bool:
        """Copy one file or directory back to the client (no-op copy when local)."""
        local.parent.mkdir(parents=True, exist_ok=True)
        if self._runner is None:
            src = Path(remote)
            if src.resolve() == local.resolve():
                return True
            cmd = ["cp", "-r", str(src), str(local)]
            return subprocess.run(cmd, capture_output=True).returncode == 0
        return self._runner.download(remote, local, recursive=True).returncode == 0
