"""SURF on the UV-21 Pro port (radios/uv21/surf/uv21_surf.c, the port's main screen), run in the
emulated board of sim/uv21/board.py: the real image, virtual time from its own SysTick waits, and
a model band (made-up stations and a CW wake beacon on 440.850).

What must hold (Jeff, 2026-11-24): the radio boots into SURF; UP / DOWN move the listening
frequency a channel; the top side key jumps to a higher peak and the bottom one to a lower; SURF
hears the CW wake on 440.850 while it surfs; SURF is the only screen (port 14: no VFO), so EXIT
leaves it in SURF; and nothing in it ever gives the RF chip a transmit setting."""
import os
import re
import sys

import pytest

pytest.importorskip("unicorn")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim.uv21.board import Station, UV21Board, build  # noqa: E402

REG_STATE, REG_GPIO, REG_PA = 0x30, 0x33, 0x36
AF_FM = 0x6140                  # REG_47 with the audio open on FM (AF_MUTE 0x6040: shut)


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


def wait(board, seconds):
    """run seconds of virtual time"""
    end = board.seconds + seconds
    while board.seconds < end:
        board.step(1_000_000)


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
    assert "blowfang uv21 port 14 build=" in surfing.boot_text
    assert "surf 70cm 439.500 25k" in surfing.boot_text


def test_hears_the_cw_wake_on_440850_while_surfing(surfing):
    assert surfing.wake, surfing.wake_text[-400:]
    level, floor = int(surfing.wake.group(1)), int(surfing.wake.group(2))
    assert level >= floor + 20 or level > -60
    assert "uv21 wake hz=1a46d650" in surfing.wake_text                 # 440,850,000
    # and WAKE HEARD on the bottom line (cyan), the only wake text SURF shows (Jeff, 2026-11-24)
    wait(surfing, 3)
    rows = surfing.screen_rgb()[152:160]
    assert any(b > 200 and g > 150 and r < 160 for row in rows for r, g, b in row)


def test_bottom_line_is_blank_until_a_wake_is_heard(built):
    # no standing "WAKE 440.850" (Jeff, 2026-11-24: "totally confusing"): a band with no wake beacon
    b = UV21Board(*built, band=[Station("a", 441_200_000, -80)])
    b.run(until="boot done", limit=300_000_000)
    wait(b, 6)
    rows = b.screen_rgb()[152:160]
    assert all(px == (0, 0, 0) for row in rows for px in row)


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
    wait(b, 6)
    assert sel(b) == 439_500_000
    tap(b, "SIDE1", 1.0)                                                # top: the next peak up
    assert sel(b) == 439_700_000
    tap(b, "SIDE1", 1.0)
    assert sel(b) == 440_100_000
    tap(b, "BAND", 1.0)                                                 # bottom: the next peak down
    assert sel(b) == 439_700_000
    tap(b, "BAND", 1.0)
    assert sel(b) == 439_200_000


def test_level_is_drawn_once_it_settles_not_on_every_read(built):
    # a carrier on the start frequency: SURF parks on it and plays it. Its reads wander (1 dB of
    # noise), but the level on the screen is taken 0.1 s after the squelch opens and then left
    # alone (Jeff, 2026-11-24), and the frequency beside it is never redrawn
    b = UV21Board(*built, band=[Station("a", 439_500_000, -80)])
    b.run(until="boot done", limit=300_000_000)
    wait(b, 4)

    def strip(x0, x1):
        return [row[x0:x1] for row in b.screen_rgb()[11:22]]
    level, freq = strip(58, 128), strip(0, 58)
    assert any(px != (0, 0, 0) for row in level for px in row), "no level drawn"
    wait(b, 4)
    assert strip(58, 128) == level
    assert strip(0, 58) == freq


def test_axis_labels_are_the_top_and_bottom_frequencies(surfing):
    # absolute MHz at the top and bottom bins (Jeff, 2026-11-24: "show e.g. 463.5 and 461.0"),
    # drawn in the 5x5 font in the label column: something lit there, at both ends
    rows = surfing.screen_rgb()
    for y0 in (22, 146):
        assert any(px not in ((0, 0, 0), (40, 0, 40)) for row in rows[y0:y0 + 6] for px in row[0:19]), y0


def test_exit_stays_in_surf(surfing):
    # no VFO screen since port 14 (Jeff, 2026-11-24: "Take out the VFO code from the UV-21 port")
    f = sel(surfing)
    out = tap(surfing, "EXIT", 1.0)
    assert "end sw=" not in out, out[-300:]
    assert sel(surfing) == f


def test_never_gives_the_rf_chip_a_transmit_setting(surfing):
    assert surfing.tx_watch, "no state writes seen"
    for reg, v in surfing.tx_watch:
        if reg == REG_STATE:
            assert not (v & 0x00F0 and v & 0x000E), hex(v)
        elif reg == REG_PA:
            assert v == 0x0022, hex(v)
        elif reg == REG_GPIO:
            assert not v & 0x001C, hex(v)


