"""Interface tests using RNS 1.5.x with a fake HAL and fake Reticulum instance."""

from __future__ import annotations

from collections import deque
import threading
import time

import pytest

RNS = pytest.importorskip("RNS")

from sx1302_reticulum import interface as interface_module  # noqa: E402
from sx1302_reticulum import structs  # noqa: E402
from sx1302_reticulum.config import BoardProfile  # noqa: E402
from sx1302_reticulum.hal import HalError  # noqa: E402
from sx1302_reticulum.interface import SX1302Interface  # noqa: E402
from sx1302_reticulum.worker import WorkerError  # noqa: E402


def wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def make_rx(payload: bytes, status: int = structs.STAT_CRC_OK) -> structs.LgwPktRx:
    packet = structs.LgwPktRx()
    packet.status = status
    packet.size = len(payload)
    packet.payload[: len(payload)] = payload
    packet.rssic = -72.5
    packet.rssis = -72.5
    packet.snr = 6.25
    packet.freq_hz = 868_100_000
    packet.if_chain = 8
    packet.rf_chain = 0
    return packet


class FakeInstance:
    """Minimal stand-in for ``RNS.Reticulum`` used by ``Interface.__init__``."""

    configdir = None

    def __getattr__(self, name):
        if name.startswith("_default_"):
            return lambda *args, **kwargs: 0
        raise AttributeError(name)


class FakeOwner:
    def __init__(self):
        self.packets = []
        self.event = threading.Event()

    def inbound(self, data, interface):
        self.packets.append((data, interface))
        self.event.set()


class FakeHal:
    def __init__(self, *, start_error: Exception | None = None):
        self.configure_calls = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.sent: list[bytes] = []
        self.rx_batches: deque[list[structs.LgwPktRx]] = deque()
        self.start_error = start_error

    def configure(self, profile):
        self.configure_calls += 1

    def start(self):
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error

    def stop(self):
        self.stop_calls += 1

    def receive(self, max_packets=8):
        return self.rx_batches.popleft() if self.rx_batches else []

    def send(self, packet):
        self.sent.append(bytes(packet.payload[: packet.size]))

    def status(self, rf_chain=0, select=structs.TX_STATUS):
        return structs.TX_FREE

    def time_on_air_ms(self, packet):
        return 0


@pytest.fixture
def rns_log(monkeypatch):
    monkeypatch.setattr(
        RNS.Reticulum, "get_instance", staticmethod(lambda: FakeInstance())
    )
    messages: list[str] = []
    monkeypatch.setattr(
        RNS, "log", lambda message, *args, **kwargs: messages.append(str(message))
    )
    return messages


@pytest.fixture
def fake_hal(monkeypatch):
    hal = FakeHal()
    monkeypatch.setattr(interface_module, "Hal", lambda path=None: hal)
    return hal


def make_config(profile_path, **extra):
    config = {
        "name": "Test SX1302",
        "type": "SX1302",
        "interface_enabled": "yes",
        "board_profile": str(profile_path),
        "reset": "no",
        "rx_idle_sleep": "0.001",
        "status_poll": "0.001",
        "tx_complete_margin": "0.01",
    }
    config.update(extra)
    return config


def test_interface_lifecycle(rns_log, fake_hal, write_profile):
    profile_path = write_profile()
    profile = BoardProfile.from_ini(profile_path)
    owner = FakeOwner()

    interface = SX1302Interface(owner, make_config(profile_path))

    assert interface.HW_MTU == 254
    assert interface.FIXED_MTU is True
    assert interface.AUTOCONFIGURE_MTU is False
    assert interface.shared_medium is True
    assert interface.reports_phy_stats is True
    assert interface.bitrate == pytest.approx(profile.bitrate_bps)
    assert interface.online is False

    interface.final_init()
    interface.radio.wait_ready(2)
    assert wait_until(lambda: interface.online)

    fake_hal.rx_batches.append([make_rx(b"hello")])
    assert owner.event.wait(2)
    data, source = owner.packets[0]
    assert data == b"hello"
    assert source is interface
    assert interface.rxb == 5
    assert interface.r_stat_rssi == pytest.approx(-72.5)
    assert interface.r_stat_snr == pytest.approx(6.25)
    assert 0.0 <= interface.r_stat_q <= 100.0

    interface.process_outgoing(b"outbound")
    assert wait_until(lambda: fake_hal.sent == [b"outbound"])
    assert interface.txb == 8

    sent_before = len(fake_hal.sent)
    interface.process_outgoing(b"x" * 255)
    assert len(fake_hal.sent) == sent_before
    assert interface.tx_drops == 1

    interface.detach()
    assert interface.detached is True
    assert interface.online is False
    assert fake_hal.stop_calls == 1

    interface.detach()
    interface.teardown()
    assert fake_hal.stop_calls == 1
    assert not interface.radio.is_thread_alive()


def test_ifac_overhead_reduces_hw_mtu(rns_log, fake_hal, write_profile):
    interface = SX1302Interface(FakeOwner(), make_config(write_profile()))
    assert interface.HW_MTU == 254

    interface.ifac_identity = object()
    interface.ifac_size = 16
    interface.final_init()
    interface.radio.wait_ready(2)

    assert interface.HW_MTU == 238
    assert interface._max_on_air == 254

    # A 238-byte Reticulum frame plus the 16-byte IFAC tag is still 254 bytes.
    interface.process_outgoing(b"y" * 254)
    assert wait_until(lambda: fake_hal.sent == [b"y" * 254])

    interface.detach()


def test_stanza_overrides_are_applied(rns_log, write_profile):
    profile_path = write_profile()
    interface = SX1302Interface(
        FakeOwner(),
        make_config(
            profile_path,
            frequency="869525000",
            spreadingfactor="10",
            codingrate="6",
            txpower="12",
            spi_path="/dev/spidev1.0",
        ),
    )

    assert interface.profile.frequency == 869_525_000
    assert interface.profile.spreadingfactor == 10
    assert interface.profile.codingrate == 6
    assert interface.profile.tx_power == 12
    assert interface.profile.spi_path == "/dev/spidev1.0"
    assert interface.bitrate == pytest.approx(interface.profile.bitrate_bps)

    interface.detach()


def test_missing_board_profile_is_rejected(rns_log):
    with pytest.raises(ValueError, match="board_profile"):
        SX1302Interface(FakeOwner(), make_config(""))


def test_startup_failure_leaves_interface_offline(rns_log, monkeypatch, write_profile):
    hal = FakeHal(start_error=HalError("cannot start"))
    monkeypatch.setattr(interface_module, "Hal", lambda path=None: hal)
    interface = SX1302Interface(FakeOwner(), make_config(write_profile()))

    interface.final_init()
    with pytest.raises(WorkerError, match="cannot start"):
        interface.radio.wait_ready(2)

    assert interface.online is False
    assert interface.radio.startup_error is not None

    interface.detach()
    assert hal.stop_calls == 0
