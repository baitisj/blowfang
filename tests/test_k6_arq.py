"""Upload ARQ end to end on this machine: the K6 loader's reports (k6_mod.c via km_host) as the
air audio the K6 sends (F/1800, the chip's framing), decoded back by modem/k6mod.py's reports()
through noise, plus the decisions tools/k6_upload.py makes from them and the CLI's --arq.
SPDX-License-Identifier: MIT"""
import os
import subprocess
import sys
import types

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_mod import KEY, fake_module, need_host, run_host_reports  # noqa: E402

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import fsk, k6, k6mod, pager  # noqa: E402
import k6_upload  # noqa: E402


def air(packets, fs=48000, snr_db=None, seed=1):
    """packets as the K6's chip sends them (F/1800, L framing) with silence around"""
    a = types.SimpleNamespace(prime=False, baud=1800, payload="L", later_preamble=None, first_preamble=None,
                              lead_ms=150.0, stream=False, phy="F", level=0.5, invert=False, comp=None,
                              max_gain=30.0, tones=None, cw_id=None, cw_wpm=20)
    x = k6.build_audio(list(packets), a, fs)
    x = np.concatenate([np.zeros(fs // 2), x, np.zeros(fs // 2)])
    if snr_db is not None:
        rms = np.sqrt(np.mean(x[x != 0] ** 2))
        x = x + np.random.default_rng(seed).normal(0, rms * 10 ** (-snr_db / 20), len(x))
    return x


@need_host
@pytest.mark.parametrize("snr_db", [None, 12])
def test_partway_report_through_the_air(snr_db):
    img = fake_module(500)
    pk = k6mod.packets(img, 9, 64, arq=True)
    lost = [2, 5]
    _, status, text = run_host_reports([p for i, p in enumerate(pk) if i not in lost])
    reps = k6mod.reports(air([status, status, text], snr_db=snr_db), 48000)
    kinds = [r["kind"] for _, r in reps]
    assert kinds.count("status") >= 1 and "text" in kinds, reps
    st, joined = k6mod.collect(reps)
    assert st["state"] == "partway" and st["missing"] == lost and st["xfer"] == 9
    assert joined == "hello\n"
    assert k6_upload.next_step(st, len(pk), 9) == ("send", lost)


def test_next_step():
    base = dict(xfer=4, state="partway", missing=[], r=1, ret=0, ms=0, stack_free=0)
    assert k6_upload.next_step(None, 7, 4) == ("send", list(range(7)))
    assert k6_upload.next_step(dict(base, missing=[0, 6]), 7, 4) == ("send", [0, 6])
    assert k6_upload.next_step(base, 7, 4) == ("send", list(range(7)))
    assert k6_upload.next_step(dict(base, state="ran", ret=-9, ms=1500, stack_free=900), 7, 4) == \
        ("done", "ran: returned -9 in 1500 ms, 900 B stack free")
    assert k6_upload.next_step(dict(base, state="refused", r=-4), 7, 4) == ("stop", "refused: wrong key")
    assert k6_upload.next_step(dict(base, xfer=5), 7, 4)[0] == "stop"


def test_switch_size():
    """only a silence with nothing ever heard at this size changes it"""
    st = dict(xfer=4, state="partway", missing=[3])
    assert k6_upload.other_size("XL") == "L" and k6_upload.other_size("L") == "XL"
    assert k6_upload.switch_size(None, False, True)
    assert not k6_upload.switch_size(None, True, True)       # it worked before: a lost send
    assert not k6_upload.switch_size(st, False, True)        # a report: the size works
    assert not k6_upload.switch_size(None, False, False)     # --no-fallback


def fake_upload(monkeypatch, k6_size, argv, d24=None):
    """k6_upload.main against a K6 that hears only k6_size chunks and runs the module once it
    has every chunk of a transfer: the sends it was given, as (payload, serial, chunks, rung).
    d24: None, an image without the rate ladder (rates F12,F18); "ok", one that takes D/2400;
    "deaf", one that takes it but hears no D/2400 chunk (a path that isn't flat)"""
    sends, got, backs = [], {}, []
    fake_upload.backs = backs
    monkeypatch.setattr(k6_upload, "rig", lambda a, *v: str(a.freq) if v == ("f",) else "0")
    monkeypatch.setattr(k6_upload, "key_and_play", lambda *a, **k: None)
    monkeypatch.setattr(k6_upload, "Recorder", lambda a, d: type("R", (), {
        "samples": lambda self: 0, "stop": lambda self: None, "path": ""})())
    monkeypatch.setattr(k6_upload.time, "sleep", lambda s: None)

    def mod_wav(a, path, serial, xfer, only=None, payload=None, rung=None):
        sends.append((payload, serial, list(only), rung or "F18"))
        if payload == k6_size and (rung != "D24" or d24 == "ok"):
            got.setdefault(xfer, set()).update(only)
        return ""

    def d24_send(a, d, rnd, serial, xfer, todo, payload, back=False):
        assert d24 is not None, "asked an image without the ladder for D/2400"
        backs.append(back)
        return "up.wav", 5000.0, mod_wav(a, "", serial, xfer, todo, payload, rung="F18" if back else "D24")

    def listen(a, rec, since, xfer, seconds):
        if xfer not in got:
            return None, "", []
        count = k6_upload.chunks_of(a, k6_size)
        missing = sorted(set(range(count)) - got[xfer])
        state = "ran" if not missing else "partway"
        return dict(xfer=xfer, state=state, missing=missing, have=count - len(missing), count=count,
                    r=1, ret=0, ms=5, stack_free=900, rates="F12,F18" if d24 is None else "F12,F18,D24",
                    rung="F18", fell=None), "", []

    monkeypatch.setattr(k6_upload, "mod_wav", mod_wav)
    monkeypatch.setattr(k6_upload, "d24_send", d24_send)
    monkeypatch.setattr(k6_upload, "listen", listen)
    import modem.k6 as k6
    import soundfile
    monkeypatch.setattr(k6, "cw", lambda *a, **k: np.zeros(10))
    monkeypatch.setattr(soundfile, "write", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["k6_upload.py", MOD_SRC, "--key-file", "/dev/null"] + argv)
    k6_upload.main()
    return sends


MOD_SRC = os.path.join(ROOT, "c", "k6", "mod", "hello.c")


@need_host
def test_upload_falls_back_to_l(monkeypatch, capsys):
    """a K6 set to L hears no XL chunk: the second send is every chunk at L, a new transfer"""
    sends = fake_upload(monkeypatch, "L", [])
    assert [p for p, *_ in sends] == ["XL", "L"]
    assert sends[1][1] > sends[0][1]                            # a newer serial
    assert sends[1][2] == list(range(len(sends[1][2]))) and len(sends[1][2]) > len(sends[0][2])
    assert "ran: returned 0" in capsys.readouterr().out


@need_host
def test_upload_stays_xl(monkeypatch):
    """a K6 at XL: one send, no fallback; --no-fallback keeps XL through silence"""
    assert [p for p, *_ in fake_upload(monkeypatch, "XL", [])] == ["XL"]
    assert [p for p, *_ in fake_upload(monkeypatch, "L", ["--no-fallback", "--rounds", "2"])] == ["XL", "XL"]


def test_start_rung():
    """the IC-7100 never asks for D/2400; auto asks only when the K6 is known to take it"""
    fresh = k6_upload.path_state({}, "pluto")
    assert fresh == {"rung": "D24", "clean": 0, "k6_d24": False}
    assert k6_upload.start_rung("auto", "ic7100", fresh) == "F18"
    assert k6_upload.start_rung("F18", "ic7100", fresh) == "F18"
    with pytest.raises(SystemExit):
        k6_upload.start_rung("D24", "ic7100", fresh)
    assert k6_upload.start_rung("auto", "pluto", fresh) == "F18"            # not known yet
    known = dict(fresh, k6_d24=True)
    assert k6_upload.start_rung("auto", "pluto", known) == "D24"
    assert k6_upload.start_rung("auto", "pluto", dict(known, rung="F18")) == "F18"
    assert k6_upload.start_rung("D24", "pluto", fresh) == "D24"
    assert k6_upload.start_rung("F18", "pluto", known) == "F18"


def test_after_send_steps_down():
    ok = dict(fell=None)
    assert k6_upload.after_send("D24", ok, 0.0, "auto") == ("D24", None)
    assert k6_upload.after_send("D24", ok, 0.15, "auto") == ("D24", None)
    assert k6_upload.after_send("D24", ok, 0.5, "auto")[0] == "F18"
    assert k6_upload.after_send("D24", None, None, "auto")[0] == "F18"
    assert k6_upload.after_send("D24", dict(fell="crc"), 0.0, "auto")[0] == "F18"
    assert k6_upload.after_send("D24", dict(fell="revert"), 0.0, "auto")[0] == "F18"
    assert k6_upload.after_send("D24", dict(fell="quiet"), 0.0, "auto") == ("D24", None)  # an old session's end
    assert k6_upload.after_send("D24", None, None, "D24") == ("D24", None)    # forced
    assert k6_upload.after_send("F18", None, None, "auto") == ("F18", None)


def test_lost_share():
    assert k6_upload.lost_share(0, 0, 0) is None
    assert k6_upload.lost_share(10, 0, 10) == 0.0
    assert k6_upload.lost_share(10, 2, 7) == 0.5
    assert k6_upload.lost_share(4, 0, 0) == 1.0


def test_end_upload_ladder():
    """a step down marks the path F18; two clean uploads mark it D24 again; a lossy one resets"""
    ps = {"rung": "D24", "clean": 0, "k6_d24": True}
    ps = k6_upload.end_upload(ps, True, 0.4, True)
    assert ps["rung"] == "F18" and ps["clean"] == 0
    ps = k6_upload.end_upload(ps, False, 0.0, True)
    assert ps == {"rung": "F18", "clean": 1, "k6_d24": True}
    lossy = k6_upload.end_upload(ps, False, 0.2, True)
    assert lossy["clean"] == 0 and lossy["rung"] == "F18"
    unfinished = k6_upload.end_upload(ps, False, 0.0, False)
    assert unfinished["clean"] == 0
    ps = k6_upload.end_upload(ps, False, 0.01, True)
    assert ps == {"rung": "D24", "clean": 0, "k6_d24": True}
    assert k6_upload.end_upload(ps, False, 0.0, True) == ps                # D24 stays D24


def test_rate_state_round_trip(tmp_path):
    p = str(tmp_path / "state" / "k6-rate.json")
    assert k6_upload.load_rates(p) == {}
    k6_upload.save_rates({"pluto": {"rung": "F18", "clean": 1, "k6_d24": True}}, p)
    assert k6_upload.load_rates(p)["pluto"]["rung"] == "F18"
    open(p, "w").write("not json")
    assert k6_upload.load_rates(p) == {}


def test_d24_track_levels():
    """control record pre-emphasised at 3 kHz before emphasis, the gap, then D/2400 flat at 2.2 kHz"""
    fs = 48000
    t = np.arange(fs // 2) / fs
    ctl = 0.3 * np.sin(2 * np.pi * 1000 * t)                 # 1 kHz: pre-emphasis keeps its level
    d24 = 0.3 * np.sign(np.sin(2 * np.pi * 1200 * t))
    x, full = k6_upload.d24_track(ctl, d24, fs)
    gap = int(k6_upload.D24_GAP_S * fs)
    assert len(x) == len(ctl) + gap + len(d24)
    assert np.max(np.abs(x)) <= 1.0
    hz = x * full
    assert abs(np.max(np.abs(hz[fs // 10:len(ctl)])) - 3000) < 150
    assert np.all(hz[len(ctl) + 2000:len(ctl) + gap] == 0) or np.max(np.abs(hz[len(ctl) + 4000:len(ctl) + gap])) < 50
    assert abs(np.max(np.abs(hz[len(ctl) + gap:])) - k6_upload.D24_PEAK_HZ) < 1


def test_flat_track_back_to_f1800():
    """a D/2400 record asking for F/1800, flat at 2.2 kHz, then F/1800 chunks pre-emphasised"""
    fs = 48000
    t = np.arange(fs // 2) / fs
    d24 = 0.3 * np.sign(np.sin(2 * np.pi * 1200 * t))
    f18 = 0.3 * np.sin(2 * np.pi * 1000 * t)
    x, full = k6_upload.flat_track([(d24, 2200.0, False), (f18, 3000.0, True)], fs)
    hz = x * full
    assert abs(np.max(np.abs(hz[:len(d24)])) - 2200) < 1
    assert abs(np.max(np.abs(hz[-fs // 4:])) - 3000) < 150


def test_ctl_wav_both_ways(monkeypatch, tmp_path):
    """the control records: at F/1800 asking phy=D baud=2400, at D/2400 asking phy=F, each at the
    upload's packet size"""
    a = types.SimpleNamespace(payload="XL", level=0.3)
    seen = []
    real = subprocess.run
    monkeypatch.setattr(k6_upload.subprocess, "run", lambda cmd, **k: seen.append(cmd) or real(cmd, **k))
    k6_upload.ctl_wav(a, str(tmp_path / "up.wav"))
    k6_upload.ctl_wav(a, str(tmp_path / "back.wav"), "L", back=True)
    up, back = seen
    assert up[up.index("--phy") + 1:up.index("--phy") + 4] == ["F", "--baud", "1800"]
    assert up[up.index("--set") + 1:] == ["phy=D", "baud=2400"] and up[up.index("--payload") + 1] == "XL"
    assert back[back.index("--phy") + 1:back.index("--phy") + 4] == ["D", "--baud", "2400"]
    assert back[back.index("--set") + 1:] == ["phy=F"] and back[back.index("--payload") + 1] == "L"
    assert (tmp_path / "up.wav").stat().st_size > 0 and (tmp_path / "back.wav").stat().st_size > 0


def test_key_and_play_flat_dev(capsys):
    """a shaped D/2400 track goes to pluto_bench without --preemph, full scale = flat_dev"""
    a = types.SimpleNamespace(via="pluto", freq=440_850_000, pluto_gain=-52.0, level=0.3,
                              pluto_peak=3000.0, dry_run=True)
    k6_upload.key_and_play(a, "up.wav", flat_dev=4946.2)
    out = capsys.readouterr().out
    assert "--dev 4946" in out and "--preemph" not in out
    k6_upload.key_and_play(a, "up.wav")
    out = capsys.readouterr().out
    assert "--preemph --gain -52.0 --dev 10000" in out


@need_host
def test_upload_auto_learns_then_asks_d24(monkeypatch, tmp_path, capsys):
    """auto: the first upload goes at F/1800 and learns the K6 takes D24 from its report; the
    next goes at D/2400 in one send"""
    monkeypatch.setattr(k6_upload, "D24_MIN_CHUNKS", 1)     # hello.c is 4 chunks
    st = str(tmp_path / "rate.json")
    argv = ["--via", "pluto", "--rate-state", st, "--rate", "auto"]
    sends = fake_upload(monkeypatch, "XL", argv, d24="ok")
    assert [r for *_, r in sends] == ["F18"]
    assert k6_upload.load_rates(st)["pluto"]["k6_d24"] is True
    sends = fake_upload(monkeypatch, "XL", argv, d24="ok")
    assert [r for *_, r in sends] == ["D24"]
    assert k6_upload.load_rates(st)["pluto"]["rung"] == "D24"


@need_host
def test_upload_d24_deaf_steps_down_and_comes_back(monkeypatch, tmp_path, capsys):
    """a D/2400 send that brings no report: the rest at F/1800 (no size switch on that silence),
    the path marked F18; two clean F/1800 uploads mark it D24 again"""
    monkeypatch.setattr(k6_upload, "D24_MIN_CHUNKS", 1)
    st = str(tmp_path / "rate.json")
    k6_upload.save_rates({"pluto": {"rung": "D24", "clean": 0, "k6_d24": True}}, st)
    argv = ["--via", "pluto", "--rate-state", st, "--rate", "auto"]
    sends = fake_upload(monkeypatch, "XL", argv, d24="deaf")
    assert [(p, r) for p, _, _, r in sends] == [("XL", "D24"), ("XL", "F18")]
    assert fake_upload.backs == [False, True]          # the F/1800 resend led by a D/2400 record
    assert "the rest goes at F/1800" in capsys.readouterr().out
    assert k6_upload.load_rates(st)["pluto"] == {"rung": "F18", "clean": 0, "k6_d24": True}
    for clean in (1, 0):
        assert [r for *_, r in fake_upload(monkeypatch, "XL", argv, d24="deaf")] == ["F18"]
        assert k6_upload.load_rates(st)["pluto"]["clean"] == clean
    assert k6_upload.load_rates(st)["pluto"]["rung"] == "D24"


@need_host
def test_upload_small_module_stays_f1800(monkeypatch, tmp_path, capsys):
    """auto, a K6 known to take D24, but hello.c's 4 chunks: asking would cost more than it saves"""
    st = str(tmp_path / "rate.json")
    k6_upload.save_rates({"pluto": {"rung": "D24", "clean": 0, "k6_d24": True}}, st)
    assert [r for *_, r in fake_upload(monkeypatch, "XL", ["--via", "pluto", "--rate-state", st, "--rate", "auto"], d24="ok")] == ["F18"]
    assert "F/1800 is quicker" in capsys.readouterr().out
    assert k6_upload.load_rates(st)["pluto"]["rung"] == "D24"          # the path stays D24
    assert [r for *_, r in fake_upload(monkeypatch, "XL", ["--via", "pluto", "--rate-state", st, "--rate", "D24"],
                                       d24="ok")] == ["D24"]              # forced: asks anyway


def test_start_rung_size_limit():
    known = {"rung": "D24", "clean": 0, "k6_d24": True}
    n = k6_upload.D24_MIN_CHUNKS
    assert k6_upload.start_rung("auto", "pluto", known, n - 1) == "F18"
    assert k6_upload.start_rung("auto", "pluto", known, n) == "D24"
    assert k6_upload.start_rung("D24", "pluto", known, 1) == "D24"


@need_host
def test_upload_default_is_f1800(monkeypatch, tmp_path):
    """until the image's D/2400 receive holds a long upload, the default never asks"""
    monkeypatch.setattr(k6_upload, "D24_MIN_CHUNKS", 1)
    st = str(tmp_path / "rate.json")
    k6_upload.save_rates({"pluto": {"rung": "D24", "clean": 0, "k6_d24": True}}, st)
    assert [r for *_, r in fake_upload(monkeypatch, "XL", ["--via", "pluto", "--rate-state", st], d24="ok")] == ["F18"]


@need_host
def test_upload_ic7100_never_asks(monkeypatch, tmp_path):
    st = str(tmp_path / "rate.json")
    k6_upload.save_rates({"ic7100": {"rung": "D24", "clean": 0, "k6_d24": True}}, st)
    assert [r for *_, r in fake_upload(monkeypatch, "XL", ["--rate-state", st], d24="ok")] == ["F18"]


@need_host
def test_cli_arq_upload_and_reports(tmp_path):
    key = tmp_path / "k"
    key.write_text(KEY.hex() + "\n")
    code = tmp_path / "m.bin"
    code.write_bytes(bytes(150))
    w = tmp_path / "up.wav"
    r = subprocess.run([sys.executable, "-m", "modem.k6", "mod", str(w), str(code), "--key-file", str(key),
                        "--serial", "321", "--xfer", "7", "--phy", "F", "--baud", "1800", "--payload", "L", "--arq",
                        "--telemetry", "3", "--copies", "1"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "reply asked" in r.stderr
    x, fs = k6._read(str(w))
    d = fsk.Demodulator("F", 1800, fsk.FS, 64)
    got = [k6mod.parse(p) for _, p, _, _ in d.demodulate(fsk.resample(x, fs, fsk.FS))]
    got = [g for g in got if g]
    assert len(got) == 3 and all(g[0] == 7 for g in got)
    img = b"".join(g[3] for g in sorted(got, key=lambda g: g[1]))
    assert img[14] == 3          # the telemetry parts asked for
    assert all(pager._whiten(p)[0] == k6mod.MAGIC_ARQ
               for p in k6mod.packets(img[:16 + 150 + 8], 7, 64, arq=True))
    r = subprocess.run([sys.executable, "-m", "modem.k6", "mod", str(w), str(code), "--key-file", str(key),
                        "--telemetry", "3"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode != 0 and "--arq" in r.stderr
    # the reports subcommand on a recording of the K6
    pk = k6mod.packets(k6mod.image(bytes(150), 0, 321, KEY, tlm=3), 7, 64, arq=True)
    _, status, text = run_host_reports(pk)
    import soundfile as sf
    rec = tmp_path / "rec.wav"
    sf.write(str(rec), air([status, text], snr_db=15), 48000, subtype="PCM_16")
    r = subprocess.run([sys.executable, "-m", "modem.k6", "reports", str(rec)], cwd=ROOT, capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr
    assert "xfer 7 ran, 3/3 chunks, r=3 (ready)" in r.stdout and "hello" in r.stdout, r.stdout


def test_reports_ignore_other_packets():
    """voice and module packets on the channel are not reports"""
    pk = k6mod.packets(fake_module(120), 2, 64)
    assert k6mod.reports(air(pk), 48000) == []


def test_reports_on_a_scrap_of_audio():
    """the bench's first decode pass can see only a few samples of a growing recording"""
    for n in (0, 10, 2000):
        assert k6mod.reports(np.zeros(n), 48000) == []


def test_recorder_marks_by_samples_written(tmp_path):
    a = types.SimpleNamespace(dry_run=True)
    rec = k6_upload.Recorder(a, str(tmp_path))
    assert rec.samples() == 0 and len(rec.audio()) == 0
    np.arange(48000 * 2, dtype=np.int16).tofile(rec.path)
    assert rec.samples() == 96000 and len(rec.audio(rec.samples() - 100)) == 100


@need_host
def test_listen_decodes_the_tail_of_a_long_recording(tmp_path):
    """explorer-scan-6: after 30 minutes, decoding the whole recording each pass never reached the
    report. tail_reports decodes the last minute, keeps what it saw before, and counts nothing
    twice."""
    img = fake_module(300)
    pk = k6mod.packets(img, 7, 64, arq=True)
    _, status, text = run_host_reports(pk)
    a = types.SimpleNamespace(dry_run=True, phy="F", baud=1800)
    rec = k6_upload.Recorder(a, str(tmp_path))
    early = air([status])                                    # a report near the start
    gap = np.zeros(48000 * 300)                              # five quiet minutes
    late = air([status, text])
    calls = []
    real = k6mod.reports
    k6mod.reports = lambda x, *r: calls.append(len(x)) or real(x, *r)
    try:
        reps = []
        for part in (early, gap, late):
            with open(rec.path, "ab") as f:
                f.write((part * 32767).astype(np.int16).tobytes())
            reps = k6_upload.tail_reports(a, rec, 0, reps)
        reps = k6_upload.tail_reports(a, rec, 0, reps)           # one more pass: nothing doubles
    finally:
        k6mod.reports = real
    assert max(calls) <= 48000 * k6_upload.TAIL_S
    kinds = [r["kind"] for _, r in reps]
    assert kinds.count("status") == 2 and kinds.count("text") >= 1, reps
    t = [t for t, _ in reps]
    assert t[0] < 2 and t[1] > 300
    st, joined = k6mod.collect([(t, r) for t, r in reps if t > 300])
    assert st["xfer"] == 7 and joined == "hello\n"
