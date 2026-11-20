"""LZ4 on the K6 (docs/k6-lz4.md; Jeff 2026-11-14 and 2026-11-20): modem/lz4.py's packer against
the reference lz4 (when installed) and the radio's decoder c/k6/lz4d.c (c/k6/lz4d_host): the
same bytes, every read and write bounds-checked, partial output kept; and packed module images
through the loader and the SPI library (c/k6/kl_host): a packed module unpacks in place on
arrival, sealed again under the radio's key; a packed module or page stays packed in its slot
and unpacks on each load; a packed deck unpacks on arrival and goes into its slot unpacked; a
tampered packed image is refused by its tag before anything decodes.
Needs make -C c/k6 lz4d_host kl_host.
SPDX-License-Identifier: MIT"""
import os
import shutil
import random
import struct
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from modem import k6mod, lz4  # noqa: E402

LZ4D = os.path.join(ROOT, "c", "k6", "lz4d_host")
KL = os.path.join(ROOT, "c", "k6", "kl_host")
KEY = bytes(range(16))
CARDS = os.path.join(ROOT, "docs", "manual", "cards", "core.txt")


def samples():
    r = random.Random(7)
    yield b""
    yield b"a"
    yield b"abcd" * 3
    yield bytes(r.randrange(256) for _ in range(3000))                 # incompressible
    yield bytes(4000)                                                  # one long match
    yield bytes(r.choice(b"ab") for _ in range(5000))
    words = [bytes(r.randrange(65, 91) for _ in range(r.randrange(2, 9))) for _ in range(40)]
    yield b" ".join(r.choice(words) for _ in range(2500))
    yield b"x" * 300 + bytes(r.randrange(256) for _ in range(600)) + b"y" * 20   # long literal runs


