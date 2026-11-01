# SX1302 Reticulum Interface — Implementation Plan

Status: Phases 0–3 complete; Phase 4 implemented and loader-verified; live hardware validation (Phase 5) next
Date: 2026-11-01
Baseline: Semtech `sx1302_hal` v2.1.0 (now vendored under `vendor/sx1302_hal/`)  
Reference: [`SX1302_RETICULUM_FINDINGS.md`](SX1302_RETICULUM_FINDINGS.md)

## 1. Goal

Provide a Reticulum interface that uses an SX1302/SX1250 concentrator over SPI
as a single raw LoRa node, **in-process**, with no packet forwarder, no GWMP,
and no RNode KISS emulation.

The interface will:

- Be loaded by Reticulum as an external interface module (`type = SX1302`).
- Drive the SX1302 through `libloragw.so` using `ctypes`.
- Use one fixed LoRa configuration (frequency, bandwidth, SF, CR, TX power)
  selected at startup.
- Exchange raw Reticulum packets up to a 254-byte hardware MTU.
- Run all HAL access in one radio worker thread.
- Start and stop cleanly with Reticulum.

### Definition of done

- `rnsd` loads the interface without patching Reticulum.
- `rnstatus` reports the interface online with a plausible bitrate.
- Bidirectional `rnping`/`rnprobe` works against a second LoRa node.
- Packets larger than the hardware MTU are fragmented by Reticulum and
  delivered correctly.
- Restart and shutdown leave the SPI/GPIO state clean.
- Board assumptions, configuration, and troubleshooting are documented.

## 2. Scope

### In scope for the first release

- SPI-connected SX1302 with SX1250 radio(s), on a Linux host.
- One RF chain and one RX IF chain.
- LoRa PHY: private sync word, explicit header, CRC enabled.
- 125/250/500 kHz bandwidth, SF5–SF12, CR 4/5–4/8.
- Reticulum external interface module and supporting Python package.
- Optional minimal HAL patch for boards without an STTS751 sensor.
- Build, smoke-test, install, and validation tooling.

### Out of scope for the first release

- LoRaWAN, GWMP, network-server compatibility, or multi-channel gateway use.
- RNode KISS protocol compatibility and RNode 508-byte split/reassembly.
- Runtime frequency/SF/BW reconfiguration.
- USB/MCU concentrator variants.
- Full CSMA/CAD and duty-cycle enforcement.
- Supporting multiple SX1302 instances in one process (the HAL has global state).

These can be revisited after the first release if the hardware proves usable.

## 3. Target repository layout

```text
.
├── README.md                         # project overview and quick start
├── PLAN.md                           # this document
├── SX1302_RETICULUM_FINDINGS.md      # research notes and references
├── .gitignore
├── interfaces/
│   └── SX1302.py                     # thin external-interface shim for RNS
├── sx1302_reticulum/
│   ├── __init__.py
│   ├── config.py                     # config parsing and validation
│   ├── structs.py                    # ctypes mirrors of HAL structs
│   ├── hal.py                        # libloragw.so loading and API wrappers
│   ├── worker.py                     # single radio worker thread
│   └── interface.py                  # SX1302Interface implementation
├── patches/
│   └── 0001-optional-temperature-sensor.patch
├── scripts/
│   ├── build_hal.sh                  # build libloragw.so from vendored source
│   ├── abi_check.py                  # Python vs C struct size/offset check
│   ├── smoke_test.py                 # standalone hardware TX/RX check
│   └── install_interface.sh          # install/symlink the interface module
├── tests/
│   ├── test_config.py
│   ├── test_structs.py
│   └── test_worker.py                # uses a fake HAL
├── examples/
│   ├── config.ini                    # Reticulum interface stanza
│   └── board.conf                    # board-specific HAL settings
└── vendor/
    ├── README.md                     # provenance and patch policy
    └── sx1302_hal/                   # upstream Semtech code, kept pristine
```

The vendored tree remains unmodified where practical. Board-specific or
required source changes are kept as patches under `patches/` and applied to a
build copy by `scripts/build_hal.sh`. Generated objects and the shared library
go under `build/`, which is ignored by git.

