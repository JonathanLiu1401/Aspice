"""Optional SOS command discovery on the host that owns the CIW workarea."""

from __future__ import annotations

import os
import shlex
import subprocess

from virtuoso_bridge.transport.ssh import CommandResult


class SOSEnvironmentError(RuntimeError):
    """The optional SOS installation is unavailable or incorrectly configured."""


class LocalSOSRunner:
    """POSIX runner for explicitly local Virtuoso sessions, never inferred from TCP."""

    def run_command(self, command: str, *, timeout: float) -> CommandResult:
        result = subprocess.run(
            ["/bin/sh", "-l"], input=command + "\n", text=True,
            capture_output=True, timeout=timeout, check=False,
        )
        return CommandResult(result.returncode, result.stdout, result.stderr)


def sos_runner(owner):
    """Return the explicit GUI-filesystem runner used by SOS commands."""
    runner = getattr(owner, "sos_runner", None)
    if runner is not None:
        return runner
    runner = getattr(owner, "gui_runner", None)
    if runner is not None:
        return runner
    local_opt_in = os.environ.get("VB_SOS_LOCAL_FILESYSTEM", "").strip().lower()
    if os.name == "posix" and local_opt_in in {"1", "true", "yes", "on"}:
        return LocalSOSRunner()
    return None


def resolve_soscmd(owner, explicit: str | None, *, timeout: float) -> str:
    """Resolve once per operation; never fall back from an explicit installation."""
    runner = sos_runner(owner)
    if runner is None:
        raise SOSEnvironmentError(
            "SOS requires the GUI-host SSH runner or an explicitly local POSIX client. "
            "Plain TCP and native Windows local sessions have no SOS filesystem runner."
        )
    profile = getattr(getattr(owner, "_tunnel", None), "_profile", None)
    configured = explicit
    if configured is None and profile:
        configured = os.environ.get(f"VB_SOS_COMMAND_{profile}") or None
    if configured is None:
        configured = os.environ.get("VB_SOS_COMMAND") or None
    if configured is not None:
        if (not configured.strip() or configured.startswith("-")
                or any(c in configured for c in "\x00\r\n\t")
                or ("/" in configured and not configured.startswith("/"))):
            raise ValueError("SOS command must be one absolute POSIX executable path or command name.")
        selection = "vb_sos=$(command -v " + shlex.quote(configured) + ") || exit 127"
    else:
        # Expand CLIOSOFT_DIR on the execution host, not on the Python client.
        selection = '''vb_sos=$(command -v soscmd) || {
  if [ -n "${CLIOSOFT_DIR:-}" ] && [ -x "$CLIOSOFT_DIR/bin/soscmd" ]; then
    vb_sos="$CLIOSOFT_DIR/bin/soscmd"
  else
    exit 127
  fi
}'''
    command = selection + '''
case "$vb_sos" in /*) ;; *) exit 126 ;; esac
[ -f "$vb_sos" ] && [ -x "$vb_sos" ] || exit 126
printf '%s\\n' "$vb_sos"
'''
    result = runner.run_command(command, timeout=timeout)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SOSEnvironmentError(
            "SOS executable unavailable on the GUI host. Install/configure SOS there, "
            "or set VB_SOS_COMMAND (profile suffix supported) to an absolute executable "
            "or site wrapper that prepares its environment. "
            f"Exit {result.returncode}. {detail}"
        )
    executable = result.stdout.strip()
    if (not executable.startswith("/") or any(c in executable for c in "\x00\r\n\t")):
        raise SOSEnvironmentError("SOS discovery returned an ambiguous/non-absolute executable path.")
    return executable
