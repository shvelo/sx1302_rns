#!/usr/bin/env bash
#
# Reset the SX1302 on a RAK2287 carrier.
#
# The RAK2287 Raspberry Pi HAT uses GPIO 17 for SX1302 reset. Other carriers
# may use a different line; set SX1302_RESET_GPIO or edit this script.
#
# The line is addressed by GPIO controller and line offset (libgpiod), not by
# the legacy sysfs global number. On Raspberry Pi 5 the global numbers are
# offset by the RP1 base, so writing "17" to /sys/class/gpio/export does not
# address header line 17 and the kernel rejects it with EINVAL.
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

# Find the sysfs chip directory that belongs to /dev/${GPIO_CHIP}. sysfs names
# chips by their global base (gpiochip512), which does not necessarily match
# the character-device index (gpiochip4).
sysfs_chip_dir() {
    local want major minor
    [[ -e "/dev/${GPIO_CHIP}" ]] || return 1
    want="$(stat -Lc '%t:%T' "/dev/${GPIO_CHIP}" 2>/dev/null)" || return 1

    local dir
    for dir in /sys/class/gpio/gpiochip*; do
        [[ -r "${dir}/dev" ]] || continue
        read -r major minor < "${dir}/dev" || continue
        if [[ "$(printf '%x:%x' "$major" "$minor")" == "$want" ]]; then
            printf '%s\n' "$dir"
            return 0
        fi
    done
    return 1
}

# Legacy sysfs fallback. Exports the line using the controller's global base so
# the write is valid on systems where the base is not zero.
sysfs_pulse() {
    local chip_dir base global dir
    chip_dir="$(sysfs_chip_dir)" || return 1
    base="$(cat "${chip_dir}/base" 2>/dev/null)" || return 1
    [[ "$base" =~ ^[0-9]+$ ]] || return 1

    global=$((base + GPIO))
    dir="/sys/class/gpio/gpio${global}"

    if [[ ! -d "$dir" ]]; then
        if ! printf '%s\n' "$global" > /sys/class/gpio/export 2>/dev/null; then
            # An already exported line makes the write fail with EBUSY but is
            # still usable; anything else is a real failure.
            [[ -d "$dir" ]] || return 1
        fi
        sleep "$WAIT"
    fi

    printf 'out\n' > "${dir}/direction" 2>/dev/null || return 1
    printf '0\n' > "${dir}/value" 2>/dev/null || return 1
    sleep "$WAIT"
    printf '1\n' > "${dir}/value" 2>/dev/null || return 1
    sleep "$WAIT"
    printf '0\n' > "${dir}/value" 2>/dev/null || return 1
    sleep "$WAIT"

    if [[ "$ACTION" == "stop" ]]; then
        # Release the sysfs handle after the final reset pulse.
        printf '%s\n' "$global" > /sys/class/gpio/unexport 2>/dev/null || true
    fi
}

detect_gpioset_major() {
    local version
    version="$(gpioset --version 2>/dev/null | head -n 1 || true)"
    if [[ "$version" =~ v2 ]]; then
        printf '2\n'
    else
        printf '1\n'
    fi
}

# libgpiod v1 and v2 disagree on how the chip is selected. In both versions
# "wait" mode keeps the line driven until the process is stopped, which gives a
# clean reset pulse instead of releasing the line right after each write. The
# line is released after the final low level; the carrier's pull-down keeps the
# SX1302 out of reset.
GPIO_SET_MAJOR=""
gpioset_hold() {
    local level="$1" pid
    if [[ "$GPIO_SET_MAJOR" == "2" ]]; then
        gpioset --mode=wait -c "$GPIO_CHIP" "${GPIO}=${level}" &
    else
        gpioset --mode=wait "$GPIO_CHIP" "${GPIO}=${level}" &
    fi
    pid=$!

    sleep "$WAIT"
    if ! kill -0 "$pid" 2>/dev/null; then
        wait "$pid" 2>/dev/null || true
        return 1
    fi

    kill -TERM "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
}

gpioset_pulse() {
    gpioset_hold 0 || return 1
    gpioset_hold 1 || return 1
    gpioset_hold 0 || return 1
}

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

if command -v gpioset >/dev/null 2>&1 && [[ -e "/dev/${GPIO_CHIP}" ]]; then
    GPIO_SET_MAJOR="$(detect_gpioset_major)"
    if gpioset_pulse; then
        exit 0
    fi
fi

if [[ -w /sys/class/gpio/export ]] && sysfs_pulse; then
    exit 0
fi

printf 'error: could not drive GPIO %s on %s\n' "$GPIO" "$GPIO_CHIP" >&2
describe_line
printf 'hint: if the line is claimed by another driver, disable that overlay or set SX1302_RESET_GPIO/SX1302_GPIO_CHIP\n' >&2
exit 1
