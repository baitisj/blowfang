"""Live talk-through (modem/k6live.py, tools/k6_talk.py): sotto frames sent as one open-ended
stream message while they come in. The packets must decode on the PC's demodulator, play on
the K6's own receive and play code (c/k6/pager_host_lr, streaming) exactly as the M0 decoder
plays the same frames, and keep ahead of a K6 playing in real time. SPDX-License-Identifier: MIT"""
import os
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import fsk, k6live, pager  # noqa: E402

K6 = os.path.join(ROOT, "c", "k6")
HOST_LR = os.path.join(K6, "pager_host_lr")
FX = os.path.join(ROOT, "c", "fixed")
need_host = pytest.mark.skipif(
    not (os.path.exists(HOST_LR) and os.path.exists(os.path.join(FX, "sotto_dec_fx_m0"))),
    reason="needs make -C c/k6 and make -C sotto/c VARIANT=v3.7 fixed")


def frames_of(n, seed=1):
    rng = np.random.default_rng(seed)
    return [bytes(rng.integers(0, 256, 6, dtype=np.uint8)) for _ in range(n)]


def stream_packets(frames, depth=4, msg_id=7):
    pk = k6live.Packer("1200", depth, msg_id)
    out = []
    for f in frames:
        g = pk.push(f)
        out += g or []
    out += pk.flush() or []
    return out, pk


def demod(x):
    d = fsk.Demodulator("F", k6live.BAUD, fsk.FS, 128)
    return [p for _, p, _, _ in d.demodulate(fsk.resample(x, fsk.FS, fsk.FS))]


def test_packer_lays_out_open_ended_groups():
    fr = frames_of(205)
    pk, packer = stream_packets(fr)
    prof = pager.profile("XL", "1200", 4)
    assert packer.fpg == 80 and len(pk) == 3 * 4          # 205 frames: 2 full groups and a padded one
    for i, p in enumerate(pk):
        q = prof.unpack(p)
        assert q["total"] == 0xFFFF and q["seq"] == i and q["depth"] == 4 and not q["short"]
        assert q["msg_id"] == 7 and q["parity"] is None
        for s, f in enumerate(prof.packet_frames(i)):
            assert q["frames"][s] == (fr[f] if f < len(fr) else bytes(6))
    # the K6 reads total 0xFFFF's last group (819 at depth 4) as a short one: the stream stops first
    assert packer.max_groups == 0xFFFF // 80 and packer.max_s == pytest.approx(819 * 3.2)
    assert k6live.Packer("1200", 1).max_groups == 0xFFFF // 20
    big = k6live.Packer("1200", 4)
    big.g = big.max_groups
    with pytest.raises(ValueError):
        for f in frames_of(80):
            big.push(f)


def test_parity_group():
    fr = frames_of(80)
    pk = k6live.Packer("1200", 4, 3, parity=True)
    g = [x for f in fr if (x := pk.push(f))][0]
    prof = pager.profile("XL", "1200", 4)
    assert len(g) == 5 and prof.unpack(g[4])["parity"] is not None
    acc = bytes(len(prof.unpack(g[0])["frames"]) * 6)
    for p in g[:4]:
        acc = bytes(a ^ b for a, b in zip(acc, b"".join(prof.unpack(p)["frames"])))
    assert prof.unpack(g[4])["parity"] == acc


def test_air_ratio_keeps_up_only_where_it_should():
    assert k6live.air_ratio("1200", 4) == pytest.approx(0.822, abs=0.002)
    assert all(k6live.air_ratio("1200", d) < 0.85 for d in (8, 4, 2, 1))
    assert all(k6live.air_ratio(c, 1) < 0.45 for c in ("600", "400"))
    assert k6live.air_ratio("2400", 4) > 1.5                # sotto 2400 can't stream live
    assert k6live.air_ratio("1200", 4, parity=True) > k6live.MAX_RATIO
    assert k6live.air_ratio("1200", 8, parity=True) < k6live.MAX_RATIO


def test_filler_looks_like_noise_to_the_k6():
    f = k6live.FILLER
    assert 0x55 not in f and 0xAA not in f and not k6live._has_sync(f + f[:8])
    assert k6live._has_sync(b"\x12" + pager.SYNC + b"\x34")
    assert k6live.make_filler() == f                        # the same every run


def test_modulator_is_phase_continuous_across_chunks():
    data = bytes(range(256)) * 3
    whole = k6live.Modulator(ramp_ms=0)(data)
    m = k6live.Modulator(ramp_ms=0)
    parts = np.concatenate([m(data[i:i + 37]) for i in range(0, len(data), 37)])
    assert len(parts) == len(whole) == round(len(data) * 8 * fsk.FS / k6live.BAUD)
    assert np.max(np.abs(parts - whole)) < 1e-6
    assert np.max(np.abs(np.diff(parts))) < 0.3 * 2 * np.pi * 1800 / fsk.FS * 1.01   # no jumps


