"""The PC's stop signal for a running module (c/k6/mod/k6_stop.h, insight 31): the pattern the
K6 looks for, built for the host and fed RSSI sequences; the WSPR beacon stopping on it on an
emulated M0; and tools/k6_bench.py stop keying it against a fake rig. SPDX-License-Identifier: MIT"""
import ctypes
import importlib.util
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HDR = os.path.join(ROOT, "c", "k6", "mod", "k6_stop.h")
sys.path.insert(0, HERE)


class Stop(ctypes.Structure):
    _fields_ = [("t_on", ctypes.c_uint32), ("t_seen", ctypes.c_uint32), ("on", ctypes.c_uint8),
                ("n", ctypes.c_uint8)]


@pytest.fixture(scope="module")
def feed(tmp_path_factory):
    cc = shutil.which("clang") or shutil.which("cc")
    if not cc:
        pytest.skip("needs a C compiler")
    d = tmp_path_factory.mktemp("stop")
    src = d / "stop.c"
    src.write_text('#include "%s"\nint feed(k6stop_t *s, uint32_t ms, int dbm) '
                   '{ return k6stop_feed(s, ms, dbm); }\n' % HDR)
    so = d / "stop.so"
    subprocess.run([cc, "-shared", "-fPIC", "-O2", "-Wall", "-Werror", "-o", str(so), str(src)], check=True)
    lib = ctypes.CDLL(str(so))
    lib.feed.argtypes = [ctypes.POINTER(Stop), ctypes.c_uint32, ctypes.c_int]
    return lib.feed


def run(feed, spans, end_ms, every=100, t0=0, drop=()):
    """RSSI -40 inside the (from, to) ms spans, else -120, read every `every` ms; drop: ms of
    single missed readings inside a carrier. The ms of each stop."""
    s, out = Stop(), []
    for t in range(t0, end_ms, every):
        up = any(a <= t < b for a, b in spans) and t not in drop
        if feed(ctypes.byref(s), t & 0xFFFFFFFF, -40 if up else -120):
            out.append(t)
    return out


def test_two_bursts_stop(feed):
    got = run(feed, [(10_000, 13_500), (15_500, 19_000)], 30_000)
    assert len(got) == 1 and 19_200 <= got[0] <= 19_400, got


@pytest.mark.parametrize("spans", [
    [(10_000, 20_000)],                                  # a CW ID or a data send: one long carrier
    [(10_000, 13_500)],                                  # one burst only
    [(10_000, 11_500), (13_000, 14_500)],                # two kerchunks
    [(10_000, 13_500), (13_900, 17_400)],                # 0.4 s apart: one carrier with a dropout
    [(10_000, 13_500), (19_100, 22_600)],                # 5.6 s apart: too late
    [(10_000, 13_500), (14_400, 21_000)],                # the second too long
    [(10_000, 17_000), (19_000, 22_500)],                # the first too long
])
def test_other_carriers_do_not_stop(feed, spans):
    assert run(feed, spans, 40_000) == []


def test_dropouts_inside_a_burst_are_ignored(feed):
    got = run(feed, [(10_000, 13_500), (15_500, 19_000)], 30_000, drop=(11_000, 16_000, 16_100))
    assert len(got) == 1


def test_a_stop_after_other_traffic_and_across_the_ms_wrap(feed):
    t0 = 0xFFFFFFFF - 12_000 + 1                      # ms() wraps between the two bursts
    spans = [(t0, t0 + 9_000), (t0 + 11_000, t0 + 14_500), (t0 + 16_500, t0 + 20_000)]
    s, got = Stop(), []
    for t in range(t0, t0 + 30_000, 50):
        up = any(a <= t < b for a, b in spans)
        if feed(ctypes.byref(s), t & 0xFFFFFFFF, -40 if up else -120):
            got.append(t - t0)
    assert len(got) == 1 and 20_200 <= got[0] <= 20_400, got


def test_coarse_feeding_still_works(feed):
    """a module that feeds every 250 ms (wait loops with other work)"""
    assert len(run(feed, [(10_000, 13_500), (15_500, 19_000)], 30_000, every=250)) == 1


