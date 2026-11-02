# Testing on a Raspberry Pi

This guide covers hardware bring-up and Reticulum integration on a Raspberry
Pi with a RAK2287 (SX1302 + SX1250): OS setup, SPI/GPIO permissions, building
the patched `libloragw.so`, raw LoRa transmit and receive, and the in-process
Reticulum interface with two-node tests.

> **Scope:** sections 4–7 cover raw LoRa bring-up with the smoke test.
> Section 8 covers the in-process Reticulum interface and two-node tests.

## 1. What you need

- A Raspberry Pi 3, 4, or 5. A 64-bit Raspberry Pi OS (Trixie or newer) is
  recommended. On Bookworm, install `gpiod` from `bookworm-backports` so the
  reset script gets libgpiod v2.
- A RAK2287 module and a carrier that exposes it over SPI. Most RAK Raspberry Pi
  HATs use `/dev/spidev0.0` and GPIO 17 for SX1302 reset; the RAK2004 Pi HAT
  Ver B uses GPIO 25.
- A **connected antenna or dummy load before any transmission.** Never run a
  TX test with an open RF connector.
- A second LoRa node for receive/transmit verification. The easiest option is a
  second SX1302 board running this same smoke test. A plain LoRa transmitter
  configured to the same PHY settings also works.
- A stable 5 V supply. Undervoltage causes intermittent SPI and reset failures.

For EU868, keep to 868.1 MHz, 14 dBm, and the regional duty-cycle limit (1% for
this band) while testing.

## 2. Prepare Raspberry Pi OS

Update the system and install build and GPIO tooling:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y git build-essential python3-venv python3-pip gpiod i2c-tools
```

On Bookworm, `gpiod` is libgpiod v1. The reset script needs v2, so install it
from backports after enabling `bookworm-backports`:

```bash
sudo apt install -t bookworm-backports gpiod
```

Check the version with `gpioset --version`. Trixie and newer already ship v2.

Enable SPI, and optionally I2C:

```bash
sudo raspi-config nonint do_spi 0
sudo raspi-config nonint do_i2c 0
```

`do_spi 0` means "enable" in `raspi-config`'s non-interactive mode. On
Bookworm the equivalent manual setting is `dtparam=spi=on` in
`/boot/firmware/config.txt`.

Add your user to the device groups, then log out and back in (or reboot):

```bash
sudo usermod -aG spi,gpio,i2c "$USER"
sudo reboot
```

After reboot, verify the devices:

```bash
ls -l /dev/spidev0.0
gpioinfo | head -20
i2cdetect -y 1
```

`/dev/spidev0.0` must exist. I2C is only needed if you later use a board with an
STTS751 temperature sensor; the RAK2287 does not have one.

If the device nodes exist but your user still cannot open them, check the group
ownership and confirm the new group membership is active in a fresh login:

```bash
id
groups
```

## 3. Clone, build, and run the unit tests

```bash
git clone https://github.com/shvelo/sx1302_rns.git
cd sx1302_rns
git checkout feature/reticulum-sx1302-interface
```

### Automated setup

`scripts/setup_pi.sh` performs the steps in sections 2 and 3 for you: it
installs system packages, enables SPI and I2C, adds your user to the `spi`,
`gpio`, and `i2c` groups, creates `.venv`, installs the package and Reticulum,
builds the patched HAL, runs the unit tests, and installs the Reticulum shim.

```bash
./scripts/setup_pi.sh
```

Run it as your normal user, not with `sudo`; it uses `sudo` for the system
steps. If it changed SPI/I2C or group membership, reboot or log out and back
in, check `/dev/spidev0.0`, then re-run with `--skip-system`. Use `--skip-build`,
`--skip-tests`, `--skip-system`, or `--skip-interface` to skip individual
steps, and `--configdir` if your Reticulum configuration is not in
`~/.reticulum`.

The script prints the Reticulum stanza to add and the commands for the first
hardware test. The rest of this section describes the manual equivalent.

### Manual setup

Create a virtual environment and install the package, Reticulum, and pytest:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e . pytest
```

The project pins Reticulum 1.5.7. The interface tests need it; tests that need
hardware or RNS are skipped automatically when those are unavailable.

