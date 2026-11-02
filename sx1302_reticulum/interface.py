"""Reticulum interface backed by an in-process SX1302 HAL.

Reticulum loads this class from an external interface shim; the shim supplies
``RNS`` and ``Interface`` in its globals and exposes ``interface_class``.
"""

from __future__ import annotations

from pathlib import Path

import RNS
from RNS.Interfaces.Interface import Interface

from .config import BoardProfile, ConfigError
from .hal import Hal
from .radio import Radio
from .worker import RxPacket, TxQueueFull, WorkerError


HW_MTU = 254

# RNode-compatible SNR quality mapping.
Q_SNR_MIN_BASE = -9
Q_SNR_MAX = 6
Q_SNR_STEP = 2

_INT_OVERRIDES = (
    ("frequency", "frequency"),
    ("bandwidth", "bandwidth"),
    ("spreadingfactor", "spreadingfactor"),
    ("codingrate", "codingrate"),
    ("txpower", "tx_power"),
    ("tx_power", "tx_power"),
    ("preamble", "preamble"),
    ("if_frequency", "if_frequency"),
    ("reset_gpio", "reset_gpio"),
)


class SX1302Interface(Interface):
    """Single-channel raw-LoRa Reticulum interface for SX1302 boards."""

    AUTOCONFIGURE_MTU = False
    FIXED_MTU = True

    def __init__(self, owner, configuration):
        super().__init__()

        c = Interface.get_config_obj(configuration)

        self.owner = owner
        self.name = c["name"] if "name" in c else "SX1302"

        profile_path = c["board_profile"] if "board_profile" in c else None
        if profile_path is None or not str(profile_path).strip():
            raise ValueError(
                "No board_profile specified for the SX1302 interface; "
                "set it to a board profile INI file"
            )

        try:
            profile = BoardProfile.from_ini(self._resolve_path(profile_path))
        except (ConfigError, OSError) as exc:
            raise ValueError(f"invalid board profile: {exc}") from exc

        overrides: dict[str, object] = {}
        for key, attribute in _INT_OVERRIDES:
            if key in c and str(c[key]).strip():
                try:
                    overrides[attribute] = int(str(c[key]).strip(), 0)
                except ValueError as exc:
                    raise ValueError(
                        f"configuration option {key} must be an integer"
                    ) from exc

        if "spi_path" in c and str(c["spi_path"]).strip():
            overrides["spi_path"] = str(c["spi_path"]).strip()
        if "channel_mode" in c and str(c["channel_mode"]).strip():
            overrides["channel_mode"] = str(c["channel_mode"]).strip()
        if "reset_script" in c and str(c["reset_script"]).strip():
            overrides["reset_script"] = self._resolve_path(c["reset_script"])
        if "reset_enabled" in c:
            overrides["reset_enabled"] = c.as_bool("reset_enabled")

        if overrides:
            try:
                profile = profile.with_overrides(**overrides)
            except ConfigError as exc:
                raise ValueError(f"invalid SX1302 overrides: {exc}") from exc

        self.profile = profile
        self.HW_MTU = HW_MTU
        self._max_on_air = HW_MTU
        self.bitrate = profile.bitrate_bps
        self.bitrate_kbps = round(self.bitrate / 1000.0, 2)
        self.shared_medium = True
        self.reports_phy_stats = True
        self.r_stat_rssi = None
        self.r_stat_snr = None
        self.r_stat_q = None

        self.radio: Radio | None = None
        self._shutdown_done = False

        library = c["library"] if "library" in c and str(c["library"]).strip() else None
        self._library = str(self._resolve_path(library)) if library else None

        self._queue_size = self._option_int(c, "queue_size", 16)
        self._rx_batch = self._option_int(c, "rx_batch", 8)
        self._rx_idle_sleep = self._option_float(c, "rx_idle_sleep", 0.01)
        self._status_poll = self._option_float(c, "status_poll", 0.005)
        self._tx_free_timeout = self._option_float(c, "tx_free_timeout", 5.0)
        self._tx_complete_margin = self._option_float(c, "tx_complete_margin", 2.0)
        self._stop_timeout = self._option_float(c, "stop_timeout", 10.0)
        self._duty_cycle_percent = self._option_float(c, "duty_cycle_percent", 0.0)
        if not 0.0 <= self._duty_cycle_percent <= 100.0:
            raise ValueError("configuration option duty_cycle_percent must be between 0 and 100")
        self._duty_cycle_window_s = self._option_float(c, "duty_cycle_window", 3600.0)
        if self._duty_cycle_window_s <= 0.0:
            raise ValueError("configuration option duty_cycle_window must be positive")
        self._lbt_rssi_threshold = self._option_float_optional(c, "lbt_rssi_threshold")
        self._lbt_check_ms = self._option_float(c, "lbt_check_ms", 50.0)
        if self._lbt_check_ms <= 0.0:
            raise ValueError("configuration option lbt_check_ms must be positive")
        self._lbt_timeout = self._option_float(c, "lbt_timeout", 5.0)
        if self._lbt_timeout < 0.0:
            raise ValueError("configuration option lbt_timeout must not be negative")
        self._use_reset = c.as_bool("reset") if "reset" in c else True

    # ------------------------------------------------------------------
    # Reticulum lifecycle
    # ------------------------------------------------------------------

    def final_init(self):
        """Start the radio worker. Called by Reticulum after ``add_interface``."""
        if self.radio is not None or self.detached or self._shutdown_done:
            return

        # IFAC is added on top of HW_MTU after interface_post_init(), but the
        # HAL still only accepts 255 bytes on air. Leave room for the IFAC tag
        # so an IFAC-enabled interface cannot build an oversized frame.
        if getattr(self, "ifac_identity", None) is not None:
            overhead = int(getattr(self, "ifac_size", 0) or 0)
            self.HW_MTU = max(1, HW_MTU - overhead)
            RNS.log(
                f"{self} reserves {overhead} bytes for IFAC; "
                f"hardware MTU is {self.HW_MTU}",
                RNS.LOG_NOTICE,
            )

        self.radio = Radio(
            self.profile,
            on_rx=self._handle_rx,
            on_state=self._handle_state,
            on_tx=lambda payload: None,
            hal_factory=self._make_hal,
            log=self._log,
            log_error=self._log_error,
            reset=self._use_reset,
            queue_size=self._queue_size,
            rx_batch=self._rx_batch,
            rx_idle_sleep=self._rx_idle_sleep,
            status_poll=self._status_poll,
            tx_free_timeout=self._tx_free_timeout,
            tx_complete_margin=self._tx_complete_margin,
            stop_timeout=self._stop_timeout,
            duty_cycle_percent=self._duty_cycle_percent,
            duty_cycle_window_s=self._duty_cycle_window_s,
            lbt_rssi_threshold=self._lbt_rssi_threshold,
            lbt_check_ms=self._lbt_check_ms,
            lbt_timeout_s=self._lbt_timeout,
        )
        RNS.log(f"Starting {self}", RNS.LOG_NOTICE)
        self.radio.start()

    def reconfigure(self, profile: BoardProfile | str | Path) -> None:
        """Retune the radio at runtime.

        ``profile`` may be a :class:`BoardProfile` or the path to a board
        profile INI file. The change is applied by the worker thread; frames
        queued before the change may still use the old modulation.
        """
        if self.radio is None:
            raise RuntimeError("interface has not been started")

        if not isinstance(profile, BoardProfile):
            try:
                profile = BoardProfile.from_ini(self._resolve_path(profile))
            except (ConfigError, OSError) as exc:
                raise ValueError(f"invalid board profile: {exc}") from exc

        self.profile = profile
        self.bitrate = profile.bitrate_bps
        self.bitrate_kbps = round(self.bitrate / 1000.0, 2)
        self.radio.reconfigure(profile)

    def detach(self):
        self.detached = True
        self._shutdown()

    def teardown(self):
        self._shutdown()

    def process_outgoing(self, data):
        if not self.online or self.detached or self.radio is None:
            return
        if not data:
            return

        if len(data) > self._max_on_air:
            self.tx_drops += 1
            self.tx_dropped_bytes += len(data)
            self._log_error(
                f"dropping {len(data)}-byte outbound frame; "
                f"on-air limit is {self._max_on_air}"
            )
            return

        try:
            self.radio.send(data)
        except TxQueueFull:
            self.tx_drops += 1
            self.tx_dropped_bytes += len(data)
            RNS.log(
                f"Transmit queue is full on {self}; dropping {len(data)} bytes",
                RNS.LOG_WARNING,
            )
            return
        except WorkerError as exc:
            self._log_error(f"could not queue outbound frame on {self}: {exc}")
            return

        self.txb += len(data)

    def should_ingress_limit(self):
        # Match the RNode interface: LoRa links are slow enough that
        # Reticulum's announce pacing should not be gated on this interface.
        return False

    def __str__(self):
        return f"SX1302Interface[{self.name}]"

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------

    def _make_hal(self):
        return Hal(self._library)

    def _handle_state(self, online: bool) -> None:
        self.online = online
        if online:
            RNS.log(f"{self} is online", RNS.LOG_NOTICE)
        elif not self.detached:
            RNS.log(f"{self} is offline", RNS.LOG_WARNING)

    def _handle_rx(self, packet: RxPacket) -> None:
        if self.detached:
            return
        self.r_stat_rssi = packet.rssi
        self.r_stat_snr = packet.snr
        self.r_stat_q = self._quality(packet.snr)
        self.rxb += len(packet.payload)
        try:
            self.owner.inbound(packet.payload, self)
        except Exception as exc:
            self._log_error(f"error dispatching inbound frame on {self}: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        self.online = False

        radio = self.radio
        if radio is None:
            return
        if not radio.stop():
            self._log_error(f"timed out waiting for {self} radio to stop")

    def _quality(self, snr: float) -> float:
        sf_offset = self.profile.spreadingfactor - 7
        snr_min = Q_SNR_MIN_BASE - sf_offset * Q_SNR_STEP
        span = Q_SNR_MAX - snr_min
        quality = round(((snr - snr_min) / span) * 100, 1)
        return min(100.0, max(0.0, quality))

    def _log(self, message: str) -> None:
        RNS.log(message, RNS.LOG_NOTICE)

    def _log_error(self, message: str) -> None:
        RNS.log(message, RNS.LOG_ERROR)

    @staticmethod
    def _resolve_path(value) -> Path:
        path = Path(str(value)).expanduser()
        if path.is_absolute():
            return path

        instance = RNS.Reticulum.get_instance()
        configdir = getattr(instance, "configdir", None) if instance else None
        if configdir:
            return (Path(configdir) / path).resolve()
        return path.resolve()

    @staticmethod
    def _option_int(config, key: str, default: int) -> int:
        if key not in config or not str(config[key]).strip():
            return default
        try:
            return int(str(config[key]).strip(), 0)
        except ValueError as exc:
            raise ValueError(f"configuration option {key} must be an integer") from exc

    @staticmethod
    def _option_float(config, key: str, default: float) -> float:
        if key not in config or not str(config[key]).strip():
            return default
        try:
            return float(str(config[key]).strip())
        except ValueError as exc:
            raise ValueError(f"configuration option {key} must be a number") from exc

    @staticmethod
    def _option_float_optional(config, key: str) -> float | None:
        if key not in config or not str(config[key]).strip():
            return None
        try:
            return float(str(config[key]).strip())
        except ValueError as exc:
            raise ValueError(f"configuration option {key} must be a number") from exc
