"""Generic Radio interface that wraps RadioWorker.

This provides a protocol-independent interface so other front-ends
(e.g. Meshcore, custom applications) can use the same SX1302 HAL layer.
"""

from __future__ import annotations

from typing import Callable

from .worker import RadioWorker, RxPacket, TxQueueFull, WorkerError, Counters
from .config import BoardProfile


class Radio:
    """Generic radio interface over the SX1302 concentrator.

    This class wraps ``RadioWorker`` and exposes a clean, protocol-independent
    API: ``start()``, ``stop()``, ``send()``, callbacks for RX, state changes,
    and counters. It can be driven directly or used by a protocol-specific
    adapter such as ``SX1302Interface`` (Reticulum) or a Meshcore adapter.
    """

    def __init__(
        self,
        profile: BoardProfile,
        *,
        on_rx: Callable[[RxPacket], None] | None = None,
        on_state: Callable[[bool], None] | None = None,
        on_tx: Callable[[bytes], None] | None = None,
        hal_factory: Callable[[], object] | None = None,
        log: Callable[[str], None] | None = None,
        log_error: Callable[[str], None] | None = None,
        reset: bool = True,
        queue_size: int = 16,
        rx_batch: int = 8,
        rx_idle_sleep: float = 0.01,
        status_poll: float = 0.005,
        tx_free_timeout: float = 5.0,
        tx_complete_margin: float = 2.0,
        stop_timeout: float = 10.0,
        duty_cycle_percent: float = 0.0,
        duty_cycle_window_s: float = 3600.0,
        lbt_rssi_threshold: float | None = None,
        lbt_check_ms: float = 50.0,
        lbt_timeout_s: float = 5.0,
    ):
        self.profile = profile
        self.on_rx = on_rx
        self.on_state = on_state
        self.on_tx = on_tx
        self.log = log
        self.log_error = log_error if log_error is not None else log

        self.worker = RadioWorker(
            profile,
            hal_factory=hal_factory,
            on_rx=on_rx,
            on_state=on_state,
            on_tx=on_tx,
            log=log,
            log_error=log_error,
            reset=reset,
            queue_size=queue_size,
            rx_batch=rx_batch,
            rx_idle_sleep=rx_idle_sleep,
            status_poll=status_poll,
            tx_free_timeout=tx_free_timeout,
            tx_complete_margin=tx_complete_margin,
            stop_timeout=stop_timeout,
            duty_cycle_percent=duty_cycle_percent,
            duty_cycle_window_s=duty_cycle_window_s,
            lbt_rssi_threshold=lbt_rssi_threshold,
            lbt_check_ms=lbt_check_ms,
            lbt_timeout_s=lbt_timeout_s,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the radio thread. Returns before the HAL is ready."""
        self.worker.start()

    def wait_ready(self, timeout: float | None = None) -> None:
        """Block until the radio is online (or failed to start)."""
        self.worker.wait_ready(timeout)

    def stop(self, timeout: float | None = None) -> bool:
        """Request shutdown and wait for the radio thread to finish."""
        return self.worker.stop(timeout)

    # ------------------------------------------------------------------
    # Transmit
    # ------------------------------------------------------------------

    def send(self, payload: bytes) -> None:
        """Queue a payload for transmission.

        Raises ``TxQueueFull`` if the bounded queue is full, or
        ``WorkerError`` if the payload exceeds the 254-byte HAL limit.
        """
        self.worker.submit(payload)

    def reconfigure(self, profile: BoardProfile) -> None:
        """Retune the radio at runtime without restarting the process.

        The new profile is applied by the worker thread; queued payloads
        continue to be transmitted with the new modulation.
        """
        self.worker.reconfigure(profile)

    @property
    def duty_cycle_percent(self) -> float:
        return self.worker.duty_cycle_percent

    @duty_cycle_percent.setter
    def duty_cycle_percent(self, value: float) -> None:
        self.worker.duty_cycle_percent = value

    # ------------------------------------------------------------------
    # State / Counters
    # ------------------------------------------------------------------

    @property
    def online(self) -> bool:
        return self.worker.online

    @property
    def counters(self) -> Counters:
        return self.worker.counters

    @property
    def startup_error(self) -> BaseException | None:
        return self.worker.startup_error

    @property
    def last_rssi(self) -> float | None:
        return self.worker.last_rssi

    @property
    def last_snr(self) -> float | None:
        return self.worker.last_snr

    @property
    def tx_queue_depth(self) -> int:
        return self.worker.tx_queue_depth

    def is_thread_alive(self) -> bool:
        thread = getattr(self.worker, '_thread', None)
        return thread is not None and thread.is_alive()
