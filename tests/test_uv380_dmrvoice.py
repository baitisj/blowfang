"""sotto in DMR voice bursts on the UV-380's HR_C6000 (docs/uv380-c6000-modem.md, "Sotto in DMR voice
bursts"): the voice FEC (modem/dmrvoice.py and its C twin c/common/dmr_voice.h, byte for byte),
its soft Viterbi, a voice call through audio and back (tools/uv380_dmrburst.py vwav / voice), the
page 3 -> air map learned from the raw test pattern, and the RAM modules (c6vcheck, c6dvtx and its
wrappers), which must build position-independent and stay dry unless a wrapper says otherwise.
SPDX-License-Identifier: MIT"""
import os
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import dmrsotto as ds  # noqa: E402
from modem import dmrvoice as dv  # noqa: E402

MOD = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod")
TOOLS = all(shutil.which(t) for t in ("clang", "ld.lld", "llvm-objcopy", "llvm-nm"))
CC = shutil.which("cc") or shutil.which("gcc")


def rand_msg(nsf, seed=1):
    return np.random.default_rng(seed).integers(0, 256, nsf * 6).astype(np.uint8).tobytes()


@pytest.mark.skipif(not CC, reason="needs a C compiler")
def test_c_header_makes_the_same_payloads(tmp_path):
    src = tmp_path / "dv.c"
    src.write_text(r'''
#include <stdio.h>
#include "dmr_sotto.h"
#include "dmr_voice.h"
int main(void) {
    unsigned char msg[6 * 30], b[12], p[27];
    for (unsigned i = 0; i < sizeof msg; i++) msg[i] = (unsigned char)(i * 11 + 5);
    unsigned n = ds_nblocks(sizeof msg, 5);
    for (unsigned i = 0; i < n; i++) {
        ds_make(b, i, msg, sizeof msg, 3, "AG7EW", 5);
        dv_encode(b, p);
        fwrite(p, 1, 27, stdout);
    }
    for (unsigned k = 0; k < 70; k++) {
        dv_pn(k, p);
        fwrite(p, 1, 27, stdout);
    }
    return 0;
}
''')
    exe = tmp_path / "dv"
    subprocess.run([CC, "-O2", "-Wall", "-Werror", "-I", os.path.join(ROOT, "c", "common"), str(src),
                    "-o", str(exe)], check=True)
    out = subprocess.run([str(exe)], check=True, capture_output=True).stdout
    msg = bytes((i * 11 + 5) & 255 for i in range(180))
    want = b"".join(dv.pack(msg, 3, "AG7EW")) + b"".join(dv.pn(k) for k in range(70))
    assert out == want


def test_payload_fills_216_bits_with_204_coded():
    assert len(set(dv.PLACE.tolist())) == dv.CODED
    assert all(len(dv.encode(b)) == dv.BYTES for b in ds.pack(rand_msg(4), 1, "AG7EW"))
    assert len(dv.pn(0)) == dv.BYTES and dv.pn(0) != dv.pn(1)


def test_soft_viterbi_corrects_errors():
    rng = np.random.default_rng(5)
    blocks = ds.pack(rand_msg(40, seed=2), 2, "AG7EW")
    for b in blocks:
        s = dv.hard(dv.encode(b))
        s[rng.choice(dv.BITS, 6, replace=False)] *= -1           # 6 hard errors a burst
        assert dv.decode(s)[0] == b
        n = dv.hard(dv.encode(b)) + rng.normal(0, 0.6, dv.BITS)   # about 3 dB Eb/N0
        assert dv.decode(n)[0] == b


def test_a_burst_too_damaged_fails_its_crc_and_is_concealed():
    blocks = ds.pack(rand_msg(20, seed=3), 2, "AG7EW")
    soft = [dv.hard(dv.encode(b)) for b in blocks]
    soft[4] = -soft[4]                                            # an inverted burst
    soft[6] = None                                                # a burst not heard
    r, got = dv.unpack(soft)
    assert got[4] is None and got[6] is None
    assert r["lost"] == 2 and r["bad"] == 1
    assert r["msg"][:9 * 2] == b"".join(b[1:10] for b in blocks[2:4])


def test_voice_call_comes_back_through_audio():
    import uv380_dmrburst as tool
    m = rand_msg(40, seed=4)
    blocks = ds.pack(m, 4, "AG7EW")
    pay = tool.voice_payloads(blocks)
    assert len(pay) % 6 == 0 and len(pay) >= len(blocks)
    x = tool.symbols_audio(tool.voice_stream_symbols(pay), 48000, 0.35)
    ev, vb, (r, got) = tool.decode_voice(x, 48000)
    assert [k for k, _ in vb] == list(range(len(pay)))
    assert got[:len(blocks)] == blocks
    assert r["msg"] == m and r["text"] == "AG7EW" and r["end"] and r["lost"] == 0
    kinds = [e["dtype"] for e in ev if e.get("kind") == "data" and e.get("slot") == 1]
    assert kinds.count(1) == 3 and kinds.count(2) == 1            # LC headers, terminator


