"""The UV-380 I2S probe's analysis (firmware/uv380-openrtx/i2sprobe.c, the part that runs on any
host) and the report that reads its lines (tools/uv380_i2sprobe.py). The radio part (pins, I2S,
DMA) only runs on the radio. Needs a C compiler. SPDX-License-Identifier: MIT"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "firmware", "uv380-openrtx", "i2sprobe.c")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import uv380_i2sprobe as tool  # noqa: E402

CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
pytestmark = pytest.mark.skipif(CC is None, reason="needs a C compiler")


class Stats(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint32) for n in ("n", "nz", "changes")] + [("mean", ctypes.c_int32)] + \
               [(n, ctypes.c_uint32) for n in ("rms", "peak_hz", "peak_pm")]


SAY = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p)


class Ops(ctypes.Structure):
    _fields_ = [("say", SAY), ("c6000_write", ctypes.c_void_p), ("c6000_read", ctypes.c_void_p),
                ("sleep_ms", ctypes.c_void_p), ("stop", ctypes.c_void_p), ("adc_capture", ctypes.c_void_p),
                ("ctx", ctypes.c_void_p)]


@pytest.fixture(scope="module")
def lib():
    d = tempfile.mkdtemp()
    so = os.path.join(d, "i2sprobe.so")
    subprocess.run([CC, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", SRC, "-o", so, "-lm"],
                   check=True)
    yield ctypes.CDLL(so)
    shutil.rmtree(d)


def analyse(lib, x, stride=1, fs=8000):
    x = np.ascontiguousarray(x, np.int16)
    s = Stats()
    lib.i2sp_analyse(x.ctypes.data_as(ctypes.c_void_p), len(x) // stride, stride, fs, ctypes.byref(s))
    return s


def test_tone(lib):
    """a 1000 Hz tone at 8 kHz: its bin, nearly all the power, its RMS"""
    t = np.arange(1024) / 8000
    s = analyse(lib, 3000 * np.sin(2 * np.pi * 1000 * t) + 200)
    assert (s.peak_hz, s.n) == (1000, 1024) and s.peak_pm > 950
    assert abs(s.rms - 2121) < 10 and abs(s.mean - 200) <= 1 and s.nz > 1000


def test_silence_and_noise(lib):
    s = analyse(lib, np.zeros(1024))
    assert (s.nz, s.rms, s.peak_pm, s.changes) == (0, 0, 0, 0)
    n = np.random.default_rng(1).normal(0, 1000, 1024)
    s = analyse(lib, n)
    assert s.peak_pm < 150 and 900 < s.rms < 1100


def test_two_channels(lib):
    """interleaved words: each channel by its stride, the tone it holds (bins are fs / 64 apart:
    600 Hz at 16 kHz falls in the band around 500)"""
    t = np.arange(1024) / 16000
    x = np.empty(2048)
    x[0::2] = 8000 * np.sin(2 * np.pi * 1250 * t)
    x[1::2] = 4000 * np.sin(2 * np.pi * 600 * t)
    a = analyse(lib, x, 2, 16000)
    b = analyse(lib, x[1:], 2, 16000)
    assert a.peak_hz == 1250 and a.peak_pm > 950
    assert b.peak_hz == 500 and b.peak_pm > 800


def test_format_and_pass(lib):
    buf = ctypes.create_string_buffer(b"c SPI3 m e0=c9 mx=1 P16 8000 n=2048", 200)
    s = analyse(lib, 3000 * np.sin(2 * np.pi * 1000 * np.arange(1024) / 8000))
    lib.i2sp_format(buf, 200, b"a0", ctypes.byref(s))
    line = buf.value.decode()
    assert line.endswith(f" a0:nz={s.nz} ch={s.changes} m={s.mean} r={s.rms} pk=1000/{s.peak_pm}")
    said = []
    ops = Ops(SAY(lambda ctx, ln: said.append(ln.decode())))
    lib.i2sp_pass(ctypes.byref(ops))
    assert said == ["i2sprobe: the radio only"]
    # the tool reads it back: the tone's capture first
    rep = tool.report(["i2sprobe start e0=c9", "census e0=c9 PA15=F PC10=F", line + " a1:nz=0 ch=0 m=0 r=0 pk=0/0",
                       "c SPI3 m e0=c9 mx=0 P16 8000 n=2048 a0:nz=9 ch=9 m=0 r=40 pk=2500/90",
                       "adc PC3 8000 n=2048 a:nz=2048 ch=900 m=2048 r=3 pk=125/40"])
    assert any("with any nonzero" in r and r.startswith("3 captures, 3 with") for r in rep), rep
    assert any(r.startswith("captures with the 1000 Hz tone") and r.endswith(": 1") for r in rep), rep
    assert any("mx=1 P16 8000: a0" in r for r in rep) and rep[-1].startswith("PC3 ADC: rms 3"), rep
