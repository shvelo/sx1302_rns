"""Meshcore adapter shim for SX1302 concentrators.

This shows how option 1 works: Meshcore can use the same ``Radio`` class
as Reticulum, only the adapter layer (packet encoding/decoding, callbacks)
is different. A full Meshcore integration would implement ``meshcore_rx()``
and ``meshcore_tx()`` using Meshcore's packet format.

For now this is a structural template. It loads the ``Radio`` class and
exposes the interface loader contract Meshcore uses.
"""

import sys
import importlib.util

# Ensure the package is importable regardless of how Meshcore loads this.
try:
    from sx1302_reticulum import Radio
except ImportError:
    # Fallback: assume PYTHONPATH includes the repo root.
    pass

# Meshcore would set its globals before exec()ing this file,
# then read ``interface_class``. This adapter maps Meshcore packets
# to the ``Radio`` instance.


def interface_class(config_obj):
    """Return a Meshcore adapter backed by ``Radio``.

    Meshcore passes its config object (not Reticulum's). The adapter
    creates a ``Radio`` instance using the board profile specified
    in the Meshcore config or defaults to the RAK2287 profile.
    """
    # Placeholder: actual Meshcore adapter would read board_profile
    # from Meshcore config and set up callbacks.
    from sx1302_reticulum.interface import SX1302Interface
    return SX1302Interface
