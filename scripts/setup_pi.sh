#!/usr/bin/env bash
#
# Set up a Raspberry Pi for the SX1302 Reticulum interface.
#
# Run from a checkout of this repository as the user who will run Reticulum.
# The script installs system packages, enables SPI/I2C, creates and populates
# ./.venv, builds the patched shared HAL, runs the unit tests and installs the
# Reticulum interface shim.
#
# System steps use sudo. Re-running the script is safe: completed steps are
# skipped or repeated idempotently.
#
# Usage:
#   ./scripts/setup_pi.sh [options]
#
# Options:
#   --configdir DIR   Reticulum config directory (default: $RNS_CONFIGDIR or
#                     ~/.reticulum)
#   --python PATH     Base interpreter for the virtual environment
#                     (default: python3)
#   --skip-system     Skip apt, raspi-config and group changes
#   --skip-build      Reuse an existing build/libloragw.so
#   --skip-tests      Do not run the unit tests
#   --skip-interface  Do not install the Reticulum interface shim
#   -h, --help        Show this help

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

CONFIGDIR="${RNS_CONFIGDIR:-$HOME/.reticulum}"
BASE_PYTHON="python3"
SKIP_SYSTEM=0
SKIP_BUILD=0
SKIP_TESTS=0
SKIP_INTERFACE=0
NEEDS_REBOOT=0
NEEDS_RELOGIN=0
ACTION_REQUIRED=0

log()  { printf '==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
    cat <<'EOF'
Usage: scripts/setup_pi.sh [options]

Set up a Raspberry Pi for the SX1302 Reticulum interface. Run from a checkout
of the repository as the user who will run Reticulum.

Options:
  --configdir DIR   Reticulum config directory (default: $RNS_CONFIGDIR or
                    ~/.reticulum)
  --python PATH     Base interpreter for the virtual environment
                    (default: python3)
  --skip-system     Skip apt, raspi-config and group changes
  --skip-build      Reuse an existing build/libloragw.so
  --skip-tests      Do not run the unit tests
  --skip-interface  Do not install the Reticulum interface shim
  -h, --help        Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --configdir)
            [[ $# -ge 2 ]] || fail "--configdir requires a directory"
            CONFIGDIR="$2"
            shift 2
            ;;
        --configdir=*)
            CONFIGDIR="${1#*=}"
            shift
            ;;
        --python)
            [[ $# -ge 2 ]] || fail "--python requires an interpreter"
            BASE_PYTHON="$2"
            shift 2
            ;;
        --python=*)
            BASE_PYTHON="${1#*=}"
            shift
            ;;
        --skip-system)    SKIP_SYSTEM=1; shift ;;
        --skip-build)     SKIP_BUILD=1; shift ;;
        --skip-tests)     SKIP_TESTS=1; shift ;;
        --skip-interface) SKIP_INTERFACE=1; shift ;;
        -h|--help)        usage; exit 0 ;;
        *)                fail "unknown argument: $1 (try --help)" ;;
    esac
done

[[ -n "$CONFIGDIR" ]] || fail "the configuration directory must not be empty"
[[ -d "$ROOT/vendor/sx1302_hal" ]] || fail "run this script from a full checkout of the repository"

if [[ "$EUID" -eq 0 ]]; then
    fail "run this script as the user who will run Reticulum, not as root (it uses sudo itself)"
fi

is_pi() {
    [[ -r /proc/device-tree/model ]] &&
        grep -qi "raspberry pi" /proc/device-tree/model
}

# ---------------------------------------------------------------------------
# System packages, SPI/I2C and device groups
# ---------------------------------------------------------------------------

check_libgpiod() {
    local version
    version="$(gpioset --version 2>/dev/null || true)"

    if [[ "$version" == *" v2."* ]]; then
        note "libgpiod: $version"
        return
    fi

    if [[ "$version" == *" v1."* ]] &&
        grep -rqs "bookworm-backports" /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null; then
        log "Installing libgpiod v2 from bookworm-backports"
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -t bookworm-backports gpiod
        version="$(gpioset --version 2>/dev/null || true)"
        if [[ "$version" == *" v2."* ]]; then
            note "libgpiod: $version"
            return
        fi
    fi

    warn "the reset script needs libgpiod v2; found: ${version:-nothing}"
    note "Trixie and newer already ship it."
    note "On Bookworm, enable bookworm-backports and then run:"
    note "  sudo apt install -t bookworm-backports gpiod"
    ACTION_REQUIRED=1
}

enable_raspi_features() {
    if ! have raspi-config; then
        note "raspi-config not found; skipping SPI/I2C enablement"
        return
    fi

    if sudo raspi-config nonint get_spi; then
        note "SPI is already enabled"
    else
        log "Enabling SPI"
        sudo raspi-config nonint do_spi 0
        NEEDS_REBOOT=1
    fi

    if sudo raspi-config nonint get_i2c; then
        note "I2C is already enabled"
    else
        log "Enabling I2C"
        sudo raspi-config nonint do_i2c 0
        NEEDS_REBOOT=1
    fi
}

