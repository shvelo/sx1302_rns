"""Fast checks of the ctypes HAL structure mirrors."""

import ctypes
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sx1302_reticulum import structs  # noqa: E402


EXPECTED_SIZES = {
    structs.LgwRssiTcomp: 20,
    structs.LgwConfBoard: 72,
    structs.LgwConfRxrf: 40,
    structs.LgwConfRxif: 40,
    structs.LgwConfDemod: 1,
    structs.LgwTxGain: 8,
    structs.LgwTxGainLut: 129,
    structs.LgwPktRx: 316,
    structs.LgwPktTx: 292,
}

EXPECTED_OFFSETS = {
    structs.LgwConfBoard: {
        "lorawan_public": 0,
        "clksrc": 1,
        "full_duplex": 2,
        "com_type": 4,
        "com_path": 8,
    },
    structs.LgwConfRxrf: {
        "enable": 0,
        "freq_hz": 4,
        "rssi_offset": 8,
        "rssi_tcomp": 12,
        "type": 32,
        "tx_enable": 36,
        "single_input_mode": 37,
    },
    structs.LgwConfRxif: {
        "enable": 0,
        "rf_chain": 1,
        "freq_hz": 4,
        "bandwidth": 8,
        "datarate": 12,
        "sync_word_size": 16,
        "sync_word": 24,
        "implicit_hdr": 32,
        "implicit_payload_length": 33,
        "implicit_crc_en": 34,
        "implicit_coderate": 35,
    },
    structs.LgwConfDemod: {"multisf_datarate": 0},
    structs.LgwTxGain: {
        "rf_power": 0,
        "dig_gain": 1,
        "pa_gain": 2,
        "dac_gain": 3,
        "mix_gain": 4,
        "offset_i": 5,
        "offset_q": 6,
        "pwr_idx": 7,
    },
    structs.LgwTxGainLut: {"lut": 0, "size": 128},
    structs.LgwPktRx: {
        "freq_hz": 0,
        "freq_offset": 4,
        "if_chain": 8,
        "status": 9,
        "count_us": 12,
        "rf_chain": 16,
        "modem_id": 17,
        "modulation": 18,
        "bandwidth": 19,
        "datarate": 20,
        "coderate": 24,
        "rssic": 28,
        "rssis": 32,
        "snr": 36,
        "snr_min": 40,
        "snr_max": 44,
        "crc": 48,
        "size": 50,
        "payload": 52,
        "ftime_received": 308,
        "ftime": 312,
    },
    structs.LgwPktTx: {
        "freq_hz": 0,
        "tx_mode": 4,
        "count_us": 8,
        "rf_chain": 12,
        "rf_power": 13,
        "modulation": 14,
        "freq_offset": 15,
        "bandwidth": 16,
        "datarate": 20,
        "coderate": 24,
        "invert_pol": 25,
        "f_dev": 26,
        "preamble": 28,
        "no_crc": 30,
        "no_header": 31,
        "size": 32,
        "payload": 34,
    },
}


@pytest.mark.parametrize(("struct_type", "expected"), EXPECTED_SIZES.items())
def test_struct_sizes(struct_type, expected):
    assert ctypes.sizeof(struct_type) == expected


@pytest.mark.parametrize(("struct_type", "fields"), EXPECTED_OFFSETS.items())
def test_struct_field_offsets(struct_type, fields):
    for field, expected in fields.items():
        assert getattr(struct_type, field).offset == expected, (
            f"{struct_type.__name__}.{field}"
        )


def test_abi_structs_cover_all_mirrors():
    assert set(structs.ABI_STRUCTS.values()) == set(EXPECTED_SIZES)


def test_payload_buffers_are_256_bytes():
    assert structs.PAYLOAD_SIZE == 256
    assert ctypes.sizeof(structs.LgwPktRx().payload) == 256
    assert ctypes.sizeof(structs.LgwPktTx().payload) == 256


def test_hal_constants():
    assert structs.LGW_COM_SPI == 0
    assert structs.LGW_RADIO_TYPE_SX1250 == 5
    assert structs.BW_125KHZ == 0x04
    assert structs.BW_250KHZ == 0x05
    assert structs.BW_500KHZ == 0x06
    assert structs.CR_LORA_4_5 == 0x01
    assert structs.CR_LORA_4_8 == 0x04
    assert structs.STAT_CRC_OK == 0x10
    assert structs.TX_FREE == 2
    assert structs.TX_GAIN_LUT_SIZE_MAX == 16