def test_live_send_decodes_and_keeps_ahead_of_the_k6():
    """60 s of talk at depth 4: every packet decodes on the PC's demodulator, in order, with the
    stream-on control packet first; every group arrives at least slack - 0.1 s before a K6
    playing in real time needs it, so the K6 never waits; the first sound comes about
    3.2 + 0.5 + 2.6 s after the talker starts."""
    fr = frames_of(1500, seed=4)
    ctl = pager.control_packet(77, None, "stream-on", 128)
    x, snd = k6live.offline(fr, msg_id=7, slack_s=0.5, enc_delay_s=0.12, control=ctl)
    got = demod(x)
    want, _ = stream_packets(fr)
    assert got == [ctl] + want
    play, waited = k6live.k6_schedule(snd.groups(), snd.packer)
    assert waited == 0.0
    margin = min(p - g[1] for p, g in zip(play[1:], snd.groups()[1:]))
    assert margin > 0.4
    assert 6.0 < play[0] < 6.8
    assert snd.log[-1][2] == "tail" and len(x) / fsk.FS == pytest.approx(snd.log[-1][1], abs=0.01)
    fills = [t1 - t0 for t0, t1, w in snd.log if w == "fill"]
    assert fills and max(fills) < 0.05


def test_no_slack_runs_with_no_margin():
    """With slack 0 the groups arrive just as they are due (the sender keeps up, barely), which
    is why the default holds group 0 back 0.5 s."""
    fr = frames_of(800, seed=4)
    _, snd = k6live.offline(fr, slack_s=0.0)
    play, waited = k6live.k6_schedule(snd.groups(), snd.packer)
    margin = min(p - g[1] for p, g in zip(play[1:], snd.groups()[1:]))
    assert margin < 0.1 and waited < 0.1


def _dec_lr(frames, good):
    with tempfile.TemporaryDirectory() as d:
        with open(f"{d}/in.bit", "wb") as f:
            f.write(b"".join(frames))
        np.asarray(good, np.uint8).tofile(f"{d}/good.u8")
        subprocess.run([os.path.join(FX, "sotto_dec_fx_m0"), "-1200", f"{d}/in.bit", f"{d}/out.raw",
                        f"{d}/good.u8"], check=True)
        return np.fromfile(f"{d}/out.raw", "<i2")


def _stream_host(packets, halves=32, store=4096):
    with tempfile.TemporaryDirectory() as d:
        with open(f"{d}/p.bin", "wb") as f:
            f.write(b"".join(packets))
        out = subprocess.run([HOST_LR, "-s", str(halves), "-x", "-b", str(store), f"{d}/p.bin", f"{d}/o.raw"],
                             check=True, capture_output=True, text=True).stdout.split()
        return dict(kv.split("=", 1) for kv in out), np.fromfile(f"{d}/o.raw", "<i2")


@need_host
@pytest.mark.parametrize("depth,lost", [(4, []), (4, [5, 30]), (2, [9]), (8, [])])
def test_k6_plays_the_open_stream(depth, lost):
    """The K6's receive and play code (pager_host_lr streaming through its 4 KB ring) plays a
    60 s open-ended stream to its last group once the sender stops, sample for sample as the M0
    decoder plays the frames (the padding included, lost packets concealed alike)."""
    fr = frames_of(1503, seed=6)
    pk, packer = stream_packets(fr, depth)
    st, pcm = _stream_host([p for i, p in enumerate(pk) if i not in lost])
    n = packer.g * packer.fpg
    padded = fr + [bytes(6)] * (n - len(fr))
    good = [1] * n
    prof = pager.profile("XL", "1200", depth)
    for q in lost:
        for f in prof.packet_frames(q):
            good[f] = 0
    assert int(st["total"]) == 0xFFFF and int(st["overruns"]) == 0 and int(st["ring"]) > 0
    assert len(pcm) == n * 320
    assert np.array_equal(pcm, _dec_lr(padded, good))


def test_talk_dry_run_writes_the_send(tmp_path):
    """k6_talk.py --sot FILE --out WAV: the whole send as audio, no rig, no lock"""
    import k6_talk
    fr = frames_of(400, seed=2)
    sot = tmp_path / "t.sot"
    sot.write_bytes(b"".join(fr))
    wav = tmp_path / "t.wav"
    assert k6_talk.main(["--sot", str(sot), "--out", str(wav), "--msg-id", "9"]) == 0
    import soundfile as sf
    x, fs = sf.read(wav)
    got = demod(x if fs == fsk.FS else fsk.resample(x, fs, fsk.FS))
    want, _ = stream_packets(fr, msg_id=9)
    assert got[1:] == want and pager.parse_control(got[0])[5] == pager.CTL_ACTIONS["stream-on"]


def test_talk_refuses_what_cannot_keep_up(tmp_path):
    import k6_talk
    sot = tmp_path / "t.sot"
    sot.write_bytes(bytes(600))
    for bad in (["--codec", "2400"], ["--parity"], ["--seconds", "600"]):
        with pytest.raises(SystemExit):
            k6_talk.main(["--sot", str(sot), "--out", str(tmp_path / "x.wav")] + bad)


