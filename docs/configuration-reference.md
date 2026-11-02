# Configuration Reference

This document describes every configuration option for the SX1302 Reticulum
interface and its board profiles.

## 1. Reticulum Interface Configuration

Add a section under `[interfaces]` in your Reticulum configuration
(`~/.reticulum/config`):

```ini
[interfaces]

  [[SX1302 LoRa]]
    type = SX1302
    enabled = yes
    board_profile = /path/to/rak2287-eu868.conf
    # library = /path/to/build/libloragw.so
```

### 1.1 Required options

| Option | Description |
|--------|-------------|
| `type` | Must be `SX1302` — selects this external interface. |
| `enabled` | `yes` or `no`. Disabled interfaces are ignored. |
| `board_profile` | Path to a board profile (see section 2). Relative paths are resolved from the Reticulum configuration directory. |

### 1.2 Optional overrides (interface level)

These override values from the board profile:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `frequency` | int | from board | Centre frequency in Hz. |
| `bandwidth` | int | from board | Channel bandwidth in Hz (125000, 250000, 500000). |
| `spreadingfactor` | int | from board | LoRa SF (7–12). |
| `codingrate` | int | from board | LoRa CR (5–8, meaning 4/5–4/8). |
| `txpower` | int | from board | TX power in dBm (0–27, board-dependent). |
| `preamble` | int | from board | Preamble length in symbols. |
| `spi_path` | string | from board | SPI device path (e.g. `/dev/spidev0.0`). |
| `channel_mode` | string | from board | `service` or `multi_sf` (see board profile). |

### 1.3 Reset handling

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `reset` | bool | `yes` | Run the reset script on start/stop. |
| `reset_gpio` | int | from board | BCM GPIO number for SX1302 reset. |
| `reset_script` | string | from board | Path to the reset script. |
| `reset_enabled` | bool | from board | Same as `reset`. |

The reset script receives `start` or `stop` as its first argument and drives
the SX1302 reset line via libgpiod v2. The SX1302 reset input is **active
high**: low → high pulse → low, then settle.

### 1.4 HAL library

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `library` | string | auto | Path to `libloragw.so`. Default search order: `$SX1302_HAL_LIB`, `build/libloragw.so` (relative to package), system `libloragw.so`. |

### 1.5 Radio worker tuning

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `queue_size` | int | 16 | Max pending TX frames. |
| `rx_batch` | int | 8 | Max packets per `lgw_receive()` call. |
| `rx_idle_sleep` | float | 0.01 | Sleep between empty RX polls (seconds). |
| `status_poll` | float | 0.005 | Sleep when waiting for `TX_FREE`/`TX_STATUS` (seconds). |
| `tx_free_timeout` | float | 5.0 | Max wait for `TX_FREE` before drop (seconds). |
| `tx_complete_margin` | float | 2.0 | Extra margin after calculated on-air time (seconds). |
| `stop_timeout` | float | 10.0 | Max time to wait for worker thread on stop (seconds). |

### 1.6 Airtime and channel access

These options are optional and disabled by default. They implement a sliding
airtime budget and a coarse listen-before-talk (LBT) gate in the worker. The
worker keeps polling the receiver while it waits, so long duty-cycle delays do
not stop inbound traffic.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `duty_cycle_percent` | float | 0 | Maximum transmit airtime as a percentage of `duty_cycle_window`. `0` disables the gate. EU868 1% would be `1`. |
| `duty_cycle_window` | float | 3600 | Sliding window for duty-cycle accounting, in seconds. |
| `lbt_rssi_threshold` | float | unset | Enable LBT when set. A recently decoded frame at or above this RSSI (dBm) marks the channel busy. |
| `lbt_check_ms` | float | 50 | Channel must have been quiet for this long before transmitting (milliseconds). Also the polling interval while waiting. |
| `lbt_timeout` | float | 5 | Maximum time to wait for a clear channel before dropping the pending frame (seconds). |

```ini
[[SX1302 LoRa]]
  type = SX1302
  board_profile = /path/to/rak2287-eu868.conf
  duty_cycle_percent = 1
  duty_cycle_window = 3600
  lbt_rssi_threshold = -90
  lbt_check_ms = 50
  lbt_timeout = 5
```

LBT is a quiet-period aid, not full carrier-sense multiple access. The HAL does
not expose an instantaneous RSSI, and the SX1302 `RX_STATUS` selector is
unimplemented, so only frames that share the configured sync word and were
fully decoded can mark the channel busy.

