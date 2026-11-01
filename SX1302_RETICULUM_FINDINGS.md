# SX1302 + Reticulum — Session Findings

Date: 2026-10-30
Repository: `/home/nick/development/sx1302_hal`
Upstream: `git@github.com:Lora-net/sx1302_hal.git`, version `2.1.0`

## 1. Question

Can the code in this repository be used to run the SX1302 over SPI as an
individual LoRa node for Reticulum?

Follow-up questions explored:

- Can Reticulum use the existing daemon over TCP?
- Can a custom Reticulum interface drive the SX1302 without a separate daemon?

## 2. Repository summary

This is Semtech's upstream SX1302 **LoRaWAN gateway** project. It contains:

- `libloragw/` — the SX1302 hardware abstraction library (HAL), built as
  `libloragw.a`, plus raw LoRa test programs.
- `packet_forwarder/` — a LoRaWAN gateway packet forwarder using the Semtech
  UDP GWMP protocol.
- `util_net_downlink/` — a UDP GWMP downlink/test utility.
- `util_chip_id/`, `util_boot/`, `util_spectral_scan/` — gateway utilities.
- `mcu_bin/` — STM32 MCU firmware for USB gateways, not used by the SPI HAL.
- `tools/reset_lgw.sh` — CoreCell GPIO reset/power script.

There is **no Reticulum, RNode, KISS, or raw-node interface code** anywhere in
the repository.

## 3. What the repository provides

| Capability | Location / detail |
|---|---|
| SPI transport (`/dev/spidev0.0`, mode 0) | `libloragw/src/loragw_spi.c` |
| HAL lifecycle | `lgw_board_setconf`, `lgw_rxrf_setconf`, `lgw_rxif_setconf`, `lgw_demod_setconf`, `lgw_txgain_setconf`, `lgw_start`, `lgw_stop` |
| Raw LoRa RX/TX | `lgw_receive`, `lgw_send`, `lgw_status` |
| Raw LoRa parameters | Frequency, BW 125/250/500 kHz, SF5–12, CR 4/5–4/8, explicit/implicit header, CRC, preamble, polarity, TX power |
| Private LoRa sync word | `lorawan_public = false` → private sync (0x12) in `sx1302_lora_syncword()` |
| Raw LoRa test programs | `libloragw/tst/test_loragw_hal_tx.c`, `test_loragw_hal_rx.c` |
| Gateway forwarder | `packet_forwarder/src/lora_pkt_fwd.c` |

The HAL is payload-agnostic, so raw Reticulum frames can pass through it. The
PHY settings Reticulum needs (private sync, explicit header, CRC on, one fixed
frequency/BW/SF/CR) are all expressible through the HAL API.

## 4. What is missing for Reticulum

Reticulum's standard LoRa interface is `RNodeInterface`, which speaks the RNode
KISS protocol. This repository implements none of it.

Missing pieces:

1. **RNode KISS protocol**
   - `CMD_DETECT` / `DETECT_RESP`
   - `CMD_FW_VERSION` (Reticulum requires >= 1.52)
   - `CMD_PLATFORM`, `CMD_MCU`
   - `CMD_FREQUENCY`, `CMD_BANDWIDTH`, `CMD_TXPOWER`, `CMD_SF`, `CMD_CR`,
     `CMD_RADIO_STATE`
   - KISS framing (`0xC0 0x00 ... 0xC0`) and escaping (`0xDB/0xDC/0xDD`)
   - Echoing configured radio values, because `RNodeInterface.validateRadioState()`
     compares frequency, bandwidth, TX power, SF, and radio state.

2. **RNode packet splitting**
   - Reticulum advertises `HW_MTU = 508`.
   - RNode firmware splits payloads larger than 254 bytes into two LoRa frames
     with a 1-byte header (high nibble sequence, low nibble flags,
     `FLAG_SPLIT = 0x01`).
   - The SX1302 HAL caps TX payloads at 255 bytes (`loragw_hal.c:1391`).

