"""Compile-time ABI check against the vendored or built HAL headers."""

from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_ctypes_layout_matches_c_compiler():
    if shutil.which("cc") is None and shutil.which("gcc") is None:
        pytest.skip("no C compiler available")

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "abi_check.py")],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout
    assert "ABI check passed" in result.stdout
