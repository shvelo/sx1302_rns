"""Low-level ctypes wrapper around libloragw.so.

All HAL entry points used by the project are declared here with explicit
argument and return types. Keeping the bindings in one place makes the unsafe
boundary auditable and gives the higher-level worker a small, typed API.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_char_p, c_float, c_int, c_uint8, c_uint32
import ctypes.util
import os
from pathlib import Path

from . import structs
from .config import BoardProfile

ROOT = Path(__file__).resolve().parent.parent


class HalError(RuntimeError):
    """Raised when a libloragw call reports an error."""


def find_library_path(explicit: str | Path | None = None) -> Path:
    """Locate libloragw.so using an explicit path, the SX1302_HAL_LIB env var, or system."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit).expanduser())
    env_path = os.environ.get("SX1302_HAL_LIB")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    # System-installed .so (via ldconfig)
    found = ctypes.util.find_library("loragw")
    if found:
        return Path(found)
    # Check candidates (explicit path, env var) before well-known locations
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    # Fallback: check a few well-known locations
    for loc in [
        Path("/usr/lib/arm-linux-gnueabihf/libloragw.so"),
        Path("/usr/local/lib/libloragw.so"),
        Path("/usr/lib/libloragw.so"),
    ]:
        if loc.is_file():
            return loc.resolve()
    raise FileNotFoundError(
        "libloragw.so not found; set SX1302_HAL_LIB or run sx1302-ctl --init "
        "to install the prebuilt HAL library"
    )


class Hal:
    """Typed wrapper around the subset of libloragw used by this project."""

    def __init__(self, library_path: str | Path | None = None):
        self.path = find_library_path(library_path)
        self.lib = ctypes.CDLL(str(self.path))
        self._bind()

    def _bind(self) -> None:
        lib = self.lib

        lib.lgw_board_setconf.argtypes = [POINTER(structs.LgwConfBoard)]
        lib.lgw_board_setconf.restype = c_int

        lib.lgw_rxrf_setconf.argtypes = [
            c_uint8,
            POINTER(structs.LgwConfRxrf),
        ]
        lib.lgw_rxrf_setconf.restype = c_int

        lib.lgw_rxif_setconf.argtypes = [
            c_uint8,
            POINTER(structs.LgwConfRxif),
        ]
        lib.lgw_rxif_setconf.restype = c_int

        lib.lgw_demod_setconf.argtypes = [POINTER(structs.LgwConfDemod)]
        lib.lgw_demod_setconf.restype = c_int

        lib.lgw_txgain_setconf.argtypes = [
            c_uint8,
            POINTER(structs.LgwTxGainLut),
        ]
        lib.lgw_txgain_setconf.restype = c_int

        lib.lgw_start.argtypes = []
        lib.lgw_start.restype = c_int

        lib.lgw_stop.argtypes = []
        lib.lgw_stop.restype = c_int

        lib.lgw_receive.argtypes = [c_uint8, POINTER(structs.LgwPktRx)]
        lib.lgw_receive.restype = c_int

        lib.lgw_send.argtypes = [POINTER(structs.LgwPktTx)]
        lib.lgw_send.restype = c_int

        lib.lgw_status.argtypes = [c_uint8, c_uint8, POINTER(c_uint8)]
        lib.lgw_status.restype = c_int

        lib.lgw_get_temperature.argtypes = [POINTER(c_float)]
        lib.lgw_get_temperature.restype = c_int

        lib.lgw_version_info.argtypes = []
        lib.lgw_version_info.restype = c_char_p

        lib.lgw_time_on_air.argtypes = [POINTER(structs.LgwPktTx)]
        lib.lgw_time_on_air.restype = c_uint32

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, profile: BoardProfile) -> None:
        """Apply a complete board profile before lgw_start()."""
        self.board_setconf(profile.board_conf())
        self.rxrf_setconf(profile.rf_chain, profile.rf_conf())
        for chain, conf in profile.rxif_configs().items():
            self.rxif_setconf(chain, conf)
        self.demod_setconf(profile.demod_conf())
        self.txgain_setconf(profile.rf_chain, profile.txgain_conf())

    def board_setconf(self, conf: structs.LgwConfBoard) -> None:
        self._check("lgw_board_setconf", self.lib.lgw_board_setconf(byref(conf)))

    def rxrf_setconf(self, rf_chain: int, conf: structs.LgwConfRxrf) -> None:
        self._check(
            "lgw_rxrf_setconf",
            self.lib.lgw_rxrf_setconf(rf_chain, byref(conf)),
        )

    def rxif_setconf(self, if_chain: int, conf: structs.LgwConfRxif) -> None:
        self._check(
            "lgw_rxif_setconf",
            self.lib.lgw_rxif_setconf(if_chain, byref(conf)),
        )

    def demod_setconf(self, conf: structs.LgwConfDemod) -> None:
        self._check("lgw_demod_setconf", self.lib.lgw_demod_setconf(byref(conf)))

    def txgain_setconf(self, rf_chain: int, conf: structs.LgwTxGainLut) -> None:
        self._check(
            "lgw_txgain_setconf",
            self.lib.lgw_txgain_setconf(rf_chain, byref(conf)),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._check("lgw_start", self.lib.lgw_start())

    def stop(self) -> None:
        self._check("lgw_stop", self.lib.lgw_stop())

    # ------------------------------------------------------------------
    # Data path
    # ------------------------------------------------------------------

    def receive(self, max_packets: int = 8) -> list[structs.LgwPktRx]:
        buffer = (structs.LgwPktRx * max_packets)()
        count = self.lib.lgw_receive(max_packets, buffer)
        if count < 0:
            raise HalError("lgw_receive failed")
        packets: list[structs.LgwPktRx] = []
        for index in range(count):
            raw = ctypes.string_at(byref(buffer[index]), ctypes.sizeof(structs.LgwPktRx))
            packets.append(structs.LgwPktRx.from_buffer_copy(raw))
        return packets

    def send(self, packet: structs.LgwPktTx) -> None:
        if packet.size > structs.PAYLOAD_SIZE - 1:
            raise ValueError("LoRa payload exceeds the 255-byte HAL limit")
        self._check("lgw_send", self.lib.lgw_send(byref(packet)))

    def status(self, rf_chain: int = 0, select: int = structs.TX_STATUS) -> int:
        code = c_uint8()
        self._check("lgw_status", self.lib.lgw_status(rf_chain, select, byref(code)))
        return code.value

    def temperature(self) -> float:
        value = c_float()
        self._check("lgw_get_temperature", self.lib.lgw_get_temperature(byref(value)))
        return value.value

    def version(self) -> str:
        value = self.lib.lgw_version_info()
        if value is None:
            raise HalError("lgw_version_info returned NULL")
        return value.decode("utf-8", "replace")

    def time_on_air_ms(self, packet: structs.LgwPktTx) -> int:
        return int(self.lib.lgw_time_on_air(byref(packet)))

    # ------------------------------------------------------------------

    @staticmethod
    def _check(function: str, result: int) -> None:
        if result != structs.LGW_HAL_SUCCESS:
            raise HalError(f"{function} failed with status {result}")
