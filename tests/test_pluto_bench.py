"""tools/pluto_bench.py, the ADALM-Pluto bench instrument: everything that runs without the Pluto
(the transmit checks, the IQ it streams, the FM audio it makes, its measurements and calibration,
how it reads iio_attr).
SPDX-License-Identifier: MIT"""
import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import pluto_bench as pl  # noqa: E402


def test_refuses_off_bench_transmits():
    assert pl.check_tx(440_850_000, -3000, -60) == 440_853_000
    assert pl.check_tx(432_100_000, 0, -45) == 432_100_000
    for hz, off, gain in [(440_850_000, 25_000, -60), (440_850_000, 0, -20), (440_850_000, 0, -95),
                          (146_520_000, 0, -60), (450_500_000, 0, -60), (430_005_000, 0, -60),
                          (430_015_000, 10_000, -60)]:
        with pytest.raises(SystemExit):
            pl.check_tx(hz, off, gain)


def test_iq_is_phase_continuous_fm():
    """the IQ chunks join without a phase step and a discriminator gives the track back"""
    fs, rate = 48_000, 480_000
    t = np.arange(int(0.25 * fs)) / fs
    track = 2200.0 * np.sign(np.sin(2 * np.pi * 300 * t))
    raw = b"".join(pl.iq_chunks(track, fs, rate, chunk=1000))
    x = np.frombuffer(raw, np.int16).astype(float)
    iq = x[0::2] + 1j * x[1::2]
    assert len(iq) == len(track) * 10 and np.all(np.abs(np.abs(iq) - pl.AMP) < 2)
    d = np.angle(iq[1:] * np.conj(iq[:-1])) * rate / (2 * np.pi)
    ref = np.interp(np.arange(1, len(iq)) / rate, t, track)
    assert np.percentile(np.abs(d - ref), 99) < 30, np.percentile(np.abs(d - ref), 99)


