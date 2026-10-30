#!/usr/bin/env python3
"""Standalone SX1302 raw-LoRa smoke test.

This script is the first hardware bring-up step. It resets the concentrator,
configures one RF chain and one RX channel from a board profile, starts the
HAL, transmits a test payload, and listens for packets from another node.

It does not require Reticulum and does not start any daemon.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sx1302_reticulum import structs  # noqa: E402
from sx1302_reticulum.config import BANDWIDTHS, CODING_RATES, ConfigError, BoardProfile  # noqa: E402
from sx1302_reticulum.hal import Hal, HalError  # noqa: E402


def _payload_from_args(args: argparse.Namespace) -> bytes:
    if args.payload_hex is not None:
        try:
            return bytes.fromhex(args.payload_hex)
        except ValueError as exc:
            raise ConfigError(f"invalid --payload-hex: {exc}") from exc
    return args.payload.encode("utf-8")


def _run_reset(profile: BoardProfile, action: str) -> None:
    command = profile.reset_command(action)
    if command is None:
        print(f"Reset {action}: skipped (disabled in board profile)")
        return
    print(f"Reset {action}: {' '.join(command)}")
    result = subprocess.run(command)
    if result.returncode != 0:
        raise RuntimeError(f"reset script failed with exit code {result.returncode}")


def _wait_tx_free(hal: Hal, profile: BoardProfile, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = hal.status(profile.rf_chain, structs.TX_STATUS)
        if code == structs.TX_FREE:
            return
        if code in (structs.TX_EMITTING, structs.TX_SCHEDULED):
            time.sleep(0.005)
            continue
        raise HalError(f"unexpected TX status before send: {code}")
    raise TimeoutError("timed out waiting for TX_FREE")


def _transmit(hal: Hal, profile: BoardProfile, payload: bytes) -> None:
    packet = profile.make_tx_packet(payload)
    time_on_air = hal.time_on_air_ms(packet)

    _wait_tx_free(hal, profile)
    print(
        f"TX {len(payload)} bytes, {time_on_air} ms airtime, "
        f"freq={profile.frequency} bw={profile.bandwidth} sf={profile.spreadingfactor} "
        f"cr=4/{profile.codingrate} power={profile.tx_power} dBm"
    )
    print(f"   payload: {payload.hex()}  |{ _printable(payload) }|")
    hal.send(packet)

    deadline = time.monotonic() + (time_on_air / 1000.0) + 2.0
    while time.monotonic() < deadline:
        code = hal.status(profile.rf_chain, structs.TX_STATUS)
        if code == structs.TX_FREE:
            print("TX complete")
            return
        if code in (structs.TX_EMITTING, structs.TX_SCHEDULED):
            time.sleep(0.005)
            continue
        raise HalError(f"unexpected TX status after send: {code}")
    raise TimeoutError("timed out waiting for TX completion")


def _printable(payload: bytes) -> str:
    return "".join(chr(byte) if 32 <= byte < 127 else "." for byte in payload)


def _receive(hal: Hal, seconds: float) -> int:
    print(f"Listening for {seconds:.1f} s...")
    deadline = time.monotonic() + seconds
    count = 0
    while time.monotonic() < deadline:
        for packet in hal.receive(8):
            count += 1
            payload = bytes(packet.payload[: packet.size])
            crc = "CRC_OK" if packet.status == structs.STAT_CRC_OK else f"status=0x{packet.status:02x}"
            print(
                f"RX #{count}: {crc} if={packet.if_chain} rf={packet.rf_chain} "
                f"freq={packet.freq_hz} bw=0x{packet.bandwidth:02x} sf={packet.datarate} "
                f"cr=0x{packet.coderate:02x} rssi={packet.rssic:.1f}/{packet.rssis:.1f} dB "
                f"snr={packet.snr:.1f} dB size={packet.size}"
            )
            print(f"   payload: {payload.hex()}  |{_printable(payload)}|")
        time.sleep(0.01)
    if count == 0:
        print("No packets received.")
    return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "examples" / "rak2287-eu868.conf",
        help="board profile (default: examples/rak2287-eu868.conf)",
    )
    parser.add_argument("--lib", help="path to libloragw.so")
    parser.add_argument("--payload", default="reticulum-sx1302 smoke test")
    parser.add_argument("--payload-hex", help="payload as hex instead of --payload")
    parser.add_argument("--tx-count", type=int, default=1, help="number of test transmissions")
    parser.add_argument("--tx-interval", type=float, default=2.0, help="seconds between transmissions")
    parser.add_argument("--rx-timeout", type=float, default=15.0, help="seconds to listen after TX")
    parser.add_argument("--rx-only", action="store_true", help="do not transmit")
    parser.add_argument("--tx-only", action="store_true", help="transmit and exit without listening")
    parser.add_argument("--no-reset", action="store_true", help="do not run the reset script")
    parser.add_argument("--frequency", type=int, help="override RF frequency in Hz")
    parser.add_argument("--bandwidth", type=int, choices=sorted(BANDWIDTHS), help="override bandwidth in Hz")
    parser.add_argument("--spreadingfactor", type=int, choices=range(5, 13), help="override SF")
    parser.add_argument("--codingrate", type=int, choices=sorted(CODING_RATES), help="override CR")
    parser.add_argument("--tx-power", type=int, help="override TX power in dBm")
    parser.add_argument("--channel-mode", choices=("service", "multi_sf"), help="override RX channel mode")
    parser.add_argument("--spi-path", help="override SPI device path")
    parser.add_argument("--if-frequency", type=int, help="override IF offset in Hz")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.rx_only and args.tx_only:
        print("error: --rx-only and --tx-only are mutually exclusive", file=sys.stderr)
        return 2

    try:
        profile = BoardProfile.from_ini(args.config)
        overrides = {}
        if args.frequency is not None:
            overrides["frequency"] = args.frequency
        if args.bandwidth is not None:
            overrides["bandwidth"] = args.bandwidth
        if args.spreadingfactor is not None:
            overrides["spreadingfactor"] = args.spreadingfactor
        if args.codingrate is not None:
            overrides["codingrate"] = args.codingrate
        if args.tx_power is not None:
            overrides["tx_power"] = args.tx_power
        if args.channel_mode is not None:
            overrides["channel_mode"] = args.channel_mode
        if args.spi_path is not None:
            overrides["spi_path"] = args.spi_path
        if args.if_frequency is not None:
            overrides["if_frequency"] = args.if_frequency
        if overrides:
            profile = profile.with_overrides(**overrides)
        payload = _payload_from_args(args)
    except (ConfigError, OSError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    print(f"Board profile: {profile.path}")
    print(
        f"SPI {profile.spi_path}, radio {'SX1250' if profile.radio_type == structs.LGW_RADIO_TYPE_SX1250 else profile.radio_type}, "
        f"RF chain {profile.rf_chain}"
    )
    print(
        f"Channel: {profile.frequency} Hz, BW {profile.bandwidth} Hz, SF{profile.spreadingfactor}, "
        f"CR 4/{profile.codingrate}, {profile.channel_mode} mode (IF chain {profile.if_chain}), "
        f"TX {profile.tx_power} dBm, ~{profile.bitrate_bps:.0f} bit/s"
    )
    print(f"TX gain LUT: {len(profile.tx_gain_lut)} entries, {profile.tx_gain_lut[0].rf_power}-{profile.tx_gain_lut[-1].rf_power} dBm")

    if not args.no_reset:
        try:
            _run_reset(profile, "start")
        except (OSError, RuntimeError) as exc:
            print(f"reset failed: {exc}", file=sys.stderr)
            return 1

    hal: Hal | None = None
    started = False
    try:
        hal = Hal(args.lib)
        print(f"Loaded HAL: {hal.path}")
        print(f"HAL version: {hal.version()}")

        print("Configuring HAL...")
        hal.configure(profile)
        print("Starting HAL (this can take several seconds)...")
        hal.start()
        started = True
        print(f"HAL started, temperature: {hal.temperature():.1f} C")

        if not args.rx_only:
            for index in range(max(1, args.tx_count)):
                if index:
                    time.sleep(max(0.0, args.tx_interval))
                _transmit(hal, profile, payload)

        if not args.tx_only:
            _receive(hal, max(0.0, args.rx_timeout))
        return 0
    except (HalError, OSError, TimeoutError, ValueError) as exc:
        print(f"smoke test failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if hal is not None and started:
            try:
                hal.stop()
                print("HAL stopped")
            except HalError as exc:
                print(f"warning: HAL stop failed: {exc}", file=sys.stderr)
        if not args.no_reset:
            try:
                _run_reset(profile, "stop")
            except (OSError, RuntimeError) as exc:
                print(f"warning: reset stop failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
