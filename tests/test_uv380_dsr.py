"""DSotto raw (modem/dsr.py, c/common/dsr_tx.c; docs/uv380-dsotto-raw.md): the frame layout, the
C framer against the Python byte for byte, the receiver on clean, noisy and noise-only input, the
simulation's headline (DSR120 beats M17 live near the FM threshold), and the bench ladder's
decode from a tap log. SPDX-License-Identifier: MIT"""
import heapq
import os
import subprocess

import numpy as np
import pytest

from modem import dsr, m17

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = os.path.join(ROOT, "c", "common", "dsr_host")


def dfree(polys):
    """free distance of a K=7 code (bit 0 = the newest input bit)"""
    def w(s, b):
        m = (s << 1 | b) & 0x7F
        return sum(bin(m & g).count("1") & 1 for g in polys)
    best, dist, pq = 10 ** 9, {1: w(0, 1)}, [(w(0, 1), 1)]
    while pq:
        d, s = heapq.heappop(pq)
        if d > dist.get(s, 1e9) or d >= best:
            continue
        for b in (0, 1):
            ns, nd = (s << 1 | b) & 63, d + w(s, b)
            if ns == 0:
                best = min(best, nd)
            elif nd < dist.get(ns, 1e9):
                dist[ns] = nd
                heapq.heappush(pq, (nd, ns))
    return best


def test_layout():
    assert dsr.DSR120.nsym * 2 == 1152 and dsr.DSR120.frame_s == pytest.approx(0.120)
    assert dsr.DSR120.nsym * 2 // 8 == 4 * 36             # four C6000 slots exactly
    assert dsr.DSR40.nsym == 192 and dsr.DSR40.frame_s == pytest.approx(0.040)
    for fmt in (dsr.DSR40, dsr.DSR120, dsr.DSR40D):
        assert sorted(fmt.perm) == list(range(fmt.ncoded))
        assert np.mean(dsr.sync_symbols(fmt.sync)) == 0                # balanced: DC from the sync
        assert set(np.abs(dsr.sync_symbols(fmt.sync))) == {3.0}       # outer symbols only
    assert dsr.DSR40.sync != dsr.DSR40D.sync
    assert dfree(dsr.POLYS[:4]) == 20 and dfree(dsr.POLYS) == 30


def test_header_and_callsign():
    frames = []
    for c in range(4):
        info = dsr.info_bytes(dsr.DSR120, dsr.T_SOTTO1200, c, bytes(18))
        d = dsr.decode_soft(dsr.DSR120, 1.0 - 2.0 * np.unpackbits(np.frombuffer(
            dsr.frame_bytes(dsr.DSR120, info)[6:], np.uint8)))[0]
        assert d["counter"] == c and d["type"] == dsr.T_SOTTO1200
        frames.append(d)
    assert dsr.callsign_from(frames) == "AG7EW"