def test_a_typed_custom_range_is_at_most_16_mhz(surfing):
    # VFO/MR 430 # 460 MENU: 30 MHz asked, the middle 16 MHz kept (Jeff, 2026-11-24: recentre's
    # offsets reach +-16 MHz, "more than enough bandwidth")
    out = "".join(tap(surfing, k, 0.2) for k in ["VM", "4", "3", "0", "#", "4", "6", "0", "MENU"])
    m = re.search(r"user (\d+\.\d+) (\d+\.\d+)", out)
    if not m:
        m, more = until(surfing, r"user (\d+\.\d+) (\d+\.\d+)", 4)
        out += more
    assert m, out[-300:]
    assert (m.group(1), m.group(2)) == ("437.000", "453.000")


def user_line(board, out):
    m = re.search(r"user (\d+\.\d+) (\d+\.\d+)", out)
    if not m:
        m, more = until(board, r"user (\d+\.\d+) (\d+\.\d+)", 4)
        out += more
    assert m, out[-300:]
    return m.group(1), m.group(2)


def test_long_pound_types_a_frequency_that_centres_custom(built):
    # Jeff, 2026-11-24: "pressing and holding the pound key function as a VFO ... this frequency
    # becomes the new center of the scan range and jumps to CUSTOM scan mode"
    b = UV21Board(*built, band=[Station("a", 441_200_000, -80)])
    b.run(until="boot done", limit=300_000_000)
    wait(b, 2)
    b.tap("#", 1.3)                                                     # held: frequency entry
    wait(b, 1.6)
    out = "".join(tap(b, k, 0.2) for k in ["4", "4", "6", "*", "5", "MENU"])
    assert user_line(b, out) == ("438.500", "454.500")                 # 70 cm's 20 MHz: 16 kept
    assert sel(b) == 446_500_000
    # (Jeff, 2026-11-24: "In TUNE, make * enter a decimal and # complete the entry ... so 446#
    # goes to 446.000 MHz")
    b.tap("#", 1.3)
    wait(b, 1.6)
    out = "".join(tap(b, k, 0.2) for k in ["4", "4", "6", "#"])
    assert user_line(b, out) == ("438.000", "454.000")
    assert sel(b) == 446_000_000


def test_long_middle_side_key_centres_custom_on_the_middle(built):
    # Jeff, 2026-11-24: "pressing and holding the center side button change the CENTER of the scan
    # range to whatever is currently tuned in the middle of the display ... jump to CUSTOM"
    b = UV21Board(*built, band=[Station("a", 441_200_000, -80)])
    b.run(until="boot done", limit=300_000_000)
    wait(b, 2)
    tap(b, "UP")
    tap(b, "UP")                                                        # 439.550
    b.tap("SIDE2", 1.3)
    m, out = until(b, r"user (\d+\.\d+) (\d+\.\d+)", 4)
    assert m, out[-300:]
    assert (m.group(1), m.group(2)) == ("431.550", "447.550")
    wait(b, 0.6)                                                        # (let go of it)
    assert sel(b) == 439_550_000
    tap(b, "SIDE2", 1.0)                                                # (short: still the next band)
    assert sel(b) == 146_000_000                                        # (2 m's centre)


class _Tunes(dict):
    """the board's RF registers, noting every frequency REG_38/39 are written to"""
    def __setitem__(self, a, v):
        super().__setitem__(a, v)
        if a == 0x39:                                   # (the driver writes REG_38, then 39)
            self.seen.add(((self.get(0x39, 0) << 16) | self.get(0x38, 0)) * 10)


def tunes(board, seconds):
    """every frequency the RF chip was tuned to through seconds of virtual time"""
    if not isinstance(board.regs, _Tunes):
        board.regs = _Tunes(board.regs)
    board.regs.seen = set()
    wait(board, seconds)
    return board.regs.seen


def carets(board):
    """the white caret pixels right of the bars, as (x, y)"""
    rows = board.screen_rgb()
    return {(x, y) for y in range(24, 150) for x in range(30, 128) if rows[y][x] == (255, 255, 255)}


@pytest.fixture(scope="module")
def steady(built):
    # carriers to give carets, the middle (439.500) quiet: SURF sweeps
    b = UV21Board(*built, band=[Station("a", 439_700_000, -80), Station("b", 440_100_000, -85)])
    b.run(until="boot done", limit=300_000_000)
    wait(b, 6)
    tap(b, "AB", 0.2)
    tap(b, "AB", 0.2)
    tap(b, "AB", 0.2)                                                   # the fastest fall: 15 s
    return b


def test_hold_stops_the_sweeps_and_the_carets(steady):
    # HOLD (Jeff, 2026-11-24): no sweeping, the carets don't fall; it listens on the middle only
    # (and the CW wake's frequency, a look every 1.5 s)
    mid = sel(steady)
    tap(steady, "0", 0.5)
    before = carets(steady)
    assert before, "no carets to watch"
    seen = tunes(steady, 5)
    assert seen <= {mid, 440_850_000}, sorted(seen)
    assert carets(steady) == before
    tap(steady, "0", 0.5)                                               # off again: sweeping
    assert len(tunes(steady, 2)) > 5


