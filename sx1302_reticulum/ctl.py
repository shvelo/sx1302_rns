"""CLI command for SX1302 Reticulum setup and verification.

This module is installed as the ``sx1302-ctl`` console script by the
``sx1302-reticulum`` pip package. It checks that the HAL library, board
profile, and SPI access are available, and guides the user through any
remaining steps needed to get the radio running.

Typical usage (system Python, no venv required):

    pip install sx1302-reticulum
    sx1302-ctl --check     # verify everything is in order
    sx1302-ctl --init     # attempt automatic setup where possible
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .hal import find_library_path, Hal
from .config import BoardProfile


# ------------------------------------------------------------------ #
# Helper: guess the Reticulum config directory (fallback to standard) #
# ------------------------------------------------------------------ #

def _default_config_dir() -> Path:
    """Return the standard Reticulum config directory, or the user's home."""
    base = Path("/etc/reticulum")
    if base.is_dir():
        return base
    home = Path.home() / ".reticulum"
    if home.is_dir():
        return home
    return Path("/etc/reticulum")  # fallback; the caller will handle missing


# ------------------------------------------------------------------ #
# Automatic setup steps (best-effort)                              #
# ------------------------------------------------------------------ #

def _install_hal_library() -> None:
    """Print instructions for installing the HAL library manually."""
    so_path = Path("/home/nick/development/sx1302_hal/build/libloragw.so")
    if not so_path.is_file():
        print("  ! HAL library source not found: " + str(so_path))
        return
    print("  To install the HAL library manually:")
    print(f"    sudo cp {so_path} /usr/local/lib/libloragw.so")
    print("    sudo ldconfig")
    print("  Or set the environment variable:")
    print("    export SX1302_HAL_LIB=/usr/local/lib/libloragw.so")


def _attempt_automatic_setup(config_dir: Path) -> None:
    """Do what we can where we have privileges."""

    # 1) Install the HAL library (requires root)
    _install_hal_library()

    # 2) Create the Reticulum interface shim symlink (may need root for dest dir)
    shim_dst = config_dir / "interfaces" / "SX1302.py"
    try:
        shim_dst.parent.mkdir(parents=True, exist_ok=True)
        # Try to find the shim: first the installed package, then the source checkout
        shim_src = None
        try:
            import sx1302_reticulum
            package_shim = Path(sx1302_reticulum.__file__).parent / "interfaces" / "SX1302.py"
            if package_shim.is_file():
                # .so is now installed; the .py shim should be alongside it
                shim_src = package_shim
            else:
                shim_src = None
        except Exception:
            shim_src = None
        if shim_src is None:
            # Fallback: project checkout path (common when developing with --editable)
            project_root = Path("/home/nick/sx1302_rns")
            if (project_root / "interfaces" / "SX1302.py").is_file():
                shim_src = project_root / "interfaces" / "SX1302.py"
        if shim_src is not None:
            if not shim_dst.is_symlink() and not shim_dst.exists():
                os.symlink(str(shim_src), str(shim_dst))
                print(f"  ✓ Created shim: {shim_dst} -> {shim_src}")
            else:
                print(f"  ✓ Shim already present: {shim_dst}")
        else:
            print("  ! Could not locate SX1302.py shim source; skipping symlink.")
    except Exception as e:
        print(f"  ! Failed to create shim: {e}")

    # 3) Try to ensure the board profile exists
    profile_src = Path("/home/nick/sx1302_rns/examples/rak2287-eu868.conf")
    profile_dst = config_dir / "rak2287-eu868.conf"
    if profile_src.is_file():
        if not profile_dst.is_file():
            try:
                profile_dst.write_text(profile_src.read_text())
                print(f"  ✓ Copied board profile: {profile_dst}")
            except Exception as e:
                print(f"  ! Could not copy profile: {e}")
        else:
            print(f"  ✓ Profile already present: {profile_dst}")
    else:
        print(f"  ! Board profile source not found: {profile_src}")