def test_tail_biting_viterbi_corrects_errors():
    rng = np.random.default_rng(3)
    for rate, n in ((4, 88), (6, 184)):
        bits = rng.integers(0, 2, n).astype(np.uint8)
        soft = 1.0 - 2.0 * dsr.tb_encode(bits, rate)
        flip = rng.choice(len(soft), len(soft) // 12, replace=False)
        soft[flip] *= -1
        out, q = dsr.tb_viterbi(soft[None], rate)
        assert (out[0] == bits).all()
        assert 0.05 < q[0] < 0.12


@pytest.mark.parametrize("fmt", ["40", "120"])
@pytest.mark.parametrize("n", [1, 2, 3, 4, 76])
def test_c_framer_matches_python(fmt, n):
    if not os.path.exists(HOST):
        pytest.skip("needs make -C c/common")
    sotto = bytes((7 * i + 3) & 0xFF for i in range(6 * n))
    r = subprocess.run([HOST, fmt, "AG7EW"], input=sotto, capture_output=True)
    assert r.returncode == 0
    assert r.stdout == dsr.tx_stream(dsr.FMTS["dsr" + fmt], sotto)


@pytest.mark.parametrize("name", ["dsr40", "dsr40d", "dsr120", "m17live"])
def test_clean_round_trip(name):
    fmt = dsr.M17LIVE if name == "m17live" else dsr.FMTS[name]
    r = dsr.sim_point(fmt, 70.0, nsf=60, seed=4)
    assert r["good"] == 60 and r["bad"] == 0


def test_tap_noise_gives_no_frames():
    rng = np.random.default_rng(9)
    x = dsr.fm_channel(np.zeros(48000 * 20), 20.0, rng=rng)       # 20 s of an empty channel
    y = dsr.front(x, dsr.FS_TAP)
    for fmt in (dsr.DSR40, dsr.DSR40D, dsr.DSR120):
        assert dsr.receive(y, fmt) == []
    assert dsr.receive_m17(y) == []


def test_dsr120_beats_m17_live_near_threshold():
    """the simulation's headline (docs/uv380-dsotto-raw.md): at 44 dB-Hz on the static channel,
    just under the FM threshold, DSR120 still gets nearly everything through and M17 live does not"""
    a = dsr.sim_point(dsr.DSR120, 44.0, nsf=300, seed=11)
    b = dsr.sim_point(dsr.M17LIVE, 44.0, nsf=300, seed=11)
    assert a["sfer"] < 0.05 and b["sfer"] > 0.5
    assert a["bad"] == 0


def test_ladder_decodes_from_a_tap_log(tmp_path):
    wav = tmp_path / "ladder.wav"
    secs, marks = dsr.ladder_wav(str(wav), nsf=24)
    assert list(marks) == list(dsr.LADDER)
    import wave
    w = wave.open(str(wav))
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16) / 32767.0 * 4800.0
    rng = np.random.default_rng(1)
    parts = [dsr.fm_channel(np.concatenate([np.zeros(48000), x, np.zeros(48000)]), 60.0, rng=rng)
             for _ in range(2)]
    tap = np.clip(np.round(-np.concatenate(parts) / 2400 * 2570), -32768, 32767).astype(int)
    lines = [f"a {i // 64} " + " ".join(f"{v & 0xFFFF:04x}" for v in tap[i:i + 64])
             for i in range(0, len(tap) - 63, 64)]
    log = tmp_path / "uv380.log"
    log.write_text("\n".join(lines) + "\n")
    rep = dsr.report_tap(str(log), nsf=24)
    table = dsr.ladder_table(rep, [-64, -66])
    assert len(table) == 2
    for ln in table:
        for name in dsr.LADDER:
            assert f"{name} 24/24" in ln, ln


def test_latency_budget():
    _, t40 = dsr.latency(dsr.DSR40)
    _, t120 = dsr.latency(dsr.DSR120)
    assert t120 - t40 == 160                    # two more superframes to fill, 80 ms more air
    _, low = dsr.latency(dsr.DSR120, lowdelay=True)
    assert low == t120 - 120


def test_decode_pluto_wav(tmp_path, capsys):
    """decode takes the discriminator wav pluto_bench rx returns (the .cf32 stays on hamlet)"""
    import wave
    fmt = dsr.DSR120
    data = dsr.tx_stream(fmt, dsr.test_sotto(45))
    x = dsr.modulate(data)
    y = dsr.fm_channel(np.concatenate([np.zeros(48000), x, np.zeros(48000)]), 60.0, offset_hz=-200.0,
                       rng=np.random.default_rng(5), tap=False)
    wav = tmp_path / "dsr.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(np.clip(np.round(y / dsr.PLUTO_WAV_DEV * 32767), -32768, 32767)
                      .astype(np.int16).tobytes())
    assert dsr.main(["decode", str(wav)]) == 0
    out = capsys.readouterr().out
    assert "src AG7EW, superframes 45 (0..44, 0 missing)" in out, out