fix_groups() {
    local user group
    user="$(id -un)"

    for group in spi gpio i2c; do
        getent group "$group" >/dev/null 2>&1 || continue

        if id -nG "$user" | tr ' ' '\n' | grep -qx "$group"; then
            continue
        fi

        log "Adding $user to the $group group"
        sudo usermod -aG "$group" "$user"
        NEEDS_RELOGIN=1
    done
}

setup_system() {
    if [[ "$SKIP_SYSTEM" -eq 1 ]]; then
        note "Skipping system setup"
        return
    fi

    if ! is_pi; then
        warn "this does not look like a Raspberry Pi; skipping system configuration"
        note "use --skip-system to silence this warning"
        return
    fi

    have sudo || fail "sudo is required for system setup; install it or use --skip-system"
    have apt-get || fail "apt-get not found; this script supports Debian-based systems only"

    log "Installing system packages"
    sudo apt-get update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
        git build-essential patch python3-venv python3-pip gpiod i2c-tools

    check_libgpiod
    enable_raspi_features
    fix_groups
}

# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------

VENV="$ROOT/.venv"

setup_python() {
    if [[ -x "$VENV/bin/python" ]]; then
        note "Using the existing virtual environment at $VENV"
    else
        log "Creating the virtual environment"
        "$BASE_PYTHON" -m venv "$VENV"
    fi

    log "Installing Python dependencies"
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet -e "$ROOT" pytest
}

# ---------------------------------------------------------------------------
# Build, tests and Reticulum integration
# ---------------------------------------------------------------------------

build_hal() {
    if [[ "$SKIP_BUILD" -eq 1 ]]; then
        [[ -f "$ROOT/build/libloragw.so" ]] ||
            fail "--skip-build was given but build/libloragw.so does not exist"
        note "Reusing $ROOT/build/libloragw.so"
        return
    fi

    log "Building the patched shared HAL"
    "$ROOT/scripts/build_hal.sh"
}

run_tests() {
    if [[ "$SKIP_TESTS" -eq 1 ]]; then
        note "Skipping unit tests"
        return
    fi

    log "Running unit tests"
    "$VENV/bin/python" -m pytest -q
}

install_interface() {
    if [[ "$SKIP_INTERFACE" -eq 1 ]]; then
        note "Skipping Reticulum interface installation"
        return
    fi

    log "Installing the Reticulum interface shim"
    PYTHON="$VENV/bin/python" "$ROOT/scripts/install_interface.sh" --configdir "$CONFIGDIR"
}

# ---------------------------------------------------------------------------

print_next_steps() {
    if [[ "$NEEDS_REBOOT" -eq 1 || "$NEEDS_RELOGIN" -eq 1 ]]; then
        printf '\n'
        log "Reboot or log out and back in"
        note "SPI/I2C and the new device groups only take effect in a fresh session."
        note "Then check the hardware with:"
        note "  ls -l /dev/spidev0.0 && gpioset --version && id"
        note "and re-run: $0 --skip-system"
    fi

    if [[ -f "$CONFIGDIR/config" ]] &&
        grep -Eq '^[[:space:]]*type[[:space:]]*=[[:space:]]*SX1302' "$CONFIGDIR/config"; then
        note "The Reticulum configuration already contains an SX1302 stanza"
    else
        printf '\n'
        log "Add the interface to $CONFIGDIR/config"
        if [[ -f "$CONFIGDIR/config" ]] &&
            grep -Eq '^\[interfaces\]' "$CONFIGDIR/config"; then
            note "Add only this block under the existing [interfaces] section:"
        else
            note "Add this to the configuration:"
        fi
        printf '\n'
        printf '[interfaces]\n\n'
        printf '  [[SX1302 LoRa]]\n'
        printf '    type = SX1302\n'
        printf '    enabled = yes\n'
        printf '    board_profile = %s/examples/rak2287-eu868.conf\n' "$ROOT"
        printf '\n'
    fi

    printf '\n'
    log "Next steps"
    note "Activate the environment in every shell that runs the interface:"
    note "  . $VENV/bin/activate"
    note ""
    note "Check the radio without Reticulum:"
    note "  ./scripts/smoke_test.py --config examples/rak2287-eu868.conf \\"
    note "    --rx-only --rx-timeout 15"
    note ""
    note "Then start Reticulum and inspect the interface:"
    note "  rnsd -v"
    note "  rnstatus"
    note ""
    note "See docs/raspberry-pi-testing.md for receive tests and troubleshooting."
}

setup_system
setup_python
build_hal
run_tests
install_interface
print_next_steps

if [[ "$ACTION_REQUIRED" -eq 1 ]]; then
    printf '\n'
    fail "setup finished, but the required steps above are still missing"
fi

printf '\n'
log "Setup complete"