def _odd_map():
    """a chip that sends each byte LSB first, the bytes in pairs swapped, every 7th bit inverted"""
    mp = []
    for p in range(dv.BITS):
        byte, bit = divmod(p, 8)
        byte = byte ^ 1 if byte < 26 else byte
        mp.append((8 * byte + 7 - bit, -1 if p % 7 == 3 else 1))
    assert len({a for a, _ in mp}) == dv.BITS
    return mp


def test_test_pattern_learns_the_chips_map_and_the_map_decodes():
    import uv380_dmrburst as tool
    mp = _odd_map()
    x = tool.symbols_audio(tool.voice_stream_symbols([dv.pn(k) for k in range(60)], mp=mp), 48000, 0.35)
    _, vb, _ = tool.decode_voice(x, 48000)
    got, weak, perm, off, plain, learned, n = tool.learn_pn(vb)
    assert perm and off == 0 and learned == 0 and plain > n // 3 and weak > 0.9
    assert got == mp
    m = rand_msg(30, seed=6)
    pay = tool.voice_payloads(ds.pack(m, 6, "AG7EW"))
    y = tool.symbols_audio(tool.voice_stream_symbols(pay, mp=mp), 48000, 0.35)
    assert tool.decode_voice(y, 48000)[2][0] is None or tool.decode_voice(y, 48000)[2][0]["msg"] != m
    assert tool.decode_voice(y, 48000, mp=got)[2][0]["msg"] == m


def test_learning_survives_a_lost_first_superframe():
    import uv380_dmrburst as tool
    sym = tool.voice_stream_symbols([dv.pn(k) for k in range(60)], lead=0, hdr=0)
    x = tool.symbols_audio(sym[6 * 288:], 48000, 0.35)         # the first superframe cut off
    _, vb, _ = tool.decode_voice(x, 48000)
    _, _, perm, off, plain, learned, _ = tool.learn_pn(vb)
    assert perm and off == 6 and plain == 0 and learned == 0


@pytest.mark.skipif(not TOOLS, reason="needs clang and lld")
def test_modules_build_for_the_openrtx_table():
    from modem import uv380mod
    for name, most in (
        ("c6vcheck.c", 4096),
        ("c6dvtx.c", 6144),
        ("c6dvtx_pn.c", 6144),
        ("c6dvtx_live.c", 6144),
        ("c6dvtx_msg.c", 6144),
    ):
        path = os.path.join(MOD, name)
        code, entry = uv380mod.build(path)
        assert entry == 0 and len(code) < most, (name, len(code))
        assert uv380mod.source_table(path) == "openrtx"


def test_dvtx_is_dry_unless_a_wrapper_says_otherwise():
    """only TX_DRY 0 keys, only wrappers set it, only at API level 4 (c6key.h refuses below), and
    the voice port is claimed before the keying, and given back on every way out after it"""
    src = open(os.path.join(MOD, "c6dvtx.c")).read()
    assert "#ifndef TX_DRY\n#define TX_DRY 1\n" in src
    assert src.count("c6k_key(") == 1 and "api->tx_tone(" not in src
    key = open(os.path.join(MOD, "c6key.h")).read()
    assert key.count("api->tx_tone(") == 1
    assert key.index("api->level < 4u") < key.index("return -1;") < key.index("api->tx_tone(")
    opened = src.index("if (vs_open(&vp))")
    assert opened < src.index("c6k_key(")
    body = src[src.index("{", opened + 20):]
    last = 0
    for m in re.finditer(r"return -?\d+;", body):
        assert "vs_close(&vp);" in body[last:m.start()], body[last:m.start()]
        last = m.end()
    wrappers = sorted(f for f in os.listdir(MOD) if f.startswith("c6dvtx_"))
    assert wrappers == ["c6dvtx_live.c", "c6dvtx_msg.c", "c6dvtx_pn.c"]
    from modem import uv380mod
    for f in wrappers:
        w = open(os.path.join(MOD, f)).read()
        assert "#define TX_DRY 0" in w
        assert uv380mod.source_level(os.path.join(MOD, f)) == 4
    check = open(os.path.join(MOD, "c6vcheck.c")).read()
    assert "tx_tone" not in check and "0x40" not in check and "0x60" not in check


def test_voice_port_leaves_claimed_pins_alone():
    """vs_open refuses PB12-PB15 if any is an output or alternate function (01 or 10)"""
    h = open(os.path.join(MOD, "c6vspi.h")).read()
    assert "((md ^ (md >> 1)) & 0x55u) != 0" in h
    for modes in range(256):
        claimed = any(((modes >> (2 * i)) & 3) in (1, 2) for i in range(4))
        assert (((modes ^ (modes >> 1)) & 0x55) != 0) == claimed
