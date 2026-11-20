"""The K6's FM receiver that codes what it hears as sotto 1200 (c/k6/mod/fmsotto.c,
tools/k6_fmsotto.py, docs/k6-fm-sotto.md): its front end and encoder built for the PC
(-DFMS_HOST) on counts from the GP0 tap's model (sim/k6if), its SPI reads against sotto's own
tables, its cell deck, and its fit in the module block. SPDX-License-Identifier: MIT"""
import os
import re
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import k6_fmsotto as F  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "fmsotto.c")
FX = os.path.join(ROOT, "sotto", "c", "fixed")
FDMA = 5.621e6
CAV = int(round(256 * 96e6 / FDMA * 16))   # 96 MHz cycles a 256-sample half, Q4


def _host(tmp_path, spi=False):
    exe = str(tmp_path / ("fms_spi" if spi else "fms_host"))
    cmd = ["cc", "-O2", "-std=c99", "-w", "-DFMS_HOST", "-DSOTTO_V36", "-DSOTTO_PITCH_THIRD",
           "-I" + os.path.join(ROOT, "c"), "-I" + FX, SRC, os.path.join(FX, "fx_tables.c"), "-o", exe]
    if spi:
        cmd.insert(1, "-DFMS_SPI")
    subprocess.run(cmd, check=True)
    return exe


