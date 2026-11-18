"""The UV-380's on-radio M17 and YSF transmit framers (c/common/m17_tx.c, ysf_tx.c,
docs/uv380-multimode.md) against the Python that built the on-air modules' data (modem/m17.py,
modem/ysf.py): every byte of every transmission must match, read 36 bytes at a time as the
HR_C6000 takes them. Then the sotto payloads decode back: M17 stream frames through modem.m17's
own decoder path, YSF sotto VW through decode_sotto_vw_payload.
Needs make -C c/common. SPDX-License-Identifier: MIT"""
import os
import subprocess

import numpy as np
import pytest

from modem import m17, ysf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "c", "common", "framers_host")
pytestmark = pytest.mark.skipif(not os.path.exists(EXE), reason="needs make -C c/common")


def run(*args, data=b""):
    r = subprocess.run([EXE, *args], input=data, capture_output=True, check=False)
    assert r.returncode == 0, (args, r.returncode, r.stderr)
    return r.stdout


def sotto(n, seed=1):
    rng = np.random.default_rng(seed)
    return bytes(rng.integers(0, 256, 6 * n, dtype=np.uint8))


@pytest.mark.parametrize("n", [1, 2, 3, 7, 75])
@pytest.mark.parametrize("repeat", [0, 1])
def test_m17_sotto_matches_python(n, repeat):
    s = sotto(n, seed=n)
    assert run("m17", "sotto", "AG7EW", "", str(repeat), data=s) == m17.tx_sotto(s, repeat=bool(repeat))


def test_m17_sotto_dst_and_lsf():
    s = sotto(4)
    out = run("m17", "sotto", "AG7EW", "N0CALL", "0", data=s)
    assert out == m17.tx_sotto(s, dst="N0CALL")
    l30 = m17.lsf("AG7EW", "N0CALL", m17.TYPE_STREAM_DATA, m17.SOTTO_META)
    assert out[48:96] == m17.frame_lsf(l30)


def test_m17_voice_bert_sms_match_python():
    c2 = sotto(30)[:16 * 11]
    assert run("m17", "voice", "AG7EW", data=c2) == m17.tx_voice(c2)
    assert run("m17", "bert", "98") == m17.tx_bert(98)
    for text in ("AG7EW M17 TEST 01", "x" * 60):
        assert run("m17", "sms", "AG7EW", text) == m17.tx_sms([text])


def test_m17_sotto_payload_layout():
    a, b = bytes(range(6)), bytes(range(10, 16))
    p = m17.sotto_payload(a, b)
    assert p[:12] == a + b and p[12:14] == m17.crc16(a + b).to_bytes(2, "big")
    assert p[14] == 0x10 and p[15] == 0
    assert m17.sotto_payload(a, b, repeat=True)[14] == 0x11


def test_ysf_sotto_matches_python_and_decodes():
    for n in (1, 5, 6, 37):
        s = sotto(n, seed=100 + n)
        out = run("ysf", "sotto", "ALL", "AG7EW", "0", data=s)
        assert out == ysf.tx_sotto(s)
        frames = out[24:]
        assert len(frames) % 120 == 0
        got = b""
        for k in range(1, len(frames) // 120 - 1):
            bits = ysf.bytes_to_bits(frames[120 * k:120 * k + 120])
            llr = 1.0 - 2.0 * bits
            fich = ysf.FICH.decode(llr[40:240])
            assert fich.sotto and fich.dt == ysf.DT_VOICE_FR and fich.fn == (k - 1) % 7
            data, ok = ysf.decode_sotto_vw_payload(llr[240:])
            assert all(ok)
            got += data
        pad = s[-6:] * (-n % 5)
        assert got == s + pad


def test_ysf_header_carries_callsigns():
    out = run("ysf", "sotto", "ALL", "AG7EW", "5", data=sotto(3))
    llr = 1.0 - 2.0 * ysf.bytes_to_bits(out[24:144])
    fich = ysf.FICH.decode(llr[40:240])
    assert fich.fi == ysf.FI_HEADER and fich.dgid == 5
    hdr = ysf.decode_header_payload(llr[240:])
    assert hdr["src"] == "AG7EW" and hdr["dest"] == "ALL"


def test_ysf_data_fr_frame_matches_python():
    d = sotto(7)[:40]
    for fn in (0, 3, 7):
        pl = ysf.data_fr_payload(d[:20], d[20:])
        want = ysf.bits_to_bytes(ysf.build_frame(ysf.FICH(fi=1, fn=fn, ft=7, dt=ysf.DT_DATA_FR), pl))
        assert run("ysf", "datafr", str(fn), data=d) == want


def test_sotto_vw_matches_sotto_softmodem_rule():
    """sotto's SottoVW (sotto modem/payload.py): coded bit i at (i % 24) * 30 + i // 24, PN9
    seeded 0x1FF; checked here by its first bits against a hand-rolled encoder"""
    from modem import fec
    f = sotto(5, seed=9)
    bits = ysf.bytes_to_bits(f).reshape(5, 48)
    u = np.concatenate([np.concatenate([x, fec.crc8(x)]) for x in bits])
    c = fec.RATE_7_18.encode(u)
    p = ysf.sotto_vw_payload(f)
    i = np.arange(720)
    assert (p[(i % 24) * 30 + i // 24] == (c ^ ysf.SOTTO_PN[(i % 24) * 30 + i // 24])).all()
