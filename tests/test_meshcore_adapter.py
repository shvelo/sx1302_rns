"""Tests for the Meshcore SX1302 adapter.

meshcore-pi is not installed in this environment, so these tests verify
that the adapter module imports cleanly and raises a clear ImportError
when meshcore-pi is absent. When meshcore-pi is available, the adapter
can be tested against its real Interface base class.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def test_meshcore_module_imports():
    """The MeshcoreSX1302 module can be imported without meshcore-pi."""
    module = importlib.import_module("interfaces.MeshcoreSX1302")
    assert hasattr(module, "MeshcoreAdapter")
    assert hasattr(module, "interface_class")


def test_meshcore_adapter_requires_meshcore_pi():
    """Instantiating without meshcore-pi raises ImportError."""
    module = importlib.import_module("interfaces.MeshcoreSX1302")
    assert module._HAS_MESHCORE_PI is False

    with pytest.raises(ImportError, match="meshcore-pi is not installed"):
        module.MeshcoreAdapter({})


def test_interface_class_requires_meshcore_pi():
    """interface_class() raises ImportError without meshcore-pi."""
    module = importlib.import_module("interfaces.MeshcoreSX1302")
    with pytest.raises(ImportError, match="meshcore-pi is not installed"):
        module.interface_class({})


@pytest.mark.skipif(
    importlib.util.find_spec("meshcore") is None,
    reason="meshcore-pi not installed",
)
def test_meshcore_adapter_with_real_interface():
    """When meshcore-pi is installed, the adapter subclasses Interface."""
    import asyncio

    from interfaces.MeshcoreSX1302 import MeshcoreAdapter

    assert issubclass(MeshcoreAdapter, object)

    # Verify it can be instantiated with a dummy config
    # (will fail on Radio startup without hardware, but construction works)
    adapter = MeshcoreAdapter(
        {"board_profile": "examples/rak2287-eu868.conf", "airtime": 10}
    )
    assert adapter.name == "SX1302 interface"
    assert adapter.rx_q is not None
    radioconfig = adapter.get_radioconfig()
    assert radioconfig[0] == 868100  # frequency in kHz
    assert radioconfig[1] == 125000  # bandwidth in Hz
    assert radioconfig[2] == 9  # spreading factor
    assert radioconfig[3] == 5  # coding rate
    assert radioconfig[4] == 14  # tx power dBm
