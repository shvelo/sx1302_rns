"""Argument handling for the Reticulum probe responder."""

from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("RNS")

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rns_echo.py"


def test_echo_help_lists_options():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for option in ("--config", "--name", "--identity", "--announce-interval"):
        assert option in result.stdout


def test_echo_rejects_negative_announce_interval():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--announce-interval", "-1"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "must not be negative" in result.stderr
