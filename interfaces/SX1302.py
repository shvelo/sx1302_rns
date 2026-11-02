"""Reticulum external interface shim for SX1302 concentrators.

Reticulum exec()s this file with ``RNS`` and ``Interface`` in its globals and
then reads ``interface_class``. Keep it minimal and use absolute imports; the
``sx1302_reticulum`` package must be importable, for example with
``python3 -m pip install -e .`` or ``PYTHONPATH``.
"""

from sx1302_reticulum.interface import SX1302Interface

interface_class = SX1302Interface
