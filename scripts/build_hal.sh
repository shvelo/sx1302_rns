#!/usr/bin/env bash
#
# Build a PIC shared version of the vendored libloragw HAL.
#
# The vendored tree is copied to build/sx1302_hal, project patches are applied,
# and the result is compiled into build/libloragw.so. The vendor tree itself is
# left untouched.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${BUILD_DIR:-$ROOT/build}"
SRC="$BUILD_DIR/sx1302_hal"
VENDOR="$ROOT/vendor/sx1302_hal"
CC="${CC:-cc}"
NM="${NM:-nm}"

log() {
    printf '==> %s\n' "$*"
}

if [[ ! -d "$VENDOR" ]]; then
    printf 'error: vendored source tree not found: %s\n' "$VENDOR" >&2
    exit 1
fi

log "Preparing build copy"
rm -rf "$SRC"
mkdir -p "$BUILD_DIR"
cp -a "$VENDOR" "$SRC"

if compgen -G "$ROOT/patches/*.patch" >/dev/null; then
    for patch_file in "$ROOT"/patches/*.patch; do
        log "Applying $(basename "$patch_file")"
        patch --batch --forward -p1 -d "$SRC" < "$patch_file"
    done
else
    log "No patches to apply"
fi

TOOLS_CFLAGS="-O2 -Wall -Wextra -std=c99 -fPIC -Iinc -I."
HAL_CFLAGS="-O2 -Wall -Wextra -std=c99 -fPIC -Iinc -I. -I../libtools/inc"

log "Building libtools (PIC)"
make -C "$SRC/libtools" clean
make -C "$SRC/libtools" all CFLAGS="$TOOLS_CFLAGS"

log "Building libloragw objects (PIC)"
make -C "$SRC/libloragw" clean
make -C "$SRC/libloragw" libloragw.a CFLAGS="$HAL_CFLAGS"

log "Linking shared HAL"
"$CC" -shared \
    -Wl,-soname,libloragw.so \
    -Wl,--no-undefined \
    -o "$BUILD_DIR/libloragw.so" \
    "$SRC"/libloragw/obj/*.o \
    "$SRC"/libtools/libtinymt32.a \
    -lm -lrt

log "Exported HAL symbols"
"$NM" -D --defined-only "$BUILD_DIR/libloragw.so" |
    awk '$2 ~ /^[TW]$/ && $3 ~ /^lgw_/ { print "    " $3 }' |
    sort

if [[ "${SKIP_ABI_CHECK:-0}" != "1" ]]; then
    log "Checking ctypes ABI"
    python3 "$ROOT/scripts/abi_check.py" --source "$SRC"
fi

log "Checking shared library load"
python3 - "$BUILD_DIR/libloragw.so" <<'PY'
import ctypes
import sys

lib = ctypes.CDLL(sys.argv[1])
lib.lgw_version_info.restype = ctypes.c_char_p
version = lib.lgw_version_info()
if not version:
    raise SystemExit("lgw_version_info() returned NULL")
print("    loaded %s, %s" % (sys.argv[1], version.decode("utf-8", "replace")))
PY

log "Built $BUILD_DIR/libloragw.so"