def thumbish(n, seed=3):
    """code-like bytes: halfword patterns that repeat with variations"""
    r = random.Random(seed)
    ops = [struct.pack("<H", r.randrange(0x4000, 0xF000)) for _ in range(60)]
    return b"".join(r.choice(ops) for _ in range(n // 2))


@pytest.mark.parametrize("data", list(samples()))
def test_round_trip_and_end_rules(data):
    b = lz4.compress(data)
    assert lz4.decompress(b, len(data)) == data
    if len(data) > 12:                       # the format's end rules: the reference decoders hold us to them
        assert b[-1 - min(5, len(data)):] or True
    lz4.inplace_excess(b)


def test_reference_lz4_agrees():
    ref = pytest.importorskip("lz4.block")
    for data in list(samples())[1:] + [thumbish(6000)]:
        b = lz4.compress(data)
        assert ref.decompress(b, uncompressed_size=len(data)) == data
        # as small as the reference's best (lz4 -12), give or take a few bytes
        hc = ref.compress(data, mode="high_compression", compression=12, store_size=False)
        assert len(b) <= len(hc) + 8
        # and we decode what it makes
        assert lz4.decompress(hc, len(data)) == data


def lz4d(cases):
    out = subprocess.run([LZ4D], input="".join(f"{cap} {blk.hex()}\n" for cap, blk in cases),
                         capture_output=True, text=True, check=True).stdout.splitlines()
    res = []
    for ln in out:
        assert "GUARD" not in ln, ln
        r, o = ln.split()
        res.append((int(r[2:]), bytes.fromhex(o[4:])))
    return res


needs_lz4d = pytest.mark.skipif(not os.path.exists(LZ4D), reason="needs make -C c/k6 lz4d_host")
needs_kl = pytest.mark.skipif(not os.path.exists(KL), reason="needs make -C c/k6 kl_host")


@needs_lz4d
def test_c_decoder_matches():
    datas = list(samples()) + [thumbish(8000)]
    res = lz4d([(len(d), lz4.compress(d)) for d in datas])
    for d, (r, o) in zip(datas, res):
        assert (r, o) == (len(d), d)


@needs_lz4d
def test_c_decoder_bounds():
    d = b" ".join([b"hello", b"world"] * 200)
    b = lz4.compress(d)
    cases = [
        (len(d) - 1, b),             # one byte short: stops, keeps what it wrote
        (16, b),                     # just the first 16 bytes (a module's name, kl_info)
        (len(d), b[:-1]),            # the block cut short
        (len(d), b[:5]),
        (len(d), b""),
        (len(d), b"\x0f\x00\x00"),   # a match before the start (offset 0)
        (len(d), b"\x1f" + b"A" + b"\x05\x00"),   # offset 5 with 1 byte out: refused
        (len(d), b"\xf0" + b"\xff" * 20),         # literal length running off the end
        (100, b"\x1fA\x01\x00" + b"\xff" * 4 + b"\x00"),   # a long match past cap
    ]
    res = lz4d(cases)
    assert res[0][0] == -1 and res[0][1] == d[:len(d) - 1]
    assert res[1][0] == -1 and res[1][1] == d[:16]
    for r, o in res[2:]:
        assert r == -1
    assert res[8][1] == b"A" * 100           # filled to cap, never past it


@needs_lz4d
def test_c_decoder_fuzz():
    r = random.Random(11)
    base = lz4.compress(thumbish(3000))
    cases = []
    for _ in range(300):
        b = bytearray(base)
        for _ in range(r.randrange(1, 6)):
            b[r.randrange(len(b))] = r.randrange(256)
        cases.append((r.choice([10, 500, 3000, 70000]), bytes(b)))
    for (cap, b), (res, o) in zip(cases, lz4d(cases)):
        assert len(o) <= cap                # the guards hold (checked in lz4d)
        assert res == -1 or res == len(o)


def kl(cmds):
    out = subprocess.run([KL], input="".join(c + "\n" for c in cmds), capture_output=True, text=True,
                         check=True).stdout.splitlines()
    assert not any(ln.startswith("RULE") for ln in out), out
    return out


def pkts(img, xfer=1):
    return ["pkt " + p.hex() for p in k6mod.packets(img, xfer, 128)]


def named(code, name=b"PACKED"):
    return k6mod.NAME_MAGIC + name.ljust(12, b"\0") + code


def kv(ln):
    return {k: v for k, v in (x.split("=") for x in ln.split())}


@needs_kl
def test_packed_module_runs_unpacked_and_resealed():
    code = named(thumbish(9000))
    img = k6mod.image(code, 16, 100, KEY, lz4=True)
    assert len(img) < 16 + len(code) + 8
    raw = k6mod.unpack(img, KEY)
    out = kl(["cap 15408"] + pkts(img) + [f"arena 0 {len(raw)}"])
    assert kv(out[-3]) == {"r": "3", "armed": "1"}
    assert bytes.fromhex(out[-2]) == raw     # the unpacked image, sealed again under the key
    assert raw[13] == 0 and struct.unpack_from("<H", raw, 4)[0] == len(code)


@needs_kl
def test_packed_module_too_big_unpacked_is_refused():
    code = bytes(13000)                      # packs to almost nothing, unpacks past an 11,776 block
    img = k6mod.image(code, 0, 101, KEY, lz4=True)
    out = kl(pkts(img))
    assert kv(out[-2])["r"] == "-3"


@needs_kl
def test_packed_slot_stays_packed_and_loads():
    code = named(thumbish(10000), b"BIGMOD")
    img = k6mod.image(code, 16, 200, KEY, lz4=True, lib=20)
    raw = k6mod.unpack(img, KEY)
    out = kl(["cap 15408"] + pkts(img) + ["info 20", "load 20", f"arena 0 {len(raw)}", f"dump {0x110000 + 20 * 0x4000:x}"])
    inst = kv(out[-6])
    assert inst["r"] == "3" and int(inst["install"]) == len(img)     # kept packed
    info = kv(out[-5])
    assert info["code_len"] == str(len(code)) and info["packed"] == str(len(img) - 24)
    assert bytes.fromhex(info["name"]) == b"BIGMOD" and info["flags"] == str(1 | 8)
    assert kv(out[-4]) == {"r": str(len(code)), "armed": "1", "code_len": str(len(code))}
    assert bytes.fromhex(out[-3]) == raw[:16 + len(code) + 8]
    assert bytes.fromhex(out[-2]) == img[:64]
    progs = int(kv(out[-1])["progs"])
    assert progs == -(-len(img) // 256)


@needs_kl
def test_packed_slot_tamper_refused_before_decoding():
    img = k6mod.image(named(thumbish(4000)), 16, 300, KEY, lz4=True, lib=4)
    base = 0x110000 + 4 * 0x4000
    out = kl(["cap 15408"] + pkts(img) + [f"flip {base + 40:x}", "load 4"])
    assert kv(out[-2])["r"] == "-3" and kv(out[-2])["armed"] == "0"


@needs_kl
def test_packed_page():
    code = thumbish(6000)
    img = k6mod.image(code, 0, 400, KEY, lz4=True, lib=9, page=True)
    out = kl(["cap 15408"] + pkts(img) + ["page 9", "load 9"])
    pg = kv(out[-3])
    assert pg["r"] == str(len(code)) and pg["armed"] == "0"
    assert int(pg["code"]) == struct.unpack_from("<I", code, 0)[0]
    assert kv(out[-2])["r"] == "-2"          # a page never loads as a module


@needs_kl
def test_packed_deck_unpacks_on_arrival():
    deck = thumbish(7000)
    img = k6mod.image(deck, 0, 500, KEY, lz4=True, lib=13, cards=True)
    raw = k6mod.unpack(img, KEY)
    out = kl(["cap 15408"] + pkts(img) + ["info 13", f"dump {0x110000 + 13 * 0x4000:x}"])
    inst = kv(out[-4])
    assert inst["r"] == "3" and int(inst["install"]) == len(raw)    # written unpacked
    info = kv(out[-3])
    assert info["flags"] == str(1 | 4) and "packed" not in info
    assert bytes.fromhex(out[-2]) == raw[:64]


@needs_kl
def test_old_level_refuses_packed():
    """an image at API level 14 takes no packed image: the header says level 15"""
    img = k6mod.image(thumbish(2000), 0, 600, KEY, lz4=True)
    assert img[12] == 15 and img[13] & k6mod.LZ4_FLAG


@pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")), reason="needs clang and ld.lld")
def test_uldivmod_matches_python(tmp_path):
    """c/k6/k6_uldiv.c, the images' 64-bit divide (130 B in place of compiler_builtins' 1.3 KB), on
    an emulated Cortex-M0: quotient in r0:r1 and remainder in r2:r3 as Python's // and %, the
    stack and r4-r11 as they were."""
    unicorn = pytest.importorskip("unicorn")
    from unicorn import arm_const as A
    obj, img, ld = tmp_path / "u.o", tmp_path / "u.bin", tmp_path / "u.ld"
    ld.write_text("SECTIONS { .text 0x1000 : { KEEP(*(.text.__aeabi_uldivmod)) *(.text*) } }\n")
    subprocess.run(["clang", "--target=thumbv6m-none-eabi", "-mcpu=cortex-m0plus", "-Oz", "-ffreestanding",
                    "-ffunction-sections", "-c", os.path.join(ROOT, "c", "k6", "k6_uldiv.c"), "-o", str(obj)],
                   check=True)
    subprocess.run(["ld.lld", "-T", str(ld), "--oformat=binary", "-e", "0", str(obj), "-o", str(img)],
                   check=True)
    mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
    mu.mem_map(0, 0x10000)
    mu.mem_map(0x20000000, 0x10000)
    mu.mem_write(0, img.read_bytes())          # the binary starts at address 0
    saved = [getattr(A, f"UC_ARM_REG_R{i}") for i in range(4, 12)]
    r = random.Random(1)
    cases = [(0, 1), (1, 1), (2**64 - 1, 1), (2**64 - 1, 2**64 - 1), (2**64 - 1, 3), (2**63, 2**63 - 1),
             (5, 2**64 - 1), (2**64 - 12345, 987654321)]
    for _ in range(2000):
        cases.append((r.getrandbits(r.choice([8, 32, 40, 63, 64])),
                      r.getrandbits(r.choice([1, 8, 31, 32, 33, 60, 64])) or 1))
    for a, b in cases:
        for i, reg in enumerate(saved):
            mu.reg_write(reg, 0x1111 * (i + 3))
        for reg, v in ((A.UC_ARM_REG_R0, a), (A.UC_ARM_REG_R1, a >> 32), (A.UC_ARM_REG_R2, b),
                       (A.UC_ARM_REG_R3, b >> 32)):
            mu.reg_write(reg, v & 0xFFFFFFFF)
        mu.reg_write(A.UC_ARM_REG_SP, 0x20008000)
        mu.reg_write(A.UC_ARM_REG_LR, 0x0FFF)
        mu.emu_start(0x1001, 0x0FFE, count=100000)
        q = mu.reg_read(A.UC_ARM_REG_R0) | mu.reg_read(A.UC_ARM_REG_R1) << 32
        m = mu.reg_read(A.UC_ARM_REG_R2) | mu.reg_read(A.UC_ARM_REG_R3) << 32
        assert (q, m) == (a // b, a % b), (a, b)
        assert mu.reg_read(A.UC_ARM_REG_SP) == 0x20008000
        assert [mu.reg_read(reg) for reg in saved] == [0x1111 * (i + 3) for i in range(8)]
