"""The UV-21 Pro's screen read back over the cable (radios/uv21/mod/scrdump.c), in the emulated
board: the module bit-bangs RDDID and RAMRD on the panel's one data line, which the board answers
from the screen SPI2 drew; tools/uv21_screen.py must rebuild the same pixels, and SURF must draw
again afterwards (the module hands SCK and SDA back to SPI2)."""
import os
import sys

import pytest

pytest.importorskip("unicorn")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import uv21_screen  # noqa: E402
from modem import uv21mod  # noqa: E402
from test_uv21_mod import MOD, built, radio, upload  # noqa: E402,F401
from test_uv21_surf import wait  # noqa: E402

ROWS = 12


def test_scrdump_reads_back_what_surf_drew(built):  # noqa: F811
    b = radio(built)
    wait(b, 2)
    drawn = [list(b.lfb[y][:128]) for y in range(ROWS)]
    img = uv21mod.image(*uv21mod.build(os.path.join(MOD, "scrdump.c"), [f"SD_ROWS={ROWS}"]))
    ret, out = upload(b, img)
    assert ret == ROWS
    rows, ident = uv21_screen.parse(out)
    assert ident is not None and (ident >> 7) & 0xFFFFFF == 0x7C89F0      # after the dummy bit
    assert sorted(rows) == list(range(ROWS))
    bit, frac = uv21_screen.align(rows)
    assert frac == 1.0
    px = uv21_screen.pixels(rows, bit)
    assert all(px[y] == [c or 0 for c in drawn[y]] for y in range(ROWS))
    # SPI2 has its pins back: SURF draws again
    for y in range(ROWS):
        b.lfb[y][:128] = [None] * 128
    wait(b, 3)
    assert any(b.lfb[y][x] is not None for y in range(ROWS) for x in range(128))