def test_audio_track_scales_and_preemphasises():
    fs = 8000
    t = np.arange(fs) / fs
    x = np.sin(2 * np.pi * 1000 * t)
    tr = pl.audio_track(x, fs, 3000.0)
    assert len(tr) == pl.FS_TRACK and abs(np.max(np.abs(tr)) - 3000) < 5
    lo = pl.audio_track(0.3 * np.sin(2 * np.pi * 300 * t), fs, 3000.0, preemph=True)
    hi = pl.audio_track(0.3 * np.sin(2 * np.pi * 2500 * t), fs, 3000.0, preemph=True)
    at1k = pl.audio_track(0.3 * x, fs, 3000.0, preemph=True)
    r = lambda y: np.sqrt(np.mean(y[len(y) // 2:] ** 2))  # noqa: E731
    assert abs(r(at1k) / r(0.3 * 3000 * x) - 1) < 0.05
    assert r(lo) < 0.5 * r(at1k) < r(hi) * 0.5 * 1.2


def _fm(offset, dev, fs=pl.IQ_FS_SAVE, n=None, seed=1):
    n = n or fs
    t = np.arange(n) / fs
    f = offset + dev * np.sin(2 * np.pi * 800 * t) * (t > 0.3)
    iq = 0.1 * np.exp(2j * np.pi * np.cumsum(f) / fs)
    iq[: int(0.1 * fs)] = 0
    rng = np.random.default_rng(seed)
    return (iq + 0.001 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))).astype(np.complex64)


def test_measure_reads_a_carrier():
    m = pl.measure(_fm(3210.0, 2500.0))
    assert abs(m["offset"] - 3210) < 30 and 2300 < m["dev"] < 2700 and 0.85 < m["share"] < 0.95, m
    assert abs(m["level_dbfs"] + 20) < 1.5 and m["floor_dbfs"] < -45, m
    assert pl.measure(_fm(0.0, 0.0) * 0)["offset"] is None


def test_cal_moves_the_crystal_the_right_way():
    """a true 440.850 carrier read 1,000 Hz high: the LO was 1,000 Hz low, so the crystal is slow
    by 2.27 ppm; with that xo the same LO setting lands on the carrier"""
    xo = pl.xo_from(1000.0, 440_850_000, 40_000_000)
    assert xo == 40_000_000 - 91
    assert abs(440_850_000 * xo / 40_000_000 - (440_850_000 - 1000)) < 5


def test_cal_saves_and_setup_applies(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOWFANG_WORK", str(tmp_path))
    cap = str(tmp_path / "id.cf32")
    _fm(-2000.0, 2500.0).tofile(cap)
    import json
    with open(cap + ".json", "w") as f:
        json.dump({"hz": 440_850_000, "fs": pl.IQ_FS_SAVE, "xo": 39_999_914, "utc": "x"}, f)
    pl.main(["cal", cap, "--save"])
    c = pl.load_cal()
    assert c and abs(c["xo"] - pl.xo_from(-2000.0, 440_850_000, 39_999_914)) <= 1, c
    calls = []
    monkeypatch.setattr(pl, "_iio", lambda args, uri=None, check=True: calls.append(args) or "")
    pl.setup(rx_hz=146_520_000, rx_gain=40)
    flat = [" ".join(map(str, a)) for a in calls]
    assert any(f"xo_correction {c['xo']}" in s for s in flat), flat
    assert any("altvoltage1 powerdown 1" in s for s in flat)
    assert any(f"altvoltage0 frequency {pl.RX_PARK_HZ}" in s for s in flat)   # quiet parks the RX LO first
    gain = [s for s in flat if "hardwaregain" in s]
    assert gain == ["-q -c -i ad9361-phy voltage0 hardwaregain 40"], gain
    assert all(("-i" in a or "-o" in a or "-d" in a) for a in calls)


def test_reads_iio_attr_lines():
    out = "dev 'ad9361-phy', channel 'altvoltage1' (output), attr 'frequency', value '440850000'\n"
    assert pl.parse_value(out) == "440850000"
    assert pl.parse_value("39999914\n") == "39999914"


def test_decimator_matches_one_shot():
    rng = np.random.default_rng(3)
    x = (rng.standard_normal(40_000) * 300).astype(np.int16)
    whole = pl.Decimator(2_400_000, 10)(x.tobytes())
    d = pl.Decimator(2_400_000, 10)
    parts = np.concatenate([d(x[a:a + 3998].tobytes()) for a in range(0, len(x), 3998)])
    assert len(whole) == len(parts) and np.allclose(whole, parts, atol=1e-4)


def test_refuses_to_stream_over_usb():
    """over usb: iio_writedev holds the interface and the TX LO couldn't be powered down"""
    with pytest.raises(SystemExit, match="usb:"):
        pl.stream(np.zeros(480), uri="usb:1.38.5", rf=False)
    assert pl.URI.startswith("ip:") or os.environ.get("PLUTO_URI")


def test_sweep_if_and_level():
    """the sweep's IF: the LO below the channel, refused when the LO or its image leave 70 cm;
    the level envelope scales the IQ, and the IF moves the tone"""
    assert pl.check_tx(440_850_000, 0, -70, 250_000) == 440_600_000
    with pytest.raises(SystemExit):
        pl.check_tx(430_400_000, 0, -70, 250_000)       # image 429.9 MHz
    with pytest.raises(SystemExit):
        pl.check_tx(440_850_000, 0, -70, 600_000)       # more than IF_MAX
    lev = np.r_[np.ones(4800), np.full(4800, 0.01)]
    raw = b"".join(pl.iq_chunks(np.zeros(9600), level=lev, if_hz=250_000.0))
    x = np.frombuffer(raw, np.int16).astype(float)
    iq = x[0::2] + 1j * x[1::2]
    assert abs(np.abs(iq[:1000]).max() - pl.AMP) < 2 and abs(np.abs(iq[-1000:]).max() - pl.AMP / 100) < 2
    f = np.angle(iq[101:200] * np.conj(iq[100:199])).mean() * pl.RATE / (2 * np.pi)
    assert abs(f - 250_000) < 10


def test_follow_sees_only_new_lines(tmp_path):
    import threading
    import time
    log = tmp_path / "k6.log"
    log.write_text("K6 MOD say fsk24 start hz=1 n=1\n")
    def later():
        time.sleep(0.2)
        with open(log, "a") as f:
            f.write("noise\nK6 MOD say fsk24 start hz=440850000 n=36\n")
    threading.Thread(target=later).start()
    _, ln = pl.follow(str(log), "say fsk24 start", 5.0, poll=0.02)
    assert "n=36" in ln


def test_steps_refuses_a_gain_out_of_range():
    with pytest.raises(SystemExit):
        pl.main(["steps", "x.wav", "--gains", "-70,-20", "--rf-off"])


def test_k6_upload_via_pluto_sends_with_pluto_bench(capsys):
    """k6_upload --via pluto: the Pluto sends (pre-emphasised, about 3 kHz peak at any --level),
    and a separate CW ID is skipped (pluto_bench IDs every send)"""
    import types
    import k6_upload as ku
    a = types.SimpleNamespace(via="pluto", freq=440_850_000, pluto_gain=-52.0, level=0.3, dry_run=True)
    ku.key_and_play(a, "up1.wav")
    out = capsys.readouterr().out
    assert "pluto_bench.py send up1.wav --hz 440850000 --preemph --gain -52.0 --dev 10000" in out
    ku.key_and_play(a, "id.wav", "id")
    assert capsys.readouterr().out == ""
    a.pluto_peak, a.level = 2500.0, 1.0
    ku.key_and_play(a, "up2.wav")
    assert "--dev 2500" in capsys.readouterr().out


def test_as_flag_sets_the_holder(monkeypatch):
    """--as WHO = $K6_BENCH_AS=WHO without an env prefix (allow rules match whole commands)"""
    monkeypatch.delenv("K6_BENCH_AS", raising=False)
    monkeypatch.setattr(pl, "cmd_info", lambda a: None)
    pl.main(["--as", "fsk24", "info"])
    assert os.environ["K6_BENCH_AS"] == "fsk24"


def test_a_pluto_send_does_not_wake_the_ic7100_keeper(tmp_path, monkeypatch):
    """the Pluto IDs itself (MCW first and last): its sends go in the logs but not in last_tx /
    last_id, so the keeper never keys the IC-7100 for a periodic ID after one (11-17)"""
    import subprocess as sp
    import time
    import k6_bench as kb
    monkeypatch.setenv("BLOWFANG_WORK", str(tmp_path))
    monkeypatch.delenv("K6_BENCH_AS", raising=False)

    class P:
        def __init__(self, *a, **k):
            self.stdin = open(os.devnull, "wb")

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(sp, "Popen", P)
    monkeypatch.setattr(pl, "tx_power", lambda on, uri=None: None)
    pl.stream(np.zeros(4800), uri="ip:192.168.2.1", rf=True, what="test", hz=440_850_000)
    assert kb._read("last_tx") == 0.0 and kb._read("last_id") == 0.0
    for later in (0, 300, 540, 600, 900):
        assert kb.keeper_due(time.time() + later, kb._read("last_tx"), kb._read("last_id")) is None
    assert "self-IDed" in open(kb.path("txlog")).read()
    assert "MCW" in open(kb.path("cwid_log")).read()


def test_peak_puts_the_wavs_own_peak_at_the_deviation():
    # modem.k6 audio is half scale: --dev 3700 sends it at 1850 Hz; --peak 3000 sends 3000 Hz
    x = 0.5 * np.sin(2 * np.pi * 1200 * np.arange(4800) / 48000)
    assert pl.full_scale_dev(x, 3700.0) == 3700.0
    dev = pl.full_scale_dev(x, 3700.0, peak=3000.0)
    assert abs(np.max(np.abs(pl.audio_track(x, 48000, dev))) - 3000.0) < 1.0
    with pytest.raises(SystemExit):
        pl.full_scale_dev(np.zeros(10), 3000.0, peak=3000.0)


def test_wav_only_sink_streams_fm_audio(tmp_path):
    """rx --wav-only: parts in, 8 kHz discriminator audio out, the same tone and deviation as one
    piece, and a carrier level each 0.1 s"""
    import soundfile as sf
    fs = pl.IQ_FS_SAVE
    t = np.arange(2 * fs) / fs
    iq = (0.1 * np.exp(1j * 2 * np.pi * np.cumsum(2500 * np.sin(2 * np.pi * 1000 * t)) / fs)).astype(np.complex64)
    out = str(tmp_path / "a.wav")
    s = pl.AudioSink(out, 8000)
    for i in range(0, len(iq), 13107):            # odd part sizes: the filters keep their state
        s(iq[i:i + 13107])
    s.close()
    x, r = sf.read(out)
    assert r == 8000 and abs(len(x) - 16000) <= 1
    y = x[2000:]
    assert abs(np.argmax(np.abs(np.fft.rfft(y))) * r / len(y) - 1000) < 2
    assert abs(np.std(y) * np.sqrt(2) * pl.WAV_FULL_DEV - 2500) < 100
    assert len(s.levels) == 20 and all(abs(v + 20) < 0.5 for v in s.levels)


def test_channels_sink_splits_one_capture(tmp_path):
    """rx --channels: one int16 IQ stream at 2.4 MS/s with two FM carriers -> a WAV each with its
    own tone and deviation, the empty channel's level 40 dB down, parts of odd sizes"""
    import soundfile as sf
    rate, lo = pl.RATE, 441_000_000
    t = np.arange(rate) / rate                     # 1 s
    iq = np.zeros(len(t), complex)
    for hz, tone, dev in ((440_250_000, 1000, 2500), (441_650_000, 700, 4000)):
        ph = 2 * np.pi * (hz - lo) * t + 2 * np.pi * np.cumsum(dev * np.sin(2 * np.pi * tone * t)) / rate
        iq += 0.1 * np.exp(1j * ph)
    raw = np.empty(2 * len(t), np.int16)
    raw[0::2], raw[1::2] = np.round(iq.real * 2048), np.round(iq.imag * 2048)
    raw = raw.tobytes()
    hzs = (440_250_000, 441_650_000, 440_825_000)
    sinks = [pl.ChannelSink(pl.channel_path(str(tmp_path / "c.wav"), h), h, lo) for h in hzs]
    ms = pl.MultiSink(sinks)
    i = 0
    while i < len(raw):
        n = 4 * 77_777
        ms(raw[i:i + n])
        i += n
    ms.close()
    for s, (tone, dev) in zip(sinks[:2], ((1000, 2500), (700, 4000))):
        x, r = sf.read(pl.channel_path(str(tmp_path / "c.wav"), s.hz))
        assert r == 8000 and abs(len(x) - 8000) <= 2
        y = x[1000:]
        assert abs(np.argmax(np.abs(np.fft.rfft(y))) * r / len(y) - tone) < 2
        assert abs(np.std(y) * np.sqrt(2) * pl.WAV_FULL_DEV - dev) < 150
        assert len(s.levels) == 10 and all(abs(v + 20) < 0.7 for v in s.levels[1:])
    assert max(sinks[2].levels[1:]) < -60
    assert tmp_path.joinpath("c_440.8250.wav").exists()


def test_stream_fir_matches_one_piece():
    from scipy import signal as sps
    x = np.random.default_rng(1).standard_normal(5000).astype(np.float32)
    h = sps.firwin(31, 0.2)
    f = pl.StreamFir(h, 3, np.float32)
    y = np.concatenate([f(x[i:i + 101]) for i in range(0, len(x), 101)])
    ref = sps.lfilter(h, 1.0, x)[::3]
    assert len(y) == len(ref) and np.allclose(y, ref, atol=1e-4)


def test_chan_refuses_an_unlisted_binary(tmp_path):
    """chan: only a build whose sha256 is in tools/pluto_chan/allowed.sha256 goes onto the Pluto"""
    b = tmp_path / "evil.armhf"
    b.write_bytes(b"\x7fELF not reviewed")
    with pytest.raises(SystemExit, match="not in"):
        pl.chan_allowed(str(b))


def test_chan_stream_to_files(tmp_path):
    """pluto_chan -f's records (levels, an over, the end) -> a FLAC an over, levels.json, overs.json"""
    import io
    import struct

    import soundfile as sf
    from pluto_chan import pluto_chan as pc
    x = (3000 * np.sin(2 * np.pi * 500 * np.arange(8000) / 8000)).astype(np.int16)
    fb = io.BytesIO()
    sf.write(fb, x, 8000, format="FLAC", subtype="PCM_16")
    flac = fb.getvalue()
    s = b"L" + bytes([2]) + struct.pack("<I", 0) + struct.pack("20b", *([-70] * 10 + [-30] * 10))
    s += b"O" + bytes([1]) + struct.pack("<II", 7, len(flac)) + flac
    s += b"E" + bytes([0]) + struct.pack("<I", 20)
    r = pc.write_stream(io.BytesIO(s), str(tmp_path), [440250000, 441650000], 0.0)
    assert r["overs"] == 1 and r["secs"] == 2.0 and r["bytes"] == len(s)
    name = "441.6500_19700101T0000007Z.flac"
    y, fs = sf.read(str(tmp_path / name), dtype="int16")
    assert fs == 8000 and np.array_equal(y, x)
    lv = json.load(open(tmp_path / "levels.json"))
    assert lv["levels_dbfs_100ms"] == [[-70] * 10, [-30] * 10]


def test_bin_stream_ids():
    """earshot's stream ID = bin + 1, bin k at LO + k * 50 kHz, k - 512 below the LO (earshot README)"""
    assert [pl.bin_stream(h, 160_000_000) for h in (162_400_000, 162_475_000, 162_550_000)] == [49, 51, 52]
    assert pl.bin_stream(470_309_441, 470_000_000) == 7
    assert pl.bin_stream(146_145_000, 146_195_000) == 512
    assert pl.bin_stream(145_230_000, 146_300_000) == 512 - 21 + 1


def test_chan_bins_refuses_an_unpinned_producer(tmp_path):
    """chan --bins: the producer that goes onto the Pluto must be the one pinned in pluto_fw.py"""
    p = tmp_path / "pluto-fpga-source"
    p.write_bytes(b"\x7fELF not pinned")
    a = pl.main.__globals__["argparse"].Namespace(bins=str(p), hz=146_300_000, gain=45, bin="x", uri=None)
    with pytest.raises(SystemExit, match="not the pinned earshot producer"):
        pl.chan_bins(a, [146_610_000], None)