## 4. Architecture

```text
Reticulum process
│
├── external interface module: <configdir>/interfaces/SX1302.py
│     └── imports sx1302_reticulum; exports interface_class
│
└── SX1302Interface(Interface)
      │  process_outgoing() -> TX queue
      │  worker RX callback -> owner.inbound()
      │
      └── RadioWorker thread
            ├── owns every libloragw call
            ├── polls lgw_receive()
            ├── serializes lgw_send()
            ├── polls lgw_status()
            └── updates RSSI/SNR and lifecycle state
                  │
                  └── ctypes -> build/libloragw.so
                        ├── /dev/spidevX.Y
                        ├── optional I2C temperature sensor
                        └── GPIO reset/power (script or Python)
```

### Component responsibilities

| Component | Responsibility |
|---|---|
| `interfaces/SX1302.py` | Satisfy Reticulum's external module contract; expose `interface_class`. |
| `sx1302_reticulum/interface.py` | Implement `Interface` behavior, properties, RX dispatch, TX queueing, lifecycle. |
| `sx1302_reticulum/worker.py` | Serialize all HAL calls; own the RX/TX loop and shutdown event. |
| `sx1302_reticulum/hal.py` | Load the shared library, declare `argtypes`/`restype`, wrap HAL calls. |
| `sx1302_reticulum/structs.py` | Exact `ctypes.Structure` mirrors and constants. |
| `sx1302_reticulum/config.py` | Parse/validate Reticulum and board configuration; derive HAL settings. |
| `scripts/build_hal.sh` | Apply patches, compile PIC objects, link `libloragw.so`. |
| `scripts/abi_check.py` | Fail fast if Python struct layout does not match the C ABI. |

### Threading and data flow

- Reticulum calls `process_outgoing(data)` from its own threads; the interface
  only appends to a bounded TX queue.
- One worker thread owns the HAL for its entire lifetime.
- RX loop: `lgw_receive()` → filter `STAT_CRC_OK` → copy payload → update
  `r_stat_rssi`/`r_stat_snr` → `owner.inbound(payload, self)`.
- TX loop: dequeue only when `lgw_status(TX_STATUS)` reports `TX_FREE` →
  `lgw_send()` → poll until the packet is no longer emitting → next item.
- Half-duplex behavior is implicit: RX and TX are serialized by the worker.
- `lgw_start()` runs in the worker so Reticulum startup is not blocked; the
  interface reports `online = False` until initialization succeeds.

### Interface properties

