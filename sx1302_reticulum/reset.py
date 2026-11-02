"""Run the board reset script shared by the smoke test and the interface."""

from __future__ import annotations

import os
import subprocess
from typing import Callable

from .config import BoardProfile


class ResetError(RuntimeError):
    """Raised when the board reset script cannot be run or fails."""


def run_reset(
    profile: BoardProfile,
    action: str,
    log: Callable[[str], None] | None = None,
    timeout: float = 30.0,
) -> None:
    """Run ``profile``'s reset script for ``action``.

    The script inherits the current environment plus the profile's reset GPIO
    override. An explicitly exported ``SX1302_RESET_GPIO`` wins, so a single
    run can target a different carrier without editing the profile.

    Output is captured and passed to ``log`` line by line. This keeps the
    reset sequence readable in both the smoke test and Reticulum's log without
    interleaving partial lines from the child process.
    """
    command = profile.reset_command(action)
    if command is None:
        if log is not None:
            log(f"Reset {action}: skipped (disabled in board profile)")
        return

    env = os.environ.copy()
    for key, value in profile.reset_env().items():
        env.setdefault(key, value)

    if log is not None:
        log(f"Reset {action}: {' '.join(command)}")
        gpio = env.get("SX1302_RESET_GPIO")
        if gpio is not None:
            log(f"Reset GPIO: {gpio}")

    try:
        result = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ResetError(
            f"reset script timed out after {timeout:g} s"
        ) from exc
    except OSError as exc:
        raise ResetError(f"could not run reset script: {exc}") from exc

    if log is not None:
        for line in (result.stdout + result.stderr).splitlines():
            log(line)

    if result.returncode != 0:
        raise ResetError(
            f"reset script failed with exit code {result.returncode}"
        )
