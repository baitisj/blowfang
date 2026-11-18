"""YSF (C4FM with Yaesu framing) from the UV-380's HR_C6000 physical layer mode (modem/ysf.py,
firmware/uv380-openrtx/mod/c6ysftx*.c, docs/uv380-ysf.md): the field codes against the public
YSF layout's fixed points (sync, whitening, Golay table entries, interleavers), every plan
decoding back from its bytes and through the chip's shape, the module headers matching the
builder, and the transmit rules the modules keep. Whether a Yaesu radio takes the frames is the
bench's to say (the FTM-300D, with Jeff).
SPDX-License-Identifier: MIT"""
import os
import re
import shutil
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from modem import ysf  # noqa: E402

MOD = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod")
TOOLS = all(shutil.which(t) for t in ("clang", "ld.lld", "llvm-objcopy", "llvm-nm"))


def header_bytes(path):
    src = open(path).read()
    body = src[src.index("YSF_TX[YSF_TX_N] = {"):]
    return bytes(int(x, 16) for x in re.findall(r"0x([0-9a-f]{2})", body))


def test_fixed_points():
    """the public layout's constants: sync D4 71 C9 63 4D, the whitening bytes (PN9, seed 0x1C9),
    Golay(24,12) codewords for data 1 and 2, the interleavers' first rows"""
    assert ysf.SYNC_BYTES.hex() == "d471c9634d"
    assert ysf.WHITENING_BYTES.hex() == "93d751219c2f6cd0ef0ff83df1732094ed1e7cd8"
    assert ysf.golay24_encode(1) == 0x0018EB and ysf.golay24_encode(2) == 0x00293E
    assert list(ysf.IL_FICH[:6]) == [0, 40, 80, 120, 160, 2]
    assert list(ysf.IL_CSD[:10]) == [0, 40, 80, 120, 160, 200, 240, 280, 320, 2]
    assert list(ysf.IL_VCH[:3]) == [0, 4, 8] and ysf.IL_VCH[26] == 1
    assert ysf.crc16(b"123456789") == 0x31C3 ^ 0xFFFF  # CRC-16/XMODEM (CCITT, init 0), inverted


def test_golay_corrects_three():
    cw = ysf.golay24_encode(0xABC)
    for e in (0, 1 << 3, 1 << 3 | 1 << 17, 1 << 1 | 1 << 11 | 1 << 23):
        assert ysf.golay24_decode(cw ^ e) == (0xABC, True)


def test_fich_round_trip_and_layout():
    f = ysf.FICH(fi=ysf.FI_COMMS, cs=2, fn=5, ft=6, dt=ysf.DT_VD_MODE2, dgid=0)
    assert f.to_bytes() == bytes([0x60, 0x2E, 0x02, 0x00])
    llr = 1.0 - 2.0 * f.encode()
    assert ysf.FICH.decode(llr) == f
    llr[[3, 50, 120, 199]] *= -1                        # a few wrong bits: the code takes them
    assert ysf.FICH.decode(llr) == f


def test_dn_fields_round_trip():
    d = ysf.dch2_encode(b"AG7EW     ")
    assert ysf.dch2_decode(1.0 - 2.0 * d) == b"AG7EW     "
    a = ysf.ambe_silence()
    assert ysf.ambe_b0(a) == 124
    v = ysf.vch_encode(a)
    llr = 1.0 - 2.0 * v
    llr[ysf.IL_VCH[[0, 4, 80]]] *= -1                   # one bit of three triplets
    back, bad = ysf.vch_decode(llr)
    assert np.array_equal(back, a) and bad == 3


@pytest.mark.parametrize("plan", ysf.PLANS)
def test_header_is_the_builders(plan):
    data = ysf.build(plan)
    assert header_bytes(os.path.join(MOD, f"ysftx_{plan}.h")) == data
    assert len(data) <= ysf.MAX_BYTES and (len(data) - 24) % ysf.FRAME_BYTES == 0


def test_plans_decode_from_their_bytes():
    s = ysf.summarize(ysf.decode_bytes(ysf.build("dn")))
    assert s["frames"] == s["fich_ok"] == 39
    assert s["fi"] == {"header": 1, "comms": 37, "terminator": 1}
    assert s["src"] == {"AG7EW"} and s["dest"] == {"ALL"}
    assert s["dch_ok"] == s["dch"] == 37 and s["silence"] == s["ambe"] == 185
    assert s["fn"][:9] == [0, 1, 2, 3, 4, 5, 6, 0, 1]
    s = ysf.summarize(ysf.decode_bytes(ysf.build("data")))
    assert s["dch_ok"] == s["dch"] == 74 and len(s["data"]) == 59
    assert s["data"][0] == b"00 AG7EW YSF DATA   " and s["data"][-1].startswith(b"58 ")