### 1.7 Runtime retuning

The interface can be retuned without restarting the process. From Python:

```python
interface.reconfigure("/path/to/another-board.conf")
# or pass a BoardProfile:
interface.reconfigure(profile.with_overrides(frequency=869_525_000))
```

The worker stops the HAL, applies the new profile, and restarts it. Frames that
were already queued may still be sent with the old modulation; frames submitted
after the reconfiguration call use the new settings.

## 2. Board Profile Format

A board profile is an INI file describing the hardware. The example
`examples/rak2287-eu868.conf` is for a RAK2287 on a RAK2004 Pi HAT Ver B.

### 2.1 `[board]` section

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `spi_path` | string | yes | SPI device path. |
| `radio_type` | string | yes | `SX1250` (or `SX1255`, `SX1257`). |
| `clksrc` | int | yes | Clock source index (0 = SX1302 internal). |
| `full_duplex` | bool | no | `yes`/`no`. SX1302 is half-duplex. |
| `single_input_mode` | bool | no | `yes`/`no`. |
| `rssi_offset` | float | yes | RSSI calibration offset (dB). |
| `rssi_tcomp_a`–`e` | float | no | Temperature compensation coefficients (if STTS751 present). |
| `reset_script` | string | no | Path to reset script. |
| `reset_gpio` | int | no | BCM GPIO for SX1302 reset (active high). |
| `reset_start_args` | string | no | Args for `start` (default: `start`). |
| `reset_stop_args` | string | no | Args for `stop` (default: `stop`). |
| `reset_enabled` | bool | no | Run reset script (default: `yes`). |

**Temperature sensor**: If the board lacks an STTS751, the optional-sensor
patch (`patches/0001-optional-temperature-sensor.patch`) makes the HAL
report 25.0 °C and the `rssi_tcomp_*` coefficients are ignored.

### 2.2 `[rf]` section

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `chain` | int | yes | RF chain index (0 or 1). |
| `frequency` | int | yes | Centre frequency in Hz. |
| `tx_enable` | bool | yes | `yes`/`no`. |
| `tx_power` | int | yes | Default TX power in dBm. |

### 2.3 `[channel]` section

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `mode` | string | yes | `service` (single channel, IF8) or `multi_sf` (IF0). |
| `if_frequency` | int | yes | IF offset in Hz (0 for service channel). |
| `bandwidth` | int | yes | Channel bandwidth in Hz. |
| `spreadingfactor` | int | yes | LoRa SF (7–12). |
| `codingrate` | int | yes | LoRa CR (5–8). |
| `preamble` | int | no | Preamble symbols (default: 8). |

### 2.4 `[txgain.*]` sections

One section per supported TX power level. Keys:

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `rf_power` | int | yes | Output power in dBm. |
| `pa_gain` | int | yes | PA gain index (0–3). |
| `pwr_idx` | int | yes | DAC power index (board-specific). |

Entries must be sorted by ascending `rf_power`. The HAL interpolates between
entries. Use the table from your board vendor's gateway configuration.

## 3. Environment Variables

| Variable | Description |
|----------|-------------|
| `SX1302_HAL_LIB` | Override `libloragw.so` path (highest priority). |
| `SX1302_RESET_GPIO` | Override reset GPIO (BCM number). |
| `SX1302_RESET_SCRIPT` | Override reset script path. |

## 4. IFAC (Interface Access Control)

IFAC adds a 16-byte authentication tag to every frame. Configure in Reticulum:

```ini
[interfaces]

  [[SX1302 LoRa]]
    type = SX1302
    enabled = yes
    board_profile = /path/to/rak2287-eu868.conf
    ifac_passphrase = your_shared_secret
```

- Both nodes must use the same passphrase.
- IFAC reduces the effective hardware MTU from 254 to 238 bytes.
- The interface advertises the reduced MTU automatically.

## 5. Example: Complete Reticulum Config

```ini
[reticulum]
  enable_transport = no
  share_instance = yes

[logging]
  loglevel = 3

[interfaces]

  [[SX1302 LoRa]]
    type = SX1302
    enabled = yes
    board_profile = examples/rak2287-eu868.conf
    # library = build/libloragw.so
    # ifac_passphrase = secret
    # queue_size = 16
    # duty_cycle_percent = 1
    # duty_cycle_window = 3600
    # lbt_rssi_threshold = -90
    # lbt_check_ms = 50
    # lbt_timeout = 5
```
