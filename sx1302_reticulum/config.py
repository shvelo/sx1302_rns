"""Board profile parsing and HAL configuration helpers.

A board profile is an INI file that describes the physical SX1302 board and
the single LoRa channel this project uses. It is deliberately separate from
the Reticulum interface configuration so the same hardware settings can be
reused by the smoke test and the runtime interface.
"""

from __future__ import annotations

from configparser import ConfigParser, Error as ConfigParserError
from dataclasses import dataclass, replace
from pathlib import Path
import shlex

from . import structs


class ConfigError(ValueError):
    """Raised when a board profile is missing or invalid."""


RADIO_TYPES = {
    "SX1250": structs.LGW_RADIO_TYPE_SX1250,
    "SX1255": structs.LGW_RADIO_TYPE_SX1255,
    "SX1257": structs.LGW_RADIO_TYPE_SX1257,
    "SX1272": structs.LGW_RADIO_TYPE_SX1272,
    "SX1276": structs.LGW_RADIO_TYPE_SX1276,
}

BANDWIDTHS = {
    125_000: structs.BW_125KHZ,
    250_000: structs.BW_250KHZ,
    500_000: structs.BW_500KHZ,
}

CODING_RATES = {
    5: structs.CR_LORA_4_5,
    6: structs.CR_LORA_4_6,
    7: structs.CR_LORA_4_7,
    8: structs.CR_LORA_4_8,
}

CHANNEL_MODES = ("service", "multi_sf")


