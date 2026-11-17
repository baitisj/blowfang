"""the UV-380's voice SPI port helper (firmware/uv380-openrtx/mod/c6vspi.h) run on the host: vs_open
takes PB12-PB15 only when free or set up as V_SPI on an idle SPI2, and vs_close puts every register
back (docs/uv380-c6000-modem.md, "Voice bursts (V0-V2)").
SPDX-License-Identifier: MIT"""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod")
CC = shutil.which("cc") or shutil.which("gcc")


_VS_HOST = r"""
#include <stdio.h>
#include <stdint.h>
#include <string.h>
static uint32_t regs[64], addrs[64];
static unsigned nregs;
static volatile uint32_t *reg(uint32_t a) {
    for (unsigned i = 0; i < nregs; i++)
        if (addrs[i] == a)
            return &regs[i];
    addrs[nregs] = a;
    regs[nregs] = 0;
    return &regs[nregs++];
}
#define VS_REG(a) (*reg(a))
#define VS_HOST_TEST 1
#include "c6vspi.h"
#define G(o) VS_REG(VS_GPIOB + (o))
int main(int argc, char **argv) {
    unsigned modes, spe, i2se, dma3, dma4;
    sscanf(argv[1], "%x", &modes);
    spe = argv[2][0] == '1'; i2se = argv[3][0] == '1'; dma3 = argv[4][0] == '1'; dma4 = argv[5][0] == '1';
    (void)argc;
    G(VS_MODER) = 0x00A5A5A5u | (modes << 24);
    G(VS_OTYPER) = 0x1234u | 0x1000u;
    G(VS_PUPDR) = 0x5A5A5A5Au;
    G(VS_OSPEEDR) = 0x33333333u;
    G(VS_ODR) = 0x5000u;
    VS_REG(VS_SPI2) = spe << 6;
    VS_REG(VS_SPI2 + 0x1Cu) = i2se << 10;
    VS_REG(VS_DMA1 + 0x10u + 0x18u * 3u) = dma3;
    VS_REG(VS_DMA1 + 0x10u + 0x18u * 4u) = dma4;
    VS_REG(VS_RCC_AHB1ENR) = 2u;
    uint32_t before[4] = {G(VS_MODER), G(VS_OTYPER), G(VS_PUPDR), G(VS_OSPEEDR)};
    vs_port p;
    int r = vs_open(&p);
    uint32_t open_m = G(VS_MODER) >> 24, open_t = G(VS_OTYPER), changed = 0;
    if (r == 0)
        vs_close(&p);
    uint32_t after[4] = {G(VS_MODER), G(VS_OTYPER), G(VS_PUPDR), G(VS_OSPEEDR)};
    changed = memcmp(before, after, sizeof before) != 0;
    printf("%d %d %02x %x %d %x\n", r, r == 0 ? p.borrowed : -1, open_m, (open_t >> 12) & 0xFu,
           changed, G(VS_BSRR));
    return 0;
}
"""


@pytest.mark.skipif(not CC, reason="needs a C compiler")
def test_voice_port_takes_free_or_idle_spi2_pins_only(tmp_path):
    """vs_open takes PB12-PB15 when free (input/analog), or set up as V_SPI on SPI2 (modes a9: PB12
    output, PB13-15 alternate function, as the UV-380 was found 11-17) while SPI2 and its DMA are
    idle; it refuses anything else and changes nothing then; vs_close puts every register back"""
    src = tmp_path / "vs.c"
    src.write_text(_VS_HOST)
    exe = tmp_path / "vs"
    subprocess.run([CC, "-O1", "-Wall", "-Werror", "-I", MOD, "-I", os.path.dirname(MOD), str(src), "-o", str(exe)],
                   check=True)

    def run(modes, spe=0, i2se=0, dma3=0, dma4=0):
        out = subprocess.run([str(exe), f"{modes:02x}", str(spe), str(i2se), str(dma3), str(dma4)],
                             check=True, capture_output=True, text=True).stdout.split()
        return int(out[0]), int(out[1]), int(out[2], 16), int(out[3], 16), int(out[4]), int(out[5], 16)

    for modes in (0x00, 0xFF, 0x33, 0xCC):                       # inputs and analog: free
        r, borrowed, open_m, open_t, changed, bsrr = run(modes)
        assert (r, borrowed, open_m, open_t, changed) == (0, 0, 0x45, 0, 0), modes
        assert bsrr == 0x5000 | (0xA000 << 16)                     # PB12/PB14 high, PB13/PB15 low again
    r, borrowed, open_m, open_t, changed, _ = run(0xA9)          # V_SPI on idle SPI2: borrowed
    assert (r, borrowed, open_m, open_t, changed) == (0, 1, 0x45, 0, 0)
    for busy in ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)):
        assert run(0xA9, *busy)[0] == -1, busy                   # SPI2 or its DMA running
    for modes in range(256):                                     # every other claimed pattern
        claimed = any(((modes >> (2 * i)) & 3) in (1, 2) for i in range(4))
        if claimed and modes != 0xA9:
            r, _, open_m, _, changed, _ = run(modes)
            assert r == -1 and open_m == modes and changed == 0, modes
