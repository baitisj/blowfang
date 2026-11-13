"""tools/uv380_micprobe.py: the setting whose audio is a stretch of the reference stands out by
its correlation; the others (noise) don't. micprobe.c and micadc.c build and fit.
SPDX-License-Identifier: MIT"""
import os
import shutil
import sys
import wave

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import uv380_micprobe as mp  # noqa: E402

TOOLS = all(shutil.which(t) for t in ("clang", "ld.lld", "llvm-objcopy", "llvm-nm"))


def lines_for(k, x):
    out = [f"mod say set {k} r26=fd pd9=0 mic=1 00=00"]
    x = np.clip(np.round(x), -32768, 32767).astype(int)
    for i in range(0, len(x) - 63, 64):
        out.append(f"a {i // 64} " + " ".join(f"{int(v) & 0xFFFF:04x}" for v in x[i:i + 64]))
    out.append(f"mod say got {k} rms=1 zc=1 pk=1 ov=0 al=0")
    return out


def test_the_mic_setting_stands_out(tmp_path, capsys):
    rng = np.random.default_rng(1)
    n = 48000 * 20 // 6                     # 20 s at 8 kHz: voice-band noise in syllables
    syl = np.repeat(rng.random(n // 1600 + 1) ** 2, 1600)[:n]
    syl = np.convolve(syl, np.ones(400) / 400, "same")
    ref = mp.bandpass(rng.standard_normal(n) * 3000 * syl)
    ref48 = np.interp(np.arange(0, len(ref), 1 / 6), np.arange(len(ref)), ref)
    with wave.open(str(tmp_path / "ref.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(np.clip(ref48, -32768, 32767).astype("<i2").tobytes())
    log = ["mod say micprobe reg00=5a r26=7d"]
    for k in range(4):
        if k == 2:      # a room's worth of the reference: delayed, quieter, filtered, noisy
            x = np.convolve(ref[30000:39600], [0.5, 0.3, 0.1], "same") * 0.2
            x += rng.standard_normal(len(x)) * 150
        else:
            x = rng.standard_normal(9600) * 2000
        log += lines_for(k, x)
    (tmp_path / "log").write_text("\n".join(log) + "\n")
    assert mp.main([str(tmp_path / "log"), "--ref", str(tmp_path / "ref.wav"),
                    "--wav-dir", str(tmp_path / "w")]) == 0
    rows = [ln.split() for ln in capsys.readouterr().out.splitlines() if ln[:3].strip().isdigit()]
    corr = {int(r[0]): float(r[3]) for r in rows}
    env = {int(r[0]): float(r[4]) for r in rows}
    assert corr[2] > 0.5 and all(corr[k] < 0.1 for k in (0, 1, 3)), corr
    assert env[2] > 0.8 and all(env[k] < env[2] - 0.15 for k in (0, 1, 3)), env
    assert sorted(os.listdir(tmp_path / "w")) == [f"set0{k}.wav" for k in range(4)]


@pytest.mark.skipif(not TOOLS, reason="needs clang and lld")
def test_micprobe_and_micadc_fit():
    from modem import uv380mod
    for name, most in (
        ("micprobe.c", 3072),
        ("micprobe2.c", 3072),
        ("micadc.c", 2048),
    ):
        path = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod", name)
        code, entry = uv380mod.build(path)
        assert entry == 0 and len(code) < most, (name, len(code))
        assert uv380mod.source_table(path) == "openrtx"
