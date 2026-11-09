"""EXPLORE's code pages (c/k6/kp.h, tools/k6_pages.py) on an emulated M0: the sweep (xscan.c),
Listen for ID (xlisten.c, xdmr.c) and the repeater table (rt.c), each linked against a stand-in
image (k6_arena, the functions the pages call, a build ID), fit the decoder pool, carry a good
table and the image's build ID; called through their tables from the pool's address, they return
the sizes the image expects and their calls into the image land on the image's functions.
Needs clang and ld.lld. SPDX-License-Identifier: MIT"""
import os
import shutil
import struct
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import k6_pages  # noqa: E402

pytestmark = pytest.mark.skipif(not (shutil.which("clang") and shutil.which("ld.lld")),
                                reason="needs clang and ld.lld")

FLASH, RAM = 0x08002000, 0x20001028
DEC_OFF = 4096 + 1280
STOP = 0x08001000
# the image's functions the pages call (k6_pages.py imports): each a stub that counts its calls
# and returns its first argument (cw_utoa returns where it stopped writing)
IMPORTS = [
    "cw_call",
    "cw_div",
    "cw_feed",
    "cw_flush",
    "cw_init",
    "cw_utoa",
    "ex_find",
    "ex_on_raster",
    "ex_over",
    "ex_own_clock",
    "ex_spur",
]
PAGES = {
    "sweep": ["xscan.c"],
    "listen": ["xlisten.c", "xdmr.c"],
    "table": ["rt.c"],
}


def cflags():
    res = subprocess.run(["clang", "-print-resource-dir"], capture_output=True, text=True).stdout.strip()
    return ["--target=thumbv6m-none-eabi", "-mcpu=cortex-m0", "-mthumb", "-mfloat-abi=soft", "-nostdinc",
            "-isystem", os.path.join(ROOT, "sotto", "c", "fixed", "cm4", "inc"), "-isystem",
            os.path.join(res, "include"), "-ffunction-sections", "-fdata-sections", "-std=c99",
            "-ffreestanding", "-I" + os.path.join(ROOT, "c"), "-I" + os.path.join(ROOT, "sotto", "c"), "-Oz"]


IMAGE_C = r"""
#include <stdint.h>
uint32_t k6_arena[11776 / 4];
volatile uint32_t hits[32];
#define STUB(name, k) uintptr_t name(uintptr_t a) { hits[k]++; return a; }
%s
uintptr_t api_any(uintptr_t a) { hits[31]++; return 0; }
void _start(void) {}
"""
SIZES_C = r"""
#include <stdint.h>
#include "k6/xscan.h"
#include "k6/xlisten.h"
#include "k6/xdmr.h"
const uint32_t want[3] = {sizeof(xs_t), sizeof(xl_t), sizeof(xd_t)};
"""
IMAGE_LD = """
MEMORY { FLASH : ORIGIN = 0x08002000, LENGTH = 64K
         RAM : ORIGIN = 0x20001028, LENGTH = 20424 }
SECTIONS {
  .text : { *(.text .text.*) } > FLASH
  .rodata : { *(.rodata .rodata.*) } > FLASH
  .k6_id : { KEEP(*(.note.gnu.build-id)) } > FLASH
  .bss (NOLOAD) : { *(.bss .bss.* COMMON) } > RAM
  /DISCARD/ : { *(.ARM.exidx*) }
}
"""


def cc(src, obj, extra=()):
    subprocess.run(["clang", *cflags(), *extra, "-c", src, "-o", obj], check=True)