3. **CSMA / airtime management**
   - RNode firmware does DIFS + random backoff + CAD and reports airtime and
     channel statistics.
   - `lgw_send()` transmits without CSMA. Optional LBT requires an extra SX1261
     companion radio (CoreCell design).

4. **RSSI/SNR and status reporting**
   - `lgw_pkt_rx_s` provides `rssic`, `rssis`, and `snr`; the RNode KISS status
     messages must be synthesized.

5. **Runtime configuration handling**
   - `lgw_rxif_setconf()` refuses to change configuration while running
     (`loragw_hal.c:552`).
   - Reticulum sends config commands quickly and validates shortly afterwards;
     restarting the HAL per command is too slow.
   - Preconfigure the HAL at startup and have the interface echo the values.

## 5. Reticulum interface facts discovered

### 5.1 RNode TCP mode

- `RNodeInterface` supports `port = tcp://host`.
- Reticulum is the TCP **client**; the remote side must be a TCP **server**.
- The TCP port is hardcoded to **7633** (`RNodeInterface.py`, `TCPConnection.TARGET_PORT`).
- The current code passes the entire string after `tcp://` to
  `getaddrinfo(host, 7633)`. Therefore `tcp://host:port` does not work as
  expected; use `tcp://host` and port 7633, or put a port forward in front.
- The TCP peer must speak the same RNode KISS protocol as the serial interface.

### 5.2 External interface modules

Reticulum supports user-supplied interface modules without patching RNS:

- `RNS/Reticulum.py` sets `interfacepath = configdir + "/interfaces"`
  (typically `~/.reticulum/interfaces`).
- For a config `type = SX1302`, Reticulum looks for
  `<interfacepath>/SX1302.py`.
- The file is `exec()`'d with globals `Interface` and `RNS`.
- The module must define a global `interface_class` pointing to the interface
  class.
- This is the preferred integration path for a custom interface.

### 5.3 Other Reticulum TCP/pipe interfaces

- `TCPClientInterface` / `TCPServerInterface` use Reticulum HDLC framing over
  TCP. They are for Reticulum-over-IP links, not a radio driver.
- `PipeInterface` uses Reticulum HDLC over stdin/stdout and spawns a subprocess.
  It is not a daemon, but it is still a subprocess.
- None of these speak RNode KISS or GWMP.

## 6. Existing daemons: why they cannot be used over TCP

### 6.1 `packet_forwarder/lora_pkt_fwd`

- It is a Semtech GWMP **UDP client**.
- It `connect()`s UDP sockets to a LoRaWAN network server
  (`lora_pkt_fwd.c:1576`, `lora_pkt_fwd.c:1635`).
- It never listens on TCP.
- It has no KISS/RNode framing.
- It forwards LoRaWAN uplinks/downlinks as GWMP JSON/binary, not raw LoRa frames.
- It expects a `global_conf.json` with a board-specific TX gain LUT; no such
  config is included in the repository.

### 6.2 `util_net_downlink`

- Binds a UDP socket (`SOCK_DGRAM`).
- Speaks GWMP downlink JSON to the packet forwarder.
- Not a TCP server and not RNode KISS.

### 6.3 `util_chip_id`, `util_boot`, `util_spectral_scan`

Unrelated to raw LoRa packet transport.

### 6.4 Indirect GWMP translator

Technically possible:

- Run `lora_pkt_fwd` on one channel with `lorawan_public = false`.
- Write a fake LoRaWAN network server that speaks GWMP on one side and RNode
  KISS on TCP 7633 on the other.

Not recommended:

- Two daemons and two protocols to debug.
- Still need RNode split/reassembly, config echo, RSSI/SNR, and CSMA.
- Still subject to LoRaWAN gateway semantics (channel plan, jitqueue, downlink
  JSON, stats).
- Still needs a board-specific TX gain LUT.

## 7. Hardware / platform constraints

These apply regardless of whether a daemon or an in-process interface is used.