def test_monitor_only_opens_the_squelch(steady):
    # MON (Jeff, 2026-11-24): the audio opens on the middle; the sweeps and the caret fall go on
    tap(steady, "*", 0.5)
    # the audio open, but for the short hushes of a refresh sweep or a wake look: sampled, since
    # one look can land in one of those
    af, end = [], steady.seconds + 1.5
    while steady.seconds < end:
        steady.step(20_000)
        af.append(steady.regs.get(0x47))
    assert af.count(AF_FM) > len(af) * 0.6, [hex(v or 0) for v in af]
    before = carets(steady)
    seen = tunes(steady, 7)                                             # (a refresh sweep every 5 s)
    assert len(seen) > 5, sorted(seen)
    assert carets(steady) != before                                     # (they fell)
    tap(steady, "*", 0.5)


def test_hold_and_monitor_together(steady):
    mid = sel(steady)
    tap(steady, "*", 0.3)
    tap(steady, "0", 0.5)
    seen = tunes(steady, 4)
    assert seen <= {mid, 440_850_000}, sorted(seen)
    tap(steady, "SIDE1", 1.0)                                           # a jump still works
    assert sel(steady) != mid
    tap(steady, "0", 0.3)
    tap(steady, "*", 0.3)


# ---- the receive front end's band switch (port 12) ----
# The stock firmware switches the receive path with PA14 (VHF, 220) and PA13 (UHF), the SWD pins;
# ports 1 to 11 never drove them (Jeff, 2026-11-24: "signals are extremely weak on this radio"),
# and drove the UV-5RH's switch on the RF chip's GPIO5/GPIO6 instead, which on the UV-21 probably
# lights the top LED (Jeff: "turns on faintly on certain bands"). Key 6 steps through the ways to
# switch while the polarity is unknown.
PA = 0                          # (tests/test_uv21_bringup.py's port A)
GPIO56 = 0x0060


def front_end(board, seconds=2.0):
    """how often each (PA13, PA14) level pair was seen, sampled through seconds of virtual time"""
    seen, end = {}, board.seconds + seconds
    while board.seconds < end:
        board.step(200_000)
        k = (board.pins.out(PA, 13), board.pins.out(PA, 14))
        seen[k] = seen.get(k, 0) + 1
    return seen


def most(seen):
    return max(seen, key=seen.get)


def test_the_receive_band_switch_follows_the_band(built):
    b = UV21Board(*built, band=[Station("a", 441_200_000, -80)])
    out = b.run(until="boot done", limit=300_000_000)
    wait(b, 2)
    assert b.pins.mode(PA, 13) == 1 and b.pins.mode(PA, 14) == 1, out[-300:]   # outputs, not SWD
    seen = front_end(b)
    assert set(seen) == {(1, 0)}, seen                                  # 70 cm: the UHF pin high
    gpio = [v for r, v in b.tx_watch if r == REG_GPIO]
    assert gpio and not any(v & GPIO56 for v in gpio), [hex(v) for v in gpio]   # no GPIO5/6
    tap(b, "SIDE2", 1.0)                                                # GMRS
    tap(b, "SIDE2", 1.0)                                                # MAR/WX
    seen = front_end(b)
    assert most(seen) == (0, 1) and set(seen) <= {(0, 1), (1, 0)}, seen    # VHF (UHF: wake looks)


def test_key_6_steps_through_the_ways_to_switch(built):
    b = UV21Board(*built, band=[Station("a", 162_400_000, -80)])
    b.run(until="boot done", limit=300_000_000)
    tap(b, "SIDE2", 1.0)
    tap(b, "SIDE2", 1.0)                                                # MAR/WX: VHF
    out = tap(b, "6", 1.0)
    assert "fe rx path lo" in out, out[-300:]
    assert most(front_end(b)) == (1, 0)                                 # the VHF pin low
    out = tap(b, "6", 1.0)
    assert "fe rx path hi+56" in out, out[-300:]
    n = len(b.tx_watch)
    assert most(front_end(b)) == (0, 1)
    assert any(r == REG_GPIO and v & 0x20 for r, v in b.tx_watch[n:])  # GPIO5: VHF, the 5RH way
    out = tap(b, "6", 1.0)
    assert "fe rx path swd" in out, out[-300:]
    assert b.pins.mode(PA, 13) == 2 and b.pins.mode(PA, 14) == 2        # back to SWD
    out = tap(b, "6", 1.0)
    assert "fe rx path hi" in out, out[-300:]
    n = len(b.tx_watch)
    assert most(front_end(b)) == (0, 1)
    assert not any(r == REG_GPIO and v & GPIO56 for r, v in b.tx_watch[n:])
