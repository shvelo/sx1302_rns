"""Radio worker tests against a fake HAL, without hardware or Reticulum."""

from __future__ import annotations

from collections import deque
import threading
import time

import pytest

from sx1302_reticulum import structs
from sx1302_reticulum.config import BoardProfile
from sx1302_reticulum.hal import HalError
from sx1302_reticulum.worker import (
    RadioWorker,
    TxQueueFull,
    WorkerError,
)


def make_rx(
    payload: bytes,
    status: int = structs.STAT_CRC_OK,
    rssi: float = -77.5,
    snr: float = 5.25,
) -> structs.LgwPktRx:
    packet = structs.LgwPktRx()
    packet.status = status
    packet.size = len(payload)
    packet.payload[: len(payload)] = payload
    packet.rssic = rssi
    packet.rssis = rssi
    packet.snr = snr
    packet.freq_hz = 868_100_000
    packet.if_chain = 8
    packet.rf_chain = 0
    return packet


class FakeHal:
    def __init__(
        self,
        *,
        receive_gate: threading.Event | None = None,
        receive_errors: int = 0,
        start_error: Exception | None = None,
        after_send_status: int = structs.TX_FREE,
        airtime_ms: int = 0,
    ):
        self.thread_ids: set[int] = set()
        self.configure_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.receive_calls = 0
        self.sent: list[bytes] = []
        self.rx_batches: deque[list[structs.LgwPktRx]] = deque()
        self.receive_gate = receive_gate
        self.receive_errors = receive_errors
        self.start_error = start_error
        self.after_send_status = after_send_status
        self.airtime_ms = airtime_ms

    def _touch(self) -> None:
        self.thread_ids.add(threading.get_ident())

    def configure(self, profile: BoardProfile) -> None:
        self._touch()
        self.configure_calls += 1

    def start(self) -> None:
        self._touch()
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error

    def stop(self) -> None:
        self._touch()
        self.stop_calls += 1

    def receive(self, max_packets: int = 8) -> list[structs.LgwPktRx]:
        self._touch()
        self.receive_calls += 1
        if self.receive_gate is not None:
            self.receive_gate.wait(5)
        if self.receive_errors > 0:
            self.receive_errors -= 1
            raise HalError("simulated receive failure")
        if self.rx_batches:
            return self.rx_batches.popleft()
        return []

    def send(self, packet: structs.LgwPktTx) -> None:
        self._touch()
        self.sent.append(bytes(packet.payload[: packet.size]))

    def status(self, rf_chain: int = 0, select: int = structs.TX_STATUS) -> int:
        self._touch()
        if not self.sent:
            return structs.TX_FREE
        return self.after_send_status

    def time_on_air_ms(self, packet: structs.LgwPktTx) -> int:
        self._touch()
        return self.airtime_ms


def wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def make_worker(profile: BoardProfile, hal: FakeHal, **kwargs) -> RadioWorker:
    kwargs.setdefault("reset", False)
    kwargs.setdefault("rx_idle_sleep", 0.001)
    kwargs.setdefault("status_poll", 0.001)
    kwargs.setdefault("tx_complete_margin", 0.05)
    return RadioWorker(profile, hal=hal, **kwargs)


@pytest.fixture
def profile(write_profile) -> BoardProfile:
    return BoardProfile.from_ini(write_profile())


def test_rx_dispatch_filters_crc_and_counts(profile):
    hal = FakeHal()
    hal.rx_batches.append(
        [
            make_rx(b"hello"),
            make_rx(b"bad", status=structs.STAT_CRC_BAD),
            make_rx(b"x" * 255),
        ]
    )
    received = []
    event = threading.Event()

    def on_rx(packet):
        received.append(packet)
        event.set()

    worker = make_worker(profile, hal, on_rx=on_rx)
    worker.start()
    worker.wait_ready(2)

    assert event.wait(2)
    assert wait_until(
        lambda: worker.counters.crc_errors == 1 and worker.counters.oversized == 1
    )
    assert received[0].payload == b"hello"
    assert received[0].rssi == pytest.approx(-77.5)
    assert received[0].snr == pytest.approx(5.25)
    assert worker.counters.rx_packets == 1
    assert worker.counters.rx_bytes == 5
    assert worker.counters.crc_errors == 1
    assert worker.counters.oversized == 1
    assert worker.last_rssi == pytest.approx(-77.5)
    assert worker.last_snr == pytest.approx(5.25)

    assert worker.stop()