1. **Temperature sensor requirement**
   - On SPI builds, `lgw_start()` requires an I2C STTS751 temperature sensor.
   - Without it: `ERROR: no temperature sensor found.` and startup fails
     (`loragw_hal.c:1116`).
   - `lgw_receive()` calls `lgw_get_temperature()` on every fetch
     (`loragw_hal.c:1292`).
   - Many SX1302 modules do not have this sensor/wiring. A small HAL patch is
     needed to make it optional or return a fixed temperature.

2. **Reset/power GPIO**
   - `tools/reset_lgw.sh` hardcodes CoreCell GPIOs 23/18/22/13.
   - The HAL does not reset the chip itself; the application must run the reset
     script or replicate the GPIO sequence.

3. **TX gain LUT**
   - `lgw_start()` calibrates radios using `lgw_txgain_setconf()` data.
   - The repository has no default LUT.
   - The test program only sets a single `pwr_idx`.
   - A real board-specific LUT (from the vendor `global_conf.json` or a
     calibration) is needed for correct TX power.

4. **Concentrator RX architecture**
   - 8 multi-SF IF chains (125 kHz only) + 1 LoRa service channel + 1 FSK chain.
   - For a single node, disable all but one IF chain.
   - For BW 125 kHz: use a multi-SF chain with only one SF enabled via
     `lgw_demod_setconf`, or use the service channel.
   - For BW 250/500 kHz: use the LoRa service channel (IF chain 8).
   - The service channel supports one SF/BW at a time.

5. **Half-duplex operation**
   - No RX during TX.
   - `lgw_send()` is non-blocking; poll `lgw_status()` until `TX_FREE` before
     the next TX.
   - `lgw_receive()` must be polled regularly.

6. **Payload size**
   - `lgw_pkt_tx_s.size` is capped at 255 bytes (`loragw_hal.c:1391`).
   - `lgw_pkt_rx_s.payload` is 256 bytes.
   - A custom interface should set `HW_MTU <= 255` so Reticulum fragments at
     the packet layer.

7. **Power/host class**
   - SX1302 + SX1250 is gateway-class power.
   - Requires a Linux host with SPI, I2C, and GPIO.
   - Not a low-power SX127x/SX126x-style node.

8. **Duty cycle**
   - The HAL does not enforce regional duty-cycle limits.
   - Optional LBT requires the SX1261 companion radio.
   - Otherwise the application must enforce airtime limits.

## 8. Chosen direction: custom interface without a daemon

The current direction is an in-process Reticulum interface:

- No separate daemon.
- No TCP/socket IPC.
- Reticulum loads a Python interface module.
- That module loads the C HAL as a shared library (`libloragw.so`) via
  `ctypes` and drives the SX1302 directly.
- The C library runs inside the Reticulum process.

This is the pragmatic "no daemon" approach. Pure Python SPI is not practical:
the SX1302 requires firmware loading, radio calibration, AGC/ARB firmware,
register sequences, and timing handling that are already implemented in
`libloragw`. Reimplementing that in Python would be a large, risky port.

## 9. Proposed architecture

```
Reticulum process
  |
  +-- external interface module: ~/.reticulum/interfaces/SX1302.py
        |
        +-- SX1302Interface(Interface)
              |
              +-- ctypes -> libloragw.so
                    |
                    +-- /dev/spidev0.0
                    +-- I2C (temperature sensor, optional patch)
                    +-- GPIO reset/power (reset_lgw.sh or Python GPIO)
```

### 9.1 Threading model

Use a single radio worker thread that owns all HAL calls:

- `process_outgoing(data)` pushes data onto a TX queue.
- The worker loop:
  1. Polls `lgw_receive()` for RX packets.
  2. Dispatches each CRC-OK packet via `self.owner.inbound(payload, self)`.
  3. If the TX queue is non-empty and `lgw_status()` reports `TX_FREE`, calls
     `lgw_send()` and waits for TX completion.
- This serializes HAL access and naturally handles half-duplex operation.
- Avoid calling `lgw_receive()` and `lgw_send()` concurrently from multiple
  Python threads.

### 9.2 Interface properties