def _counts(audio, fs, dev=3000.0, offset=0.0, seed=1):
    """audio (+-1 at fs) as FM on a carrier, through the GP0 tap's model: counts at FDMA / 64"""
    from scipy import signal as sps
    from sim.k6if import frontend
    fz = 48000
    y = sps.resample_poly(audio, fz // fs, 1)
    f = np.clip(y, -1, 1) * dev + offset
    z = np.exp(2j * np.pi * np.cumsum(f) / fz)
    z = np.concatenate([np.ones(fz // 4) * z[0], z, np.ones(fz // 4) * z[-1]])
    c = frontend.receive(z, fz, cn0_db=None, bw=8000.0, fdma=FDMA, seed=seed)
    return np.clip(c, -64, 63).astype(np.int8)


def _run(exe, counts, tmp_path, env=None):
    cf, bf, af = tmp_path / "c.i8", tmp_path / "o.bit", tmp_path / "o.raw"
    counts.tofile(cf)
    r = subprocess.run([exe, str(cf), str(bf), str(af), str(CAV)], check=True, capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})))
    return bf.read_bytes(), np.fromfile(af, np.int16), r.stderr


def _voice(seconds=3.0):
    import soundfile as sf
    x, fs = sf.read(os.path.join(ROOT, "sotto", "docs", "samples", "v38", "librispeech_3536_f__original16.flac"))
    from scipy import signal as sps
    x = sps.resample_poly(x, 1, fs // 8000)[:int(seconds * 8000)]
    return x / np.max(np.abs(x)), 8000


def test_a_tone_comes_out_at_8_khz(tmp_path):
    """a 700 Hz FM tone: 8,000 samples a second out, the tone on top, the tap's noise 25 dB under"""
    fs, n = 8000, 3 * 8000
    t = np.arange(n) / fs
    _, pcm, err = _run(_host(tmp_path), _counts(0.5 * np.sin(2 * np.pi * 700 * t), fs, offset=400.0), tmp_path)
    assert "lost=0" in err
    assert abs(len(pcm) - (n + fs // 2)) < 200            # the audio and the two quarter-second carriers
    p = pcm[fs:fs + 2 * fs].astype(float)
    P = np.abs(np.fft.rfft(p * np.hanning(len(p)))) ** 2
    f = np.fft.rfftfreq(len(p), 1 / fs)
    k = int(np.argmax(P))
    assert abs(f[k] - 700) < 2
    band = (f > 100) & (f < 3800)
    sig = P[k - 6:k + 7].sum()
    assert 10 * np.log10(sig / (P[band].sum() - sig)) > 25


def test_the_spi_reads_give_sottos_own_superframes(tmp_path):
    """the module's reads (codebook at 0x100000, the cell deck in a library slot, the DCT from 21
    values) code exactly as the tables in sotto do"""
    x, fs = _voice(2.0)
    c = _counts(x, fs)
    deck = tmp_path / "cells.bin"
    deck.write_bytes(F.deck())
    a, _, _ = _run(_host(tmp_path), c, tmp_path)
    b, _, _ = _run(_host(tmp_path, spi=True), c, tmp_path, env={"FMS_DECK": str(deck)})
    assert len(a) == len(b) > 6 * 40
    assert a == b


def test_a_deck_that_doesnt_add_up_is_refused(tmp_path):
    d = bytearray(F.deck())
    d[100] ^= 1
    bad = tmp_path / "bad.bin"
    bad.write_bytes(bytes(d))
    exe = _host(tmp_path, spi=True)
    r = subprocess.run([exe, os.devnull, str(tmp_path / "o.bit")], capture_output=True, text=True,
                       env=dict(os.environ, FMS_DECK=str(bad)))
    assert r.returncode == 2 and "doesn't check" in r.stderr


def test_the_deck_holds_every_vector_once():
    d = F.deck()
    defs, arrays = F.parse_cells()
    assert d[:4] == b"K6SC" and d[4] == 1
    assert list(d[5:10]) == [defs["IVF_CD"], defs["IVF_C0"], defs["IVF_C1"], defs["IVF_C2"], defs["IVF_MAXN"]]
    assert int.from_bytes(d[12:16], "little") == sum(d[16:]) & 0xFFFFFFFF
    nc = defs["IVF_C0"] + defs["IVF_C1"] + defs["IVF_C2"]
    off = 16 + nc * defs["IVF_CD"]
    starts = np.frombuffer(d[off:off + 2 * (nc + 3)], "<u2")
    vec = np.frombuffer(d[off + 2 * (nc + 3):], "<u2")
    assert len(vec) == 512 + 512 + 256
    assert sorted(vec[:512]) == list(range(512)) and sorted(vec[1024:]) == list(range(256))
    assert starts[0] == 0 and starts[-1] == len(vec)
    assert int(np.max(np.diff(starts[:defs["IVF_C0"] + 1]))) <= defs["IVF_MAXN"]


def test_the_dct_from_21_values_is_sottos_table():
    m = re.search(r"DCTQ\[21\] = \{([^}]*)\}", open(SRC).read())
    q = [int(v) for v in m.group(1).split(",")]
    t = re.search(r"fx_dct\[400\] = \{([^}]*)\}", open(os.path.join(FX, "fx_tables.c")).read())
    tab = [int(v) for v in t.group(1).replace("\n", " ").split(",") if v.strip()]
    for j in range(1, 20):
        for i in range(20):
            mm, sg = ((2 * i + 1) * j) % 80, 1
            if mm > 40:
                mm = 80 - mm
            if mm > 20:
                mm, sg = 40 - mm, -1
            assert sg * q[mm] == tab[j * 20 + i], (j, i)


def test_it_fits_the_module_block(tmp_path):
    """code under the 11,776-byte check in build.sh, and code + scratch inside level 13's 15,408"""
    from modem import k6mod
    code, _ = k6mod.build(SRC)
    assert len(code) + 24 <= 11776
    exe = str(tmp_path / "sizes")
    prog = tmp_path / "sizes.c"
    prog.write_text('#include <stdio.h>\nint main(void);\n#define main fms_main\n#include "%s"\n#undef main\n'
                    'int main(void) { printf("%%u %%u\\n", (unsigned)sizeof(fe_t), slim_enc_bytes()); return 0; }\n'
                    % SRC)
    subprocess.run(["cc", "-w", "-DFMS_HOST", "-DSOTTO_V36", "-DSOTTO_PITCH_THIRD", "-I" + os.path.join(ROOT, "c"),
                    "-I" + FX, "-I" + os.path.dirname(SRC), str(prog), os.path.join(FX, "fx_tables.c"), "-o", exe],
                   check=True)
    # both structs hold only 16- and 32-bit fields: the same size on the PC as on the M0
    fe, enc = map(int, subprocess.run([exe], check=True, capture_output=True, text=True).stdout.split())
    need = 16 + len(code) + 8 + 4 + 512 + (fe + 8) + enc   # the K6's fe_t has two pointers more
    assert need <= k6mod.ARENA_BYTES_13 - 256, need


def test_a_log_back_into_superframes(tmp_path):
    log = ("K6 MOD run serial=1 code=10210 B\n"
           "fms start ks=5621 cfgr1=00000002 v25=080001d1 spi=1\n"
           "s 0 " + "01" * 24 + "\n"
           "fms 1 sps=8000 pk=13021 skip=0 miss=0 isr=31 rd=15\n"
           "s 8 " + "02" * 18 + "\n")
    got = F.frames_from_log(log)
    assert sorted(got) == [0, 1, 2, 3, 8, 9, 10]
    bits, gaps = F.bitstream(got)
    assert len(bits) == 11 * 6 and gaps == 4
    assert bits[4 * 6:8 * 6] == bytes(24) and bits[-6:] == b"\x02" * 6
