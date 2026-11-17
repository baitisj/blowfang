"""M17 from the UV-380's HR_C6000 physical layer mode (modem/m17.py, firmware/uv380-openrtx/mod/
c6m17tx*.c, docs/uv380-m17-scope.md milestone 1): the frames against OpenRTX's reference packet
frame, every plan decoding back from its bytes and through the chip's shape, the module headers
matching the builder, and the transmit rules the modules keep. What the real chip sends is the
bench's to say (the Pluto, OpenRTX's m17_replay_tool).
SPDX-License-Identifier: MIT"""
import os
import re
import shutil
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from modem import m17  # noqa: E402

MOD = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod")
C2 = os.path.join(ROOT, "tests", "data", "uv380", "m17-voice.c2")
TOOLS = all(shutil.which(t) for t in ("clang", "ld.lld", "llvm-objcopy", "llvm-nm"))

# OpenRTX tests/unit/M17_packet.cpp: an SMS packet frame carrying "OpenRTX" as captured over the
# air (their byte 32 corrected), counter 11, EOF
OPENRTX_SMS_FRAME = bytes([
    0x75, 0xFF, 0xF6, 0xD4, 0xC2, 0x59, 0x82, 0x96, 0x84, 0x63, 0x9A, 0x26,
    0xF6, 0xD8, 0xB8, 0xF0, 0x9D, 0x0D, 0x4C, 0xD0, 0x00, 0x03, 0x9F, 0x05,
    0xEE, 0x6E, 0x72, 0x2F, 0x23, 0xDA, 0x96, 0xFE, 0xCB, 0x70, 0x9B, 0x09,
    0x52, 0x03, 0xD7, 0xB3, 0xA2, 0xB2, 0x52, 0xB9, 0xAD, 0xA9, 0x39, 0xA3])


def voice():
    return open(C2, "rb").read()


def header_bytes(path):
    src = open(path).read()
    body = src[src.index("M17_TX[M17_TX_N] = {"):]
    return bytes(int(x, 16) for x in re.findall(r"0x([0-9a-f]{2})", body))


def test_packet_frame_is_openrtxs():
    p = m17.sms_payload("OpenRTX")
    assert p[9:11] == bytes([0xB8, 0x34])
    assert m17.frame_packet(p, True, 11) == OPENRTX_SMS_FRAME
    f = m17.decode_frame("packet", OPENRTX_SMS_FRAME[2:])
    assert f["eof"] and f["counter"] == 11 and f["data"][:11] == p


def test_callsign_and_crc():
    assert m17.callsign_text(m17.callsign("AG7EW")) == "AG7EW"
    assert m17.callsign("") == b"\xff" * 6
    assert m17.crc16(b"") == 0xFFFF
    assert m17.crc16(b"A") == 0x206E and m17.crc16(b"123456789") == 0x772B   # the spec's examples


def test_golay_corrects_three():
    cw = m17.golay_encode(0xABC)
    for e in (0, 1 << 3, 1 << 3 | 1 << 17, 1 << 0 | 1 << 11 | 1 << 23):
        assert m17.golay_decode(cw ^ e) == 0xABC


def test_lich_and_stream_round_trip():
    l30 = m17.lsf("AG7EW")
    assert m17.decode_frame("lsf", m17.frame_lsf(l30)[2:])["ok"]
    for fn in range(7):
        f = m17.decode_frame("stream", m17.frame_stream(l30, fn, bytes(range(fn, fn + 16)), fn == 6)[2:])
        assert f["fn"] == fn and f["last"] == (fn == 6) and f["lich_ok"] and f["lich_idx"] == fn % 6
        assert f["payload"] == bytes(range(fn, fn + 16))


@pytest.mark.parametrize("plan", ["bert", "sms", "voice"])
def test_header_is_the_builders(plan):
    data = m17.build(plan, voice() if plan == "voice" else None)
    assert header_bytes(os.path.join(MOD, f"m17tx_{plan}.h")) == data
    assert len(data) % m17.FRAME_BYTES == 0 and len(data) <= m17.MAX_BYTES


