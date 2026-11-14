"""The BLOWFANG alarm clock (c/k6/mod/alarm.c: midi.c with MIDI_ALARM) on an emulated M0: it
waits for its UTC time on the core's clock (module API level 8), showing UTC and the countdown,
then plays its songs; a held key cancels it; it refuses with no clock, no time set, or an image
before level 8; and tools/k6_midi.py alarm sets the time (and songs) in a built module.
SPDX-License-Identifier: MIT"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_i2s import ROOT, need_clang  # noqa: E402
from test_k6_midi import TEST_TXT, emulate, played, songs_table  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tools"))
import k6_midi  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "alarm.c")
H = 3_600_000


@pytest.fixture(scope="module")
def built():
    from modem import k6mod
    return k6mod.build(SRC)


def armed(built, at, songs=TEST_TXT):
    code, entry = built
    code = k6_midi.patch(code, songs_table(songs))
    return k6_midi.set_alarm(code, at), entry


def fix(utc_ms, w=400):
    return {"fix": (0, utc_ms, 9 | 11 << 8 | 2026 << 16, w)}


@need_clang
def test_builds_at_level_8_and_fits(built):
    from modem import k6mod
    code, entry = built
    assert k6mod.source_level(SRC) == 8 and entry == 16   # after its name (K6_MODULE_NAME)
    assert code.count(k6_midi.ALARM_TAG) == 1
    assert 24 + len(code) + 512 <= 11_776                      # the SPI voice images' block


@need_clang
def test_waits_for_its_time_then_plays(built):
    """the clock at 06:58:30 UTC, the alarm at 07:00: 90 s of countdown, then the song"""
    clock = fix(6 * H + 58 * 60_000 + 30_000)
    ret, said, writes, log = emulate(armed(built, 7 * 60), level=8, clock=clock)
    assert ret == 1, said
    assert said[0] == "alarm at 07:00 utc now 06:58:30 w=400", said
    assert said[1] == "alarm ring" and said[3] == "midi play 1/1 Test tune"
    frames = log["lcd"]["frames"]
    first = frames[0]["text"]
    assert first[0] == " BLOWFANG ALARM" and first[1] == "UTC 06:58:30"
    assert first[2] == "alarm 07:00 UTC" and first[3] == "in 00:01:30"
    assert first[4] == "clock +-0.4 s" and first[5] == "hold a key: cancel"
    ring = [f for f in frames if f["text"][0] == " MIDI player"][0]["us"]
    assert 89_500_000 <= ring <= 90_500_000, ring             # on time, within the 100 ms poll
    assert sum(f["text"][0] == " BLOWFANG ALARM" for f in frames) >= 85   # redrawn each second
    assert np.array_equal(played(log), k6_midi.render_table(songs_table(TEST_TXT)))


@need_clang
def test_tomorrow_when_the_time_has_passed(built):
    """at 07:00:05 an alarm for 07:00 is a day away: it says so, and a held key cancels it"""
    ret, said, writes, log = emulate(armed(built, 7 * 60), level=8, clock=fix(7 * H + 5_000),
                                     keys=((2_000_000, 3_500_000),))
    assert ret == -2 and said[-1] == "alarm at 07:00 utc now 07:00:05 w=400", said
    assert log["lcd"]["frames"][0]["text"][3] == "in 23:59:55"
    assert not log["ccr4"] and not writes                    # never touched the speaker


@need_clang
def test_refusals(built):
    code = armed(built, 7 * 60)
    ret, said, *_ = emulate(code, level=8, clock={})
    assert ret == -6 and said[-1] == "alarm: no clock (run aisclk or aprsclk first)"
    ret, said, *_ = emulate(built, level=8, clock=fix(H))
    assert ret == -7 and said[-1] == "alarm: no time set (k6_midi.py alarm)"
    ret, said, *_ = emulate(code, level=7)
    assert ret == -8


def test_set_alarm_and_the_tool(tmp_path):
    code = b"\x00" * 10 + k6_midi.ALARM_TAG + b"\xff\xff" + b"tail"
    out = k6_midi.set_alarm(code, 7 * 60 + 30)
    assert out[18:20] == (450).to_bytes(2, "little") and len(out) == len(code)
    with pytest.raises(ValueError):
        k6_midi.set_alarm(code, 1440)
    with pytest.raises(ValueError):
        k6_midi.set_alarm(b"no tag here", 0)
    assert k6_midi.parse_hhmm("7:05") == 425 and k6_midi.parse_hhmm("23:59") == 1439
    inp = tmp_path / "in.bin"
    inp.write_bytes(code)
    k6_midi.main(["alarm", str(inp), str(tmp_path / "out.bin"), "--at", "06:45"])
    assert (tmp_path / "out.bin").read_bytes()[18:20] == (405).to_bytes(2, "little")