def test_the_pc_signal_fits_the_pattern():
    """stop.wav's length as keyed (lead 0.2 s, tail 0.05 s) and the gap stay inside k6_stop.h's
    windows with margin for rigctl's latency"""
    sys.path.insert(0, ROOT)
    from modem import psk31
    spec = importlib.util.spec_from_file_location("k6_bench", os.path.join(ROOT, "tools", "k6_bench.py"))
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    x = psk31.modulate(b.STOP_TEXT, 48000, 1000.0, 0.3, lead_s=0.3, tail_s=0.1)
    on = len(x) / 48000 + 0.25
    h = open(HDR).read()

    def ms(name):
        return int(h.split(f"#define {name} ")[1].split("u")[0]) / 1000
    assert ms("K6STOP_ON_MIN") + 0.5 < on < ms("K6STOP_ON_MAX") - 1.5, on
    assert ms("K6STOP_GAP_MIN") + 0.5 < b.STOP_GAP_S < ms("K6STOP_GAP_MAX") - 2.0


def test_bench_stop_keys_twice(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("k6_bench", os.path.join(ROOT, "tools", "k6_bench.py"))
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    monkeypatch.setenv("BLOWFANG_WORK", str(tmp_path))
    monkeypatch.delenv("K6_BENCH_AS", raising=False)
    played = []
    monkeypatch.setattr(b, "play", lambda wav, device=None, dry_run=False, max_s=None: played.append(wav))
    monkeypatch.setattr(b, "stop_wav", lambda: "stop.wav")
    monkeypatch.setattr(b.time, "sleep", lambda s: None)
    from test_k6_bench import Rig
    r = Rig()
    monkeypatch.setattr(b, "rigctl", lambda rigctld=None: r)
    b.main(["stop"])
    assert played == ["stop.wav", "stop.wav"] and r.calls.count("T 1") == 2 and r.ptt == 0
    assert open(b.path("txlog")).read().count("module stop signal") == 2
    r2 = Rig(hz=446_000_000)
    monkeypatch.setattr(b, "rigctl", lambda rigctld=None: r2)
    with pytest.raises(SystemExit):
        b.main(["stop"])
    assert "T 1" not in r2.calls
    b.claim("explore", 10)
    monkeypatch.setattr(b, "rigctl", lambda rigctld=None: r)
    with pytest.raises(SystemExit, match="held by explore"):
        b.main(["stop"])


WSPR = os.path.join(ROOT, "c", "k6", "mod", "wsprtx.c")


def beacon(tmp_path, carriers, every=5):
    from test_k6_i2s import START, rssi_reg, run as emu
    src = tmp_path / "wsprstop.c"
    src.write_text('#define WSPR_REPEAT 2\n#define WSPR_EVERY %d\n#include "%s"\n' % (every, WSPR))

    def rssi(us):
        return rssi_reg(-40 if any(a <= us < b for a, b in carriers) else -120)
    tx = {}
    ret, said, _, _ = emu({**START, 0x67: rssi}, src=str(src), level=3, tx=tx)
    return ret, said, [n for n, *_ in tx.get("log", ())]


@pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")), reason="needs clang and ld.lld")
def test_the_beacon_stops_between_frames(tmp_path):
    """the first frame goes out at once (no sync mark), then the PC's two bursts at 200 s:
    the beacon says so and returns -8 instead of waiting for the second slot at 600 s"""
    ret, said, log = beacon(tmp_path, [(200_000_000, 203_500_000), (205_500_000, 209_000_000)])
    assert ret == -8, said
    assert said[-1] == "wspr stopped by the PC after 1 sent", said
    assert log.count("tx_on") == 1 and log[-1] == "tx_off"


@pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")), reason="needs clang and ld.lld")
def test_a_cw_id_between_frames_does_not_stop_the_beacon(tmp_path):
    ret, said, log = beacon(tmp_path, [(150_000_000, 160_000_000)], every=2)
    assert ret == 2, said
    assert log.count("tx_on") == 2
