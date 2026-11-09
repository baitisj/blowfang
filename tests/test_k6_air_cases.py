"""Every on-air recording kept in tests/data/k6/air/ with a sidecar NAME.json decodes as it did
when it was added (tools/k6_air.py add), so a decoder change that loses a real signal fails CI.
Also: the Morse-by-ear decoder on synthetic MCW and FM-quieting CW, and `add` round-tripping a
recording. SPDX-License-Identifier: MIT"""
import importlib.util
import json
import os

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("k6_air", os.path.join(ROOT, "tools", "k6_air.py"))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)
pytest.importorskip("soundfile")

CASES = A.cases()


def test_every_sidecar_names_its_recording():
    assert CASES, "no sidecars in tests/data/k6/air"
    for name, meta in CASES:
        assert meta["file"] == name + ".flac" and os.path.exists(os.path.join(A.AIR, meta["file"])), name
        assert meta["what"] and meta["checks"], name
        for c in meta["checks"]:
            assert c["decoder"] in A.DECODERS and not A.empty(c["expect"]), (name, c)
        assert os.path.getsize(os.path.join(A.AIR, meta["file"])) <= A.MAX_BYTES, name


@pytest.mark.parametrize("name,meta", CASES, ids=[n for n, _ in CASES])
def test_recording_decodes_as_when_added(name, meta):
    for c, want, got in A.run_case(meta):
        assert got == want, (c["decoder"], c.get("opts"))


def _cw(text, wpm, fs=48000):
    import sys
    sys.path.insert(0, ROOT)
    from modem import k6
    return k6.cw(text, fs, wpm, 800.0, 0.4)


@pytest.mark.parametrize("wpm", [13, 20, 25])
def test_cw_by_ear_mcw(wpm):
    rng = np.random.default_rng(wpm)
    x = np.concatenate([np.zeros(24000), _cw("AG7EW SOTTO TEST", wpm), np.zeros(24000)])
    x = x + 0.05 * rng.standard_normal(len(x))
    assert A.cw_decode(x, 48000, "tone") == "AG7EW SOTTO TEST"
    assert A.cw_decode(x, 48000, "tone", wpm=wpm) == "AG7EW SOTTO TEST"


def test_cw_by_ear_fm_quieting():
    """cwkey keys the K6's carrier: on FM the IC-7100 hears hiss that goes quiet while it is down"""
    rng = np.random.default_rng(5)
    key = np.convolve(np.abs(_cw("AG7EW", 20)), np.ones(480) / 480, "same") > 0.08
    key = np.concatenate([np.zeros(24000, bool), key, np.zeros(24000, bool)])
    hiss = 0.3 * rng.standard_normal(len(key))
    x = np.where(key, 0.01 * hiss, hiss)
    assert A.cw_decode(x, 48000, "quiet") == "AG7EW"


def test_nothing_decodes_from_noise():
    x = 0.1 * np.random.default_rng(1).standard_normal(48000 * 3)
    assert A.cw_decode(x, 48000, "tone") == ""


def test_parse_check():
    assert A.parse_check("cw:mode=quiet,wpm=20") == ("cw", {"mode": "quiet", "wpm": 20})
    assert A.parse_check("fsk:phy=F,baud=1800,payload=XL") == ("fsk", {"phy": "F", "baud": 1800, "payload": "XL"})
    with pytest.raises(SystemExit):
        A.parse_check("sstv")


def test_add_round_trip(tmp_path):
    import soundfile as sf
    import sys
    sys.path.insert(0, ROOT)
    from modem import psk31
    fs = 48000
    x = np.concatenate([np.zeros(fs * 2), _cw("AG7EW", 20), np.zeros(fs // 2),
                        psk31.modulate("AG7EW TEST", fs, 1000.0, 0.3), np.zeros(fs * 2)])
    rec = tmp_path / "rec.wav"
    sf.write(str(rec), x, fs)
    flac, side, meta = A.add(str(rec), "k6-demo", ["cw:mode=tone,tone=800"], "a test", d=str(tmp_path),
                             t_from=1.5, t_to=1.5 + 4.0)
    assert meta["checks"][0]["expect"] == {"text": "AG7EW"}
    assert json.load(open(side))["source"].startswith("rec.wav [1.50, 5.50]")
    (n, m), = A.cases(str(tmp_path))
    assert n == "k6-demo" and [g == w for _, w, g in A.run_case(m, str(tmp_path))] == [True]
    with pytest.raises(SystemExit, match="exists"):
        A.add(str(rec), "k6-demo", ["cw"], "again", d=str(tmp_path), t_from=1.5, t_to=5.5)
    with pytest.raises(SystemExit, match="decoded nothing"):
        A.add(str(rec), "k6-quiet", ["cw"], "silence", d=str(tmp_path), t_from=0, t_to=1.5)
    p = A.decode("psk31", {}, x[int(5.5 * fs):], fs)
    assert "AG7EW TEST" in p["text"]
