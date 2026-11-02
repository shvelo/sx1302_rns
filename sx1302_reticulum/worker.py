"""Radio worker that owns every libloragw call.

The HAL keeps global state and is not thread-safe in the way this project uses
it. One :class:`RadioWorker` therefore creates the HAL, configures and starts
it, and performs all RX/TX operations from a single dedicated thread. The
Reticulum interface only submits payloads and receives callbacks.

The worker is deliberately independent of Reticulum so it can be tested with a
fake HAL and used by other front ends.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import queue
import threading
import time
from typing import Callable, Protocol

from . import structs
from .config import BoardProfile
from .hal import Hal, HalError
from .reset import ResetError, run_reset


# The HAL copies at most 255 payload bytes into ``lgw_pkt_tx_s``. Reticulum
# needs a little headroom for its own framing, so the interface advertises 254.
MAX_PAYLOAD = 254


class WorkerError(RuntimeError):
    """Raised when the worker cannot start or cannot accept a request."""


class TxQueueFull(WorkerError):
    """Raised when the bounded transmit queue is full."""


class RadioHal(Protocol):
    """Subset of :class:`sx1302_reticulum.hal.Hal` used by the worker."""

    def configure(self, profile: BoardProfile) -> None: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def receive(self, max_packets: int = 8) -> list[structs.LgwPktRx]: ...

    def send(self, packet: structs.LgwPktTx) -> None: ...

    def status(self, rf_chain: int = 0, select: int = structs.TX_STATUS) -> int: ...

    def time_on_air_ms(self, packet: structs.LgwPktTx) -> int: ...


@dataclass(frozen=True)
class RxPacket:
    """A decoded packet handed to the interface."""

    payload: bytes
    rssi: float
    snr: float
    frequency: int
    if_chain: int
    rf_chain: int
    status: int


@dataclass
class Counters:
    """Runtime counters, readable from any thread."""

    rx_packets: int = 0
    rx_bytes: int = 0
    tx_packets: int = 0
    tx_bytes: int = 0
    crc_errors: int = 0
    oversized: int = 0
    rx_errors: int = 0
    tx_errors: int = 0
    tx_timeouts: int = 0
    queue_drops: int = 0
    tx_airtime_ms: int = 0
    lbt_busy_count: int = 0
    lbt_drops: int = 0
    duty_cycle_waits: int = 0


# The HAL has global state. Only one worker may own it per process.
_ACTIVE_LOCK = threading.Lock()
_ACTIVE_WORKER: "RadioWorker | None" = None


class RadioWorker:
    """Start, poll and stop an SX1302 HAL from one thread.

    Parameters are intentionally simple: the interface supplies callbacks and
    timing, while tests can supply a fake HAL and run the same lifecycle.
    """

    def __init__(
        self,
        profile: BoardProfile,
        *,
        hal: RadioHal | None = None,
        hal_factory: Callable[[], RadioHal] | None = None,
        on_rx: Callable[[RxPacket], None] | None = None,
        on_tx: Callable[[bytes], None] | None = None,
        on_state: Callable[[bool], None] | None = None,
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
        if hal is not None and hal_factory is not None:
            raise ValueError("pass either hal or hal_factory, not both")
        if queue_size < 1:
            raise ValueError("queue_size must be at least 1")
        if not 0.0 <= duty_cycle_percent <= 100.0:
            raise ValueError("duty_cycle_percent must be between 0 and 100")
        if duty_cycle_window_s <= 0.0:
            raise ValueError("duty_cycle_window_s must be positive")
        if lbt_check_ms <= 0.0:
            raise ValueError("lbt_check_ms must be positive")
        if lbt_timeout_s < 0.0:
            raise ValueError("lbt_timeout_s must not be negative")

        self.profile = profile
        self.max_payload = MAX_PAYLOAD
        self.on_rx = on_rx
        self.on_tx = on_tx
        self.on_state = on_state
        self.log = log
        self.log_error = log_error if log_error is not None else log
        self.reset = reset
        self.rx_batch = max(1, rx_batch)
        self.rx_idle_sleep = max(0.0, rx_idle_sleep)
        self.status_poll = max(0.0, status_poll)
        self.tx_free_timeout = max(0.0, tx_free_timeout)
        self.tx_complete_margin = max(0.0, tx_complete_margin)
        self.stop_timeout = max(0.0, stop_timeout)
        self.duty_cycle_window_s = float(duty_cycle_window_s)
        self._tx_history: deque[tuple[float, float]] = deque()
        self._history_lock = threading.Lock()
        self.duty_cycle_percent = duty_cycle_percent
        self.lbt_rssi_threshold = lbt_rssi_threshold
        self.lbt_check_ms = lbt_check_ms
        self.lbt_timeout_s = lbt_timeout_s

        self._hal = hal
        self._hal_factory = hal_factory if hal is None else None
        self._hal_started = False

        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=queue_size)
        self._config_queue: queue.Queue[BoardProfile | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._online_event = threading.Event()
        self._ready_event = threading.Event()
        self._startup_error: BaseException | None = None
        self._counters = Counters()

        self._last_rssi: float | None = None
        self._last_snr: float | None = None
        self._last_rx_monotonic: float | None = None
        self._last_rx_rssi: float | None = None

    # ------------------------------------------------------------------
    # Public API (never touches the HAL)
    # ------------------------------------------------------------------

    @property
    def online(self) -> bool:
        return self._online_event.is_set()

    @property
    def counters(self) -> Counters:
        return self._counters

    @property
    def startup_error(self) -> BaseException | None:
        return self._startup_error

    @property
    def tx_queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def last_rssi(self) -> float | None:
        return self._last_rssi

    @property
    def last_snr(self) -> float | None:
        return self._last_snr

    def start(self) -> None:
        """Start the worker thread. Returns before the HAL is ready."""
        global _ACTIVE_WORKER

        if self._thread is not None:
            raise WorkerError("worker has already been started")

        with _ACTIVE_LOCK:
            if _ACTIVE_WORKER is not None and _ACTIVE_WORKER is not self:
                raise WorkerError(
                    "another SX1302 worker is already running in this process"
                )
            _ACTIVE_WORKER = self

        self._thread = threading.Thread(
            target=self._run,
            name="sx1302-radio",
            daemon=True,
        )
        self._thread.start()

    def wait_ready(self, timeout: float | None = None) -> None:
        """Wait for the startup attempt to finish and raise on failure."""
        if not self._ready_event.wait(timeout):
            raise WorkerError("timed out waiting for the radio to start")
        if self._startup_error is not None:
            raise WorkerError(
                f"radio startup failed: {self._startup_error}"
            ) from self._startup_error

    def submit(self, payload: bytes) -> None:
        """Queue a payload for transmission.

        Raises :class:`TxQueueFull` when the bounded queue is full. Calling
        this before the HAL is online is allowed; the queue drains once the
        radio starts.
        """
        if len(payload) > self.max_payload:
            raise WorkerError(
                f"payload is {len(payload)} bytes, limit is {self.max_payload}"
            )
        try:
            self._queue.put_nowait(bytes(payload))
        except queue.Full as exc:
            self._counters.queue_drops += 1
            raise TxQueueFull("transmit queue is full") from exc

    def reconfigure(self, profile: BoardProfile) -> None:
        """Request a runtime radio reconfiguration.

        The worker will stop RX, call ``hal.configure()`` and ``hal.start()``
        with the new profile, then resume RX. This changes frequency, BW, SF,
        CR, TX power, and other board settings without restarting the process.

        Only one reconfiguration may be pending at a time; subsequent calls
        replace the previous pending request.
        """
        # Drain any pending reconfig request so only the latest applies.
        try:
            while True:
                self._config_queue.get_nowait()
        except queue.Empty:
            pass
        self._config_queue.put_nowait(profile)

    @property
    def duty_cycle_percent(self) -> float:
        return self._duty_cycle_percent

    @duty_cycle_percent.setter
    def duty_cycle_percent(self, value: float) -> None:
        value = max(0.0, min(100.0, float(value)))
        if value <= 0.0:
            # Disabled: forget past airtime so a later re-enable starts fresh.
            with self._history_lock:
                self._tx_history.clear()
        self._duty_cycle_percent = value

    def stop(self, timeout: float | None = None) -> bool:
        """Ask the worker to stop and wait for it.

        Idempotent and safe to call from any thread except the worker itself.
        Returns ``True`` when the worker thread has finished.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            return False
        thread.join(self.stop_timeout if timeout is None else timeout)
        return not thread.is_alive()

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        global _ACTIVE_WORKER

        hal = self._hal
        try:
            if self.reset:
                run_reset(self.profile, "start", log=self.log)

            if hal is None:
                if self._hal_factory is None:
                    hal = Hal()
                else:
                    hal = self._hal_factory()
                self._hal = hal

            hal.configure(self.profile)
            hal.start()
            self._hal_started = True

            self._online_event.set()
            self._notify_state(True)
            self._ready_event.set()
            self._loop(hal)
        except Exception as exc:
            if not self._ready_event.is_set():
                self._startup_error = exc
            self._log_error(f"radio worker stopped: {exc}")
        finally:
            self._online_event.clear()
            if hal is not None and self._hal_started:
                try:
                    hal.stop()
                except Exception as exc:
                    self._log_error(f"HAL stop failed: {exc}")
                self._hal_started = False
            self._notify_state(False)
            if self.reset:
                try:
                    run_reset(self.profile, "stop", log=self.log)
                except (OSError, ResetError) as exc:
                    self._log_error(f"reset stop failed: {exc}")
            with _ACTIVE_LOCK:
                if _ACTIVE_WORKER is self:
                    _ACTIVE_WORKER = None
            self._ready_event.set()

    def _loop(self, hal: RadioHal) -> None:
        rx_errors = 0
        pending: bytes | None = None
        pending_packet: structs.LgwPktTx | None = None
        pending_airtime_ms = 0
        duty_ready_at = 0.0
        duty_wait_logged = False
        lbt_wait_started: float | None = None

        while not self._stop_event.is_set():
            if self._check_config(hal):
                # The modulation changed; re-encode any packet we were about
                # to transmit and recompute its duty-cycle budget.
                if pending is not None:
                    pending_packet = self.profile.make_tx_packet(pending)
                    pending_airtime_ms = hal.time_on_air_ms(pending_packet)
                duty_ready_at = 0.0
                duty_wait_logged = False

            if self._stop_event.is_set():
                break

            if self._receive(hal):
                rx_errors += 1
            else:
                rx_errors = 0

            if self._stop_event.is_set():
                break

            if pending is None:
                try:
                    pending = self._queue.get_nowait()
                except queue.Empty:
                    pass

                if pending is None:
                    backoff = self._idle_backoff(rx_errors)
                    if not self._stop_event.wait(backoff):
                        continue
                    break

                pending_packet = self.profile.make_tx_packet(pending)
                pending_airtime_ms = hal.time_on_air_ms(pending_packet)
                duty_ready_at = 0.0
                duty_wait_logged = False
                lbt_wait_started = None

            now = time.monotonic()

            # Duty-cycle gate. Recompute lazily, and keep receiving while we
            # wait so a long transmission ban does not deafen the interface.
            if duty_ready_at < now:
                duty_ready_at = self._duty_cycle_ready_at(now, pending_airtime_ms)

            if duty_ready_at > now:
                if not duty_wait_logged:
                    self._counters.duty_cycle_waits += 1
                    self._log(
                        f"duty cycle limit ({self._duty_cycle_percent:g}% over "
                        f"{self.duty_cycle_window_s:g} s): delaying TX by "
                        f"{duty_ready_at - now:.2f} s"
                    )
                    duty_wait_logged = True
                self._stop_event.wait(max(0.001, min(self.rx_idle_sleep or 0.01,
                                                     duty_ready_at - now)))
                continue

            # Coarse listen-before-talk. The HAL does not expose an
            # instantaneous RSSI, so this uses recently decoded frames as a
            # proxy for channel activity.
            if self._channel_busy(now):
                self._counters.lbt_busy_count += 1
                if lbt_wait_started is None:
                    lbt_wait_started = now
                waited = now - lbt_wait_started
                if waited >= self.lbt_timeout_s:
                    self._counters.lbt_drops += 1
                    self._log_error(
                        "LBT: channel stayed busy for "
                        f"{waited:.2f} s; dropping pending frame"
                    )
                    pending = None
                    pending_packet = None
                else:
                    self._stop_event.wait(
                        max(0.001, min(self.lbt_check_ms / 1000.0,
                                       self.lbt_timeout_s - waited))
                    )
                continue
            lbt_wait_started = None

            if pending_packet is None:
                continue

            try:
                self._transmit(
                    hal, pending, pending_packet, pending_airtime_ms
                )
            except HalError as exc:
                self._counters.tx_errors += 1
                self._log_error(f"transmit failed: {exc}")

            pending = None
            pending_packet = None
            duty_ready_at = 0.0
            duty_wait_logged = False

            if rx_errors:
                # A failing receive path should not turn into a busy loop.
                self._stop_event.wait(self._idle_backoff(rx_errors))

    def _idle_backoff(self, rx_errors: int) -> float:
        if rx_errors:
            return min(1.0, 0.01 * (2 ** min(rx_errors, 7)))
        return self.rx_idle_sleep

    def _receive(self, hal: RadioHal) -> bool:
        """Poll the HAL once. Returns True when the poll failed."""
        try:
            packets = hal.receive(self.rx_batch)
        except HalError as exc:
            self._counters.rx_errors += 1
            self._log_error(f"receive failed: {exc}")
            return True

        for packet in packets:
            self._dispatch(packet)
        return False

    def _dispatch(self, packet: structs.LgwPktRx) -> None:
        # Remember recent channel activity even when the frame is unusable;
        # the LBT gate uses it as a coarse carrier-sense proxy.
        self._last_rx_monotonic = time.monotonic()
        self._last_rx_rssi = float(packet.rssic)

        if packet.status != structs.STAT_CRC_OK:
            self._counters.crc_errors += 1
            return

        payload = bytes(packet.payload[: packet.size])
        if not payload:
            return
        if len(payload) > self.max_payload:
            self._counters.oversized += 1
            self._log_error(
                f"dropping {len(payload)}-byte packet; limit is {self.max_payload}"
            )
            return

        self._counters.rx_packets += 1
        self._counters.rx_bytes += len(payload)
        self._last_rssi = packet.rssic
        self._last_snr = packet.snr

        if self.on_rx is not None:
            event = RxPacket(
                payload=payload,
                rssi=float(packet.rssic),
                snr=float(packet.snr),
                frequency=int(packet.freq_hz),
                if_chain=int(packet.if_chain),
                rf_chain=int(packet.rf_chain),
                status=int(packet.status),
            )
            try:
                self.on_rx(event)
            except Exception as exc:
                self._log_error(f"receive callback failed: {exc}")

    def _transmit(
        self,
        hal: RadioHal,
        payload: bytes,
        packet: structs.LgwPktTx,
        time_on_air_ms: int,
    ) -> None:
        self._wait_tx_free(hal)

        if self._stop_event.is_set():
            return

        hal.send(packet)
        self._record_tx(time_on_air_ms)
        self._counters.tx_packets += 1
        self._counters.tx_bytes += len(payload)
        self._counters.tx_airtime_ms += time_on_air_ms

        deadline = time.monotonic() + time_on_air_ms / 1000.0 + self.tx_complete_margin
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                return
            code = hal.status(self.profile.rf_chain, structs.TX_STATUS)
            if code == structs.TX_FREE:
                self._notify_tx(payload)
                return
            if code in (structs.TX_EMITTING, structs.TX_SCHEDULED):
                if self._stop_event.wait(self.status_poll):
                    return
                continue
            raise HalError(f"unexpected TX status after send: {code}")

        self._counters.tx_timeouts += 1
        self._log_error(
            f"timed out waiting for TX completion after {time_on_air_ms} ms airtime"
        )

    def _wait_tx_free(self, hal: RadioHal) -> None:
        if self._stop_event.is_set():
            return

        deadline = time.monotonic() + self.tx_free_timeout
        while time.monotonic() < deadline:
            code = hal.status(self.profile.rf_chain, structs.TX_STATUS)
            if code == structs.TX_FREE:
                return
            if code in (structs.TX_EMITTING, structs.TX_SCHEDULED):
                if self._stop_event.wait(self.status_poll):
                    return
                continue
            if code == structs.TX_OFF:
                raise HalError("TX is disabled or the RF chain is not transmitting")
            raise HalError(f"unexpected TX status before send: {code}")

        raise HalError(
            f"timed out after {self.tx_free_timeout:.1f} s waiting for TX_FREE"
        )

    def _check_config(self, hal: RadioHal) -> bool:
        """Check for a pending reconfiguration request and apply it.

        Returns ``True`` when the profile changed.
        """
        try:
            new_profile = self._config_queue.get_nowait()
        except queue.Empty:
            return False

        self._log("Applying runtime radio reconfiguration")
        self._reconfigure(hal, new_profile)
        return True

    def _reconfigure(self, hal: RadioHal, new_profile: BoardProfile) -> None:
        """Stop RX, reconfigure the HAL, and resume RX with a new profile."""
        hal.stop()
        hal.configure(new_profile)
        hal.start()

        # Update the worker's profile so TX uses the new settings
        self.profile = new_profile
        self._log(
            f"Radio reconfigured: {new_profile.frequency} Hz, "
            f"BW {new_profile.bandwidth}, SF{new_profile.spreadingfactor}, "
            f"CR {new_profile.codingrate}, {new_profile.tx_power} dBm"
        )

    # ------------------------------------------------------------------
    # Duty cycle and listen-before-talk
    # ------------------------------------------------------------------

    def _duty_cycle_ready_at(self, now: float, airtime_ms: float) -> float:
        """Return the earliest monotonic time a new transmission may start.

        Airtime is accounted over a sliding window of
        ``duty_cycle_window_s`` seconds. A duty cycle of 0 disables the gate.
        """
        if self._duty_cycle_percent <= 0.0:
            return now

        allowed_ms = (
            self.duty_cycle_window_s
            * 1000.0
            * self._duty_cycle_percent
            / 100.0
        )
        if airtime_ms > allowed_ms:
            # A single frame larger than the whole window budget can never
            # satisfy the limit. Let it through only once the window has
            # drained, so we do not send an unbounded burst of them.
            with self._history_lock:
                self._prune_tx_history(now)
                if self._tx_history:
                    return self._tx_history[-1][0] + self.duty_cycle_window_s
            self._log_error(
                f"packet airtime {airtime_ms} ms exceeds the "
                f"{allowed_ms:.0f} ms duty-cycle budget; sending once the "
                "window is clear"
            )
            return now

        with self._history_lock:
            self._prune_tx_history(now)
            excess_ms = sum(airtime for _, airtime in self._tx_history)
            excess_ms += airtime_ms - allowed_ms
            if excess_ms <= 0.0:
                return now

            # Entries leave the window in timestamp order, so wait until enough
            # of the oldest airtime has aged out.
            for timestamp, airtime in self._tx_history:
                excess_ms -= airtime
                if excess_ms <= 0.0:
                    return max(now, timestamp + self.duty_cycle_window_s)

        return now

    def _prune_tx_history(self, now: float) -> None:
        """Drop entries that have aged out. Caller must hold ``_history_lock``."""
        cutoff = now - self.duty_cycle_window_s
        while self._tx_history and self._tx_history[0][0] <= cutoff:
            self._tx_history.popleft()

    def _record_tx(self, airtime_ms: float) -> None:
        if self._duty_cycle_percent <= 0.0:
            return
        now = time.monotonic()
        with self._history_lock:
            self._prune_tx_history(now)
            self._tx_history.append((now, float(airtime_ms)))

    def _channel_busy(self, now: float) -> bool:
        """Coarse carrier sense based on recently decoded frames.

        The SX1302 HAL does not expose an instantaneous RSSI and its
        ``RX_STATUS`` selector is unimplemented, so this treats a frame
        decoded within the last ``lbt_check_ms`` whose RSSI is at or above
        the threshold as a busy channel. It cannot see transmissions that
        do not share our sync word.
        """
        if self.lbt_rssi_threshold is None:
            return False
        if self._last_rx_monotonic is None or self._last_rx_rssi is None:
            return False
        if now - self._last_rx_monotonic >= self.lbt_check_ms / 1000.0:
            return False
        return self._last_rx_rssi >= self.lbt_rssi_threshold

    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.log is not None:
            self.log(message)

    def _notify_state(self, online: bool) -> None:
        if self.on_state is None:
            return
        try:
            self.on_state(online)
        except Exception as exc:
            self._log_error(f"state callback failed: {exc}")

    def _notify_tx(self, payload: bytes) -> None:
        if self.on_tx is None:
            return
        try:
            self.on_tx(payload)
        except Exception as exc:
            self._log_error(f"transmit callback failed: {exc}")

    def _log_error(self, message: str) -> None:
        if self.log_error is not None:
            self.log_error(message)
