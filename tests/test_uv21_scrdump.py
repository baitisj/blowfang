"""The UV-21 Pro's screen read back over the cable (radios/uv21/mod/scrdump.c), in the emulated
board: the module bit-bangs RDDID and RAMRD on the panel's one data line, which the board answers
from the screen SPI2 drew; tools/screen_grab.py must rebuild the same pixels from both its packed
(c/common/scrgrab.h) and raw lines, and SURF must draw again afterwards (the module hands SCK and
SDA back to SPI2)."""
import os
import sys

import pytest

pytest.importorskip("unicorn")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import screen_grab  # noqa: E402
from modem import uv21mod  # noqa: E402
from test_uv21_mod import MOD, built, radio, upload  # noqa: E402,F401
from test_uv21_surf import wait  # noqa: E402

ROWS = 12


def dump(b, *defines):
    drawn = [[c or 0 for c in b.lfb[y][:128]] for y in range(ROWS)]
    img = uv21mod.image(*uv21mod.build(os.path.join(MOD, "scrdump.c"), [f"SD_ROWS={ROWS}", *defines]))
    ret, out = upload(b, img)
    assert ret == ROWS
    ident = screen_grab.ID.search(out)
    assert ident and (int(ident.group(1), 16) >> 7) & 0xFFFFFF == 0x7C89F0      # after the dummy bit
    return drawn, out


def test_scrdump_packs_what_surf_drew_and_surf_draws_again(built):  # noqa: F811
    b = radio(built)
    wait(b, 2)
    drawn, out = dump(b)
    w, h, fmt, px = screen_grab.grab(out)
    assert (w, h, fmt) == (128, ROWS, screen_grab.SCR_RGB565)
    assert [px[y] for y in range(ROWS)] == drawn
    for y in range(ROWS):
        b.lfb[y][:128] = [None] * 128
    wait(b, 3)
    assert any(b.lfb[y][x] is not None for y in range(ROWS) for x in range(128))


def test_scrdump_raw_lines_line_up_at_9_bits(built):  # noqa: F811
    b = radio(built)
    wait(b, 2)
    drawn, out = dump(b, "SD_RAW=1")
    rows, _ = screen_grab.parse(out)
    assert sorted(rows) == list(range(ROWS))
    bit, frac = screen_grab.align(rows)
    assert (bit, frac) == (9, 1.0)
    assert [screen_grab.pixels(rows, bit)[y] for y in range(ROWS)] == drawn