Build the patched shared HAL. This copies the vendored tree to `build/`, applies
`patches/*.patch`, compiles with `-fPIC`, links `build/libloragw.so`, and runs
the ABI and load checks:

```bash
./scripts/build_hal.sh
```

The build takes a minute or two on a Pi. A successful run ends with
`==> Built .../build/libloragw.so`.

Run the hardware-independent tests:

```bash
python3 -m pytest -q
```

They should all pass without a radio attached.

## 4. Confirm the reset line

The example profile sets `reset_gpio = 25` in `[board]`, which is the SX1302
reset line on the RAK2004 Pi HAT Ver B. It runs
`scripts/reset_rak2287.sh start` before loading the HAL.

Most other RAK2287 Pi HATs use GPIO 17. If your carrier is one of those, change
`reset_gpio` in the profile. You can also override it per run: the
`SX1302_RESET_GPIO` environment variable takes precedence over the profile.

**Check your HAT revision.** If the HAL reports
`chip version is 0x05 (v0.5)` followed by
`Failed to set SX1250_0 in STANDBY_RC mode`, the reset pulse is not reaching the
SX1302. Fully power-cycle the Pi (unplug power for about 10 seconds), then check
that `reset_gpio` matches your HAT.

Test it manually first:

```bash
./scripts/reset_rak2287.sh start
```

The script addresses the reset line by GPIO controller and line offset through
libgpiod v2, which avoids the legacy sysfs numbering entirely. On a Pi 5 the
script uses `gpiochip4`.

To drive a different line manually:

```bash
SX1302_RESET_GPIO=17 ./scripts/reset_rak2287.sh start
```

If the reset script reports a permission error, re-check the `gpio` group and
make sure you are not relying on an old login session.

> **libgpiod requirement:** the script requires libgpiod v2 (`gpioset` with
> `--toggle`). Raspberry Pi OS Trixie and newer ship it. On Bookworm, install
> `gpiod` from backports as shown in section 2.
>
> `SX1302_RESET_WAIT` is in seconds. The script converts it to integer
> milliseconds because v2 does not accept fractional periods.
>
> The RAK2287's SX1302 reset input is **active high**: the script drives it
> high for one wait period to assert reset, then low again to let the chip run.

If the script fails, it prints the current `gpioinfo` entry for the line. A
consumer name other than `unused` means a kernel driver or overlay already owns
the pin.

## 5. First hardware run: start and listen

Start with RX only. This exercises reset, SPI, HAL configuration, `lgw_start()`,
and the optional-temperature-sensor patch without transmitting:

```bash
./scripts/smoke_test.py \
  --config examples/rak2287-eu868.conf \
  --rx-only \
  --rx-timeout 15
```

A healthy start looks roughly like:

```text
Board profile: .../examples/rak2287-eu868.conf
SPI /dev/spidev0.0, radio SX1250, RF chain 0
Channel: 868100000 Hz, BW 125000 Hz, SF9, CR 4/5, service mode (IF chain 8), TX 14 dBm, ~1758 bit/s
TX gain LUT: 16 entries, 12-27 dBm
Reset start: .../scripts/reset_rak2287.sh start
Loaded HAL: .../build/libloragw.so
HAL version: ...
Configuring HAL...
Starting HAL (this can take several seconds)...
HAL started, temperature: 25.0 C
Listening for 15.0 s...
No packets received.
HAL stopped
```

`temperature: 25.0 C` is expected: the patch treats a missing STTS751 as
unavailable and returns a safe fixed value.

If the HAL prints a lot of diagnostic output, that is normal for the upstream
Semtech code. The important line is `HAL started`.

## 6. Transmit test

Connect the antenna first.

```bash
./scripts/smoke_test.py \
  --config examples/rak2287-eu868.conf \
  --tx-only \
  --payload "sx1302 raw lora test"
```

The script prints the airtime, payload hex, and waits for the transmission to
complete:

```text
TX 20 bytes, 76 ms airtime, freq=868100000 bw=125000 sf=9 cr=4/5 power=14 dBm
   payload: 73783133303220726177206c6f72612074657374  |sx1302 raw lora test|
TX complete
```

