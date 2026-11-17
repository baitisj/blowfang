"""The SETTINGS module (c/k6/mod/settings.c, module API level 10): the backlight's idle time
(Jeff 2026-11-17: "the backlight remains on the entire time, draining battery"), changed with
UP / DOWN at once and saved on EXIT, on an emulated M0 with the real keypad matrix."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_k6_i2s import ROOT, START, need_clang, run  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "settings.c")
pytestmark = need_clang


def go(taps, level=10, settings=None):
    """taps: (from_us, key), each held 100 ms"""
    pytest.importorskip("unicorn")
    settings = {} if settings is None else settings
    keypad = [(t, t + 100_000, k) for t, k in taps]
    lcd = {}
    ret, said, _, _ = run(START, src=SRC, level=level, lcd=lcd, keypad=keypad,
                          keys=[(a, b) for a, b, _ in keypad], settings=settings, count=2_000_000_000)
    return ret, said, settings, lcd


def test_name_level_and_size():
    from modem import k6mod
    code, entry = k6mod.build(SRC)
    assert k6mod.source_level(SRC) == 10
    assert b"K6NMSETTINGS" in code[:16] and entry == 16
    assert len(code) < 2048, len(code)


def test_up_twice_then_exit_saves_the_new_delay():
    ret, said, st, lcd = go([(1_000_000, "U"), (1_500_000, "U"), (2_000_000, "X")])
    assert ret == 4 and st["light"] == 4 and st["saves"] == [4], (ret, st)
    assert said == ["settings light=4 secs=20 saved"], said
    # each UP set it at once (save 0), then EXIT saved the last one
    assert [(i, v, s) for _, i, v, s in st["log"]] == [(0, -1, 0), (0, 3, 0), (0, 4, 0), (0, 4, 1)]
    texts = [f["text"] for f in lcd["frames"]]
    assert texts[1][:3] == ["SETTINGS", "> LIGHT OFF: 10 S", "  AFTER NO KEY OR MSG"], texts[1]   # (0: ui_begin)
    assert any(t[1] == "> LIGHT OFF: 15 S" for t in texts)
    assert texts[-1][1] == "> LIGHT OFF: 20 S" and texts[-1][5] == "SAVED", texts[-1]


def test_down_wraps_to_two_minutes_and_always_on():
    ret, _, st, lcd = go([(1_000_000, "D"), (1_500_000, "D"), (2_000_000, "X")], settings={"light": 1})
    assert ret == 7 and st["saves"] == [7]
    assert any(f["text"][1] == "> LIGHT OFF: NEVER" and f["text"][2] == "  ALWAYS ON" for f in lcd["frames"])
    assert lcd["frames"][-1]["text"][1] == "> LIGHT OFF: 2 MIN"


def test_exit_with_no_change_writes_nothing():
    ret, said, st, lcd = go([(1_000_000, "X")], settings={"light": 5})
    assert ret == 5 and st["saves"] == [] and said == ["settings light=5 secs=30 unchanged"]
    assert lcd["frames"][-1]["text"][5] == "UNCHANGED"


def test_two_idle_minutes_save_and_leave():
    ret, said, st, lcd = go([(1_000_000, "U")])
    assert ret == 3 and st["saves"] == [3] and said == ["settings light=3 secs=15 saved"]
    assert lcd["st"]["us"] > 120_000_000


def test_a_refused_save_says_so():
    ret, said, st, lcd = go([(1_000_000, "U"), (1_500_000, "X")], settings={"refuse_save": True})
    assert ret == -1 and said == ["settings light=3 secs=15 save refused"]
    assert lcd["frames"][-1]["text"][5] == "NOT SAVED"


def test_an_image_before_level_10_is_refused():
    ret, said, st, _ = go([], level=9)
    assert ret == -3 and said == [] and "log" not in st
