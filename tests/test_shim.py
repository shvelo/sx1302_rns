"""External interface shim and install helper tests."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

RNS = pytest.importorskip("RNS")

from RNS.Interfaces.Interface import Interface  # noqa: E402
from RNS.vendor.configobj import ConfigObj  # noqa: E402

from sx1302_reticulum.interface import SX1302Interface  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "interfaces" / "SX1302.py"
EXAMPLE_STANZA = ROOT / "examples" / "reticulum-sx1302.conf"


def test_example_stanza_uses_the_reticulum_interfaces_section():
    config = ConfigObj(str(EXAMPLE_STANZA))

    stanza = config["interfaces"]["SX1302 LoRa"]
    assert stanza["type"] == "SX1302"
    assert stanza.as_bool("enabled") is True
    assert "board_profile" in stanza


def test_shim_defines_interface_class_when_executed_by_reticulum():
    # Reticulum exec()s the shim with only RNS and Interface in globals. It
    # does not provide __file__, __name__ or the package import path.
    namespace = {"RNS": RNS, "Interface": Interface}

    exec(SHIM.read_text(), namespace)

    assert namespace["interface_class"] is SX1302Interface


def test_install_script_symlinks_shim(tmp_path):
    script = ROOT / "scripts" / "install_interface.sh"
    result = subprocess.run(
        [str(script), "--configdir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    destination = tmp_path / "interfaces" / "SX1302.py"
    assert destination.is_symlink()
    assert destination.resolve() == SHIM.resolve()

    again = subprocess.run(
        [str(script), "--configdir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert again.returncode == 0, again.stderr
    assert "already installed" in again.stdout
