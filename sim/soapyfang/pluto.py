"""The Pluto over libiio, hamlet's way: PlutoIioSource with the real tools/pluto_bench.py (quiet
first, the stored crystal correction, the bench's URI). A module of its own so that what the UV-21
tests import (they give PlutoIioSource a stand-in) doesn't pull in the bench tools.
SPDX-License-Identifier: MIT"""
from __future__ import annotations

import os
import sys

from .sources import PlutoIioSource

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "tools")


def open_pluto(spec="pluto", rate=None, center_hz=440_000_000, gain=None, dbm_offset=None):
    """'pluto' or 'pluto:URI' -> a PlutoIioSource (receive only)"""
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    import pluto_bench
    return PlutoIioSource(pluto_bench, spec[len("pluto:"):] or None, rate=rate or 2_400_000,
                          center_hz=center_hz, gain=50 if gain is None else gain, dbm_offset=dbm_offset)