def test_plans_decode_from_their_bytes():
    s = m17.summarize(m17.decode_bytes(m17.build("sms")))
    assert s["lsf"] == 20 and s["lsf_bad"] == 0 and s["packets"] == 20 and s["src"] == "AG7EW"
    assert s["texts"][0] == "AG7EW M17 TEST 01"
    s = m17.summarize(m17.decode_bytes(m17.build("bert")))
    assert s["bert_frames"] == 98 and s["bert_errors"] == 0
    c2 = voice()
    s = m17.summarize(m17.decode_bytes(m17.build("voice", c2)))
    assert s["lsf"] == 1 and s["stream"] == len(c2) // 16 and s["lich_bad"] == 0
    assert s["payload"][:len(c2)] == c2


@pytest.mark.parametrize("outer_hz", [2400.0, 1944.0])
def test_the_chips_shape_decodes(outer_hz):
    """RRC alpha 0.2 (the chip) read through M17's alpha 0.5 matched filter, at the target
    deviation and at the chip's own DMR deviation (TX_DEV 100), with some noise"""
    data = m17.build("sms")
    disc = m17.modulate(data, alpha=0.2, outer_hz=outer_hz)
    disc = disc + np.random.default_rng(3).normal(0, 300, len(disc))
    s = m17.summarize(m17.demod(disc))
    assert s["packets"] == 20 and s["packets_bad"] == 0
    fr = m17.demod(m17.modulate(m17.build("bert"), alpha=0.2, outer_hz=outer_hz))
    s = m17.summarize(fr)
    assert s["bert_frames"] >= 97 and s["bert_errors"] == 0
    outer = np.mean([f["outer_hz"] for f in fr])
    assert abs(outer / outer_hz - 1) < 0.05


def test_slots_carry_the_data_then_eot():
    """the module's m17_slot(): 36 bytes from pos, EOT (0x55 0x5D by stream position) past the end"""
    data = m17.build("sms")
    src = open(os.path.join(MOD, "c6m17tx.c")).read()
    assert "s[i] = j < M17_TX_N ? M17_TX[j] : (j & 1u) ? 0x5Du : 0x55u;" in src

    def slot(pos):
        return bytes(data[j] if j < len(data) else (0x5D if j & 1 else 0x55) for j in range(pos, pos + 36))
    out = b"".join(slot(p) for p in range(0, len(data) + 72, 36))
    assert out[:len(data)] == data and out[len(data):len(data) + 48] == m17.eot()


def test_m17_keeps_the_transmit_rules():
    """as c6phytx: dry unless a wrapper says otherwise, keys only on a level 4 image through
    tx_tone once, says nothing between tx_tone and the key-down, leaves the registers as found,
    and the key-up fits tx_tone's 5 s"""
    src = open(os.path.join(MOD, "c6m17tx.c")).read()
    assert "#ifndef TX_DRY\n#define TX_DRY 1\n" in src
    assert src.count("api->tx_tone(") == 1
    keyed = src.index("if (!TX_DRY) {\n        if (api->level < 4u) {")
    assert keyed < src.index("return -1;", keyed) < src.index("api->tx_tone(")
    body = src[src.index("q.quiet = 1;"):src.index("flush_notes(api, &q, 2000u);")]
    assert "api->say(" not in body
    assert src.index("c6_save(") < src.index("api->c6000_write(0x60, 0x00)")
    assert src.index("c6_restore(") < src.index("flush_notes(api, &q, 2000u);")
    assert "#error" in src and "TX_MS > 5000u" in src
    assert "#define TX_DEV 123u" in src
    from modem import uv380mod
    for plan in ("bert", "sms", "voice"):
        w = os.path.join(MOD, f"c6m17tx_{plan}.c")
        assert uv380mod.source_level(w) == 4
        t = open(w).read()
        assert "#define TX_DRY 0" in t and f"#define M17_PLAN_{plan.upper()}" in t


@pytest.mark.skipif(not TOOLS, reason="needs clang and lld")
def test_modules_build_for_the_openrtx_table():
    from modem import uv380mod
    for name in ("c6m17tx.c", "c6m17tx_bert.c", "c6m17tx_sms.c", "c6m17tx_voice.c"):
        path = os.path.join(MOD, name)
        code, entry = uv380mod.build(path)
        assert entry == 0 and len(code) < 12288, (name, len(code))
        assert uv380mod.source_table(path) == "openrtx"


