#!/usr/bin/env bash
#
# Reset the SX1302 on a RAK2287 carrier.
#
# Most RAK2287 Raspberry Pi HATs use GPIO 17 for SX1302 reset. RAK2004 Pi HAT
# Ver B (and some Helium miner carriers) use GPIO 25. Other carriers may use a
# different line; set SX1302_RESET_GPIO or edit this script.
#
# The line is addressed by GPIO controller and line offset through libgpiod v2,
# which avoids the legacy sysfs global numbering. On Raspberry Pi 5 the header
# GPIOs are on gpiochip4.
#
# Requires libgpiod v2 (gpiod). Raspberry Pi OS Trixie and newer provide it; on
# Bookworm install gpiod from bookworm-backports.
#
# Usage:
#   ./reset_rak2287.sh start
#   ./reset_rak2287.sh stop
#
# Environment overrides:
#   SX1302_RESET_GPIO   line offset within the controller (default 17)
#   SX1302_GPIO_CHIP    gpiochip name, e.g. gpiochip0 or gpiochip4
#   SX1302_RESET_WAIT   seconds to hold each level (default 0.1)
#
# This script intentionally does not touch a power-enable line. The RAK2287
# module is powered by its carrier.

set -euo pipefail

ACTION="${1:-start}"
GPIO="${SX1302_RESET_GPIO:-17}"
GPIO_CHIP="${SX1302_GPIO_CHIP:-}"
WAIT="${SX1302_RESET_WAIT:-0.1}"

usage() {
    printf 'usage: %s {start|stop}\n' "$(basename "$0")" >&2
}

if [[ "$ACTION" == "-h" || "$ACTION" == "--help" ]]; then
    usage
    exit 0
fi

if [[ "$ACTION" != "start" && "$ACTION" != "stop" ]]; then
    usage
    exit 2
fi

if ! [[ "$GPIO" =~ ^[0-9]+$ ]]; then
    printf 'error: SX1302_RESET_GPIO must be a numeric line offset\n' >&2
    exit 2
fi

if ! [[ "$WAIT" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    printf 'error: SX1302_RESET_WAIT must be a non-negative number of seconds\n' >&2
    exit 2
fi

# gpioset v2 takes an integer hold period in milliseconds and rejects decimal
# seconds, so convert here.
wait_whole="${WAIT%%.*}"
wait_frac=""
if [[ "$WAIT" == *.* ]]; then
    wait_frac="${WAIT#*.}"
fi
wait_frac="${wait_frac}000"
wait_frac="${wait_frac:0:3}"
WAIT_MS=$((10#${wait_whole} * 1000 + 10#${wait_frac}))
if ((WAIT_MS < 1)); then
    WAIT_MS=1
fi

if [[ -z "$GPIO_CHIP" ]]; then
    model=""
    if [[ -r /proc/device-tree/model ]]; then
        model="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
    fi
    case "$model" in
        *"Raspberry Pi 5"* | *"Compute Module 5"*)
            GPIO_CHIP="gpiochip4"
            ;;
        *)
            GPIO_CHIP="gpiochip0"
            ;;
    esac
fi

describe_line() {
    command -v gpioinfo >/dev/null 2>&1 || return 0

    local info
    info="$(gpioinfo "$GPIO_CHIP" 2>/dev/null |
        grep -E "^[[:space:]]*line[[:space:]]+${GPIO}:" || true)"
    if [[ -n "$info" ]]; then
        printf 'line: %s\n' "$info" >&2
    else
        printf 'line: %s has no offset %s\n' "$GPIO_CHIP" "$GPIO" >&2
    fi
}

if ! command -v gpioset >/dev/null 2>&1; then
    printf 'error: gpioset not found; install libgpiod v2 (gpiod)\n' >&2
    exit 1
fi

if [[ ! -e "/dev/${GPIO_CHIP}" ]]; then
    printf 'error: /dev/%s does not exist\n' "$GPIO_CHIP" >&2
    printf 'hint: run "gpioinfo" (or "ls /dev/gpiochip*") and set SX1302_GPIO_CHIP\n' >&2
    exit 1
fi

# The RAK2287 exposes an active-high SX1302 reset input: high asserts reset,
# low lets the chip run. gpioset's toggle sequence starts at the given value,
# toggles after each period, and exits when the next period is 0. Starting at 0
# therefore gives low -> high -> low, ending in the run state.
gpioset_pulse() {
    gpioset -t "${WAIT_MS}ms,${WAIT_MS}ms,0" -c "$GPIO_CHIP" "${GPIO}=0"
}

if gpioset_pulse; then
    # Let the SX1302 finish booting before the HAL opens SPI.
    sleep "$WAIT"
    exit 0
fi

printf 'error: could not drive GPIO %s on %s\n' "$GPIO" "$GPIO_CHIP" >&2
describe_line
printf 'hint: if the line is claimed by another driver, disable that overlay or set SX1302_RESET_GPIO/SX1302_GPIO_CHIP\n' >&2
exit 1
