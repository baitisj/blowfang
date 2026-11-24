"""SURF on the UV-21 Pro port (radios/uv21/surf/uv21_surf.c, the port's main screen), run in the
emulated board of sim/uv21/board.py: the real image, virtual time from its own SysTick waits, and
a model band (made-up stations and a CW wake beacon on 440.850).

What must hold (Jeff, 2026-11-24): the radio boots into SURF; UP / DOWN move the listening
frequency a channel; the top side key jumps to a higher peak and the bottom one to a lower; SURF
hears the CW wake on 440.850 while it surfs; EXIT goes to the VFO screen (on SURF's frequency) and
A/B comes back; and nothing in it ever gives the RF chip a transmit setting."""
import os
import re
import sys

import pytest

pytest.importorskip("unicorn")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim.uv21.board import Station, UV21Board, build  # noqa: E402

REG_STATE, REG_GPIO, REG_PA = 0x30, 0x33, 0x36


@pytest.fixture(scope="module")
def built():
    return build()


def until(board, text, seconds, chunk=1_000_000):
    """run until the serial line shows text (a regex), or seconds of virtual time pass"""
    out, end = "", board.seconds + seconds
    while board.seconds < end:
        out += board.step(chunk)
        m = re.search(text, out)
        if m:
            return m, out
    return None, out


def tap(board, key, settle=0.4):
    board.tap(key, 0.15)
    end = board.seconds + 0.15 + settle
    out = ""
    while board.seconds < end:
        out += board.step(1_000_000)
    return out


def sel(board):
    """MENU: SURF says the listening frequency, Hz"""
    board.tap("MENU", 0.15)
    m, out = until(board, r"sel (\d+)\.(\d+) ", 6)
    assert m, out[-300:]
    return int(m.group(1)) * 1_000_000 + int(m.group(2).ljust(6, "0"))


@pytest.fixture(scope="module")
def surfing(built):
    b = UV21Board(*built)
    out = b.run(until="boot done", limit=300_000_000)
    m, more = until(b, r"surf 70cm 439\.500 25k", 5)
    assert m, (out + more)[-400:]
    b.boot_text = out + more
    m, more = until(b, r"wake 440\.8500 (-?\d+) (-?\d+)", 20)        # the CW ident, while sweeping
    b.wake = m
    b.wake_text = more
    return b


def test_boots_into_surf(surfing):
    assert "blowfang uv21 port 7 build=" in surfing.boot_text
    assert "surf 70cm 439.500 25k" in surfing.boot_text


def test_hears_the_cw_wake_on_440850_while_surfing(surfing):
    assert surfing.wake, surfing.wake_text[-400:]
    level, floor = int(surfing.wake.group(1)), int(surfing.wake.group(2))
    assert level >= floor + 20 or level > -60
    assert "uv21 wake hz=1a46d650" in surfing.wake_text                 # 440,850,000


def test_up_and_down_move_a_channel(surfing):
    f0 = sel(surfing)
    tap(surfing, "UP")
    assert sel(surfing) == f0 + 25_000
    tap(surfing, "DOWN")
    tap(surfing, "DOWN")
    assert sel(surfing) == f0 - 25_000
    tap(surfing, "UP")


def test_side_keys_jump_to_the_next_peak_up_and_down(built):
    # three steady carriers round the start (439.500): the sweeps give each a caret
    b = UV21Board(*built, band=[Station("a", 439_700_000, -80), Station("b", 440_100_000, -85),
                                 Station("c", 439_200_000, -90)])
    b.run(until="boot done", limit=300_000_000)
    until(b, r"$^", 6)
    assert sel(b) == 439_500_000
    tap(b, "SIDE1", 1.0)                                                # top: the next peak up
    assert sel(b) == 439_700_000
    tap(b, "SIDE1", 1.0)
    assert sel(b) == 440_100_000
    tap(b, "BAND", 1.0)                                                 # bottom: the next peak down
    assert sel(b) == 439_700_000
    tap(b, "BAND", 1.0)
    assert sel(b) == 439_200_000


def test_exit_goes_to_the_vfo_screen_on_surfs_frequency_and_ab_comes_back(surfing):
    f = sel(surfing)
    surfing.tap("EXIT", 0.15)
    m, out = until(surfing, r"end sw=\d+", 3)
    assert m, out[-300:]
    until(surfing, r"$^", 1)
    assert abs(surfing.tuned_hz() - f) < 1000, (surfing.tuned_hz(), f)  # the VFO took it
    surfing.tap("AB", 0.15)
    m, out = until(surfing, r"surf \S+ \d+\.\d+ ", 4)
    assert m, out[-300:]


def test_never_gives_the_rf_chip_a_transmit_setting(surfing):
    assert surfing.tx_watch, "no state writes seen"
    for reg, v in surfing.tx_watch:
        if reg == REG_STATE:
            assert not (v & 0x00F0 and v & 0x000E), hex(v)
        elif reg == REG_PA:
            assert v == 0x0022, hex(v)
        elif reg == REG_GPIO:
            assert not v & 0x001C, hex(v)