def test_tx_order_and_backpressure(profile):
    gate = threading.Event()
    hal = FakeHal(receive_gate=gate)
    tx_done = threading.Event()
    worker = make_worker(profile, hal, queue_size=2, on_tx=lambda payload: tx_done.set())
    worker.start()
    worker.wait_ready(2)

    assert wait_until(lambda: hal.receive_calls >= 1)

    worker.submit(b"one")
    worker.submit(b"two")
    with pytest.raises(TxQueueFull):
        worker.submit(b"three")
    assert worker.counters.queue_drops == 1

    gate.set()
    assert wait_until(lambda: len(hal.sent) == 2)
    assert hal.sent == [b"one", b"two"]
    assert tx_done.wait(2)
    assert worker.counters.tx_packets == 2
    assert worker.counters.tx_bytes == 6

    assert worker.stop()


def test_tx_timeout_is_counted(profile):
    hal = FakeHal(after_send_status=structs.TX_EMITTING, airtime_ms=5)
    tx_done = threading.Event()
    worker = make_worker(profile, hal, on_tx=lambda payload: tx_done.set())
    worker.start()
    worker.wait_ready(2)

    worker.submit(b"timeout")
    assert wait_until(lambda: worker.counters.tx_timeouts == 1)
    assert worker.counters.tx_packets == 1
    assert not tx_done.is_set()
    assert hal.sent == [b"timeout"]

    assert worker.stop()


def test_start_failure_is_reported(profile):
    hal = FakeHal(start_error=HalError("boom"))
    states = []
    worker = make_worker(profile, hal, on_state=states.append)
    worker.start()

    with pytest.raises(WorkerError, match="boom"):
        worker.wait_ready(2)

    assert not worker.online
    assert isinstance(worker.startup_error, HalError)
    assert states == [False]
    assert worker.stop()


def test_receive_errors_back_off_then_recover(profile):
    hal = FakeHal(receive_errors=2)
    hal.rx_batches.append([make_rx(b"after-errors")])
    received = []
    event = threading.Event()

    def on_rx(packet):
        received.append(packet.payload)
        event.set()

    worker = make_worker(profile, hal, on_rx=on_rx)
    worker.start()
    worker.wait_ready(2)

    assert event.wait(3)
    assert received == [b"after-errors"]
    assert worker.counters.rx_errors >= 2
    assert worker.counters.rx_packets == 1

    assert worker.stop()


def test_only_one_worker_can_own_the_hal(profile):
    first = make_worker(profile, FakeHal())
    second = make_worker(profile, FakeHal())
    first.start()
    first.wait_ready(2)

    with pytest.raises(WorkerError, match="already running"):
        second.start()

    assert first.stop()


def test_stop_is_graceful_and_idempotent(profile):
    hal = FakeHal()
    states = []
    worker = make_worker(profile, hal, on_state=states.append)
    worker.start()
    worker.wait_ready(2)
    assert worker.online

    assert worker.stop()
    assert not worker.online
    assert hal.stop_calls == 1
    assert states == [True, False]
    assert worker.stop()
    assert hal.stop_calls == 1


def test_reset_runs_around_the_hal(profile, monkeypatch):
    actions = []
    monkeypatch.setattr(
        "sx1302_reticulum.worker.run_reset",
        lambda profile, action, log=None: actions.append(action),
    )
    hal = FakeHal()
    worker = RadioWorker(profile, hal=hal, reset=True, rx_idle_sleep=0.001)
    worker.start()
    worker.wait_ready(2)
    assert worker.stop()

    assert actions == ["start", "stop"]


def test_submit_rejects_payload_over_limit(profile):
    hal = FakeHal()
    worker = make_worker(profile, hal)
    worker.start()
    worker.wait_ready(2)

    with pytest.raises(WorkerError, match="limit"):
        worker.submit(b"x" * 255)

    assert worker.stop()


def test_all_hal_calls_happen_on_the_worker_thread(profile):
    hal = FakeHal()
    hal.rx_batches.append([make_rx(b"thread-check")])
    worker = make_worker(profile, hal)
    worker.start()
    worker.wait_ready(2)
    assert wait_until(lambda: hal.receive_calls > 1)

    worker.submit(b"out")
    assert wait_until(lambda: hal.sent == [b"out"])
    assert worker.stop()

    assert hal.thread_ids
    assert threading.get_ident() not in hal.thread_ids
    assert hal.thread_ids == {worker._thread.ident}


def test_reconfigure_applies_a_new_profile(profile):
    hal = FakeHal()
    worker = make_worker(profile, hal)
    worker.start()
    worker.wait_ready(2)

    new_profile = profile.with_overrides(frequency=869_525_000)
    worker.reconfigure(new_profile)

    assert wait_until(lambda: worker.profile.frequency == 869_525_000)
    assert wait_until(lambda: hal.configure_calls == 2)
    assert hal.start_calls == 2
    assert hal.stop_calls >= 1
    assert worker.online

    assert worker.stop()