@pytest.mark.parametrize("outer_hz", [2700.0, 1944.0])
def test_the_chips_shape_decodes(outer_hz):
    """RRC alpha 0.2 through the same matched filter, at YSF's deviation and the chip's own DMR
    deviation (TX_DEV 100), with noise and a DC offset"""
    for plan in ysf.PLANS:
        data = bytes([ysf.PREAMBLE]) * 60 + ysf.build(plan) + bytes([ysf.PREAMBLE]) * 60
        disc = ysf.modulate(data, outer_hz=outer_hz) + 150.0
        disc = disc + np.random.default_rng(3).normal(0, 300, len(disc))
        fr = ysf.demod(disc)
        s = ysf.summarize(fr)
        assert s["frames"] == s["fich_ok"] == 39 and s["dch_ok"] == s["dch"]
        outer = np.median([f["outer_hz"] for f in fr])
        assert abs(outer / outer_hz - 1) < 0.05


def test_slots_carry_the_data_then_idle():
    src = open(os.path.join(MOD, "c6ysftx.c")).read()
    assert "s[i] = j < YSF_TX_N ? YSF_TX[j] : (uint8_t)YSF_IDLE;" in src
    assert "#define YSF_IDLE 0x77u" in src


def test_ysf_keeps_the_transmit_rules():
    """as c6m17tx: dry unless a wrapper says otherwise, keys only on a level 4 image through
    tx_tone once, says nothing between tx_tone and the key-down, leaves the registers as found,
    and the key-up fits tx_tone's 5 s"""
    src = open(os.path.join(MOD, "c6ysftx.c")).read()
    assert "#ifndef TX_DRY\n#define TX_DRY 1\n" in src
    assert src.count("api->tx_tone(") == 1
    keyed = src.index("if (!TX_DRY) {\n        if (api->level < 4u) {")
    assert keyed < src.index("return -1;", keyed) < src.index("api->tx_tone(")
    body = src[src.index("q.quiet = 1;"):src.index("flush_notes(api, &q, 2000u);")]
    assert "api->say(" not in body
    assert src.index("c6_save(") < src.index("api->c6000_write(0x60, 0x00)")
    assert src.index("c6_restore(") < src.index("flush_notes(api, &q, 2000u);")
    assert "#error" in src and "TX_MS > 5000u" in src
    assert "#define TX_DEV 140u" in src
    from modem import uv380mod
    for plan in ysf.PLANS:
        w = os.path.join(MOD, f"c6ysftx_{plan}.c")
        assert uv380mod.source_level(w) == 4
        t = open(w).read()
        assert "#define TX_DRY 0" in t and f"#define YSF_PLAN_{plan.upper()}" in t


@pytest.mark.skipif(not TOOLS, reason="needs clang and lld")
def test_modules_build_for_the_openrtx_table():
    from modem import uv380mod
    for name in ("c6ysftx.c", "c6ysftx_dn.c", "c6ysftx_data.c"):
        path = os.path.join(MOD, name)
        code, entry = uv380mod.build(path)
        assert entry == 0 and len(code) < 12288, (name, len(code))
        assert uv380mod.source_table(path) == "openrtx"


def test_decode_a_capture(tmp_path):
    """a synthetic Pluto capture (cf32 at 240 kS/s, carrier 50 kHz below the capture's centre, as
    pluto_bench.py rx --hz 441900000 sees 441.850) through decode_capture"""
    pytest.importorskip("scipy")
    from scipy import signal
    data = bytes([ysf.PREAMBLE]) * 60 + ysf.build("dn")[:24 + 120 * 12] + bytes([ysf.PREAMBLE]) * 60
    disc48 = ysf.modulate(data)
    fs = 240_000
    pad = np.zeros(4800)
    d = signal.resample_poly(np.concatenate([pad, disc48, pad]), fs // 8000, 48000 // 8000)
    ph = 2 * np.pi * np.cumsum(d - 50_000.0) / fs
    iq = np.exp(1j * ph).astype(np.complex64)
    iq += (np.random.default_rng(1).normal(0, 0.05, (len(iq), 2)) @ [1, 1j]).astype(np.complex64)
    cap = tmp_path / "y.cf32"
    iq.tofile(cap)
    open(str(cap) + ".json", "w").write('{"fs": 240000}')
    out = "\n".join(ysf.decode_capture(str(cap)))
    assert "frames 12, FICH good 12" in out and "from AG7EW to ALL" in out
    off = float(re.search(r"carrier ([-+]\d+) Hz", out).group(1))
    assert abs(off + 50_000) < 1000


def test_decode_a_runner_wav(tmp_path):
    """pluto_bench.py rx OUT.wav as the runner returns it: 48 kHz PCM_16, 5 kHz = full scale,
    an FM ident before the burst some kHz below it"""
    pytest.importorskip("scipy")
    from scipy.io import wavfile
    data = bytes([ysf.PREAMBLE]) * 60 + ysf.build("data") + bytes([ysf.PREAMBLE]) * 60
    t = np.arange(48000) / 48000
    ident = -2500.0 + 2000 * np.sin(2 * np.pi * 800 * t)
    disc = np.concatenate([np.zeros(4800), ident, ysf.modulate(data) + 120.0, np.zeros(4800)])
    disc += np.random.default_rng(4).normal(0, 200, len(disc))
    path = str(tmp_path / "y.wav")
    wavfile.write(path, 48000, np.clip(disc / ysf.WAV_FULL_DEV * 32767, -32768, 32767).astype(np.int16))
    out = "\n".join(ysf.decode_capture(path))
    assert "frames 39, FICH good 39" in out and "data channel blocks good 74/74" in out
