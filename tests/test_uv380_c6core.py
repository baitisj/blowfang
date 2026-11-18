"""The UV-380's HR_C6000 core (c/common/c6_core.c, docs/uv380-multimode.md) on a fake chip
(c/common/c6core_host.c): the 4FSK engine must put a whole M17 or YSF sotto transmission on the
air byte for byte after the preamble, then pad; stop at its deadline; and the DMR engine must
send the headers, voice bursts in A-F order with their payloads, and the terminator, one burst
every other slot. Every register a profile touches must be back as it was.
Needs make -C c/common. SPDX-License-Identifier: MIT"""
import os
import re
import subprocess

import numpy as np
import pytest

from modem import m17, ysf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "c", "common", "c6core_host")
pytestmark = pytest.mark.skipif(not os.path.exists(EXE), reason="needs make -C c/common")


def run(*args, data=b""):
    r = subprocess.run([EXE, *args], input=data, capture_output=True, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout, r.stderr.decode()


def stats(err):
    return {k: int(v) for k, v in re.findall(r"(\w+)=(-?\d+)", err.splitlines()[0])}


def changed(err):
    """registers not as they started, apart from the flags 0x82 (0x83 clears them)"""
    line = [x for x in err.splitlines() if x.startswith("changed")][0]
    return [x for x in line.split()[1:] if not x.startswith("82=")]


def sotto(n, seed=3):
    return bytes(np.random.default_rng(seed).integers(0, 256, 6 * n, dtype=np.uint8))


@pytest.mark.parametrize("n", [1, 20, 75])
def test_m17_stream_goes_out_whole(n):
    s = sotto(n, seed=n)
    air, err = run("fsk4", "m17", "AG7EW", data=s)
    tx = m17.tx_sotto(s)
    st = stats(err)
    assert air[:36] == bytes([0x77]) * 36                   # the preamble fill before slot 1
    assert air[36:36 + len(tx)] == tx
    assert set(air[36 + len(tx):]) <= {0x55, 0x5D}          # then EOT pad
    assert st["r"] == 0 and st["done"] == 1 and st["late"] == 0 and st["bytes"] == len(tx)
    assert re.search(r"chk=36/", err)
    assert changed(err) == []


@pytest.mark.parametrize("n", [1, 37])
def test_ysf_stream_goes_out_whole(n):
    s = sotto(n, seed=50 + n)
    air, err = run("fsk4", "ysf", "AG7EW", data=s)
    tx = ysf.tx_sotto(s)
    assert air[36:36 + len(tx)] == tx and set(air[36 + len(tx):]) == {0x77}
    assert stats(err)["r"] == 0 and changed(err) == []


def test_deadline_stops_early_and_restores():
    s = sotto(40)
    air, err = run("fsk4", "m17", "AG7EW", "500", data=s)
    st = stats(err)
    assert st["r"] == -1 and st["done"] == 0
    assert st["slots"] <= 500 // 30
    assert air[36:36 + 36 * (st["slots"] - 1)] == m17.tx_sotto(s)[:36 * (st["slots"] - 1)]
    assert changed(err) == []


def test_dmr_call_order_and_payloads():
    out, err = run("dmr", "12")
    bursts = [ln.split() for ln in out.decode().splitlines()]
    kinds = [b[2] for b in bursts]
    assert kinds == ["50=10"] * 3 + [f"50={0x08 | (k % 6) << 4:02x}" for k in range(12)] + ["50=20"]
    for k in range(12):
        want = bytes((k * 16 + i) & 0xFF for i in range(27)).hex()
        assert bursts[3 + k][3] == "pay=" + want
    edges = [int(b[1]) for b in bursts]
    assert all(b - a == 2 for a, b in zip(edges, edges[1:]))   # every other slot: direct mode
    st = stats(err)
    assert st["r"] == 0 and st["hdr"] == 3 and st["voice"] == 12 and st["term"] == 1
    assert changed(err) == ["22=40"]                           # encode stop, as c6dvtx leaves it


MOD = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod")


def test_sotto_modules_build_and_fit():
    """c6sotto (the core + a framer as a RAM module, keyed by tx_tone) for each mode, dry and on
    air: position independent, level 4, well inside the 16 KB arena"""
    import shutil
    if not shutil.which("clang") or not shutil.which("ld.lld"):
        pytest.skip("needs clang and ld.lld")
    from modem import uv380mod
    for air in ("m17", "ysf", "dmr"):
        for dry in ("_dry", ""):
            src = os.path.join(MOD, f"c6sotto_{air}{dry}.c")
            assert uv380mod.source_level(src) == 4
            code, entry = uv380mod.build(src)
            assert entry == 0 and len(code) < 8192, (src, len(code))


def test_sotto_module_is_dry_unless_a_wrapper_says_otherwise():
    """c6sotto keys only through c6key.h's c6k_key (level 4: ident first, transmit guard), only
    with TX_DRY 0, which only the three on-air wrappers set"""
    src = open(os.path.join(MOD, "c6sotto.c")).read()
    assert "#ifndef TX_DRY\n#define TX_DRY 1\n" in src
    assert "api->tx_tone(" not in src and src.count("c6k_key(") == 1
    assert "if (!TX_DRY && c6k_key(" in src
    key = open(os.path.join(MOD, "c6key.h")).read()
    assert key.index("api->level < 4u") < key.index("api->tx_tone(")
    live = sorted(f for f in os.listdir(MOD) if f.startswith("c6sotto_")
                  and "#define TX_DRY 0" in open(os.path.join(MOD, f)).read())
    assert live == ["c6sotto_dmr.c", "c6sotto_m17.c", "c6sotto_ysf.c"]
