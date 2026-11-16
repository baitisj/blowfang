"""Plain 2FSK from the UV-380's HR_C6000 physical layer mode (firmware/uv380-openrtx/mod/c6phytx.c,
c/common/c6phy_fsk.h, modem/c6phy.py, docs/uv380-c6phy.md): the C stream against the Python one
byte for byte, the symbol packing, the chip's 4FSK read back as 2400 and 1200 baud FSK by our
demodulator (fsk24rx_uv on the emulated K6 is in test_k6_fsk24.py), and the transmit rules the
modules keep. What the real chip sends is the bench's
to say (the Pluto and the K6).
SPDX-License-Identifier: MIT"""
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import c6phy, fsk24, pager  # noqa: E402

MOD = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod")
CC = shutil.which("cc") or shutil.which("gcc")
TOOLS = all(shutil.which(t) for t in ("clang", "ld.lld", "llvm-objcopy", "llvm-nm"))

CASES = [(2400, 0, 13), (2400, 1, 13), (2400, 2, 13), (1200, 2, 6), (1200, 0, 3), (2400, 2, 0)]


def c_slots(tmp_path, baud, pol, npk, nslots, seq0=0):
    src = tmp_path / "t.c"
    src.write_text(f"""#include <stdio.h>
#include "c6phy_fsk.h"
int main(void) {{
    cf_gen g;
    uint8_t s[CF_SLOT];
    cf_fill(s, {baud}u, {pol}u);
    for (unsigned i = 0; i < CF_SLOT; i++) printf("%02x", s[i]);
    printf("\\n%u %u\\n", cf_fit(4090u, 2400u), cf_fit(4090u, 1200u));
    cf_init(&g, {baud}u, {pol}u, {npk}u, {seq0}u);
    for (unsigned k = 0; k < {nslots}u; k++) {{
        cf_slot(&g, s);
        for (unsigned i = 0; i < CF_SLOT; i++) printf("%02x", s[i]);
        printf(" %d\\n", cf_done(&g));
    }}
    return 0;
}}
""")
    exe = tmp_path / "t"
    subprocess.run([CC, "-O2", "-Wall", "-Wextra", "-Werror", "-I", os.path.join(ROOT, "c", "common"),
                    str(src), "-o", str(exe)], check=True)
    out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout.split("\n")
    fill = bytes.fromhex(out[0])
    fits = tuple(int(x) for x in out[1].split())
    rows = [ln.split() for ln in out[2:] if ln]
    return fill, fits, [bytes.fromhex(r[0]) for r in rows], [int(r[1]) for r in rows]