# ------------------------------------------------------------------ #
# CLI entry point                                                    #
# ------------------------------------------------------------------ #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "SX1302 Reticulum setup and verification tool.\n"
            "Run ``sx1302-ctl --check`` to report status, or ``sx1302-ctl --init`` "
            "to attempt automatic setup where possible."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check that the setup is complete and report status",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help=(
            "Attempt automatic setup steps where possible. "
            "This creates the Reticulum interface shim symlink and copies a "
            "sample board profile into the config directory. "
            "Remaining steps (group membership, SPI enable, reboot) must be "
            "completed manually."
        ),
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="PATH",
        help=(
            "Path to the Reticulum configuration directory (default: the "
            "standard location, ``~/.reticulum/config`` or ``/etc/reticulum``)."
        ),
    )
    parser.add_argument(
        "--output-config",
        action="store_true",
        help="Output the board config file path and exit",
    ),
    args = parser.parse_args(argv)

    # Resolve the config directory
    config_dir = Path(args.profile) if args.profile else _default_config_dir()
    if not config_dir.is_dir():
        print(f"! Config directory not found: {config_dir}")
        print("  Create it or pass --profile /path/to/config")
        return 1

    # Output config path if requested
    if args.output_config:
        profile_path = config_dir / "rak2287-eu868.conf"
        if profile_path.is_file():
            print(profile_path)
        else:
            print(f"! Board profile not found: {profile_path}")
        return 0

    # ------------------------------------------------------------------
    # HAL library
    # ------------------------------------------------------------------
    try:
        hal = Hal()
        print(f"✓ HAL library found: {hal.path}")
    except FileNotFoundError as e:
        if args.init:
            print("HAL library not found — showing installation instructions...")
            _install_hal_library()
            print(
                "  After installing, run: sx1302-ctl --check"
            )
        else:
            print("✗ HAL library not found.")
            print("  Run: sx1302-ctl --init   (will show how to install the .so)")
            return 1

    # ------------------------------------------------------------------
    # Board profile
    # ------------------------------------------------------------------
    profile_path = config_dir / "rak2287-eu868.conf"
    if profile_path.is_file():
        try:
            profile = BoardProfile.from_ini(str(profile_path))
            print(f"✓ Board profile found: {profile_path}")
        except Exception as e:
            print(f"! Board profile present but failed to parse: {e}")
    else:
        print(f"! Board profile not found: {profile_path}")
        print("  Copy one from the project examples/ directory")

    # ------------------------------------------------------------------
    # SPI device
    # ------------------------------------------------------------------
    spi_path = "/dev/spidev0.0"
    if os.path.exists(spi_path):
        print(f"✓ SPI device found: {spi_path}")
    else:
        print(f"! SPI device not found: {spi_path}")
        print("  Enable SPI:")
        print("    • raspi-config → Interface Options → SPI → Yes")
        print("    • Or add 'dtparam=spi=on' to /boot/firmware/config.txt")

    # ------------------------------------------------------------------
    # User groups
    # ------------------------------------------------------------------
    result = subprocess.run(["id", "-Gn"], capture_output=True, text=True)
    groups = result.stdout.strip().split()
    has_spi = "spi" in groups
    has_gpio = "gpio" in groups
    print(f"  Supplementary groups: {groups}")
    if not has_spi or not has_gpio:
        print("  Note: Your user must be in the 'spi' and 'gpio' groups.")
        print("  Add them now and then log out / reboot:")
        print(f"    sudo usermod -aG spi,gpio $(whoami)")
        print("  (A new login session or reboot is required for the groups to take effect.)")

    # ------------------------------------------------------------------
    # Automatic setup (if --init)
    # ------------------------------------------------------------------
    if args.init:
        print("\n--- Attempting automatic setup ---")
        _attempt_automatic_setup(config_dir)
        print("--- Automatic setup complete ---")
        print(
            "  Review the output above. Steps that still require manual action "
            "are noted above (group membership, SPI enable, reboot)."
        )

    # ------------------------------------------------------------------
    # --check: just report status, return 0 if the essentials are present
    # ------------------------------------------------------------------
    if args.check:
        # Check HAL library is accessible
        hal_ok = True
        try:
            # Retry HAL in case --init just installed it
            hal_test = Hal()
        except FileNotFoundError:
            hal_ok = False

        essentials = [
            hal_ok,
            profile_path.is_file(),
            os.path.exists("/dev/spidev0.0"),
        ]
        if all(essentials):
            print("\nAll essential checks passed.")
            return 0
        else:
            print("\nSome essential checks failed. Run sx1302-ctl --check for details.")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())