- `HW_MTU = 255` (or 254 for a safety margin).
- `shared_medium = True`.
- `AUTOCONFIGURE_MTU = False`.
- `bitrate` computed from SF/BW/CR using the same formula as RNode:
  `sf * ((4.0 / cr) / (2**sf / (bw / 1000))) * 1000`.
- Optional: `reports_phy_stats = True`, update `r_stat_rssi` and `r_stat_snr`
  from the last RX packet.
- `online = False` until the radio is initialized, then `True`.

## 10. Implementation plan

### Phase 1 — Build a shared HAL

- Build `libloragw.so` with position-independent code:
  - `-fPIC -shared`
  - include `libloragw/inc`, `libloragw`, `libtools/inc`
  - link `libtinymt32` (from `libtools`), `-lm`, `-lrt`
- Verify exported symbols with `nm -D libloragw.so | grep ' T lgw_'`.
- Option A: add a `shared` target to `libloragw/Makefile`.
- Option B: compile the sources directly into a separate `.so` without
  modifying the repository Makefile.

Note: building `libloragw` alone fails to link its tests because `libtinymt32`
is missing. Build `libtools` first (`make -C libtools`) or use the top-level
`make`.

### Phase 2 — Standalone Python hardware smoke test

Before involving Reticulum, write a small Python script that:

1. Runs `reset_lgw.sh start`.
2. Loads `libloragw.so` via `ctypes`.
3. Configures one RF chain and one IF chain.
4. Calls `lgw_start()`.
5. Sends a known raw LoRa packet with `lgw_send()`.
6. Polls `lgw_receive()` for packets.

This de-risks the board-specific issues: SPI access, reset GPIO, temperature
sensor, TX LUT, and antenna/PA configuration.

### Phase 3 — Write the external interface module

Create `~/.reticulum/interfaces/SX1302.py`:

- Define `SX1302Interface(Interface)`.
- Define `interface_class = SX1302Interface`.
- Implement:
  - `__init__`: parse config, load `libloragw.so`, start worker thread.
  - `process_outgoing(data)`: enqueue data for TX.
  - `teardown()` / `detach()`: stop worker, call `lgw_stop()`, optionally run
    `reset_lgw.sh stop`.
  - `__str__`: `SX1302Interface[name]`.
- Use absolute imports inside the module; it is `exec()`'d by Reticulum and
  relative imports will not work.

### Phase 4 — HAL configuration

Recommended startup sequence:

1. Run reset/power GPIO sequence.
2. `lgw_board_setconf`:
   - `lorawan_public = False` (private sync word)
   - `clksrc = 0` (radio A)
   - `full_duplex = False`
   - `com_type = LGW_COM_SPI` (0)
   - `com_path = b"/dev/spidev0.0"`
3. `lgw_rxrf_setconf(0, ...)`:
   - `enable = True`
   - `freq_hz = <Reticulum frequency>`
   - `type = LGW_RADIO_TYPE_SX1250` (5) or the board's actual radio
   - `tx_enable = True`
   - `rssi_offset` / `rssi_tcomp` from the board (0 if unknown)
   - `single_input_mode` per board
4. Disable RF chain 1 (`enable = False`).
5. Configure the RX IF chain:
   - **Preferred:** LoRa service channel, IF chain 8:
     - `enable = True`
     - `rf_chain = 0`
     - `freq_hz = 0`
     - `bandwidth = BW_125KHZ (0x04)` / `BW_250KHZ (0x05)` /
       `BW_500KHZ (0x06)`
     - `datarate = <SF 5..12>`
     - `implicit_hdr = False`
     - `implicit_crc_en = True`
     - `implicit_coderate = <CR 0x01..0x04>` (only used if implicit)
   - Alternative for BW 125 kHz only: multi-SF IF chain 0:
     - `enable = True`, `rf_chain = 0`, `freq_hz = 0`,
       `bandwidth = BW_125KHZ`
     - `lgw_demod_setconf` with only the desired SF enabled in
       `multisf_datarate` (bit mapping SF5=bit0 ... SF12=bit7; verify against
       the HAL).
   - Disable all other IF chains (0–7, 9) unless using one of them.