| Property | Value / behavior |
|---|---|
| `HW_MTU` | `254` (one byte of margin below the HAL's 255-byte TX cap). |
| `AUTOCONFIGURE_MTU` | `False`. |
| `shared_medium` | `True`. |
| `bitrate` | Computed from SF/BW/CR with the RNode formula. |
| `online` | Set only after successful `lgw_start()` and first worker tick. |
| `reports_phy_stats` | `True`; expose last RX RSSI/SNR. |
| `__str__` | `SX1302Interface[<name>]`. |

## 5. Key technical decisions

| Decision | Choice | Rationale |
|---|---|---|
| Integration model | In-process external Reticulum interface | Avoids daemon, socket IPC, and a second protocol stack. |
| HAL access | `ctypes` to `libloragw.so` | Reuses firmware loading, calibration, AGC/ARB, and timing code. |
| Concurrency | One worker thread owns the HAL | HAL is global-state and not designed for concurrent calls. |
| Framing | Raw LoRa payloads; no KISS or RNode split | Reticulum's interface MTU handles fragmentation at the packet layer. |
| MTU | 254 bytes | HAL TX limit is 255; leaves room and avoids split logic. |
| PHY | Private sync, explicit header, CRC on | Matches Reticulum's raw LoRa expectations. |
| Channel plan | One RF chain, one IF/service chain | A gateway receiver is not needed for a single node. |
| Runtime config | Configure once at startup | HAL refuses IF reconfiguration while running. |
| Temperature sensor | Optional HAL patch | Many SX1302 boards lack the STTS751; current HAL makes it fatal. |
| TX gain | Board-specific LUT from config | The upstream tree has no universal LUT; incorrect values risk PA damage or poor range. |
| Reset/power | External script by default, configurable | Reuses `reset_lgw.sh`; board GPIO differences stay outside Python. |
| Duty cycle/CSMA | Not enforced in MVP; documented | The SX1302 has no built-in CAD/LBT without the SX1261 companion. |

## 6. Work breakdown

### Phase 0 — Bootstrap (complete on this branch)

- Create branch `feature/reticulum-sx1302-interface`.
- Move upstream code to `vendor/sx1302_hal/`.
- Add repository README, plan, and project `.gitignore`.
- Record provenance and patch policy for the vendored tree.

Exit criteria: upstream tree still builds from its new location; project root
contains only project-owned files.

### Phase 1 — Shared HAL and ABI safety (implemented)

Implemented artifacts:

- `patches/0001-optional-temperature-sensor.patch`
- `scripts/build_hal.sh`
- `scripts/abi_probe.c`
- `scripts/abi_check.py`
- `sx1302_reticulum/structs.py`
- `tests/test_structs.py`, `tests/test_abi.py`, `tests/test_hal_load.py`

Tasks:

1. Add `patches/0001-optional-temperature-sensor.patch`:
   - Do not fail `lgw_start()` when no STTS751 is present; mark the sensor
     unavailable.
   - Make `lgw_receive()` skip temperature compensation or use a safe fixed
     temperature when unavailable.
2. Implement `scripts/build_hal.sh`:
   - Copy or mirror `vendor/sx1302_hal` into `build/sx1302_hal`.
   - Apply patches.
   - Build `libtinymt32` and all `libloragw` sources with `-fPIC`.
   - Link `build/libloragw.so` with `-lm -lrt`.
   - Print exported `lgw_*` symbols for verification.
3. Add a C ABI probe that prints `sizeof` and `offsetof` for every struct
   crossing the `ctypes` boundary.
4. Implement `sx1302_reticulum/structs.py` and `scripts/abi_check.py`; make the
   check part of the test suite.
5. Add a load test that opens the `.so`, resolves required symbols, and checks
   the version/string APIs.

Exit criteria:

- `libloragw.so` builds reproducibly from a clean checkout.
- `nm -D build/libloragw.so` exports the required HAL symbols.
- Every mirrored struct passes size and field-offset checks.
- Importing the package does not require hardware.

### Phase 2 — Hardware bring-up

Status: **complete** (2026-10-31).

Validated hardware and configuration:

- RAK2287 (SX1302 + SX1250, SPI version) on a RAK2004 Pi HAT Ver B.
- SX1302 reset is GPIO 25 on this HAT and the reset input is **active high**
  (low → high pulse → low, then settle).
- SPI `/dev/spidev0.0`; HAL v2.1.0 built as `build/libloragw.so`.
- EU868: 868.1 MHz, BW 125 kHz, SF9, CR 4/5, 14 dBm, service channel
  (IF chain 8), private sync word.
- `lgw_start()` succeeds with the optional temperature-sensor patch. The
  RAK2287 has no STTS751, so the HAL reports 25.0 °C.
- One board transmitted; a second node received and decoded the payload.

The working profile is `examples/rak2287-eu868.conf`; it sets
`reset_gpio = 25` so no environment override is required.

Tasks:

1. Define a board profile (`examples/rak2287-eu868.conf`) covering:
   - SPI path, radio type, RF chain, TX LUT, RSSI offset/compensation,
     single-input mode, reset script, and GPIO ownership.
2. Implement `scripts/smoke_test.py`:
   - Run reset/power sequence.
   - Load the shared HAL.
   - Configure board, RF chain, RX IF chain, TX gain LUT.
   - Call `lgw_start()`.
   - Send a known payload with `lgw_send()`.
   - Poll `lgw_receive()` and print packet metadata.
   - Stop cleanly with `lgw_stop()`.
3. Validate the temperature-sensor patch on a board without STTS751.
4. Confirm the chosen RX chain strategy:
   - Prefer the LoRa service channel (IF chain 8) for all bandwidths.
   - Use a multi-SF chain with a single SF enabled as the 125 kHz fallback.
5. Capture the working board configuration and any required GPIO changes.

Exit criteria:

- SX1302 starts without a temperature sensor when patched.
- A raw packet can be transmitted.
- A raw packet can be received and decoded by a second LoRa node.
- The board profile reproduces the result after a cold boot.

### Phase 3 — Interface core

Status: **complete** (2026-11-01).

Implemented:

- `sx1302_reticulum/reset.py`: shared reset runner with environment
  propagation, captured output, and clear failure reporting.
- `sx1302_reticulum/worker.py`: one radio thread owns every HAL call. It has a
  bounded TX queue, RX polling, CRC and MTU filtering, TX-free and completion
  polling with timeouts, counters, receive-error backoff, graceful shutdown,
  and optional reset-script handling around `lgw_start()`/`lgw_stop()`.
- `sx1302_reticulum/interface.py`: Reticulum 1.5.7 `Interface` subclass with
  stanza overrides, `HW_MTU = 254`, fixed MTU, shared-medium and PHY-stat
  reporting, `owner.inbound()` dispatch, `process_outgoing()`, and idempotent
  `detach()`/`teardown()`.
- `scripts/smoke_test.py` now uses the shared reset runner.
- Tests: `tests/test_reset.py`, `tests/test_worker.py`, and
  `tests/test_interface.py`, including a fake HAL that records thread IDs to
  prove no HAL call happens outside the worker thread.

Test result: 56 passed with Reticulum 1.5.7 installed; without RNS, the
Reticulum-dependent tests are skipped (48 passed, 2 skipped).

Tasks:

1. Implement configuration parsing and validation:
   - Frequency, bandwidth, SF, CR, TX power ranges.
   - SPI path, reset script, radio type, LUT, timing parameters.
   - Clear errors for unsupported combinations.
2. Implement `hal.py` wrappers with explicit `argtypes`/`restype`.
3. Implement `worker.py`:
   - Bounded TX queue.
   - RX polling with a configurable idle sleep.
   - TX completion polling with timeout and error recovery.
   - Graceful shutdown event and thread join.
   - Counters for RX/TX, CRC failures, and errors.
4. Implement `interface.py`:
   - Reticulum `Interface` subclass and `process_outgoing()`.
   - `owner.inbound()` dispatch with correct metadata handling.
   - `online`, `bitrate`, `HW_MTU`, and PHY statistics.
   - Idempotent `detach()`/`teardown()`.
5. Add unit tests using a fake HAL object:
   - TX queue ordering and backpressure.
   - RX dispatch and CRC filtering.
   - Start failure, TX timeout, and shutdown paths.
   - No HAL call outside the worker thread.

Exit criteria:

- Unit tests pass without hardware.
- A fake HAL can drive a complete RX/TX lifecycle.
- Interface construction and teardown are deterministic.

### Phase 4 — Reticulum integration

Status: **implemented** (2026-11-01); live `rnsd` validation pending.

Implemented:

- `interfaces/SX1302.py`: minimal shim with an absolute import and
  `interface_class = SX1302Interface`. It works under Reticulum's `exec()`
  contract, which supplies only `RNS` and `Interface` in globals.
- `scripts/install_interface.sh`: symlinks the shim into
  `<configdir>/interfaces/`, optionally installs the package in editable mode,
  and warns when the active Python cannot import it.
- `examples/reticulum-sx1302.conf`: commented `[[SX1302 LoRa]]` stanza with all
  supported overrides.
- `pyproject.toml`: installable package with Reticulum pinned to
  `>=1.5.7,<1.6` (1.5.7 is the tested release).
- `tests/test_shim.py`: executes the shim the way Reticulum does and verifies
  it exposes the class; also checks the install helper's symlink behavior.

Remaining for Phase 5: start `rnsd` on hardware, confirm `rnstatus` shows the
interface online with the expected MTU, and run `rnping`/`rnprobe` both ways.

Loader validation: RNS 1.5.7 was started in-process with a temporary
`[interfaces]` configuration and a fake HAL. It loaded `SX1302.py`, constructed
the interface, set `mode`, `OUT`, and IFAC state, started the worker, and showed
the interface online with `HW_MTU = 254` and ~1757.8 bit/s. With an IFAC
passphrase, the interface reduced `HW_MTU` to 238 so the 16-byte IFAC tag still
fits the 255-byte HAL limit. TX submission and clean detach also worked.

Tasks:

1. Add `interfaces/SX1302.py`:
   - Use absolute imports.
   - Define `interface_class = SX1302Interface`.
   - Keep the shim minimal because Reticulum `exec()`s it.
2. Add `scripts/install_interface.sh`:
   - Install/symlink the shim into `<configdir>/interfaces/`.
   - Optionally install the Python package in editable mode.
3. Add an example Reticulum stanza:

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
     board_profile = /path/to/board.conf
   ```

4. Pin and document a tested Reticulum version.
5. Verify that Reticulum's external-module loader finds and instantiates the
   interface without patching RNS.

Exit criteria:

- Reticulum starts with the interface enabled.
- `rnstatus` shows the interface and derived bitrate.
- Startup failure is visible and does not crash Reticulum.

### Phase 5 — End-to-end validation and hardening

Tasks:

1. Validate against an RNode configured to the same frequency, bandwidth, SF,
   CR, and private sync word.
2. Run `rnstatus`, `rnping`, and `rnprobe` in both directions.
3. Test payloads above 254 bytes to confirm Reticulum fragmentation.
4. Measure throughput, latency, and packet loss at several SF/BW settings.
5. Test cold start, warm restart, `lgw_stop()`, and reset-script cleanup.
6. Test SPI/I2C/GPIO permissions as the actual Reticulum user.
7. Run a soak test and check for queue growth, descriptor leaks, and stuck TX.
8. Decide whether to add a simple airtime budget or leave duty-cycle policy to
   the operator/Reticulum.

Exit criteria:

- All functional tests pass on the target board.
- No crash, hang, or stuck TX after repeated restarts.
- Performance and limitations are documented.

### Phase 6 — Packaging and documentation

Tasks:

1. Finalize `README.md`, board profiles, and configuration reference.
2. Document build prerequisites, permissions, reset GPIO behavior, and the
   temperature-sensor patch.
3. Document known limitations: no CSMA/CAD, no duty-cycle enforcement, one
   instance per process, no runtime retuning.
4. Add a troubleshooting section for SPI, I2C, GPIO, TX LUT, and logging.
5. Optionally provide a lightweight `systemd` unit for the Reticulum daemon
   (not a radio daemon).

Exit criteria: a new user can build, install, configure, and diagnose the
interface from the repository documentation alone.

## 7. HAL startup sequence

The eventual implementation should follow this order:

1. Run the board reset/power sequence.
2. Configure the board:
   - `lorawan_public = false` (private sync word)
   - `clksrc = 0`
   - `full_duplex = false`
   - `com_type = LGW_COM_SPI`
   - `com_path = <spi_path>`
3. Configure RF chain 0:
   - `enable = true`, `tx_enable = true`
   - `freq_hz = <Reticulum frequency>`
   - `type = <board radio type>` (SX1250 is `5`)
   - Board RSSI offset/temperature compensation and input mode.
4. Disable RF chain 1.
5. Configure the RX IF chain:
   - Preferred: LoRa service channel, IF chain 8, with the configured
     bandwidth, SF, explicit header, and CRC.
   - 125 kHz fallback: multi-SF chain 0 with only the required SF enabled.
   - Disable all unused IF chains.
6. Apply the board TX gain LUT.
7. Call `lgw_start()` in the worker thread.
8. Only then mark the interface online.

The interface will not attempt runtime retuning in the first release.

## 8. RX/TX contract

### Receive

- Poll `lgw_receive()` with a small batch size.
- Accept only `status == STAT_CRC_OK`.
- Copy exactly `size` bytes from the fixed payload buffer.
- Update RSSI/SNR statistics from `rssic`, `rssis`, and `snr`.
- Drop packets larger than `HW_MTU` with a clear counter/log; Reticulum should
  not generate them, but the boundary must be defensive.

### Transmit

- Reject payloads larger than `HW_MTU` before touching the HAL.
- Fill `lgw_pkt_tx_s` with `IMMEDIATE`, RF chain 0, LoRa modulation, configured
  bandwidth/SF/CR/power, explicit header, CRC enabled, and an 8-symbol preamble.
- Wait for `TX_FREE` before calling `lgw_send()`.
- After `lgw_send()`, poll `lgw_status(TX_STATUS)` until the transmission is no
  longer emitting, with a bounded timeout.
- Never issue a second `lgw_send()` while a transmission is active.

## 9. Testing strategy

| Level | Focus | Tooling |
|---|---|---|
| ABI | Struct size/offset and symbol availability | C probe + `ctypes` check |
| Unit | Config validation, queueing, state machine | `pytest`, fake HAL |
| Build | Clean build, patch application, reproducible `.so` | `scripts/build_hal.sh` |
| Hardware smoke | Reset, start, raw TX, raw RX | `scripts/smoke_test.py` |
| Integration | Reticulum loading, status, ping, fragmentation | `rnsd`, `rnstatus`, `rnping` |
| HIL | SX1302 ↔ RNode link quality and retransmission | Two radios |
| Soak | Restarts, long run, queue/TX health | Repeatable scripts |

Every phase should add tests that run without hardware where possible. Hardware
tests must be explicitly marked and never required by the default test run.

## 10. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| `ctypes` struct mismatch corrupts memory | High | Compile-time ABI probe; fail the build/test before hardware use. |
| Board lacks STTS751 | Blocks startup | Maintain and test the optional-sensor patch. |
| Wrong or missing TX gain LUT | Poor range or PA damage | Require a board profile; start with low power; document calibration. |
| Reset GPIO values differ by board | Board will not start | Configurable reset command; document CoreCell defaults clearly. |
| HAL logging mixes with Reticulum logs | Confusing output | Capture/redirect C stdout or document expected messages. |
| No CSMA/CAD or duty-cycle enforcement | Collisions/regulatory risk | Document limitation; optional airtime budget; recommend LBT hardware. |
| `lgw_start()` takes seconds | Blocks Reticulum startup | Initialize in the worker; report offline until ready. |
| Runtime config assumptions | Interface fails after retune attempts | Preconfigure at startup and reject unsupported changes clearly. |
| Reticulum API changes | External module breaks | Pin and test a known RNS version; keep shim thin. |
| HAL global state | Multiple interfaces conflict | Enforce one instance per process. |
| SPI/I2C/GPIO permissions | Runtime failure | Document udev/groups setup; test as the service user. |

## 11. Open questions

These should be answered before or during Phase 2:

1. Which exact SX1302 board is the target, and which radio/PA/LNA does it use?
2. Is a valid TX gain LUT available from the vendor configuration?
3. Does the board have an STTS751, and on which I2C bus/address?
4. Which GPIOs are required for reset/power, and should Python own them or
   should the external script remain authoritative?
5. What is the target frequency, bandwidth, SF, CR, and TX power?
6. What duty-cycle regime applies in the target region?
7. Which Reticulum version will be supported and pinned?
8. Is the service channel sufficient, or is a multi-SF chain needed for the
   target bandwidth/SF combination?

## 12. Suggested milestones

| Milestone | Deliverable | Gate |
|---|---|---|
| M0 | Repository split and plan | Upstream tree builds from `vendor/`. |
| M1 | Buildable shared HAL and ABI check | `.so` loads; struct checks pass. |
| M2 | Hardware smoke test | Raw TX and RX work on the target board. |
| M3 | Interface core | Fake-HAL lifecycle and unit tests pass. |
| M4 | Reticulum loads the interface | `rnstatus` shows it online. |
| M5 | Validated radio link | Bidirectional ping and fragmentation succeed. |
| M6 | Hardened release | Soak tests pass; docs complete. |

## 13. Immediate next steps

1. Confirm the target board details and TX gain LUT.
2. Add the temperature-sensor patch and the `libloragw.so` build script.
3. Generate the C ABI probe and Python struct mirrors.
4. Bring up raw TX/RX with `scripts/smoke_test.py`.
5. Only then write the Reticulum adapter and run end-to-end tests.
