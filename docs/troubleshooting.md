# Troubleshooting

This guide covers common issues during bring-up and operation.

## 1. SPI / Device Access

### `Permission denied` on `/dev/spidev0.0`

```bash
ls -l /dev/spidev0.0
# crw-rw---- 1 root spi 153, 0 ...

# Add user to spi group and re-login:
sudo usermod -a -G spi $USER
# or for systemd service:
# User=pi
# Group=spi
```

### `No such file or directory` for `/dev/spidev0.0`

```bash
# Enable SPI in /boot/firmware/config.txt (Pi 4/5) or /boot/config.txt (Pi 3):
dtparam=spi=on

# Reboot and verify:
ls /dev/spidev*
```

### SPI speed / mode issues

The HAL configures SPI mode 0 and speed internally. If you see CRC errors
or no RX:

```bash
# Check dmesg for SPI errors:
dmesg -T | grep -i spi

# Verify wiring: CS0 on CE0 (GPIO 8), SCLK on GPIO 11, MISO on GPIO 9, MOSI on GPIO 10.
```

## 2. I2C / Temperature Sensor

### `lgw_start()` hangs or fails with I2C error

The STTS751 temperature sensor is on I2C (address 0x38). If missing:

1. Apply the optional-sensor patch (`patches/0001-optional-temperature-sensor.patch`).
2. Rebuild: `./scripts/build_hal.sh`
3. The HAL will report 25.0 °C and continue.

```bash
# Verify I2C bus:
sudo i2cdetect -y 1
# Should show 38 if STTS751 is present
```

### RSSI temperature compensation

If the sensor is present but RSSI seems off, check the `rssi_tcomp_*`
coefficients in the board profile match your module's calibration data.

## 3. GPIO / Reset

### Reset script fails

```bash
# Test manually:
./scripts/reset_rak2287.sh start
./scripts/reset_rak2287.sh stop
```

The script uses `gpioset` from libgpiod v2 (package `gpiod` on Trixie,
`bookworm-backports` on Bookworm). It does **not** use sysfs.

### Wrong reset GPIO

The RAK2004 Pi HAT Ver B uses GPIO 25. Most other RAK HATs use GPIO 17.

```bash
# Check your carrier's schematic, then:
# Option 1: Edit board profile
reset_gpio = 17

# Option 2: Environment override
export SX1302_RESET_GPIO=17
```

### Active-high vs active-low

The SX1302 reset input is **active high** on RAK2287: the line must be
low → high pulse → low. The provided reset script implements this
correctly. Do not invert it.

## 4. HAL Build / ABI

### `ctypes` struct size mismatch

```bash
# Run the ABI check:
./scripts/abi_check.py
```

If it fails, the vendored HAL source and the Python structs have drifted.
Re-run `./scripts/build_hal.sh` and verify the struct definitions in
`sx1302_reticulum/hal.py` match the C headers.

### `libloragw.so` not found

```bash
# Set the library path:
export SX1302_HAL_LIB=/home/pi/sx1302_rns/build/libloragw.so

# Or in the interface config:
# library = /home/pi/sx1302_rns/build/libloragw.so
```

### Multiple HAL versions

Only one `libloragw.so` can be loaded per process. Ensure you are not
running a packet forwarder or another SX1302 program simultaneously.

## 5. TX LUT / Gain Table

### `lgw_send()` returns error / no RF output

The TX gain LUT must match your board exactly. The example profile uses
RAK2287 EU868 values from RAK's gateway configuration.

```bash
# Verify the LUT entries cover your txpower:
grep -A2 '^\[txgain' examples/rak2287-eu868.conf
```

If your `txpower` setting falls between two LUT entries, the HAL
interpolates. If it falls outside the table, TX may fail or emit at
wrong power.

### Antenna / dummy load

**Never transmit without an antenna or 50 Ω dummy load.** The SX1250
PA can be damaged by open-circuit RF.

## 6. Reticulum Interface Issues

### Interface never comes online

```bash
# Check rnsd logs:
rnsd -v 2>&1 | grep -i sx1302

# Common causes:
# - board_profile path wrong (relative to config dir)
# - HAL library not found
# - SPI permission denied
# - Reset script failed
```

### MTU mismatches / fragmentation not working

```bash
# Check advertised MTU:
rnstatus -j | jq '.interfaces[] | select(.name | contains("SX1302")) | .mtu'
# Should be 254 (or 238 with IFAC)

# Reticulum MTU is 500; link fragmentation happens automatically.
# Plain packets must fit in 254 bytes.
```

### RSSI/SNR not reported

The interface sets `reports_phy_stats = True` and updates `r_stat_rssi`
and `r_stat_snr` on each RX. If `rnprobe` shows no RSSI/SNR:

1. Check `rnsd` logs for "receive callback failed".
2. Verify the HAL's `lgw_receive()` populates the metadata fields.

## 7. Logging

### Enable debug logging

In `~/.reticulum/config`:

```ini
[logging]
  loglevel = 4
```

Or run `rnsd -v` for console debug output.

### Worker thread logs

The radio worker logs via the interface's `_log` and `_log_error`
callbacks, which go through Reticulum's logging system. Look for lines
prefixed with the interface name.

## 8. Common Error Messages

| Error | Cause | Fix |
|-------|-------|-----|
| `OSError: [Errno 13] Permission denied: '/dev/spidev0.0'` | User not in `spi` group | `usermod -a -G spi $USER`, re-login |
| `FileNotFoundError: /dev/spidev0.0` | SPI not enabled | Add `dtparam=spi=on` to `/boot/firmware/config.txt`, reboot |
| `ResetError: gpioset failed` | GPIO not exported / wrong chip | Check `gpioinfo`, verify `reset_gpio`, ensure libgpiod v2 |
| `HAL start failed: lgw_start returned -1` | SPI/I2C/GPIO conflict, or HAL already running | Stop other SX1302 processes, check wiring |
| `struct size mismatch` | Python/C ABI drift | Rebuild HAL, update Python structs if upstream changed |
| `TX_FREE timeout` | Previous TX stuck, or HAL state corrupt | Check `tx_free_timeout`, ensure `lgw_stop()` runs on exit |
| `No multicast echoes received` | AutoInterface (UDP) multicast blocked | Normal on Pi; unrelated to SX1302 interface |

## 9. Hardware Sanity Checklist

Before reporting an issue, verify:

- [ ] Antenna or dummy load connected
- [ ] 5 V supply stable (no undervoltage lightning bolt)
- [ ] SPI enabled, `/dev/spidev0.0` exists, user in `spi` group
- [ ] I2C enabled (for temperature sensor, optional)
- [ ] GPIO group access for reset (`gpio` group, or udev rule)
- [ ] Board profile matches hardware (reset_gpio, TX LUT, RSSI offset)
- [ ] No other SX1302 process running (packet forwarder, smoke test, etc.)
- [ ] EU868: 868.1 MHz, 14 dBm, duty cycle respected

## 10. Getting Help

If the above doesn't help, collect:

```bash
uname -a
cat /etc/os-release
./scripts/abi_check.py
./scripts/smoke_test.py --config examples/rak2287-eu868.conf --rx-only --rx-timeout 10
```

And the full `rnsd -v` log from startup.