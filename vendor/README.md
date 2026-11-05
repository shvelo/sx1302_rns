# Vendored upstream code

`vendor/sx1302_hal/` is a git submodule containing Semtech's SX1302 HAL and
gateway utilities, version 2.1.0.

- Upstream: `https://github.com/Lora-net/sx1302_hal.git`
- Pinned at commit `4b42025d1751e04632c0b04160e0d29dbbb222a5` (v2.1.0).
- License: Revised BSD, see `sx1302_hal/LICENSE.TXT`.

## Policy

Keep this tree as close to upstream as possible. Update it with
`git submodule update --remote vendor/sx1302_hal` or by re-pointing the
submodule. Project-specific changes should be small, reviewed, and stored as
patch files under `../patches/`. The HAL build script applies those patches to
a build copy rather than editing this tree in place.

The vendored tree keeps its own `.gitignore` for upstream build artifacts.
