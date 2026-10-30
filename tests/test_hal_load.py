"""Load the built shared HAL and verify the symbols needed by the interface."""

import ctypes
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED_LIBRARY = ROOT / "build" / "libloragw.so"
BUILT_SOURCE = ROOT / "build" / "sx1302_hal" / "libloragw" / "src" / "loragw_hal.c"

REQUIRED_SYMBOLS = (
    "lgw_board_setconf",
    "lgw_rxrf_setconf",
    "lgw_rxif_setconf",
    "lgw_demod_setconf",
    "lgw_txgain_setconf",
    "lgw_start",
    "lgw_stop",
    "lgw_receive",
    "lgw_send",
    "lgw_status",
    "lgw_get_temperature",
    "lgw_version_info",
    "lgw_time_on_air",
)

pytestmark = pytest.mark.skipif(
    not SHARED_LIBRARY.is_file(),
    reason="run scripts/build_hal.sh first",
)


def test_required_symbols_are_exported():
    library = ctypes.CDLL(str(SHARED_LIBRARY))
    missing = []
    for name in REQUIRED_SYMBOLS:
        try:
            getattr(library, name)
        except AttributeError:
            missing.append(name)
    assert not missing, f"missing symbols: {', '.join(missing)}"


def test_version_string_is_usable():
    library = ctypes.CDLL(str(SHARED_LIBRARY))
    library.lgw_version_info.restype = ctypes.c_char_p
    version = library.lgw_version_info()
    assert version is not None
    assert version.startswith(b"Version:")


def test_optional_temperature_patch_is_applied():
    source = BUILT_SOURCE.read_text()
    assert "no temperature sensor found, continuing without" in source
    assert "if (ts_fd >= 0)" in source
    assert "*temperature = 25.0f;" in source