@pytest.mark.parametrize("codec,depth,needs", [("1200", 8, False), ("600", 1, False), ("400", 1, True),
                                               ("600", 4, True)])
def test_slow_groups_keep_the_k6_listening(codec, depth, needs):
    """The K6 ends a stream 2 s after its last packet. Between a slow codec's groups (sotto 400:
    2.4 s of speech an XL packet) the sender repeats the last group's packets, so no gap between
    packets comes near 2 s, and a K6 playing in real time still never waits."""
    n = int(60 / pager.CODECS[codec][1])
    _, snd = k6live.offline(frames_of(n, seed=3), codec, depth, slack_s=0.5)
    gaps = np.diff(snd.packet_ends())
    assert gaps.max() < 1.6
    assert k6live.k6_schedule(snd.groups(), snd.packer)[1] == 0.0
    _, bare = k6live.offline(frames_of(n, seed=3), codec, depth, repeat=False)
    assert (np.diff(bare.packet_ends()).max() > k6live.END_S) == needs   # without repeats it would end


@need_host
def test_k6_plays_the_stream_with_its_repeats():
    """What goes on air at depth 8 (repeats between groups), demodulated and fed to the K6's
    code in arrival order, plays exactly the frames sent: the repeats are only duplicates."""
    fr = frames_of(1000, seed=8)
    x, snd = k6live.offline(fr, "1200", 8, msg_id=4)
    got = demod(x)
    assert sum(1 for _, _, w in snd.log if w.startswith("repeat")) > 0
    st, pcm = _stream_host(got)
    n = snd.sent * snd.packer.fpg
    assert int(st["total"]) == 0xFFFF and len(pcm) == n * 320
    assert np.array_equal(pcm, _dec_lr(fr + [bytes(6)] * (n - len(fr)), [1] * n))


FAKE_ENC = """#!/usr/bin/env python3
import sys
i = 0
while True:
    b = sys.stdin.buffer.read(640)          # 40 ms at 8 kHz
    if len(b) < 640:
        break
    sys.stdout.buffer.write(bytes([i & 255, i >> 8, 0x11, 0x22, 0x33, 0x44]))
    sys.stdout.buffer.flush()
    i += 1
"""
FAKE_PACAT = """#!/usr/bin/env python3
import os, sys
with open(os.environ["FAKE_PACAT_OUT"], "wb") as f:
    f.write(sys.stdin.buffer.read())
"""


def test_talk_live_keys_streams_and_stops(tmp_path, monkeypatch):
    """The live path end to end with a fake rig, encoder and player: a 6 s file paced as if
    spoken, --seconds 4, so the stop path pads the second group. One key-up on 440.850 under the
    PTT lock, logged; the audio holds the stream-on control and both groups, in order."""
    import k6_bench
    import k6_talk
    import soundfile as sf
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, text in (("enc", FAKE_ENC), ("pacat", FAKE_PACAT)):
        (bin_ / name).write_text(text)
        (bin_ / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_PACAT_OUT", str(tmp_path / "air.raw"))
    monkeypatch.setenv("BLOWFANG_WORK", str(tmp_path / "work"))
    calls = []

    def rigctl(_=None):
        def rig(*args):
            calls.append(args)
            return {"f": "440850000", "t": "0", "r": "None", "s": "0"}.get(args[0], "")
        return rig
    monkeypatch.setattr(k6_bench, "rigctl", rigctl)
    sf.write(tmp_path / "s.wav", np.zeros(6 * 8000), 8000, subtype="PCM_16")
    t = __import__("time").monotonic()
    assert k6_talk.main(["--in", str(tmp_path / "s.wav"), "--enc", str(bin_ / "enc"), "--seconds", "4",
                         "--no-id", "--msg-id", "0x33"]) == 0
    assert __import__("time").monotonic() - t < 20
    assert calls.count(("T", 1)) == 1 and calls.count(("T", 0)) == 1
    x = np.fromfile(tmp_path / "air.raw", "<i2") / 32767
    got = demod(x)
    prof = pager.profile("XL", "1200", 4)
    assert pager.parse_control(got[0])[5] == pager.CTL_ACTIONS["stream-on"]
    q = [prof.unpack(p) for p in got[1:]]
    assert [p["seq"] for p in q] == list(range(8)) and all(p["msg_id"] == 0x33 for p in q)
    frames = {}
    for p in q:
        for s, f in enumerate(prof.packet_frames(p["seq"])):
            frames[f] = p["frames"][s]
    n_spoken = sum(1 for f in frames.values() if f[2:] == b"\x11\x22\x33\x44")
    assert 90 <= n_spoken <= 101                            # about 4 s of 40 ms frames, then padding
    assert all(frames[i][:2] == bytes([i & 255, i >> 8]) for i in range(n_spoken))
    txlog = open(tmp_path / "work" / "state" / "txlog.txt").read()
    assert "talk-through sotto 1200 depth 4 id=33" in txlog
    runs = list((tmp_path / "work" / "runs").iterdir())
    assert len(runs) == 1 and (runs[0] / "talk.sot").stat().st_size == 6 * n_spoken