6. `lgw_txgain_setconf(0, <board LUT>)`.
7. `lgw_start()`.

Important constants:

- `LGW_COM_SPI = 0`
- `LGW_RADIO_TYPE_SX1250 = 5`
- `BW_125KHZ = 0x04`, `BW_250KHZ = 0x05`, `BW_500KHZ = 0x06`
- `CR_LORA_4_5 = 0x01` ... `CR_LORA_4_8 = 0x04`
- `STAT_CRC_OK = 0x10`
- `IMMEDIATE = 0`, `TIMESTAMPED = 1`, `ON_GPS = 2`
- `TX_STATUS = 1`, `TX_FREE = 2`

### Phase 5 — RX/TX data path

RX:

- `lgw_receive(max_pkt, rx_array)`.
- For each packet with `status == STAT_CRC_OK`:
  - `payload = bytes(pkt.payload[:pkt.size])`
  - `self.owner.inbound(payload, self)`
- Update optional RSSI/SNR statistics.

TX:

- Fill `lgw_pkt_tx_s`:
  - `freq_hz`, `tx_mode = IMMEDIATE`, `count_us = 0`
  - `rf_chain = 0`, `rf_power = <configured>`
  - `modulation = MOD_LORA`
  - `bandwidth`, `datarate = SF`, `coderate`
  - `invert_pol = False`
  - `preamble = 8` (or a Reticulum-compatible value)
  - `no_crc = False`, `no_header = False`
  - `size = len(payload)`, `payload = payload`
- Call `lgw_send()`.
- Poll `lgw_status(0, TX_STATUS, &code)` until `code == TX_FREE`.
- Never call `lgw_send()` again before the previous TX completes.

### Phase 6 — Reticulum integration

Place `SX1302.py` in the Reticulum interfaces directory, then configure:

```ini
[[SX1302 LoRa]]
  type = SX1302
  interface_enabled = yes
  frequency = 868100000
  bandwidth = 125000
  spreadingfactor = 8
  codingrate = 5
  txpower = 14
  spi_path = /dev/spidev0.0
  reset_script = /path/to/reset_lgw.sh
```

Reticulum will load the external module automatically. No patch to RNS is
required.

### Phase 7 — Validation

1. Struct-size check: compare Python `ctypes.sizeof` with C `sizeof` for every
   struct passed across the boundary. Also check field offsets.
2. Standalone TX/RX test between the SX1302 and an RNode configured to the same
   frequency, bandwidth, SF, CR, and private sync word.
3. `rnstatus` and `rnprobe` over the interface.
4. Large-packet test to confirm Reticulum fragments correctly with
   `HW_MTU = 255`.
5. Throughput/latency and packet-loss measurement.
6. Shutdown/restart test, including `lgw_stop()` and reset GPIO cleanup.
7. Permission test: SPI/I2C/GPIO access as the user running Reticulum.

## 11. ctypes notes

Structs that must be mirrored exactly:

- `lgw_conf_board_s`
- `lgw_conf_rxrf_s`
- `lgw_conf_rxif_s`
- `lgw_conf_demod_s`
- `lgw_tx_gain_s`
- `lgw_tx_gain_lut_s`
- `lgw_pkt_rx_s`
- `lgw_pkt_tx_s`

Key details:

- C99 `bool` is 1 byte → `ctypes.c_bool`.
- C enums are typically `int` → `ctypes.c_int`.
- `char com_path[64]` → `ctypes.c_char * 64`.
- `uint8_t payload[256]` → `ctypes.c_uint8 * 256`.
- Watch alignment around `uint64_t sync_word` and `uint32_t` fields.
- Validate with a small C program that prints `sizeof` and `offsetof` for each
  struct, and compare against Python.

`ctypes.CDLL` releases the GIL during C calls, but `lgw_start()` can take
seconds; run initialization in the worker thread so Reticulum startup is not
blocked.

## 12. Board patch needed for many SX1302 modules

If the board lacks the STTS751 temperature sensor, patch the HAL:

- In `lgw_start()`, do not return an error when no sensor is found; set
  `ts_fd = -1` and continue.
- In `lgw_receive()`, skip temperature compensation when
  `lgw_get_temperature()` fails, or make `lgw_get_temperature()` return a fixed
  value (e.g., 25.0 °C) when `ts_fd < 0`.

This is a small C change and does not introduce a daemon.

## 13. Risks and open questions

- **ctypes struct mismatch** can corrupt memory. Mitigate with compile-time
  `sizeof`/`offsetof` checks.
- **HAL global state** means only one SX1302 interface instance per process.
- **Board dependencies** (temperature sensor, reset GPIO, TX LUT, PA/LNA) are
  the biggest practical risks.
- **No CSMA/CAD** without the SX1261 companion radio; collisions are possible.
- **No duty-cycle enforcement**; the interface must enforce regional limits or
  rely on Reticulum's airtime accounting.
- **Reticulum fragmentation** must honor `HW_MTU = 255`; verify with packets
  larger than 255 bytes.
- **Reticulum API changes** could affect the external interface; pin a known
  Reticulum version.
- **`lgw_start()` latency** (seconds) must not block Reticulum startup.
- **HAL stdout logging** may interleave with Reticulum logs; redirect or accept
  it.

## 14. Recommendation

For an individual LoRa node, an RNode (SX1262/SX1276) remains the intended and
far simpler solution.

If the SX1302 hardware must be used, the in-process custom interface described
above is feasible:

- Use `ctypes` + `libloragw.so`.
- Configure one RF chain and one IF chain for the Reticulum frequency/SF/BW.
- Use private sync, explicit header, and CRC.
- Set `HW_MTU <= 255`.
- Run all HAL calls in one worker thread.
- Preconfigure the radio at startup; do not attempt runtime reconfiguration.

This avoids a separate daemon and avoids emulating the RNode KISS protocol and
its split/reassembly logic.

## 15. Reference index

Repository:

- `libloragw/inc/loragw_hal.h` — HAL API and structs.
- `libloragw/src/loragw_hal.c` — `lgw_start`, `lgw_receive`, `lgw_send`.
  - `:552` — config cannot change while running.
  - `:1116` — missing temperature sensor is fatal.
  - `:1292` — `lgw_receive` requires temperature.
  - `:1391` — TX payload > 255 bytes rejected.
- `libloragw/src/loragw_sx1302.c` — sync word, correlators, TX.
  - `:1111` — `sx1302_lora_syncword`.
  - `:759` — `sx1302_lora_correlator_configure`.
- `libloragw/src/loragw_spi.c` — SPI transport.
- `libloragw/tst/test_loragw_hal_tx.c` — raw TX example.
- `libloragw/tst/test_loragw_hal_rx.c` — raw RX example.
- `packet_forwarder/src/lora_pkt_fwd.c` — GWMP UDP client.
  - `:1576`, `:1635` — UDP `connect()`.
- `util_net_downlink/src/*.c` — UDP GWMP utility.
- `tools/reset_lgw.sh` — CoreCell GPIO reset/power.

Reticulum:

- `RNS/Interfaces/RNodeInterface.py`
  - `HW_MTU = 508`
  - `TCPConnection.TARGET_PORT = 7633`
  - `tcp://` parsing and `getaddrinfo(host, 7633)`
  - `validateRadioState()` checks frequency, bandwidth, TX power, SF, state
- `RNS/Reticulum.py`
  - `interfacepath = configdir + "/interfaces"`
  - external interface loading via `<type>.py` and `interface_class`
- `RNS/Interfaces/Interface.py` — base interface.
- `RNS/Interfaces/PipeInterface.py` — HDLC over stdio.
- `RNS/Interfaces/TCPInterface.py` — HDLC over TCP, not a radio driver.

RNode firmware:

- `Framing.h` — KISS commands and constants.
- `RNode_Firmware.ino` — `transmit()`, split-packet logic, CSMA, stats.
- `Utilities.h` — KISS indication helpers, `FLAG_SPLIT`, `packetSequence`.
