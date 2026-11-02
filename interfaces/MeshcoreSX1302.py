"""Meshcore adapter for SX1302 concentrators.

Bridges the generic ``Radio`` class (which wraps the SX1302 HAL via libloragw.so)
to meshcore-pi's ``Interface`` base class, so meshcore-pi can use an SX1302
as its LoRa radio.

Usage in meshcore-pi's ``config.toml``:

```toml
[[interfaces]]
sx1302 = { type = "sx1302", board_profile = "/path/to/rak2287-eu868.conf" }
```

The meshcore-pi dispatcher calls ``transmit()`` to send packets and reads
``(data, rssi, snr)`` tuples from ``rx_q`` for received packets.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from interfaces.interface import Interface
    _HAS_MESHCORE_PI = True
except ImportError:  # meshcore-pi not installed; adapter can still be imported
    Interface = object  # type: ignore[assignment,misc]
    _HAS_MESHCORE_PI = False

from sx1302_reticulum import Radio
from sx1302_reticulum.config import BoardProfile
from sx1302_reticulum.worker import RxPacket


class MeshcoreAdapter(Interface if _HAS_MESHCORE_PI else object):  # type: ignore[misc,valid-type]
    """Meshcore-pi Interface backed by the SX1302 Radio.

    Puts ``(data, rssi, snr)`` tuples onto ``rx_q`` when packets are received,
    and sends raw bytes via ``Radio.send()`` when ``transmit()`` is called.
    """

    def __init__(self, config, **kwargs):
        if not _HAS_MESHCORE_PI:
            raise ImportError(
                "meshcore-pi is not installed. Install it from "
                "https://github.com/brianwiddas/meshcore-pi to use this adapter."
            )
        super().__init__()

        self._name = "SX1302 interface"

        # Board profile path can be passed via config or kwargs
        profile_path = (
            config.get("board_profile")
            or kwargs.get("board_profile")
            or "/path/to/rak2287-eu868.conf"
        )

        self.profile = BoardProfile.from_ini(profile_path)

        # Duty cycle handling (mirrors meshcore-pi's LoRaInterface).
        # meshcore-pi paces transmissions itself via transmit_wait(), so the
        # worker-level duty-cycle gate stays disabled here to avoid applying
        # the limit twice and queueing frames for minutes.
        self.airtime_dutycycle = float(
            config.get("duty_cycle_percent", config.get("airtime", 10))
        )
        self.airtime_txtimestamp = []
        self.airtime_txtime = []
        self._max_airtime_samples = 5

        lbt_threshold = config.get("lbt_rssi_threshold")
        if lbt_threshold is not None:
            lbt_threshold = float(lbt_threshold)

        # Create the Radio; the RX callback funnels packets into rx_q
        self._radio: Optional[Radio] = Radio(
            self.profile,
            on_rx=self._on_radio_rx,
            on_state=self._on_radio_state,
            log=self._log,
            log_error=self._log_error,
            reset=True,
            duty_cycle_percent=0.0,
            lbt_rssi_threshold=lbt_threshold,
            lbt_check_ms=float(config.get("lbt_check_ms", 50.0)),
            lbt_timeout_s=float(config.get("lbt_timeout", 5.0)),
        )
        self._radio_started = False
        self._online = False

        # rx_q is created by meshcore-pi's Interface.__init__(); create it
        # ourselves if meshcore-pi is not installed.
        if not hasattr(self, "_rx_q"):
            self._rx_q = asyncio.Queue()

    # ------------------------------------------------------------------
    # Radio callbacks
    # ------------------------------------------------------------------

    def _on_radio_rx(self, packet: RxPacket) -> None:
        """Called from the Radio worker thread when a packet is received."""
        data = bytes(packet.payload)
        rssi = packet.rssi
        snr = packet.snr

        # Put onto the asyncio rx_q from the worker thread
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # No running event loop; drop the packet
            logger.debug("No running event loop; dropping received packet")
            return

        if loop.is_running():
            loop.call_soon_threadsafe(self.rx_q.put_nowait, (data, rssi, snr))
        else:
            logger.debug("Event loop not running; dropping received packet")

    def _on_radio_state(self, online: bool) -> None:
        """Called when the Radio goes online/offline."""
        self._online = online
        if online:
            logger.info("SX1302 radio is online")
        else:
            logger.warning("SX1302 radio is offline")

    def _log(self, message: str) -> None:
        logger.info(message)

    def _log_error(self, message: str) -> None:
        logger.error(message)

    # ------------------------------------------------------------------
    # Interface lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Start the Radio and wait for it to be ready."""
        self._radio.start()
        try:
            self._radio.wait_ready(timeout=30.0)
        except Exception as exc:
            logger.error(f"SX1302 radio failed to start: {exc}")
            raise
        self._radio_started = True
        logger.info("SX1302 interface started")

    async def stop(self):
        """Stop the Radio."""
        if self._radio_started:
            self._radio.stop()
            self._radio_started = False

    # ------------------------------------------------------------------
    # Interface transmit / receive
    # ------------------------------------------------------------------

    async def transmit(self, packetdata: bytes) -> float:
        """Transmit a raw MeshCore packet over the SX1302.

        Returns the transmit time in seconds (0 if unknown).
        """
        if not self._radio.online:
            logger.warning("Radio not online; dropping transmit")
            return 0.0

        try:
            self._radio.send(packetdata)
        except Exception as exc:
            logger.error(f"Transmit failed: {exc}")
            return 0.0

        # Wait for TX completion by polling the Radio's worker state.
        # The Radio worker already waits for TX_FREE internally, so by the
        # time send() returns the packet is on the air. We estimate airtime
        # from the board profile.
        airtime_ms = self._estimate_airtime_ms(len(packetdata))

        self.airtime_txtimestamp.append(asyncio.get_event_loop().time())
        self.airtime_txtime.append(airtime_ms)
        if len(self.airtime_txtimestamp) > self._max_airtime_samples:
            self.airtime_txtimestamp.pop(0)
            self.airtime_txtime.pop(0)

        return airtime_ms / 1000.0

    def transmit_wait(self) -> float:
        """Return seconds to wait before the next transmit for duty cycle.

        Mirrors meshcore-pi's LoRaInterface.transmit_wait().
        """
        if not self.airtime_txtimestamp:
            return 0.0

        tx_earliest = self.airtime_txtimestamp[0]
        tx_period = asyncio.get_event_loop().time() - tx_earliest
        tx_total = sum(self.airtime_txtime)
        duty_cycle = 100 * (tx_total / 1000) / tx_period if tx_period > 0 else 0.0

        for c in range(3):
            fraction = 1 / (1 << c)
            airtime_dutycycle = self.airtime_dutycycle * fraction

            if duty_cycle > airtime_dutycycle:
                tx_min = (
                    tx_earliest
                    + (tx_total / 1000) / (airtime_dutycycle / 100)
                    - asyncio.get_event_loop().time()
                ) * fraction
                if tx_min > 0:
                    logger.debug(
                        f"Sleep for {tx_min:0.2f}s for duty cycle "
                        f"compliance ({airtime_dutycycle}%)"
                    )
                    return tx_min

        return 0.0

    def _estimate_airtime_ms(self, payload_len: int) -> int:
        """Rough on-air time estimate for a payload of this size."""
        bps = self.profile.bitrate_bps
        if bps > 0:
            bits = (payload_len + 6) * 8  # LoRa preamble/header overhead
            return int(bits / bps * 1000)
        return 500  # conservative default

    def get_radioconfig(self):
        """Return radio config for meshcore-pi's get_radioconfig().

        Returns (frequency_khz, bandwidth_hz, spreading_factor,
                 coding_rate, tx_power_dbm, max_tx_power_dbm).
        """
        return (
            self.profile.frequency // 1000,
            self.profile.bandwidth,
            self.profile.spreadingfactor,
            self.profile.codingrate,
            self.profile.tx_power,
            27,  # max tx power for RAK2287/SX1250
        )

    @property
    def name(self):
        return self._name

    @property
    def rx_q(self):
        return self._rx_q


def interface_class(config_obj):
    """Meshcore interface loader entry point.

    meshcore-pi's configure_interfaces() calls this with the config dict
    for an interface of type ``sx1302``.
    """
    return MeshcoreAdapter(config_obj)
