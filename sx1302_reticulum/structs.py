"""ctypes mirrors of the libloragw HAL structures and constants.

The layouts in this module must match ``libloragw/inc/loragw_hal.h`` exactly.
``scripts/abi_check.py`` compiles a small C probe and compares every structure
size and field offset against these definitions. Do not reorder fields or
change field types without re-running that check.
"""

from __future__ import annotations

import ctypes

# ---------------------------------------------------------------------------
# HAL constants
# ---------------------------------------------------------------------------

LGW_HAL_SUCCESS = 0
LGW_HAL_ERROR = -1
LGW_LBT_NOT_ALLOWED = 1

LGW_XTAL_FREQU = 32_000_000
LGW_RF_CHAIN_NB = 2
LGW_IF_CHAIN_NB = 10
LGW_REF_BW = 125_000
LGW_MULTI_NB = 8
LGW_MULTI_SF_EN = 0xFF
TX_GAIN_LUT_SIZE_MAX = 16

# lgw_com_type_t
LGW_COM_SPI = 0
LGW_COM_USB = 1
LGW_COM_UNKNOWN = 2

# lgw_radio_type_t
LGW_RADIO_TYPE_NONE = 0
LGW_RADIO_TYPE_SX1255 = 1
LGW_RADIO_TYPE_SX1257 = 2
LGW_RADIO_TYPE_SX1272 = 3
LGW_RADIO_TYPE_SX1276 = 4
LGW_RADIO_TYPE_SX1250 = 5

# modulation values
MOD_UNDEFINED = 0x00
MOD_CW = 0x08
MOD_LORA = 0x10
MOD_FSK = 0x20

# bandwidth values
BW_UNDEFINED = 0x00
BW_500KHZ = 0x06
BW_250KHZ = 0x05
BW_125KHZ = 0x04

# LoRa datarate values (spreading factors)
DR_UNDEFINED = 0
DR_LORA_SF5 = 5
DR_LORA_SF6 = 6
DR_LORA_SF7 = 7
DR_LORA_SF8 = 8
DR_LORA_SF9 = 9
DR_LORA_SF10 = 10
DR_LORA_SF11 = 11
DR_LORA_SF12 = 12

DR_FSK_MIN = 500
DR_FSK_MAX = 250_000

# LoRa coding rates
CR_UNDEFINED = 0x00
CR_LORA_4_5 = 0x01
CR_LORA_4_6 = 0x02
CR_LORA_4_7 = 0x03
CR_LORA_4_8 = 0x04

# packet status
STAT_UNDEFINED = 0x00
STAT_NO_CRC = 0x01
STAT_CRC_BAD = 0x11
STAT_CRC_OK = 0x10

# TX trigger modes
IMMEDIATE = 0
TIMESTAMPED = 1
ON_GPS = 2

# lgw_status() selectors
TX_STATUS = 1
RX_STATUS = 2

# TX status codes
TX_STATUS_UNKNOWN = 0
TX_OFF = 1
TX_FREE = 2
TX_SCHEDULED = 3
TX_EMITTING = 4

# RX status codes
RX_STATUS_UNKNOWN = 0
RX_OFF = 1
RX_ON = 2
RX_SUSPENDED = 3

# ---------------------------------------------------------------------------
# Structure sizes used by the HAL
# ---------------------------------------------------------------------------

COM_PATH_SIZE = 64
PAYLOAD_SIZE = 256


class LgwRssiTcomp(ctypes.Structure):
    """Mirror of ``struct lgw_rssi_tcomp_s``."""

    _fields_ = [
        ("coeff_a", ctypes.c_float),
        ("coeff_b", ctypes.c_float),
        ("coeff_c", ctypes.c_float),
        ("coeff_d", ctypes.c_float),
        ("coeff_e", ctypes.c_float),
    ]


class LgwConfBoard(ctypes.Structure):
    """Mirror of ``struct lgw_conf_board_s``."""

    _fields_ = [
        ("lorawan_public", ctypes.c_bool),
        ("clksrc", ctypes.c_uint8),
        ("full_duplex", ctypes.c_bool),
        ("com_type", ctypes.c_int),
        ("com_path", ctypes.c_char * COM_PATH_SIZE),
    ]


class LgwConfRxrf(ctypes.Structure):
    """Mirror of ``struct lgw_conf_rxrf_s``."""

    _fields_ = [
        ("enable", ctypes.c_bool),
        ("freq_hz", ctypes.c_uint32),
        ("rssi_offset", ctypes.c_float),
        ("rssi_tcomp", LgwRssiTcomp),
        ("type", ctypes.c_int),
        ("tx_enable", ctypes.c_bool),
        ("single_input_mode", ctypes.c_bool),
    ]


