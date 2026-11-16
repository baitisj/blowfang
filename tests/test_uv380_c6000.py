"""The UV-380's HR_C6000 as a modem (docs/uv380-c6000-modem.md): sotto messages as DMR rate 1/2 data
blocks (modem/dmrsotto.py and its C twin c/common/dmr_sotto.h, byte for byte), the IC-7100 test
signal and its decoder (tools/uv380_dmrburst.py, through sim/k6if/dmrdec.py), and the RAM modules
that drive the chip (firmware/uv380-openrtx/mod/c6*.c), which must build position-independent
for the OpenRTX table. The modules poke the radio's GPIO, EXTI, SPI1 and the C6000 itself, so
they can't run on the emulator: their behaviour is checked on the bench.
SPDX-License-Identifier: MIT"""
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import dmrsotto as ds  # noqa: E402

MOD = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod")
TOOLS = all(shutil.which(t) for t in ("clang", "ld.lld", "llvm-objcopy", "llvm-nm"))
CC = shutil.which("cc") or shutil.which("gcc")


def rand_msg(nsf, seed=1):
    return np.random.default_rng(seed).integers(0, 256, nsf * 6).astype(np.uint8).tobytes()


def test_blocks_round_trip():
    m = rand_msg(250)
    b = ds.pack(m, 9, "AG7EW TEST")
    assert all(len(x) == 12 for x in b)
    assert len(b) == 2 + 2 + (len(m) + 8) // 9
    r = ds.unpack(b)
    assert r["msg"] == m and r["text"] == "AG7EW TEST" and r["id"] == 9
    assert r["codec"] == ds.CODEC_SOTTO1200 and r["lost"] == 0 and r["end"]


def test_1200_bits_a_second():
    """one 9-byte block per 60 ms burst is sotto 1200's 6 bytes per 40 ms"""
    assert ds.PAYLOAD * 8 / 0.060 == pytest.approx(1200.0)


def test_lost_and_bad_blocks_stay_in_place():
    """bursts never heard and bursts with a bad CRC lose only their own 9 bytes, also across the
    sequence number's wrap at 32 (a 10 s message is 170 blocks)"""
    m = rand_msg(250, seed=2)
    b = ds.pack(m, 1, "AG7EW")
    heard = b[:50] + b[57:100] + [b[100][:11] + bytes([b[100][11] ^ 1])] + b[101:]
    r = ds.unpack(heard)
    assert r["lost"] == 8 and r["bad"] == 1 and r["end"]
    lost = {i for i in range(len(m)) if r["msg"][i] != m[i]}
    expect = set()
    for k in list(range(50, 57)) + [100]:           # block k carries bytes (k - 2) * 9 ...
        expect |= set(range((k - 2) * 9, min(len(m), (k - 1) * 9)))
    assert lost == expect


def test_a_repeated_block_counts_once():
    m = rand_msg(20)
    b = ds.pack(m)
    assert ds.unpack(b[:5] + [b[4]] + b[5:])["msg"] == m


def test_no_head_no_message():
    assert ds.unpack(ds.pack(rand_msg(10))[1:]) is None


@pytest.mark.skipif(not CC, reason="needs a host C compiler")
def test_c_header_makes_the_same_blocks(tmp_path):
    src = tmp_path / "ds.c"
    src.write_text(r'''
#include <stdio.h>
#include "dmr_sotto.h"
int main(void) {
    unsigned char msg[6 * 40], b[12];
    for (unsigned i = 0; i < sizeof msg; i++) msg[i] = (unsigned char)(i * 7 + 3);
    const char *t = "AG7EW TEST1";
    unsigned n = ds_nblocks(sizeof msg, 11);
    for (unsigned i = 0; i < n; i++) {
        ds_make(b, i, msg, sizeof msg, 5, t, 11);
        if (ds_check(b) < 0) return 1;
        fwrite(b, 1, 12, stdout);
    }
    return 0;
}
''')
    exe = tmp_path / "ds"
    subprocess.run([CC, "-O2", "-Wall", "-Werror", "-I", os.path.join(ROOT, "c", "common"), str(src),
                    "-o", str(exe)], check=True)
    out = subprocess.run([str(exe)], check=True, capture_output=True).stdout
    msg = bytes((i * 7 + 3) & 255 for i in range(240))
    assert out == b"".join(ds.pack(msg, 5, "AG7EW TEST1"))


def test_ic7100_signal_decodes():
    import uv380_dmrburst as tool
    m = rand_msg(40, seed=3)
    blocks = ds.pack(m, 3, "AG7EW")
    x = tool.symbols_audio(tool.stream_symbols(blocks), 48000, 0.35)
    ev, got, r = tool.decode(x, 48000)
    assert got == blocks
    assert r["msg"] == m and r["text"] == "AG7EW"
    kinds = [e["dtype"] for e in ev if e.get("kind") == "data" and e.get("slot") == 1]
    assert kinds.count(3) == 3 and kinds.count(7) == len(blocks)


def test_deemphasised_signal_comes_back_through_preemphasis():
    """--deemph is for a pre-emphasising transmitter: the receiver sees it flat again"""
    import uv380_dmrburst as tool
    blocks = ds.pack(rand_msg(12, seed=4), 4, "AG7EW")
    x = tool.symbols_audio(tool.stream_symbols(blocks), 48000, 0.35, deemph=True)
    assert tool.decode(x, 48000)[1] != blocks
    assert tool.decode(tool.preemphasis(x, 48000), 48000)[1] == blocks


@pytest.mark.skipif(not TOOLS, reason="needs clang and lld")
def test_modules_build_for_the_openrtx_table():
    from modem import uv380mod
    for name, most in (
        ("c6pages.c", 4096),
        ("c6dmrrx.c", 5120),
        ("c6dmrtx.c", 4096),
        ("c6dmrrx_flat.c", 5120),
        ("c6dmrrx_raw.c", 5120),
        ("c6dmrtx_live.c", 4096),
        ("c6dmrtx_msg.c", 4096),
    ):
        path = os.path.join(MOD, name)
        code, entry = uv380mod.build(path)
        assert entry == 0 and len(code) < most, (name, len(code))
        assert uv380mod.source_table(path) == "openrtx"


def test_dmrtx_is_dry_unless_a_wrapper_says_otherwise():
    """the module only keys with TX_DRY 0, which only a bench wrapper sets"""
    src = open(os.path.join(MOD, "c6dmrtx.c")).read()
    assert "#ifndef TX_DRY\n#define TX_DRY 1\n" in src
    assert "if (!TX_DRY) {\n        if (api->tx_tone(" in src
    live = sorted(f for f in os.listdir(MOD) if "#define TX_DRY 0" in open(os.path.join(MOD, f)).read())
    assert live == ["c6dmrtx_live.c", "c6dmrtx_msg.c"]


def test_headers_write_only_what_they_say():
    """w25sec.h sends only the read-security-register command; at1846.h never writes the
    frequency (0x29, 0x2A), TX (0x30) or power registers"""
    w = open(os.path.join(MOD, "w25sec.h")).read()
    assert w.count("w25_byte(0x") == 1 and "w25_byte(0x48)" in w
    a = open(os.path.join(MOD, "at1846.h")).read()
    for reg in ("0x29", "0x2A", "0x30"):
        assert f"*reg = {reg}" not in a
