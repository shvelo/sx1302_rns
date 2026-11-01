"""Shared fixtures for the hardware-independent test suite."""

from pathlib import Path

import pytest


MINIMAL_PROFILE = """
[board]
reset_enabled = no

[rf]
frequency = 868100000
tx_power = 14

[channel]
bandwidth = 125000
spreadingfactor = 9

[txgain.12]
rf_power = 12
pa_gain = 1
pwr_idx = 4

[txgain.14]
rf_power = 14
pa_gain = 1
pwr_idx = 7
"""


@pytest.fixture
def write_profile(tmp_path: Path):
    """Return a factory that writes a board profile and returns its path."""

    def write(body: str | None = None, name: str = "board.conf") -> Path:
        path = tmp_path / name
        path.write_text(body if body is not None else MINIMAL_PROFILE)
        return path

    return write


@pytest.fixture(autouse=True)
def release_radio_worker():
    """Do not let a failed test leak the process-wide HAL ownership flag."""
    yield

    from sx1302_reticulum import worker as worker_module

    active = worker_module._ACTIVE_WORKER
    if active is not None:
        active.stop(2)
        worker_module._ACTIVE_WORKER = None
