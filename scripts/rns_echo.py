#!/usr/bin/env python3
"""Announce a Reticulum destination and answer probes, for hardware tests.

Run this on one node and use ``rnpath``/``rnprobe`` from the other node to
verify announces, path discovery, proofs and PHY statistics over the radio.
The destination accepts probes (``PROVE_ALL``) and prints every packet it
receives, including the RSSI and SNR reported by the interface.
"""

from __future__ import annotations

import argparse
import sys
import time

import RNS


DEFAULT_NAME = "sx1302.echo"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Announce a Reticulum destination and answer probes. Run this on "
            "one node, then use rnpath and rnprobe from the other."
        )
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="DIR",
        help="Reticulum configuration directory (default: the standard location)",
    )
    parser.add_argument(
        "--name",
        default=DEFAULT_NAME,
        help="full destination name in dotted notation (default: %(default)s)",
    )
    parser.add_argument(
        "--identity",
        default=None,
        metavar="PATH",
        help="reuse a saved identity so the destination hash stays stable",
    )
    parser.add_argument(
        "--save-identity",
        default=None,
        metavar="PATH",
        help="save the generated identity to PATH",
    )
    parser.add_argument(
        "--announce-interval",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="re-announce every SECONDS; 0 announces once at startup (default)",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.announce_interval < 0:
        print("error: --announce-interval must not be negative", file=sys.stderr)
        return 2

    identity = None
    if args.identity:
        try:
            identity = RNS.Identity.from_file(args.identity)
        except Exception as exc:
            print(f"error: could not load identity: {exc}", file=sys.stderr)
            return 1
        if identity is None:
            print(f"error: could not load identity from {args.identity}", file=sys.stderr)
            return 1
    if identity is None:
        identity = RNS.Identity()

    if args.save_identity:
        identity.to_file(args.save_identity)

    try:
        RNS.Reticulum(
            configdir=args.config,
            loglevel=RNS.LOG_DEBUG if args.verbose else RNS.LOG_NOTICE,
        )
    except Exception as exc:
        print(f"error: could not start Reticulum: {exc}", file=sys.stderr)
        return 1

    app_name, aspects = RNS.Destination.app_and_aspects_from_name(args.name)
    destination = RNS.Destination(
        identity,
        RNS.Destination.IN,
        RNS.Destination.SINGLE,
        app_name,
        *aspects,
    )
    destination.set_proof_strategy(RNS.Destination.PROVE_ALL)

    def packet_received(data, packet):
        details = []
        if packet.rssi is not None:
            details.append(f"RSSI {packet.rssi:.1f} dBm")
        if packet.snr is not None:
            details.append(f"SNR {packet.snr:.1f} dB")
        suffix = f" ({', '.join(details)})" if details else ""
        print(f"packet: {len(data)} bytes{suffix}")
        sys.stdout.flush()

    destination.set_packet_callback(packet_received)

    print(f"Name:             {args.name}")
    print(f"Destination hash: {destination.hexhash}")
    print(f"Identity hash:    {RNS.prettyhexrep(identity.hash)}")
    print("")
    print("Probe this node from the other end with:")
    print(f"  rnprobe {args.name} {destination.hexhash}")
    print("")
    sys.stdout.flush()

    destination.announce()
    last_announce = time.monotonic()

    try:
        while True:
            time.sleep(0.5)
            if (
                args.announce_interval
                and time.monotonic() - last_announce >= args.announce_interval
            ):
                destination.announce()
                last_announce = time.monotonic()
    except KeyboardInterrupt:
        print("")

    return 0


if __name__ == "__main__":
    sys.exit(main())