def _bool(value: str, key: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("1", "yes", "true", "on"):
        return True
    if normalized in ("0", "no", "false", "off"):
        return False
    raise ConfigError(f"{key}: expected a boolean, got {value!r}")


def _int(value: str, key: str) -> int:
    try:
        return int(value.strip(), 0)
    except ValueError as exc:
        raise ConfigError(f"{key}: expected an integer, got {value!r}") from exc


def _float(value: str, key: str) -> float:
    try:
        return float(value.strip())
    except ValueError as exc:
        raise ConfigError(f"{key}: expected a number, got {value!r}") from exc


def _get(
    parser: ConfigParser,
    section: str,
    option: str,
    default: str | None = None,
) -> str:
    if parser.has_option(section, option):
        return parser.get(section, option)
    if default is not None:
        return default
    raise ConfigError(f"missing required option [{section}] {option}")


@dataclass(frozen=True)
class TxGainEntry:
    """One entry in the HAL TX gain lookup table."""

    rf_power: int
    pa_gain: int = 0
    pwr_idx: int = 0
    dig_gain: int = 0
    dac_gain: int = 0
    mix_gain: int = 5
    offset_i: int = 0
    offset_q: int = 0

    def to_ctypes(self) -> structs.LgwTxGain:
        gain = structs.LgwTxGain()
        gain.rf_power = self.rf_power
        gain.dig_gain = self.dig_gain
        gain.pa_gain = self.pa_gain
        gain.dac_gain = self.dac_gain
        gain.mix_gain = self.mix_gain
        gain.offset_i = self.offset_i
        gain.offset_q = self.offset_q
        gain.pwr_idx = self.pwr_idx
        return gain


@dataclass(frozen=True)
class BoardProfile:
    """Validated board and single-channel radio configuration."""

    path: Path
    spi_path: str
    radio_type: int
    clksrc: int
    full_duplex: bool
    single_input_mode: bool
    rssi_offset: float
    rssi_tcomp: tuple[float, float, float, float, float]
    rf_chain: int
    frequency: int
    tx_enable: bool
    tx_power: int
    channel_mode: str
    if_frequency: int
    bandwidth: int
    spreadingfactor: int
    codingrate: int
    preamble: int
    tx_gain_lut: tuple[TxGainEntry, ...]
    reset_script: Path | None
    reset_start_args: tuple[str, ...]
    reset_stop_args: tuple[str, ...]
    reset_enabled: bool

    @classmethod
    def from_ini(cls, path: str | Path) -> "BoardProfile":
        profile_path = Path(path).expanduser().resolve()
        parser = ConfigParser(interpolation=None)
        try:
            with profile_path.open(encoding="utf-8") as handle:
                parser.read_file(handle)
        except OSError as exc:
            raise ConfigError(f"cannot read board profile {profile_path}: {exc}") from exc
        except ConfigParserError as exc:
            raise ConfigError(f"invalid board profile {profile_path}: {exc}") from exc

        for section in ("board", "rf", "channel"):
            if not parser.has_section(section):
                raise ConfigError(f"missing [{section}] section in {profile_path}")

        radio_name = _get(parser, "board", "radio_type", "SX1250").strip().upper()
        if radio_name not in RADIO_TYPES:
            raise ConfigError(
                f"[board] radio_type must be one of {', '.join(RADIO_TYPES)}"
            )

        bandwidth = _int(_get(parser, "channel", "bandwidth"), "channel.bandwidth")
        if bandwidth not in BANDWIDTHS:
            raise ConfigError("[channel] bandwidth must be 125000, 250000 or 500000")
        spreadingfactor = _int(
            _get(parser, "channel", "spreadingfactor"), "channel.spreadingfactor"
        )
        if not 5 <= spreadingfactor <= 12:
            raise ConfigError("[channel] spreadingfactor must be between 5 and 12")
        codingrate = _int(_get(parser, "channel", "codingrate", "5"), "channel.codingrate")
        if codingrate not in CODING_RATES:
            raise ConfigError("[channel] codingrate must be 5, 6, 7 or 8")

        channel_mode = _get(parser, "channel", "mode", "service").strip().lower()
        if channel_mode not in CHANNEL_MODES:
            raise ConfigError(f"[channel] mode must be one of {', '.join(CHANNEL_MODES)}")
        if channel_mode == "multi_sf" and bandwidth != 125_000:
            raise ConfigError("[channel] multi_sf mode only supports 125000 Hz bandwidth")

        reset_script_raw = parser.get("board", "reset_script", fallback="").strip()
        reset_script = None
        if reset_script_raw:
            reset_script = Path(reset_script_raw).expanduser()
            if not reset_script.is_absolute():
                reset_script = (profile_path.parent / reset_script).resolve()
        reset_enabled = _bool(
            _get(parser, "board", "reset_enabled", "yes"), "board.reset_enabled"
        )
        if reset_enabled and reset_script is None:
            raise ConfigError("[board] reset_script is required unless reset_enabled = no")

        start_args = tuple(shlex.split(_get(parser, "board", "reset_start_args", "start")))
        stop_args = tuple(shlex.split(_get(parser, "board", "reset_stop_args", "stop")))

        tx_gain_lut = _parse_tx_gain_lut(parser)
        tx_power = _int(_get(parser, "rf", "tx_power", "14"), "rf.tx_power")
        lut_powers = [entry.rf_power for entry in tx_gain_lut]
        if not lut_powers:
            raise ConfigError("no [txgain.*] entries found")
        if tx_power < min(lut_powers):
            raise ConfigError(
                f"[rf] tx_power {tx_power} is below the TX gain LUT minimum "
                f"({min(lut_powers)})"
            )

        frequency = _int(_get(parser, "rf", "frequency"), "rf.frequency")
        if not 100_000_000 <= frequency <= 1_000_000_000:
            raise ConfigError("[rf] frequency must be between 100 MHz and 1 GHz")

        rf_chain = _int(_get(parser, "rf", "chain", "0"), "rf.chain")
        if not 0 <= rf_chain < structs.LGW_RF_CHAIN_NB:
            raise ConfigError(f"[rf] chain must be 0 or 1")

        profile = cls(
            path=profile_path,
            spi_path=_get(parser, "board", "spi_path", "/dev/spidev0.0").strip(),
            radio_type=RADIO_TYPES[radio_name],
            clksrc=_int(_get(parser, "board", "clksrc", "0"), "board.clksrc"),
            full_duplex=_bool(
                _get(parser, "board", "full_duplex", "no"), "board.full_duplex"
            ),
            single_input_mode=_bool(
                _get(parser, "board", "single_input_mode", "no"),
                "board.single_input_mode",
            ),
            rssi_offset=_float(
                _get(parser, "board", "rssi_offset", "0"), "board.rssi_offset"
            ),
            rssi_tcomp=(
                _float(_get(parser, "board", "rssi_tcomp_a", "0"), "board.rssi_tcomp_a"),
                _float(_get(parser, "board", "rssi_tcomp_b", "0"), "board.rssi_tcomp_b"),
                _float(_get(parser, "board", "rssi_tcomp_c", "0"), "board.rssi_tcomp_c"),
                _float(_get(parser, "board", "rssi_tcomp_d", "0"), "board.rssi_tcomp_d"),
                _float(_get(parser, "board", "rssi_tcomp_e", "0"), "board.rssi_tcomp_e"),
            ),
            rf_chain=rf_chain,
            frequency=frequency,
            tx_enable=_bool(
                _get(parser, "rf", "tx_enable", "yes"), "rf.tx_enable"
            ),
            tx_power=tx_power,
            channel_mode=channel_mode,
            if_frequency=_int(
                _get(parser, "channel", "if_frequency", "0"), "channel.if_frequency"
            ),
            bandwidth=bandwidth,
            spreadingfactor=spreadingfactor,
            codingrate=codingrate,
            preamble=_int(_get(parser, "channel", "preamble", "8"), "channel.preamble"),
            tx_gain_lut=tx_gain_lut,
            reset_script=reset_script,
            reset_start_args=start_args,
            reset_stop_args=stop_args,
            reset_enabled=reset_enabled,
        )
        profile._validate()
        return profile

    def _validate(self) -> None:
        if self.clksrc not in (0, 1):
            raise ConfigError("[board] clksrc must be 0 or 1")
        if self.reset_script is not None and not self.reset_script.is_file():
            raise ConfigError(f"reset script does not exist: {self.reset_script}")
        if self.reset_start_args == () or self.reset_stop_args == ():
            raise ConfigError("reset argument lists must not be empty")
        if self.channel_mode == "service" and self.if_frequency != 0:
            raise ConfigError("[channel] if_frequency must be 0 in service mode")
        if self.tx_gain_lut and self.tx_power < min(entry.rf_power for entry in self.tx_gain_lut):
            raise ConfigError(
                f"tx_power {self.tx_power} is below the TX gain LUT minimum "
                f"({min(entry.rf_power for entry in self.tx_gain_lut)})"
            )
        for entry in self.tx_gain_lut:
            if not 0 <= entry.pa_gain <= 3:
                raise ConfigError("txgain pa_gain must be between 0 and 3")
            if not 5 <= entry.mix_gain <= 15:
                raise ConfigError("txgain mix_gain must be between 5 and 15")
            if not 0 <= entry.pwr_idx <= 22:
                raise ConfigError("txgain pwr_idx must be between 0 and 22")

    def with_overrides(self, **changes) -> "BoardProfile":
        """Return a validated copy with selected fields replaced."""
        updated = replace(self, **changes)
        updated._validate()
        return updated

    # ------------------------------------------------------------------
    # HAL configuration structures
    # ------------------------------------------------------------------

    def board_conf(self) -> structs.LgwConfBoard:
        conf = structs.LgwConfBoard()
        conf.lorawan_public = False  # private sync word
        conf.clksrc = self.clksrc
        conf.full_duplex = self.full_duplex
        conf.com_type = structs.LGW_COM_SPI
        conf.com_path = self.spi_path.encode("utf-8")[: structs.COM_PATH_SIZE - 1]
        return conf

    def rf_conf(self) -> structs.LgwConfRxrf:
        conf = structs.LgwConfRxrf()
        conf.enable = True
        conf.freq_hz = self.frequency
        conf.rssi_offset = self.rssi_offset
        conf.rssi_tcomp.coeff_a = self.rssi_tcomp[0]
        conf.rssi_tcomp.coeff_b = self.rssi_tcomp[1]
        conf.rssi_tcomp.coeff_c = self.rssi_tcomp[2]
        conf.rssi_tcomp.coeff_d = self.rssi_tcomp[3]
        conf.rssi_tcomp.coeff_e = self.rssi_tcomp[4]
        conf.type = self.radio_type
        conf.tx_enable = self.tx_enable
        conf.single_input_mode = self.single_input_mode
        return conf

    @property
    def if_chain(self) -> int:
        """IF chain used for the single RX channel."""
        return 8 if self.channel_mode == "service" else 0

    @property
    def bandwidth_const(self) -> int:
        return BANDWIDTHS[self.bandwidth]

    @property
    def codingrate_const(self) -> int:
        return CODING_RATES[self.codingrate]

    def rxif_conf(self, if_chain: int) -> structs.LgwConfRxif:
        conf = structs.LgwConfRxif()
        if if_chain != self.if_chain:
            conf.enable = False
            return conf

        conf.enable = True
        conf.rf_chain = self.rf_chain
        conf.freq_hz = self.if_frequency
        conf.bandwidth = self.bandwidth_const
        conf.datarate = self.spreadingfactor
        conf.implicit_hdr = False
        conf.implicit_crc_en = True
        conf.implicit_coderate = self.codingrate_const
        return conf

    def rxif_configs(self) -> dict[int, structs.LgwConfRxif]:
        return {
            chain: self.rxif_conf(chain) for chain in range(structs.LGW_IF_CHAIN_NB)
        }

    def demod_conf(self) -> structs.LgwConfDemod:
        conf = structs.LgwConfDemod()
        if self.channel_mode == "multi_sf":
            conf.multisf_datarate = 1 << (self.spreadingfactor - 5)
        else:
            conf.multisf_datarate = 0
        return conf

    def txgain_conf(self) -> structs.LgwTxGainLut:
        conf = structs.LgwTxGainLut()
        conf.size = len(self.tx_gain_lut)
        for index, entry in enumerate(self.tx_gain_lut):
            conf.lut[index] = entry.to_ctypes()
        return conf

    def make_tx_packet(self, payload: bytes) -> structs.LgwPktTx:
        if len(payload) > 255:
            raise ConfigError("LoRa payload exceeds the HAL 255-byte TX limit")
        packet = structs.LgwPktTx()
        packet.freq_hz = self.frequency
        packet.tx_mode = structs.IMMEDIATE
        packet.count_us = 0
        packet.rf_chain = self.rf_chain
        packet.rf_power = self.tx_power
        packet.modulation = structs.MOD_LORA
        packet.freq_offset = 0
        packet.bandwidth = self.bandwidth_const
        packet.datarate = self.spreadingfactor
        packet.coderate = self.codingrate_const
        packet.invert_pol = False
        packet.f_dev = 0
        packet.preamble = self.preamble
        packet.no_crc = False
        packet.no_header = False
        packet.size = len(payload)
        packet.payload[: len(payload)] = payload
        return packet

    @property
    def bitrate_bps(self) -> float:
        """Approximate LoRa bitrate using the RNode formula."""
        sf = self.spreadingfactor
        cr = self.codingrate
        bw_khz = self.bandwidth / 1000.0
        return sf * ((4.0 / cr) / ((2**sf) / bw_khz)) * 1000.0

    def reset_command(self, action: str) -> list[str] | None:
        if not self.reset_enabled or self.reset_script is None:
            return None
        if action == "start":
            return [str(self.reset_script), *self.reset_start_args]
        if action == "stop":
            return [str(self.reset_script), *self.reset_stop_args]
        raise ValueError(f"unknown reset action: {action}")


def _parse_tx_gain_lut(parser: ConfigParser) -> tuple[TxGainEntry, ...]:
    entries: list[TxGainEntry] = []
    for section in parser.sections():
        if not section.lower().startswith("txgain."):
            continue
        suffix = section.split(".", 1)[1]
        rf_power = _int(suffix, f"{section} power")
        entry = TxGainEntry(
            rf_power=_int(_get(parser, section, "rf_power", suffix), f"{section}.rf_power"),
            pa_gain=_int(_get(parser, section, "pa_gain", "0"), f"{section}.pa_gain"),
            pwr_idx=_int(_get(parser, section, "pwr_idx", "0"), f"{section}.pwr_idx"),
            dig_gain=_int(_get(parser, section, "dig_gain", "0"), f"{section}.dig_gain"),
            dac_gain=_int(_get(parser, section, "dac_gain", "0"), f"{section}.dac_gain"),
            mix_gain=_int(_get(parser, section, "mix_gain", "5"), f"{section}.mix_gain"),
            offset_i=_int(_get(parser, section, "offset_i", "0"), f"{section}.offset_i"),
            offset_q=_int(_get(parser, section, "offset_q", "0"), f"{section}.offset_q"),
        )
        entries.append(entry)
    entries.sort(key=lambda entry: entry.rf_power)
    return tuple(entries)
