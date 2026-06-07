from __future__ import annotations

import logging
import subprocess
import sys
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Default timeout for a single tool subprocess call (seconds)
DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass
class SubprocessResult:
    """Result of a subprocess execution."""
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def run_script(
    script_path: str,
    *args: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
    env_extra: dict[str, str] | None = None,
) -> SubprocessResult:
    """
    Run a Python script via subprocess and return its output.

    Args:
        script_path: Path to the .py script (absolute or relative to cwd).
        *args: Additional arguments passed to the script.
        timeout: Seconds before the process is killed.
        cwd: Working directory for the subprocess.
        env_extra: Extra environment variables to merge into the child env.

    Returns:
        SubprocessResult with stdout, stderr, returncode, and timed_out flag.
    """
    cmd = [sys.executable, script_path, *args]
    import os
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if env_extra:
        env.update(env_extra)

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        return SubprocessResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning("subprocess timed out after %.1fs: %s", timeout, " ".join(cmd))
        return SubprocessResult(
            stdout=getattr(exc, "stdout", "") or "",
            stderr=getattr(exc, "stderr", "") or "",
            returncode=-1,
            timed_out=True,
        )
    except Exception as exc:
        logger.exception("subprocess error running %s", " ".join(cmd))
        return SubprocessResult(
            stdout="",
            stderr=str(exc),
            returncode=-1,
            timed_out=False,
        )


def run_command(
    *command: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
) -> SubprocessResult:
    """
    Run an arbitrary shell command (no Python interpreter prefix).

    Useful for calling CLI tools on PATH like ``arkham-rules``.
    """
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
        )
        return SubprocessResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning("command timed out after %.1fs: %s", timeout, " ".join(command))
        return SubprocessResult(
            stdout=getattr(exc, "stdout", "") or "",
            stderr=getattr(exc, "stderr", "") or "",
            returncode=-1,
            timed_out=True,
        )
    except Exception as exc:
        logger.exception("error running command %s", " ".join(command))
        return SubprocessResult(
            stdout="",
            stderr=str(exc),
            returncode=-1,
            timed_out=False,
        )