def nm(elf):
    out = subprocess.run(["llvm-nm", elf], check=True, capture_output=True, text=True).stdout
    return {w[2]: int(w[0], 16) for w in (ln.split() for ln in out.splitlines()) if len(w) == 3}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    d = str(tmp_path_factory.mktemp("pages"))
    k6 = os.path.join(ROOT, "c", "k6")
    objs = {}
    for name, srcs in PAGES.items():
        cc(os.path.join(k6, "kp_tab.c"), f"{d}/kp_{name}.o", [f"-DKP_PAGE=KP_{name.upper()}"])
        objs[name] = [f"{d}/kp_{name}.o"]
        for s in srcs:
            o = f"{d}/{s[:-2]}.o"
            cc(os.path.join(k6, s), o)
            objs[name].append(o)
    # the pages' imports are exactly the stubs the stand-in image has
    k6_pages.cmd_imports(type("A", (), {"objs": sum(objs.values(), []), "output": f"{d}/imports.x"}))
    need = [ln.strip()[7:-1] for ln in open(f"{d}/imports.x") if ln.startswith("EXTERN(")]
    assert need == IMPORTS
    with open(f"{d}/image.c", "w") as f:
        f.write(IMAGE_C % "\n".join(f"STUB({s}, {k})" for k, s in enumerate(IMPORTS)))
    with open(f"{d}/sizes.c", "w") as f:
        f.write(SIZES_C)
    with open(f"{d}/image.ld", "w") as f:
        f.write(IMAGE_LD)
    cc(f"{d}/image.c", f"{d}/image.o")
    cc(f"{d}/sizes.c", f"{d}/sizes.o")
    elf = f"{d}/image.elf"
    subprocess.run(["ld.lld", "-T", f"{d}/image.ld", "--build-id=fast", "-e", "_start", "-u", "want",
                    f"{d}/image.o", f"{d}/sizes.o", "-o", elf], check=True)
    pages = {}
    for name in PAGES:
        out = f"{d}/page-{name}.bin"
        k6_pages.cmd_link(type("A", (), {"elf": elf, "page": name, "output": out, "objs": objs[name]}))
        pages[name] = open(out, "rb").read()
    subprocess.run(["llvm-objcopy", "-O", "binary", "--only-section=.text", "--only-section=.rodata",
                    elf, f"{d}/image.bin"], check=True)
    return elf, open(f"{d}/image.bin", "rb").read(), nm(elf), pages


class M0:
    def __init__(self, image, syms, page):
        import unicorn
        self.uc = unicorn
        mu = self.mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
        mu.mem_map(0x08000000, 0x40000)
        mu.mem_map(0x20000000, 0x6000)
        mu.mem_write(FLASH, image)
        self.org = syms["k6_arena"] + DEC_OFF + 16
        mu.mem_write(self.org, page)
        self.syms = syms
        # the module API: every call lands on api_any (word 0 is abi/level)
        self.api = 0x20005000
        mu.mem_write(self.api, struct.pack("<I", 0) + struct.pack("<I", syms["api_any"] | 1) * 63)

    def fn(self, i):
        return struct.unpack("<I", self.mu.mem_read(self.org + 8 + 4 * i, 4))[0]

    def call(self, i, *args):
        mu, uc = self.mu, self.uc
        for r, v in zip((uc.arm_const.UC_ARM_REG_R0, uc.arm_const.UC_ARM_REG_R1,
                         uc.arm_const.UC_ARM_REG_R2, uc.arm_const.UC_ARM_REG_R3), args):
            mu.reg_write(r, v)
        mu.reg_write(uc.arm_const.UC_ARM_REG_SP, 0x20005F00)
        mu.reg_write(uc.arm_const.UC_ARM_REG_LR, STOP | 1)
        mu.emu_start(self.fn(i), STOP, count=200000)
        return mu.reg_read(uc.arm_const.UC_ARM_REG_R0)

    def hits(self, name):
        k = 31 if name == "api" else IMPORTS.index(name)
        return struct.unpack("<I", self.mu.mem_read(self.syms["hits"] + 4 * k, 4))[0]

    def want(self, k):
        return struct.unpack("<I", self.mu.mem_read(self.syms["want"] + 4 * k, 4))[0]


def test_pages_fit_and_carry_table_and_id(built):
    elf, _, syms, pages = built
    bid = k6_pages.build_id(elf)
    org = syms["k6_arena"] + DEC_OFF + 16
    for pid, (name, n) in enumerate([("sweep", 12), ("listen", 14), ("table", 8)], 1):
        p = pages[name]
        assert 16 + len(p) + 8 <= 11776 - DEC_OFF, name
        assert struct.unpack_from("<II", p) == (0x504B | pid << 16 | n << 24, org), name
        assert p[-8:] == bid, name


def test_pages_run_from_the_pool(built):
    _, image, syms, pages = built
    m = M0(image, syms, pages["sweep"])
    assert m.call(1) == m.want(0)                   # xs_state_bytes
    m = M0(image, syms, pages["listen"])
    assert m.call(0) == m.want(1)                   # xl_state_bytes
    assert m.call(5) == m.want(2)                   # xd_state_bytes
    x = 0x20004000
    m.call(1, x, m.api, 440_850_000, 30)            # xl_start: into the image's cw_* and the API
    assert m.hits("cw_init") == 1 and m.hits("cw_utoa") >= 1 and m.hits("api") >= 2