@pytest.mark.skipif(not CC, reason="needs a C compiler")
@pytest.mark.parametrize("baud,pol,npk", CASES)
def test_c_header_makes_the_same_slots(tmp_path, baud, pol, npk):
    per = 4 if baud == 2400 else 8
    nslots = -(-len(c6phy.stream(baud, pol, npk)) * per // c6phy.SLOT) + 4
    fill, fits, got, done = c_slots(tmp_path, baud, pol, npk, nslots, seq0=7)
    assert fill == c6phy.fill(baud, pol)
    assert fits == (c6phy.fit(4090, 2400), c6phy.fit(4090, 1200)) == (13, 6)
    assert got == c6phy.slots(baud, pol, npk, nslots, seq0=7)
    assert done[-1] == 1


def test_stream_is_the_pager_framing():
    """polarity 0 is pager.Air's burst (lead 150 ms, 16 preamble bytes, 50 ms more before the
    second packet) around fsk24's test packets, without the tail; polarity 1 is its inverse"""
    for baud in (2400, 1200):
        pk = [fsk24.packet(c6phy.VAR[baud], k) for k in range(5)]
        air = pager.Air(preamble_bytes=16, lead_ms=150.0, tail_bytes=0, tail_ms=0.0).burst(pk, baud)
        assert c6phy.stream(baud, 0, 5) == air
        inv = c6phy.stream(baud, 1, 5)
        pk1 = [fsk24.packet(c6phy.VAR[baud] + 1, k) for k in range(5)]
        air1 = pager.Air(preamble_bytes=16, lead_ms=150.0, tail_bytes=0, tail_ms=0.0).burst(pk1, baud)
        assert inv == bytes(b ^ 0xFF for b in air1)


def test_bits_as_pairs_of_outer_symbols():
    """2400: bit 1 = +3 +3 (nibble 0x5), bit 0 = -3 -3 (0xF), MSB first; 1200: four symbols a
    bit; a slot is 144 symbols = 72 bits at 2400, 36 at 1200; the fill is the 0x55 preamble"""
    assert c6phy.expand(b"\xA5", 2400) == bytes([0x5F, 0x5F, 0xF5, 0xF5])
    assert list(c6phy.symbols(c6phy.expand(b"\xA0", 2400))[:8]) == [3, 3, -3, -3, 3, 3, -3, -3]
    assert c6phy.expand(b"\x80", 1200) == bytes([0x55] + [0xFF] * 7)
    assert list(c6phy.symbols(b"\x1B")) == [1, 3, -1, -3]           # 00 01 10 11
    assert len(c6phy.symbols(bytes(c6phy.SLOT))) == 144
    assert c6phy.fill(2400, 0) == bytes([0xF5]) * 36 and c6phy.fill(2400, 1) == bytes([0x5F]) * 36
    assert c6phy.fill(1200, 0) == bytes([0xFF, 0x55]) * 18


def test_a_key_up_carries_13_packets_at_2400():
    assert c6phy.key_up_packets(2400) == 13 and c6phy.key_up_packets(1200) == 6


@pytest.mark.parametrize("baud", (2400, 1200))
def test_the_chips_4fsk_reads_as_plain_fsk(baud):
    """the slots as the chip modulates them (RRC 4FSK, +-1944 Hz outer) through a flat
    discriminator: every packet CRC right, 0 PN9 bits wrong, the polarity 1 packets inverted;
    still all right with 250 Hz rms of noise"""
    for noise in (0.0, 250.0):
        pk, n = c6phy.sim(baud, 2, noise)
        assert len(pk) == n and all(x[3] and x[5] == 0 for x in pk), (noise, pk)
        assert [x[1] for x in pk] == [c6phy.VAR[baud] + (k & 1) for k in range(n)]
        assert [x[4] for x in pk] == [bool(k & 1) for k in range(n)]
        assert all(1500 < x[6] < 2100 for x in pk), pk


def test_fsk24_knows_the_variants():
    for vi, (baud, comp) in zip(fsk24.C6PHY, ((2400, "c6phy"), (2400, "c6phyi"), (1200, "c6phy"), (1200, "c6phyi"))):
        v = fsk24.VARIANTS[vi]
        assert (v.baud, v.comp, v.level) == (baud, comp, 1.944)
    got = fsk24.sim(n_rounds=1, ch="flat")
    assert all(got[vi][0] == got[vi][1] for vi in fsk24.C6PHY), got


@pytest.mark.skipif(not TOOLS, reason="needs clang and lld")
def test_modules_build_for_the_openrtx_table():
    from modem import uv380mod
    for name in ("c6phytx.c", "c6phytx_live.c", "c6phytx_live1200.c"):
        path = os.path.join(MOD, name)
        code, entry = uv380mod.build(path)
        assert entry == 0 and len(code) < 5120, (name, len(code))
        assert uv380mod.source_table(path) == "openrtx"


def test_phytx_keeps_the_transmit_rules():
    """dry unless a wrapper says otherwise; keys only on a level 4 image (ident first, transmit
    guard) through tx_tone, once; says nothing between tx_tone and the key-down (notes only);
    leaves the chip's registers as it found them"""
    src = open(os.path.join(MOD, "c6phytx.c")).read()
    assert "#ifndef TX_DRY\n#define TX_DRY 1\n" in src
    assert src.count("api->tx_tone(") == 1
    keyed = src.index("if (!TX_DRY) {\n        if (api->level < 4u) {")
    assert keyed < src.index("return -1;", keyed) < src.index("api->tx_tone(")
    body = src[src.index("q.quiet = 1;"):src.index("flush_notes(api, &q, 2000u);")]
    assert "api->say(" not in body
    assert src.index("c6_save(") < src.index("api->c6000_write(0x60, 0x00)")
    assert src.index("c6_restore(") < src.index("flush_notes(api, &q, 2000u);")
    assert "TX_FKHZ 440850u" in src and "#define TX_MS 4600u" in src
    from modem import uv380mod
    for f in ("c6phytx_live.c", "c6phytx_live1200.c"):
        assert uv380mod.source_level(os.path.join(MOD, f)) == 4
        assert "#define TX_DRY 0" in open(os.path.join(MOD, f)).read()


def test_decode_a_pluto_capture(tmp_path):
    """a capture as tools/pluto_bench.py rx writes it (complex64 at 240 kHz and its .json): the
    packets, which way a +3 symbol swings, the carrier offset, no gaps"""
    import json
    raw = b"".join(c6phy.slots(2400, 2, 5, 60))
    f = np.concatenate([np.full(24000, 0.0), c6phy.freq_track(raw, 240_000), np.full(24000, 0.0)]) - 49_700.0
    iq = np.exp(2j * np.pi * np.cumsum(f) / 240_000)
    rng = np.random.default_rng(3)
    iq = np.concatenate([np.zeros(24000), iq, np.zeros(24000)]) + 0.003 * (rng.normal(size=len(iq) + 48000)
                                                                         + 1j * rng.normal(size=len(iq) + 48000))
    cap = tmp_path / "cap.cf32"
    iq.astype(np.complex64).tofile(cap)
    (tmp_path / "cap.cf32.json").write_text(json.dumps({"fs": 240_000, "hz": 440_900_000}))
    out = c6phy.decode(str(cap), 2400)
    assert abs(int(out[0].split()[1]) - 300) < 20 and "from 440850000 (captured at 440900000" in out[0], out[0]
    assert out[1] == "2400 baud: 5 syncs, 5 CRC right", out
    assert "CRC right by variant: v20 3, v21 2" in out
    assert any(ln.startswith("a +3 symbol (dibit 01) goes out above the carrier") for ln in out), out
    assert out[-1] == "carrier gaps over 0.5 ms: 0", out


def test_errors_finds_a_mid_slot_overwrite(tmp_path):
    """the bench lead (11-16 run 2: every packet 16-35 bits wrong, the same bits at 1200): if the
    chip writes its 48-bit sync over TX RAM bits 132-179 of every slot, `errors` puts every wrong
    bit in data bits 33-44 of the slot (2400)"""
    import json
    sync = np.unpackbits(np.frombuffer(bytes.fromhex("D5D7F77FD757"), np.uint8))

    def clobber(sl):
        b = np.unpackbits(np.frombuffer(sl, np.uint8))
        b[132:180] = sync
        return np.packbits(b).tobytes()
    raw = b"".join(clobber(x) for x in c6phy.slots(2400, 2, 5, 60))
    f = np.concatenate([np.zeros(24000), c6phy.freq_track(raw, 240_000), np.zeros(24000)]) - 49_700.0
    iq = np.exp(2j * np.pi * np.cumsum(f) / 240_000)
    cap = tmp_path / "cap.cf32"
    iq.astype(np.complex64).tofile(cap)
    (tmp_path / "cap.cf32.json").write_text(json.dumps({"fs": 240_000, "hz": 440_900_000}))
    out = c6phy.errors(str(cap), 2400)
    assert "would be data bits 33-44" in out[0], out
    assert out[1].endswith("(100%) in data bits 33-44"), out


def test_payload_start_bits_is_the_stream_layout():
    for baud in (2400, 1200):
        st = c6phy.stream(baud, 0, 5)
        for k in range(5):
            b = c6phy.payload_start_bits(baud, k) // 8
            assert st[b:b + 64] == fsk24.packet(c6phy.VAR[baud], k)