def test_decode_a_capture(tmp_path):
    """a synthetic Pluto capture (cf32 at 240 kS/s, carrier 50 kHz below the capture's centre, as
    pluto_bench.py rx --hz 441900000 sees 441.850) through decode_capture"""
    pytest.importorskip("scipy")
    from scipy import signal
    data = m17.build("sms")[:48 * 12]
    disc48 = m17.modulate(data, alpha=0.2, outer_hz=2400.0)
    fs = 240_000
    pad = np.zeros(4800)
    d = signal.resample_poly(np.concatenate([pad, disc48, pad]), fs // 8000, 48000 // 8000)
    ph = 2 * np.pi * np.cumsum(d - 50_000.0) / fs
    iq = np.exp(1j * ph).astype(np.complex64)
    iq += (np.random.default_rng(1).normal(0, 0.05, (len(iq), 2)) @ [1, 1j]).astype(np.complex64)
    cap = tmp_path / "m.cf32"
    iq.tofile(cap)
    open(str(cap) + ".json", "w").write('{"fs": 240000}')
    out = "\n".join(m17.decode_capture(str(cap), str(tmp_path / "m.raw")))
    assert "packets 3 CRC good, 0 bad" in out
    off = float(re.search(r"carrier ([-+]\d+) Hz", out).group(1))
    assert abs(off + 50_000) < 1000
    assert os.path.getsize(tmp_path / "m.raw") > 0


def probe_capture(data, order, perm, swap=False):
    b = bytearray(data)
    if swap:
        b[36:72], b[72:108] = data[72:108], data[36:72]
    sym = m17.hypothesis_symbols(bytes(b), order, perm)
    up = np.zeros(len(sym) * 10)
    up[::10] = sym
    disc = np.convolve(up, m17.rrc(0.2, 10) * 10, "same") * 800
    disc = np.concatenate([np.zeros(4800), disc, np.zeros(4800)])
    disc += np.random.default_rng(2).normal(0, 200, len(disc))
    return m17.probe(m17.symbol_track(disc), data)


def test_probe_names_the_chips_symbol_rule():
    """probe, against the plan's bytes: a chip sending the dibits LSB first with the inner levels
    swapped is named so; one sending a pair of halves out of turn shows it slot by slot"""
    pytest.importorskip("scipy")
    data = m17.build("bert")[:48 * 30]
    out = probe_capture(data, (3, 2, 1, 0), (-1, 3, 1, -3))
    assert out[1].split()[:5] == ["lsb", "-1", "+3", "+1", "-3:"]
    assert "SER 0.00e+00" in out[5] or " 0 of " in out[5]
    out = probe_capture(data, (0, 1, 2, 3), (1, 3, -1, -3), swap=True)
    assert out[1].split()[:5] == ["msb", "+1", "+3", "-1", "-3:"]
    assert "matches sent slot: 0 2 1 3 4" in out[-1]


def test_decode_with_the_ident_kilohertz_away(tmp_path):
    """as on air (m17-bert-1117d): the FM ident sits ~6.5 kHz below the raw mode's burst in one
    key-up, so the capture's one carrier estimate lands between them. The burst still decodes,
    and its own offset is reported"""
    pytest.importorskip("scipy")
    from scipy import signal
    data = m17.build("sms")[:48 * 12]
    fs = 240_000
    disc48 = m17.modulate(data, alpha=0.2, outer_hz=3075.0)
    burst = signal.resample_poly(disc48, fs // 8000, 48000 // 8000) - 50_600.0
    t = np.arange(int(1.5 * fs)) / fs
    ident = 2500.0 * np.sin(2 * np.pi * 800 * t) - 57_100.0
    pad = np.zeros(fs // 5)
    d = np.concatenate([pad, ident, burst, pad])
    iq = np.exp(2j * np.pi * np.cumsum(d) / fs).astype(np.complex64)
    iq[:len(pad)] = 0
    iq[-len(pad):] = 0
    iq += (np.random.default_rng(1).normal(0, 0.05, (len(iq), 2)) @ [1, 1j]).astype(np.complex64)
    cap = tmp_path / "m.cf32"
    iq.tofile(cap)
    out = "\n".join(m17.decode_capture(str(cap)))
    assert "packets 3 CRC good, 0 bad" in out, out
    burst_hz = float(re.search(r"M17 burst ([-+]\d+) Hz", out).group(1))
    assert abs(burst_hz + 50_600) < 300, out
