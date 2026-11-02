# Vendored upstream code

`vendor/sx1302_hal/` contains Semtech's SX1302 HAL and gateway utilities,
version 2.1.0.

- Upstream: `git@github.com:Lora-net/sx1302_hal.git`
- Vendor import: the repository state at commit `4b42025` on the original
  branch, before the project split.
- License: Revised BSD, see `sx1302_hal/LICENSE.TXT`.

## Policy

Keep this tree as close to upstream as possible so it can be updated by
replacing the directory. Project-specific changes should be small, reviewed,
and stored as patch files under `../patches/`. The HAL build script applies
those patches to a build copy rather than editing this tree in place.

The vendored tree keeps its own `.gitignore` for upstream build artifacts.