def test_duty_cycle_delays_second_transmission(profile):
    airtime_ms = 20
    window_s = 0.2
    hal = FakeHal(airtime_ms=airtime_ms)
    sent_times: list[float] = []

    def on_tx(payload):
        sent_times.append(time.monotonic())

    worker = make_worker(
        profile,
        hal,
        on_tx=on_tx,
        duty_cycle_percent=10.0,
        duty_cycle_window_s=window_s,
    )
    worker.start()
    worker.wait_ready(2)

    worker.submit(b"first")
    assert wait_until(lambda: len(hal.sent) == 1)
    worker.submit(b"second")
    assert wait_until(lambda: len(hal.sent) == 2)

    # One packet of airtime fits exactly in the 10% of 200 ms budget, so the
    # second must wait for the first to age out of the window.
    assert sent_times[1] - sent_times[0] >= window_s * 0.8
    assert worker.counters.duty_cycle_waits == 1
    assert worker.counters.tx_airtime_ms == 2 * airtime_ms

    assert worker.stop()


def test_duty_cycle_limits_oversized_frames(profile):
    # Each frame is larger than the whole window budget. The worker may send
    # one, but must wait for the window to drain before sending the next.
    airtime_ms = 40
    window_s = 0.2
    hal = FakeHal(airtime_ms=airtime_ms)
    sent_times: list[float] = []

    worker = make_worker(
        profile,
        hal,
        on_tx=lambda payload: sent_times.append(time.monotonic()),
        duty_cycle_percent=10.0,
        duty_cycle_window_s=window_s,
    )
    worker.start()
    worker.wait_ready(2)

    worker.submit(b"first")
    assert wait_until(lambda: len(hal.sent) == 1)
    worker.submit(b"second")
    assert wait_until(lambda: len(hal.sent) == 2)

    assert sent_times[1] - sent_times[0] >= window_s * 0.8
    assert worker.counters.duty_cycle_waits == 1

    assert worker.stop()


def test_rx_continues_during_duty_cycle_wait(profile):
    hal = FakeHal(airtime_ms=40)
    received: list[bytes] = []
    rx_event = threading.Event()

    def on_rx(packet):
        received.append(packet.payload)
        rx_event.set()

    worker = make_worker(
        profile,
        hal,
        on_rx=on_rx,
        duty_cycle_percent=10.0,
        duty_cycle_window_s=0.5,
    )
    worker.start()
    worker.wait_ready(2)

    worker.submit(b"first")
    assert wait_until(lambda: len(hal.sent) == 1)
    worker.submit(b"second")

    # The second frame is gated; a packet arriving now must still be received.
    hal.rx_batches.append([make_rx(b"during-wait")])
    assert rx_event.wait(0.25)
    assert received == [b"during-wait"]
    assert len(hal.sent) == 1

    assert wait_until(lambda: len(hal.sent) == 2)
    assert worker.stop()


def test_lbt_waits_for_a_recent_frame(profile):
    hal = FakeHal(airtime_ms=1)
    hal.rx_batches.append([make_rx(b"busy", rssi=-50.0)])
    worker = make_worker(
        profile,
        hal,
        lbt_rssi_threshold=-80.0,
        lbt_check_ms=150.0,
        lbt_timeout_s=2.0,
    )
    worker.start()
    worker.wait_ready(2)
    assert wait_until(lambda: worker.counters.rx_packets == 1)

    started = time.monotonic()
    worker.submit(b"after-busy")
    assert wait_until(lambda: hal.sent == [b"after-busy"], timeout=3)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.1
    assert worker.counters.lbt_busy_count >= 1
    assert worker.counters.lbt_drops == 0

    assert worker.stop()


def test_lbt_drops_a_frame_after_the_timeout(profile):
    class AlwaysBusyHal(FakeHal):
        def receive(self, max_packets=8):
            self._touch()
            self.receive_calls += 1
            return [make_rx(b"busy", rssi=-40.0)]

    hal = AlwaysBusyHal(airtime_ms=1)
    worker = make_worker(
        profile,
        hal,
        lbt_rssi_threshold=-80.0,
        lbt_check_ms=20.0,
        lbt_timeout_s=0.15,
    )
    worker.start()
    worker.wait_ready(2)

    worker.submit(b"never-sent")
    assert wait_until(lambda: worker.counters.lbt_drops == 1)
    assert hal.sent == []
    assert worker.counters.lbt_busy_count >= 1

    assert worker.stop()
