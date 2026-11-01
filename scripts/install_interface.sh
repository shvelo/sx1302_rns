#!/usr/bin/env bash
#
# Install the Reticulum external interface shim for the SX1302 interface.
#
# Usage:
#   ./scripts/install_interface.sh [--configdir DIR] [--editable]
#
# The shim is symlinked into <configdir>/interfaces/SX1302.py. Reticulum loads
# external interfaces from that directory. --editable also installs this
# checkout into the active Python environment so the absolute import in the
# shim works.
#
# The configuration directory defaults to $RNS_CONFIGDIR or ~/.reticulum.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIGDIR="${RNS_CONFIGDIR:-$HOME/.reticulum}"
PYTHON="${PYTHON:-python3}"
EDITABLE=0

usage() {
    printf 'usage: %s [--configdir DIR] [--editable]\n' "$(basename "$0")" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --configdir)
            if [[ $# -lt 2 ]]; then
                printf 'error: --configdir requires a directory\n' >&2
                exit 2
            fi
            CONFIGDIR="$2"
            shift 2
            ;;
        --configdir=*)
            CONFIGDIR="${1#*=}"
            shift
            ;;
        -e|--editable)
            EDITABLE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'error: unknown argument: %s\n' "$1" >&2
            usage
            exit 2
            ;;
    esac
done

if [[ -z "$CONFIGDIR" ]]; then
    printf 'error: configuration directory must not be empty\n' >&2
    exit 2
fi

if [[ "$EDITABLE" == 1 ]]; then
    printf 'Installing %s into %s...\n' "$ROOT" "$PYTHON"
    "$PYTHON" -m pip install --no-deps -e "$ROOT"
fi

SHIM="$ROOT/interfaces/SX1302.py"
DEST="$CONFIGDIR/interfaces/SX1302.py"

if [[ ! -f "$SHIM" ]]; then
    printf 'error: shim not found: %s\n' "$SHIM" >&2
    exit 1
fi

mkdir -p "$CONFIGDIR/interfaces"

if [[ -L "$DEST" ]]; then
    current="$(readlink -f "$DEST")"
    if [[ "$current" == "$(readlink -f "$SHIM")" ]]; then
        printf 'Shim already installed: %s\n' "$DEST"
    else
        printf 'error: %s already points to %s; remove it first\n' "$DEST" "$current" >&2
        exit 1
    fi
elif [[ -e "$DEST" ]]; then
    printf 'error: %s exists and is not a symlink; remove it first\n' "$DEST" >&2
    exit 1
else
    ln -s "$SHIM" "$DEST"
    printf 'Installed shim: %s -> %s\n' "$DEST" "$SHIM"
fi

if ! "$PYTHON" -c "import sx1302_reticulum" >/dev/null 2>&1; then
    printf '\nwarning: %s cannot import sx1302_reticulum.\n' "$PYTHON"
    printf 'Install this checkout with:\n'
    printf '  %s -m pip install --no-deps -e %s\n' "$PYTHON" "$ROOT"
    printf 'or run Reticulum with PYTHONPATH=%s\n' "$ROOT"
fi

printf '\nAdd an [[SX1302 ...]] stanza to %s/config.\n' "$CONFIGDIR"
printf 'See %s/examples/reticulum-sx1302.conf.\n' "$ROOT"
