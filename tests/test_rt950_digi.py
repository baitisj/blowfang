"""FT8 and WSPR from the RT-950 Pro by PLL steps (radios/rt950/src/digi_tx.c on c/common/tone_fsk.c),
run in radios/rt950/sim/rt950_sim.c with its transmitter logged write by write: chip 1 goes off
before chip 0 keys up and stays quiet while keyed, every symbol's frequency is right to a fraction
of a hertz though each write is a whole 10 Hz step, the audio a USB receiver would hear decodes
to the message, the frame ends with AG7EW in CW, a key keys down at once, and nothing goes out
outside 70 cm without the run's OK. Needs make -C radios/rt950. SPDX-License-Identifier: GPL-3.0-or-later"""
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SIM = os.path.join(ROOT, "radios", "rt950", "build", "rt950_sim")
pytestmark = pytest.mark.skipif(not os.path.exists(SIM), reason="needs make -C radios/rt950")

DIAL = 440_850_000
FT8_TEXT = "CQ AG7EW CN87"
WSPR_TEXT = "AG7EW CN87 10"
FS = 12000
MORSE = {"A": ".-", "G": "--.", "7": "--...", "E": ".", "W": ".--"}


@pytest.fixture(scope="module")
def tones():
    pytest.importorskip("scipy")
    from sim.k6digi import ft8ref
    from sim.k6digi import wsprref
    return {"ft8": list(ft8ref.encode(FT8_TEXT)[1]), "wspr": list(wsprref.encode(WSPR_TEXT))}


def send(mode, tones, *args):
    """-> (log lines split, summary dict)"""
    out = subprocess.run([SIM, "--digi", mode, "--tones", "".join(map(str, tones)), "--dial", str(DIAL),
                          *map(str, args)], check=True, capture_output=True, text=True).stdout
    log = [ln.split() for ln in out.splitlines()]
    summary = {w[0]: w[1] for w in log if len(w) == 2}
    return log, summary


def writes(log):
    """[(seconds from key-up, Hz above the dial)] for every frequency the carrier was set to"""
    t0 = next(int(w[1]) for w in log if w[0] == "txon")
    on = [(0.0, int(next(w for w in log if w[0] == "txon")[2]) - DIAL)]
    return on + [((int(w[1]) - t0) / 1e6, int(w[2]) - DIAL) for w in log if w[0] == "txw"]


def mean_hz(ws, t0, t1):
    """the carrier's mean offset over t0..t1 s (each write holds until the next)"""
    acc = 0.0
    for (ta, f), (tb, _) in zip(ws, ws[1:] + [(1e9, 0)]):
        a, b = max(ta, t0), min(tb, t1)
        if b > a:
            acc += f * (b - a)
    return acc / (t1 - t0)


def test_ft8_symbols_are_right_to_a_fraction_of_a_hertz(tones):
    log, s = send("ft8", tones["ft8"])
    ws = writes(log)
    assert int(s["digi"]) == 12640 and len(ws) == 12642   # key-up, the frame's writes, back to tone 0 for the ident
    assert all(f % 10 == 0 for _, f in ws)          # (every write a whole 10 Hz step)
    err = [mean_hz(ws, k * 0.16 + 0.04, k * 0.16 + 0.12) - (1500 + 6.25 * t) for k, t in enumerate(tones["ft8"])]
    assert max(abs(e) for e in err) < 0.3, max(err, key=abs)


def test_ft8_decodes_as_a_usb_receiver_hears_it(tones):
    from sim.k6digi.ft8_wav import decode_audio
    ws = writes(send("ft8", tones["ft8"])[0])
    n = int(13.5 * FS)
    t = np.arange(n) / FS
    idx = np.searchsorted([w[0] for w in ws], t - 0.5, side="right") - 1   # (half a second of quiet first)
    f = np.array([w[1] for w in ws], float)[np.clip(idx, 0, None)]
    x = np.where(t >= 0.5, np.sin(2 * np.pi * np.cumsum(f) / FS), 0.0)
    rng = np.random.default_rng(3)
    x = x + np.sqrt(np.mean(x ** 2) / 10 ** (-10 / 10) * (FS / 2) / 2500) * rng.standard_normal(n)
    found = decode_audio(x, FS, lo=1300, hi=1800)
    assert FT8_TEXT in found, found


def test_wspr_symbols_are_right(tones):
    log, s = send("wspr", tones["wspr"], "--end", 200000)   # (the sim sends EXIT at --end: 30 s by default)
    assert int(s["digi"]) == 110592
    ws = writes(log)
    T = 8192 / 12000
    err = [mean_hz(ws, k * T + 0.1, k * T + T - 0.1) - (1500 + 12000 / 8192 * t)
           for k, t in enumerate(tones["wspr"])]
    assert max(abs(e) for e in err) < 0.1, max(err, key=abs)


def test_chip_1_goes_off_first_and_stays_quiet(tones):
    _, s = send("ft8", tones["ft8"])
    assert s["tx_c1_on"] == "0" and s["tx_traffic1"] == "0" and s["c1hz"] == "0"


def test_the_frame_ends_with_the_ident_in_cw(tones):
    log, _ = send("ft8", tones["ft8"])
    keying = [(int(w[1]), int(w[2])) for w in log if w[0] == "txp"]
    dit = 60000                                      # us at 20 wpm
    marks = [(b[0] - a[0]) for a, b in zip(keying, keying[1:]) if a[1] and not b[1]]
    gaps = [(b[0] - a[0]) for a, b in zip(keying, keying[1:]) if not a[1] and b[1]]
    els = "".join("-" if m > 2 * dit else "." for m in marks)
    letters, cur = [], els[0]
    for e, g in zip(els[1:], gaps[1:]):
        if g > 2 * dit:
            letters.append(cur)
            cur = ""
        cur += e
    letters.append(cur)
    inv = {v: k for k, v in MORSE.items()}
    assert "".join(inv[x] for x in letters) == "AG7EW"
    assert [w[0] for w in log if w[0] in ("txon", "txw", "txp", "txoff")][-1] == "txoff"


def test_a_key_keys_down_at_once(tones):
    log, s = send("ft8", tones["ft8"], "--key", "3000:X")
    assert int(s["digi"]) == -4
    off = next(int(w[1]) for w in log if w[0] == "txoff")
    assert 3.0e6 <= off < 3.01e6


def test_nothing_outside_70cm_without_the_ok(tones):
    out = subprocess.run([SIM, "--digi", "ft8", "--tones", "0123", "--dial", "144174000"], check=True,
                         capture_output=True, text=True).stdout
    assert "digi -2" in out and "txon" not in out
    out = subprocess.run([SIM, "--digi", "ft8", "--tones", "0123", "--dial", "144174000", "--outside"], check=True,
                         capture_output=True, text=True).stdout
    assert "txon" in out