A successful `lgw_send()` proves the HAL accepted the packet, but it does **not**
prove RF output. Confirm the signal with a second LoRa receiver or a spectrum
analyzer.

Repeating transmissions is useful for checking stability:

```bash
./scripts/smoke_test.py \
  --config examples/rak2287-eu868.conf \
  --tx-only \
  --tx-count 10 \
  --tx-interval 5
```

Respect the EU868 duty cycle. A 5-second interval is still aggressive for a 1%
band; use it only for short bench tests.

## 7. Receive test from a second node

Run the receiver on the Pi:

```bash
./scripts/smoke_test.py \
  --config examples/rak2287-eu868.conf \
  --rx-only \
  --rx-timeout 120
```

Then transmit from the second node. If it is another clone of this repository
on a second Pi:

```bash
./scripts/smoke_test.py \
  --config examples/rak2287-eu868.conf \
  --tx-only \
  --payload "hello from node b"
```

The receiver should print metadata and the payload:

```text
RX #1: CRC_OK if=8 rf=0 freq=868100000 bw=0x04 sf=9 cr=0x01 rssi=-42.0/-65.0 dB snr=9.5 dB size=16
   payload: 68656c6c6f2066726f6d206e6f64652062  |hello from node b|
```

For a non-SX1302 transmitter, configure it to match the profile exactly:

| Setting | Value |
|---|---|
| Frequency | 868.100 MHz |
| Bandwidth | 125 kHz |
| Spreading factor | SF9 |
| Coding rate | 4/5 |
| Sync word | Private (`0x12`) |
| Header | Explicit |
| CRC | Enabled |
| Preamble | 8 symbols |
| Payload | Any raw bytes up to 255 |

The `lorawan_public = False` setting in the board profile selects the private
LoRa sync word. If the transmitter uses the public LoRaWAN sync word (`0x34`),
the receiver will not decode it.

## 8. Run Reticulum with the interface

The interface core and the Reticulum shim are unit tested, but run the full
stack on the Pi before relying on it. From the environment created in section 3:

```bash
. .venv/bin/activate
./scripts/install_interface.sh --editable
```

`install_interface.sh` symlinks `interfaces/SX1302.py` into
`~/.reticulum/interfaces/` and installs an editable package so the shim's
absolute import works.

Add a stanza to `~/.reticulum/config` under the `[interfaces]` section. The
full commented example is in `examples/reticulum-sx1302.conf`:

```ini
[interfaces]

  [[SX1302 LoRa]]
    type = SX1302
    enabled = yes
    board_profile = /home/pi/sx1302_hal/examples/rak2287-eu868.conf
```

Start Reticulum and inspect the interface:

```bash
rnsd -v
rnstatus
```

Expected:

- The interface appears as `SX1302 LoRa` and becomes online a few seconds
  after startup, once `lgw_start()` finishes.
- The hardware MTU is 254 bytes.
- The on-air bitrate is about 1758 bit/s for BW 125 kHz, SF9, CR 4/5.
- `rnstatus` counts RX/TX bytes and reports RSSI/SNR for received packets.

Reticulum and the smoke test cannot run at the same time: the HAL has global
state and only one process can claim the concentrator.

### Confirm transmit without a peer

Reticulum only calls `process_outgoing()` when it has a packet to send, so a
newly started node with no peers legitimately shows `↑0 B`. To exercise the
transmit path, ask for a path to a destination hash that does not exist.
Reticulum broadcasts the path request on every interface:

```bash
rnpath -w 3 00112233445566778899aabbccddeeff
rnstatus
```

`rnpath` prints `Path not found` and exits non-zero, which is expected. The
SX1302 interface should now show roughly 50-70 bytes transmitted; the exact
size depends on whether the instance is a transport node. The frame is
broadcast on air, so do not loop this command: EU868 has a 1% duty cycle.

This exercises the same path a real packet uses. The CLI client connects to the
running shared instance, and the instance hands the frame to
`SX1302Interface.process_outgoing()`.

### Test with a second SX1302 node

Both nodes must use the same frequency, bandwidth, SF, CR, and sync word, and
must agree on IFAC: either neither node has a passphrase, or both use the same
one. The example profile already matches on the radio side.