class LgwConfRxif(ctypes.Structure):
    """Mirror of ``struct lgw_conf_rxif_s``."""

    _fields_ = [
        ("enable", ctypes.c_bool),
        ("rf_chain", ctypes.c_uint8),
        ("freq_hz", ctypes.c_int32),
        ("bandwidth", ctypes.c_uint8),
        ("datarate", ctypes.c_uint32),
        ("sync_word_size", ctypes.c_uint8),
        ("sync_word", ctypes.c_uint64),
        ("implicit_hdr", ctypes.c_bool),
        ("implicit_payload_length", ctypes.c_uint8),
        ("implicit_crc_en", ctypes.c_bool),
        ("implicit_coderate", ctypes.c_uint8),
    ]


class LgwConfDemod(ctypes.Structure):
    """Mirror of ``struct lgw_conf_demod_s``."""

    _fields_ = [
        ("multisf_datarate", ctypes.c_uint8),
    ]


class LgwTxGain(ctypes.Structure):
    """Mirror of ``struct lgw_tx_gain_s``."""

    _fields_ = [
        ("rf_power", ctypes.c_int8),
        ("dig_gain", ctypes.c_uint8),
        ("pa_gain", ctypes.c_uint8),
        ("dac_gain", ctypes.c_uint8),
        ("mix_gain", ctypes.c_uint8),
        ("offset_i", ctypes.c_int8),
        ("offset_q", ctypes.c_int8),
        ("pwr_idx", ctypes.c_uint8),
    ]


class LgwTxGainLut(ctypes.Structure):
    """Mirror of ``struct lgw_tx_gain_lut_s``."""

    _fields_ = [
        ("lut", LgwTxGain * TX_GAIN_LUT_SIZE_MAX),
        ("size", ctypes.c_uint8),
    ]


class LgwPktRx(ctypes.Structure):
    """Mirror of ``struct lgw_pkt_rx_s``."""

    _fields_ = [
        ("freq_hz", ctypes.c_uint32),
        ("freq_offset", ctypes.c_int32),
        ("if_chain", ctypes.c_uint8),
        ("status", ctypes.c_uint8),
        ("count_us", ctypes.c_uint32),
        ("rf_chain", ctypes.c_uint8),
        ("modem_id", ctypes.c_uint8),
        ("modulation", ctypes.c_uint8),
        ("bandwidth", ctypes.c_uint8),
        ("datarate", ctypes.c_uint32),
        ("coderate", ctypes.c_uint8),
        ("rssic", ctypes.c_float),
        ("rssis", ctypes.c_float),
        ("snr", ctypes.c_float),
        ("snr_min", ctypes.c_float),
        ("snr_max", ctypes.c_float),
        ("crc", ctypes.c_uint16),
        ("size", ctypes.c_uint16),
        ("payload", ctypes.c_uint8 * PAYLOAD_SIZE),
        ("ftime_received", ctypes.c_bool),
        ("ftime", ctypes.c_uint32),
    ]


class LgwPktTx(ctypes.Structure):
    """Mirror of ``struct lgw_pkt_tx_s``."""

    _fields_ = [
        ("freq_hz", ctypes.c_uint32),
        ("tx_mode", ctypes.c_uint8),
        ("count_us", ctypes.c_uint32),
        ("rf_chain", ctypes.c_uint8),
        ("rf_power", ctypes.c_int8),
        ("modulation", ctypes.c_uint8),
        ("freq_offset", ctypes.c_int8),
        ("bandwidth", ctypes.c_uint8),
        ("datarate", ctypes.c_uint32),
        ("coderate", ctypes.c_uint8),
        ("invert_pol", ctypes.c_bool),
        ("f_dev", ctypes.c_uint8),
        ("preamble", ctypes.c_uint16),
        ("no_crc", ctypes.c_bool),
        ("no_header", ctypes.c_bool),
        ("size", ctypes.c_uint16),
        ("payload", ctypes.c_uint8 * PAYLOAD_SIZE),
    ]


# Structures whose C layout is checked by scripts/abi_check.py.
ABI_STRUCTS = {
    "lgw_rssi_tcomp_s": LgwRssiTcomp,
    "lgw_conf_board_s": LgwConfBoard,
    "lgw_conf_rxrf_s": LgwConfRxrf,
    "lgw_conf_rxif_s": LgwConfRxif,
    "lgw_conf_demod_s": LgwConfDemod,
    "lgw_tx_gain_s": LgwTxGain,
    "lgw_tx_gain_lut_s": LgwTxGainLut,
    "lgw_pkt_rx_s": LgwPktRx,
    "lgw_pkt_tx_s": LgwPktTx,
}
