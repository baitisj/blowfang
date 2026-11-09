"""K6 SPI module library (c/k6/k6_lib.c, k6_lib.h) on this machine (c/k6/kl_host: the library
and the module loader on an emulated SPI flash, test key 00..0f): a module sent with the library
flag is checked as any module and written to its slot instead of being run; kl_info reads the
slot's header; kl_load reads it back, checks the tag again and arms km_run; a slot that wore or
was overwritten is refused; nothing is written while keyed, nor outside the library, nor across
a page; an old slot's serial doesn't open a replay. Needs make -C c/k6 kl_host.
SPDX-License-Identifier: MIT"""
import os
import random
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = os.path.join(ROOT, "c", "k6", "kl_host")
sys.path.insert(0, ROOT)
from modem import k6mod  # noqa: E402

KEY = bytes(range(16))
BASE = 0x110000
SLOT = 0x4000
KM_READY = 3
KM_BAD_TAG = -4
KM_OLD = -6
KL_EMPTY = -1
KL_BAD_HDR = -2
KL_BAD_TAG = -3
KL_IO = -4
pytestmark = pytest.mark.skipif(not os.path.exists(HOST), reason="needs make -C c/k6 kl_host")


def code(n, seed=1):
    return bytes(random.Random(seed).randrange(256) for _ in range(n))


def lib_image(n=900, serial=2000, slot=3, key=KEY, seed=1, tlm=0):
    return k6mod.image(code(n, seed), 4, serial, key, lib=slot, tlm=tlm)


def pkts(img, xfer=1):
    return ["pkt " + p.hex() for p in k6mod.packets(img, xfer, 128)]


def run(cmds):
    """kl_host's lines for the commands, as dicts of key=value (ints), and the tail: erases,
    programs and the RULE lines"""
    out = subprocess.run([HOST], input="".join(c + "\n" for c in cmds), check=True,
                         capture_output=True, text=True).stdout.split("\n")
    rules = [ln for ln in out if ln.startswith("RULE")]
    rows = []
    for ln in out:
        if ln.startswith("r=") or ln.startswith("erases="):
            rows.append({k: int(v) for k, v in (kv.split("=") for kv in ln.split())})
    return rows[:-1], rows[-1], rules


def finals(rows):
    """the rows that end a transfer (r != 1, a chunk)"""
    return [r for r in rows if r["r"] != 1]


def test_install_then_load():
    img = lib_image()
    rows, tail, rules = run(pkts(img) + ["info 3", "load 3", "info 4", "load 4"])
    inst, info, load, info4, load4 = finals(rows)
    assert inst == {"r": KM_READY, "armed": 0, "install": len(img)}   # kept, never run from the air
    assert info == {"r": 0, "code_len": 900, "entry": 4, "serial": 2000, "level": k6mod.LEVEL, "flags": 1}
    assert load == {"r": 900, "armed": 1, "code_len": 900}
    assert info4 == {"r": KL_EMPTY} and load4["r"] == KL_EMPTY and load4["armed"] == 1
    assert tail == {"erases": 1, "progs": -(-len(img) // 256)}
    assert rules == []


def test_install_spans_sectors_and_replaces():
    big = lib_image(n=9000, slot=11, serial=10)
    small = lib_image(n=300, slot=11, serial=11, seed=2)
    rows, tail, rules = run(pkts(big) + pkts(small, 2) + ["load 11", "info 11"])
    a, b, load, info = finals(rows)
    assert a["install"] == len(big) and b["install"] == len(small)
    assert load["r"] == 300 and info["serial"] == 11
    assert tail["erases"] == 3 + 1      # 9,024 B: three sectors; then one for the small image
    assert rules == []


def test_largest_module_fits_a_slot():
    # the 11,776 B arena's largest module (11,752 B with header and tag) goes in a 16 KB slot
    img = lib_image(n=11752 - 24, slot=0)
    rows, _, rules = run(pkts(img) + ["load 0"])
    inst, load = finals(rows)
    assert inst["install"] == len(img) and load["r"] == 11752 - 24 and rules == []
    with pytest.raises(ValueError):
        k6mod.image(code(SLOT - 23), 0, 1, KEY, lib=0)
    with pytest.raises(ValueError):
        k6mod.image(code(100), 0, 1, KEY, lib=12)


def test_worn_slot_is_refused():
    img = lib_image()
    for at, want in [(BASE + 3 * SLOT + 100, KL_BAD_TAG),        # in the code: the tag catches it
                     (BASE + 3 * SLOT + 16 + 900 + 2, KL_BAD_TAG),   # in the tag
                     (BASE + 3 * SLOT + 0, KL_BAD_HDR)]:         # 'K' -> 'J'
        rows, _, _ = run(pkts(img) + [f"flip {at:x}", "load 3"])
        load = finals(rows)[-1]
        assert load["r"] == want and load["armed"] == 0, hex(at)


def test_wrong_key_never_installs():
    img = lib_image(key=bytes(16))
    rows, tail, _ = run(pkts(img) + ["info 3"])
    got, info = finals(rows)
    assert got == {"r": KM_BAD_TAG, "armed": 0} and info["r"] == KL_EMPTY
    assert tail == {"erases": 0, "progs": 0}


def test_keyed_refuses_writes():
    img = lib_image()
    rows, tail, _ = run(["keyed 1"] + pkts(img) + ["keyed 0", "info 3"])
    inst, info = finals(rows)
    assert inst["install"] == KL_IO and info["r"] == KL_EMPTY
    assert tail == {"erases": 0, "progs": 0}


def test_slot_serial_does_not_open_a_replay():
    # a slot installed with serial 50, then a module with serial 60 over the air: loading the
    # slot must not lower the loader's serial, so the serial-55 module is still a replay
    lib = lib_image(serial=50)
    newer = k6mod.image(code(200, 3), 0, 60, KEY)
    older = k6mod.image(code(200, 4), 0, 55, KEY)
    rows, _, _ = run(pkts(lib) + pkts(newer, 2) + ["load 3"] + pkts(older, 3))
    inst, run60, load, run55 = finals(rows)
    assert inst["install"] == len(lib) and run60 == {"r": KM_READY, "armed": 1}
    assert load["r"] == 900 and run55["r"] == KM_OLD


def test_plain_module_still_runs():
    img = k6mod.image(code(400), 0, 7, KEY)
    rows, tail, _ = run(pkts(img))
    assert finals(rows) == [{"r": KM_READY, "armed": 1}]
    assert tail == {"erases": 0, "progs": 0}


def test_tools_ask_for_the_library():
    from modem import pager
    rec = pager.parse_control(pager.control_packet(9, None, "lib", 64, arg=5))
    assert rec[5] == 11 and rec[4] == 5
    img = lib_image(slot=7)
    assert img[13] == k6mod.LIB_FLAG and img[15] == 7
    plain = k6mod.image(code(100), 0, 1, KEY)
    assert plain[13] == 0 and plain[15] == 0