On node B, start the probe responder. It announces one destination and answers
probes:

```bash
. .venv/bin/activate
python scripts/rns_echo.py
```

It prints the name and hash:

```text
Name:             sx1302.echo
Destination hash: f0d91a8400b2b1ecfccfe29375443258
Identity hash:    <f80eddcf348fa097b41f83fe41fe8d3d>

Probe this node from the other end with:
  rnprobe sx1302.echo f0d91a8400b2b1ecfccfe29375443258
```

On node A, wait a few seconds for the announce and check what arrived:

```bash
rnstatus -A -t
rnpath -t
```

- `rnstatus -A -t` should show received bytes and an announce count on the
  SX1302 interface.
- `rnpath -t` should list the new destination one hop away.

Then probe it. In RNS 1.5.7 the destination name comes before the hash:

```bash
rnprobe sx1302.echo <destination_hash> -n 1 -s 32 -w 5 -t 20
```

A working link prints `Valid reply from <hash>`, the round-trip time, the hop
count, and RSSI/SNR. The responder also prints every packet it receives with
the RSSI and SNR reported by its own interface.

Swap the roles to test the other direction. Keep probes small and infrequent:
a 32-byte probe is roughly 0.3 s of airtime each way at SF9, and EU868 allows
1% duty cycle (about 36 s per hour) at 868.1 MHz.

### Test fragmentation with rncp

Plain Reticulum packets are not fragmented for the interface, so a direct
packet must fit the 254-byte hardware MTU. Link traffic is fragmented. Use
`rncp` to verify a payload larger than the MTU travels and reassembles:

On node B, print the listener hash, then start the listener:

```bash
rncp --listen -n -p
mkdir -p /tmp/rncp-in
rncp --listen -n -b 0 --save /tmp/rncp-in
```

`-b 0` announces once at startup; without it the listener does not announce.

On node A, send a random 1 KB file:

```bash
head -c 1024 /dev/urandom > /tmp/payload.bin
rncp /tmp/payload.bin <listener_hash>
```

The transfer uses a Reticulum link and resource segmentation. Confirm that
`/tmp/rncp-in/payload.bin` exists and matches the source. One kilobyte takes
roughly ten seconds of airtime at SF9, so do not repeat it in a loop.

## 9. Suggested test order

Run these in order and stop at the first failure:

1. `python3 -m pytest -q` — no hardware required.
2. `./scripts/smoke_test.py --rx-only --rx-timeout 15` — reset, SPI, HAL start,
   temperature fallback.
3. `./scripts/smoke_test.py --tx-only --payload "test"` — HAL accepts a TX.
4. Two-node receive test — verifies RF, PHY settings, and CRC.
5. Run `scripts/rns_echo.py` on one node and `rnsd` on the other; use
   `rnpath` and `rnprobe` from the `rnsd` node — verifies announces, path
   discovery, proofs, and PHY statistics through Reticulum. Do not run `rnsd`
   on the `rns_echo` node: `rns_echo` hosts its own Reticulum instance and owns
   the HAL.
6. `rncp` a 1 KB file between the nodes — verifies link setup and
   fragmentation over the interface.
7. Repeat step 2 after a cold boot — confirms reset and configuration are
   reproducible.
8. Long receive soak, for example `--rx-only --rx-timeout 600` — checks for
   stuck state and descriptor leaks.
9. Try a second PHY setting, for example `--spreadingfactor 7` on both nodes,
   to confirm the profile is not accidentally hard-coded.

For 250 kHz or 500 kHz bandwidth use service mode (the default). The
`multi_sf` fallback only supports 125 kHz:

```bash
./scripts/smoke_test.py --config examples/rak2287-eu868.conf \
  --channel-mode multi_sf --spreadingfactor 9 --rx-only
```

