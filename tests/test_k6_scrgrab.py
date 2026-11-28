"""c/k6/mod/scrgrab.c on an emulated Cortex-M0 (unicorn) against a stand-in K6 core: module API
level 17's screen tap (screen_tap, redraw, k6_mod.h). The core's redraw is a small M0 routine
built here that hands the tap the 8 pages of a test screen, as draw_pages does on the radio; the
module's say lines must decode (tools/screen_grab.py) to that screen, pixel for pixel, and each
fit the K6's 60-character say(). SPDX-License-Identifier: MIT"""
import os
import random
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import k6mod  # noqa: E402
import screen_grab  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "scrgrab.c")
TOOLS = all(shutil.which(t) for t in ("clang", "ld.lld", "llvm-objcopy", "llvm-nm"))
need = pytest.mark.skipif(not TOOLS, reason="needs clang/lld/llvm")

# k6_api_t after abi/level, in order (c/k6/k6_mod.h)
NAMES = ["say", "ticks", "fd_read", "fd_write", "delay_us", "tune", "rx_hz", "ms", "key_down",
         "scratch", "tx_on", "tx_off", "tx_pa", "key_poll", "cal_read", "cal_write", "fl_read",
         "fl_prog", "fl_erase", "lcd_text", "lcd_bar", "lcd_wf", "lcd_flush", "led", "clock_set",
         "clock_get", "lcd_glyphs", "setting", "sotto_play", "msg_mem", "help", "screen_tap", "redraw"]
RAM = 0x20000000
BASE, API, STUBS, TAP, REDRAW, PAGES, SCR, SP = (RAM + 0x1000, RAM + 0x8000, RAM + 0x9000, RAM + 0xA000,
                                                 RAM + 0xB000, RAM + 0xC000, RAM + 0x40000, RAM + 0x7F00)
STOP = RAM + 0x9F00

REDRAW_C = f"""
typedef void (*hook_t)(int, const unsigned char *, void *);
struct tap {{ hook_t f; void *ctx; }};
int redraw(void) {{
  volatile struct tap *t = (struct tap *){TAP:#x};
  if (!t->f) return -1;
  for (int p = 0; p < 8; p++) t->f(p, (const unsigned char *){PAGES:#x} + 128 * p, t->ctx);
  return 8;
}}
"""


def redraw_code():
    with tempfile.TemporaryDirectory() as d:
        c, o, elf, b = (os.path.join(d, n) for n in ("r.c", "r.o", "r.elf", "r.bin"))
        open(c, "w").write(REDRAW_C)
        subprocess.run(["clang", "--target=thumbv6m-none-eabi", "-mcpu=cortex-m0", "-Oz", "-ffreestanding",
                        "-c", c, "-o", o], check=True)
        subprocess.run(["ld.lld", "-Ttext", hex(REDRAW), "-e", "redraw", o, "-o", elf], check=True)
        subprocess.run(["llvm-objcopy", "-O", "binary", "-j", ".text", elf, b], check=True)
        code = open(b, "rb").read()
    assert len(code) < PAGES - REDRAW
    return code


def run(code, entry, pages, level=17, explore=False, scratch=8192):
    import unicorn
    from unicorn import arm_const as A
    mu = unicorn.Uc(unicorn.UC_ARCH_ARM, unicorn.UC_MODE_THUMB | unicorn.UC_MODE_MCLASS)
    mu.ctl_set_cpu_model(A.UC_CPU_ARM_CORTEX_M0)
    mu.mem_map(RAM, 1 << 20)
    mu.mem_write(BASE, code)
    mu.mem_write(PAGES, b"".join(pages))
    mu.mem_write(REDRAW, redraw_code())
    for i, n in enumerate(NAMES):
        mu.mem_write(STUBS + 16 * i, b"\x70\x47")          # bx lr, after the hook set r0
    slots = [STUBS + 16 * i | 1 for i in range(len(NAMES))]
    mu.mem_write(API, (1).to_bytes(2, "little") + level.to_bytes(2, "little")
                 + b"".join(s.to_bytes(4, "little") for s in slots))
    mu.mem_write(STOP, b"\x00\xbf" * 8)
    said, taps = [], []

    def on_stub(uc, addr, size, _):
        name = NAMES[(addr - STUBS) // 16]
        r0, r1 = uc.reg_read(A.UC_ARM_REG_R0), uc.reg_read(A.UC_ARM_REG_R1)
        ret = 0
        if name == "say":
            s = bytes(uc.mem_read(r0, 128)).split(b"\0")[0].decode()
            assert len(s) <= 60, s
            said.append(s)
        elif name == "scratch":
            uc.mem_write(r0, scratch.to_bytes(4, "little"))
            ret = SCR
        elif name == "screen_tap":
            taps.append(r0)
            uc.mem_write(TAP, r0.to_bytes(4, "little") + r1.to_bytes(4, "little"))
        elif name == "redraw":
            if explore:
                ret = -2
            else:                                        # the core's redraw: through the tap
                uc.reg_write(A.UC_ARM_REG_PC, REDRAW | 1)
                return
        uc.reg_write(A.UC_ARM_REG_R0, ret & 0xFFFFFFFF)

    mu.hook_add(unicorn.UC_HOOK_CODE, on_stub, begin=STUBS, end=STUBS + 16 * len(NAMES))
    mu.reg_write(A.UC_ARM_REG_R0, API)
    mu.reg_write(A.UC_ARM_REG_SP, SP)
    mu.reg_write(A.UC_ARM_REG_LR, STOP | 1)
    mu.emu_start(BASE + entry | 1, STOP, count=20_000_000)
    ret = mu.reg_read(A.UC_ARM_REG_R0)
    return ret - (1 << 32) if ret >> 31 else ret, said, taps


def screen(seed):
    """a test screen as the core would send it: a lit title bar on page 0, text-like noise below"""
    rnd = random.Random(seed)
    pages = [bytes([0xFF] * 128)]
    for _ in range(7):
        pages.append(bytes(rnd.choice((0, 0, 0, 0x3E, 0x41, 0x7F)) for _ in range(128)))
    return pages


@need
def test_scrgrab_sends_the_tapped_screen():
    code, entry = k6mod.build(SRC)
    assert k6mod.source_level(SRC) == 17
    pages = screen(3)
    ret, said, taps = run(code, entry, pages)
    assert ret == 8, said
    assert taps[-1] == 0 and taps[0] != 0          # the tap set, then taken away
    w, h, fmt, px = screen_grab.grab("\n".join(said))
    assert (w, h, fmt) == (128, 64, screen_grab.SCR_ST7565)
    for y in range(64):
        assert px[y] == [screen_grab.INK if pages[y >> 3][x] >> (y & 7) & 1 else screen_grab.PAPER
                         for x in range(128)], y
    band = [s for s in said if s.startswith("scr b ")]
    assert len(band) == 1 and not band[0].endswith(" 0")    # one band, LZ4 packed


@need
def test_scrgrab_refuses_explore_and_old_images():
    code, entry = k6mod.build(SRC)
    ret, said, taps = run(code, entry, screen(1), explore=True)
    assert ret == -2 and taps[-1] == 0 and "scrgrab: not over EXPLORE" in said
    ret, said, _ = run(code, entry, screen(1), level=16)
    assert ret == -1 and not any(s.startswith("scr ") for s in said)
