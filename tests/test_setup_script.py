"""Checks for the Pi setup script that do not touch the system."""

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup_pi.sh"


def test_setup_script_parses():
    assert SCRIPT.is_file()
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_setup_script_help_lists_options():
    result = subprocess.run(
        [str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for option in (
        "--configdir",
        "--python",
        "--skip-system",
        "--skip-build",
        "--skip-tests",
        "--skip-interface",
    ):
        assert option in result.stdout


def test_setup_script_rejects_unknown_arguments():
    result = subprocess.run(
        [str(SCRIPT), "--nope"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "unknown argument" in result.stderr