Only one process can use the HAL at a time. Do not run two smoke tests against
the same concentrator concurrently.

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Permission denied: /dev/spidev0.0` | User not in `spi` group, or old login | Add the group and log out/in; check `id`. |
| `/dev/spidev0.0` missing | SPI disabled | Run `sudo raspi-config nonint do_spi 0` and reboot. |
| `chip version is 0x05 (v0.5)` and `Failed to set SX1250_0 in STANDBY_RC mode` | Reset pulse is not reaching the SX1302; on a RAK2004 Pi HAT Ver B the reset is GPIO 25, not 17 | Power-cycle the Pi, then check `reset_gpio` in the board profile (25 for Ver B, 17 for most other HATs). If that fails, check the HAT schematic and confirm the line with `gpioinfo`. |
| `chip version is 0x00` or `Failed to set SX1250 in STANDBY_RC` | No SPI response, bad reset, poor power, or HAT not seated | Reseat the module, check 5 V supply, run the reset script, and confirm the reset GPIO with `gpioinfo`. |
| `lgw_start failed with status -1` | HAL rejected the board configuration | Read the HAL lines above the error; check SPI path, radio type, RF chain, LUT, and reset. |
| `lgw_board_setconf failed` | Invalid board profile value | Re-check `[board]`; `clksrc` must be 0 or 1 and the SPI path must be valid. |
| `txgain` or LUT error | Missing, unsorted, or out-of-range entries | Keep entries sorted by `rf_power`; `pwr_idx` is 0-22 and `mix_gain` is 5-15 for this profile. |
| `tx_power is below the TX gain LUT minimum` | TX power below the first LUT entry | Use at least 12 dBm with the RAK2287 EU868 LUT. |
| `lgw_send failed` or TX timeout | Radio still emitting, or TX path not ready | Retry; check antenna and power; make sure only one process uses the HAL. |
| No packets received | PHY mismatch, sync word mismatch, CRC disabled, or antenna issue | Match frequency, BW, SF, CR, private sync word, explicit header, CRC, and preamble on both nodes. |
| `gpioset: unrecognized option '--mode=wait'` or `invalid option -- 't'` | libgpiod v1 or an old reset script | Install libgpiod v2 (`gpiod`), update the script, and check `gpioset --version`. |
| Reset script fails on Pi 5 | Wrong GPIO chip, libgpiod missing, or pin claimed by an overlay | The script uses `gpiochip4` and libgpiod v2; check `gpioinfo gpiochip4` for the consumer. |
| `libloragw.so not found` | HAL not built or wrong working directory | Run `./scripts/build_hal.sh` or set `SX1302_HAL_LIB=/path/to/libloragw.so`. |
| Temperature is always 25.0 C | No STTS751 on the board | Expected with the optional-sensor patch. |

To see what the kernel thinks is connected:

```bash
dmesg | grep -i -E 'spi|gpio|undervoltage'
ls -l /dev/spidev*
gpioinfo
```

## 11. Raspberry Pi 5 notes

- The GPIO controller is `gpiochip4` rather than `gpiochip0`; the reset script
  detects this.
- Sysfs GPIO is often unavailable or uses offset global numbers; the script
  drives the line through libgpiod v2 instead.
- Use a 64-bit Raspberry Pi OS. A 32-bit userland works but is not the primary
  test target.
- SPI device numbering is unchanged (`/dev/spidev0.0`).

## 12. What this does not validate yet

- Multi-hour soak behaviour. Cold start, warm restart and repeated `rnsd`
  stop/start have been exercised separately; a long run is still a Phase 5
  check.
- Multiple SX1302 instances in one process. The HAL has global state.
- Throughput, latency and packet loss at other SF/BW settings.
- Raw-energy CSMA/CAD. The optional duty-cycle and LBT gates are covered by
  hardware-independent tests; their on-air behaviour at 868.1 MHz should be
  spot-checked on the Pi.

## 13. Recording a result

When a test passes or fails, capture:

```bash
uname -a
cat /etc/os-release | head -3
./scripts/smoke_test.py --version 2>/dev/null || true
python3 -c 'from sx1302_reticulum.hal import Hal; print(Hal().version())'
```

Also save the full smoke-test output, the board profile used, the payload hex,
and the RX RSSI/SNR values. That is enough to reproduce most bring-up problems.

## 14. Further documentation

- [Configuration reference](configuration-reference.md) — every interface and board profile option.
- [Troubleshooting guide](troubleshooting.md) — expanded symptom/cause/fix table, logging, and hardware checklist.
- [systemd unit](systemd-unit.md) — optional service file for running `rnsd` with the SX1302 interface.
