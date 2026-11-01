# SX1302 Reticulum Interface

An in-process [Reticulum](https://reticulum.network/) interface for Semtech
SX1302/SX1250 LoRa concentrators over SPI.

The project turns a LoRaWAN gateway concentrator into a single raw LoRa node
for Reticulum. It does **not** run a packet forwarder, does not speak GWMP, and
does not emulate RNode KISS. Instead, a Reticulum external interface loads the
SX1302 HAL as a shared library through `ctypes` and drives the radio directly.

> **Status:** Phases 0–4 complete. Two RAK2287 nodes running Reticulum 1.5.7
> exchange announces, path requests, `rnprobe` round trips in both directions,
> and a 1 KB `rncp` transfer that exercises link setup and fragmentation.
> Restart, soak and packaging checks remain. See [`PLAN.md`](PLAN.md) and the
> [Raspberry Pi test guide](docs/raspberry-pi-testing.md).

## Documentation

- [`docs/raspberry-pi-testing.md`](docs/raspberry-pi-testing.md) — step-by-step
  hardware bring-up on a Raspberry Pi with a RAK2287: OS setup, SPI/GPIO
  permissions, build, TX/RX smoke tests, and troubleshooting.
- [`PLAN.md`](PLAN.md) — phased implementation plan, architecture, risks, and
  acceptance criteria.
- [`SX1302_RETICULUM_FINDINGS.md`](SX1302_RETICULUM_FINDINGS.md) — research
  notes covering the HAL, Reticulum interfaces, hardware constraints, and the
  reasoning behind the chosen approach.
- [`vendor/README.md`](vendor/README.md) — provenance and patch policy for the
  vendored upstream tree.

## Repository layout

```text
interfaces/            Reticulum external interface shim (SX1302.py)
sx1302_reticulum/      Python package: config, ctypes HAL, worker, interface
patches/               Minimal source patches applied during the HAL build
scripts/               Build, ABI check, smoke test, install helpers
tests/                 Unit tests that do not require hardware
examples/              Example Reticulum and board configuration
docs/                  Hardware bring-up and operator documentation
vendor/sx1302_hal/     Upstream Semtech sx1302_hal v2.1.0
```

## Requirements

- Linux host with SPI, I2C, and GPIO access.
- An SX1302 concentrator board with one or more SX1250 radios.
- Python 3.9+.
- Reticulum 1.5.7 (pinned in `pyproject.toml`).
- A board-specific TX gain LUT and reset/power configuration.

## Quick start

```bash
git clone https://github.com/shvelo/sx1302_rns.git
cd sx1302_rns
./scripts/setup_pi.sh
```

On a Raspberry Pi the script installs the system packages, enables SPI/I2C,
creates `.venv`, builds the patched HAL, runs the tests, and installs the
Reticulum shim. It prints the stanza to add and the commands for the first
hardware test.

For the manual equivalent:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e . pytest

./scripts/build_hal.sh
./scripts/smoke_test.py --config examples/rak2287-eu868.conf --rx-only
./scripts/install_interface.sh --editable
```

Then add a `type = SX1302` stanza under `[interfaces]` in your Reticulum
configuration; see `examples/reticulum-sx1302.conf`. The stanza needs the path
to a board profile such as `examples/rak2287-eu868.conf`. See `PLAN.md` and the
[Pi guide](docs/raspberry-pi-testing.md) for the full sequence.

## Important limitations

- The SX1302 HAL has global state: only one interface instance per process.
- The radio configuration is fixed at startup; runtime retuning is not
  supported in the first release.
- There is no built-in CSMA/CAD or duty-cycle enforcement. Operators are
  responsible for regional airtime rules.
- The HAL TX payload limit is 255 bytes, so the interface advertises a
  254-byte hardware MTU and relies on Reticulum fragmentation. If IFAC is
  enabled, 16 bytes are reserved for the IFAC tag and the effective MTU is 238.
- Board support is hardware-specific: temperature sensor, reset GPIOs, and TX
  gain LUT must match the actual board.

## Upstream license

The vendored Semtech code retains its original Revised BSD license. See
`vendor/sx1302_hal/LICENSE.TXT`.
