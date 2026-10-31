"""Board profile parsing and HAL configuration tests."""

from pathlib import Path

import pytest

from sx1302_reticulum import structs
from sx1302_reticulum.config import BoardProfile, ConfigError

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_PROFILE = ROOT / "examples" / "rak2287-eu868.conf"

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
"""


def _write_profile(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "board.conf"
    path.write_text(body)
    return path


def test_example_profile_describes_rak2004_ver_b():
    profile = BoardProfile.from_ini(EXAMPLE_PROFILE)

    assert profile.reset_gpio == 25
    assert profile.reset_env() == {"SX1302_RESET_GPIO": "25"}
    assert profile.channel_mode == "service"
    assert profile.if_chain == 8
    assert profile.spreadingfactor == 9
    assert profile.bandwidth == 125_000
    assert profile.tx_power == 14
    assert profile.tx_gain_lut[0].rf_power == 12
    assert [entry.rf_power for entry in profile.tx_gain_lut] == sorted(
        entry.rf_power for entry in profile.tx_gain_lut
    )


def test_reset_gpio_is_optional(tmp_path):
    profile = BoardProfile.from_ini(_write_profile(tmp_path, MINIMAL_PROFILE))

    assert profile.reset_gpio is None
    assert profile.reset_env() == {}


def test_reset_gpio_must_be_in_range(tmp_path):
    body = MINIMAL_PROFILE.replace(
        "reset_enabled = no", "reset_enabled = no\nreset_gpio = 99"
    )

    with pytest.raises(ConfigError, match="reset_gpio"):
        BoardProfile.from_ini(_write_profile(tmp_path, body))


def test_reset_commands_and_env(tmp_path):
    script = tmp_path / "reset.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    body = (
        MINIMAL_PROFILE.replace(
            "reset_enabled = no",
            f"reset_enabled = yes\nreset_script = {script}\nreset_gpio = 25",
        )
    )
    profile = BoardProfile.from_ini(_write_profile(tmp_path, body))

    assert profile.reset_command("start") == [str(script), "start"]
    assert profile.reset_command("stop") == [str(script), "stop"]
    assert profile.reset_env() == {"SX1302_RESET_GPIO": "25"}


def test_tx_power_below_lut_is_rejected(tmp_path):
    body = MINIMAL_PROFILE.replace("tx_power = 14", "tx_power = 5")

    with pytest.raises(ConfigError, match="below the TX gain LUT minimum"):
        BoardProfile.from_ini(_write_profile(tmp_path, body))


def test_multi_sf_uses_if_chain_zero(tmp_path):
    body = MINIMAL_PROFILE.replace(
        "spreadingfactor = 9", "spreadingfactor = 9\nmode = multi_sf"
    )
    profile = BoardProfile.from_ini(_write_profile(tmp_path, body))

    assert profile.if_chain == 0
    assert profile.demod_conf().multisf_datarate == 1 << (9 - 5)
    assert [chain for chain, conf in profile.rxif_configs().items() if conf.enable] == [0]


def test_multi_sf_rejects_non_125khz(tmp_path):
    body = MINIMAL_PROFILE.replace(
        "bandwidth = 125000", "bandwidth = 250000"
    ).replace("spreadingfactor = 9", "spreadingfactor = 9\nmode = multi_sf")

    with pytest.raises(ConfigError, match="multi_sf"):
        BoardProfile.from_ini(_write_profile(tmp_path, body))


def test_make_tx_packet_round_trips_payload(tmp_path):
    profile = BoardProfile.from_ini(_write_profile(tmp_path, MINIMAL_PROFILE))
    payload = b"reticulum-smoke"

    packet = profile.make_tx_packet(payload)

    assert packet.size == len(payload)
    assert bytes(packet.payload[: packet.size]) == payload
    assert packet.freq_hz == 868_100_000
    assert packet.modulation == structs.MOD_LORA
    assert packet.bandwidth == profile.bandwidth_const
    assert packet.datarate == 9
    assert packet.coderate == structs.CR_LORA_4_5


def test_make_tx_packet_rejects_more_than_hal_limit(tmp_path):
    profile = BoardProfile.from_ini(_write_profile(tmp_path, MINIMAL_PROFILE))

    with pytest.raises(ConfigError, match="255-byte"):
        profile.make_tx_packet(b"x" * 256)
