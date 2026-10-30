#!/usr/bin/env bash
#
# Reset the SX1302 on a RAK2287 carrier.
#
# The RAK2287 Raspberry Pi HAT uses GPIO 17 for SX1302 reset. Other carriers
# may use a different line; set SX1302_RESET_GPIO or edit this script.
#
# Usage:
#   ./reset_rak2287.sh start
#   ./reset_rak2287.sh stop
#
# This script intentionally does not touch a power-enable line. The RAK2287
# module is powered by its carrier.

set -euo pipefail

ACTION="${1:-start}"
GPIO="${SX1302_RESET_GPIO:-17}"
GPIO_CHIP="${SX1302_GPIO_CHIP:-}"
WAIT="${SX1302_RESET_WAIT:-0.1}"

if [[ "$ACTION" != "start" && "$ACTION" != "stop" ]]; then
    printf 'usage: %s {start|stop}\n' "$(basename "$0")" >&2
    exit 2
fi

if [[ -z "$GPIO_CHIP" ]]; then
    model="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || true)"
    if [[ "$model" == *"Raspberry Pi 5"* ]]; then
        GPIO_CHIP="gpiochip4"
    else
        GPIO_CHIP="gpiochip0"
    fi
fi

export_gpio() {
    if [[ ! -d "/sys/class/gpio/gpio${GPIO}" ]]; then
        printf '%s\n' "$GPIO" > /sys/class/gpio/export
        sleep "$WAIT"
    fi
    printf 'out\n' > "/sys/class/gpio/gpio${GPIO}/direction"
}

set_gpio() {
    printf '%s\n' "$1" > "/sys/class/gpio/gpio${GPIO}/value"
    sleep "$WAIT"
}

pulse_reset() {
    # The SX1302 reset input is active high. End the sequence low so the chip
    # is released and can boot.
    set_gpio 0
    set_gpio 1
    set_gpio 0
}

if [[ -w /sys/class/gpio/export ]]; then
    export_gpio
    pulse_reset
    if [[ "$ACTION" == "stop" ]]; then
        # Release the sysfs handle after the final reset pulse.
        printf '%s\n' "$GPIO" > /sys/class/gpio/unexport 2>/dev/null || true
    fi
    exit 0
fi

if command -v gpioset >/dev/null 2>&1; then
    # libgpiod v1 syntax, matching the RAK reference script.
    gpioset "$GPIO_CHIP" "${GPIO}=0"
    sleep "$WAIT"
    gpioset "$GPIO_CHIP" "${GPIO}=1"
    sleep "$WAIT"
    gpioset "$GPIO_CHIP" "${GPIO}=0"
    sleep "$WAIT"
    exit 0
fi

printf 'error: cannot access sysfs GPIO or gpioset for GPIO %s\n' "$GPIO" >&2
exit 1
