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
        ("c6dmrrx_long.c", 5120),
        ("c6dmrtx_live.c", 4096),
        ("c6dmrtx_msg.c", 4096),
    ):
        path = os.path.join(MOD, name)
        code, entry = uv380mod.build(path)
        assert entry == 0 and len(code) < most, (name, len(code))
        assert uv380mod.source_table(path) == "openrtx"


def test_dmrtx_is_dry_unless_a_wrapper_says_otherwise():
    """the module only keys with TX_DRY 0, which only a bench wrapper sets, and only on an image
    at API level 4 (ident first, transmit guard): an older image refuses the wrappers"""
    src = open(os.path.join(MOD, "c6dmrtx.c")).read()
    assert "#ifndef TX_DRY\n#define TX_DRY 1\n" in src
    assert src.count("api->tx_tone(") == 1
    keyed = src.index("if (!TX_DRY) {\n        if (api->level < 4u) {")
    assert keyed < src.index("return -1;", keyed) < src.index("api->tx_tone(")
    live = sorted(f for f in os.listdir(MOD) if "#define TX_DRY 0" in open(os.path.join(MOD, f)).read())
    assert live == ["c6dmrtx_live.c", "c6dmrtx_msg.c", "c6phytx_live.c", "c6phytx_live1200.c"]
    live = [f for f in live if f.startswith("c6dmrtx")]      # c6phytx's: tests/test_uv380_c6phy.py
    from modem import uv380mod
    for f in live:
        assert uv380mod.source_level(os.path.join(MOD, f)) == 4


def test_image_guards_every_key_up():
    """11-16 16:12Z a hung module run left the radio keyed for 111 s with no ident: in the image
    every key-up arms the watchdog guard first and clears it after, and a module's keyed tests
    send the ident before they key"""
    patch = open(os.path.join(ROOT, "firmware", "uv380-openrtx", "uv380-blowfang.patch")).read()
    start = patch.index("+++ b/openrtx/src/rtx/OpMode_Pager.cpp")
    pager = patch[start:patch.index("\ndiff --git", start)]
    lines = [ln[1:] for ln in pager.splitlines() if ln.startswith("+")]
    code = "\n".join(lines)
    ups = [i for i, ln in enumerate(lines) if ln.strip() == "radio_enableTx();"]
    assert len(ups) == 4
    # the emulator's send (PLATFORM_LINUX) writes a file: no radio, no guard
    guarded = [i for i in ups if "txGuardOn(" in "\n".join(lines[max(0, i - 3):i])]
    assert len(guarded) == 3
    offs = [i for i, ln in enumerate(lines) if ln.strip() == "txGuardOff();"]
    assert len(offs) == 3 and all(lines[i - 1].strip() == "radio_disableRtx();" for i in offs)
    assert "TXG_IWDG" in code and "0xCCCCu" in code
    for test in ("micTest(status, ms);", "toneTest(status, ms, toneHz);"):
        at = code.index(test)
        before = code[code.rindex("if((ptt == false)", 0, at):at]
        assert "sendIdentOnly = true;" in before and "send(status);" in before


def _pager_added():
    patch = open(os.path.join(ROOT, "firmware", "uv380-openrtx", "uv380-blowfang.patch")).read()
    start = patch.index("+++ b/openrtx/src/rtx/OpMode_Pager.cpp")
    pager = patch[start:patch.index("\ndiff --git", start)]
    return "\n".join(ln[1:] for ln in pager.splitlines() if ln.startswith("+"))


def test_image_writes_no_usb_nobody_reads():
    """11-16: at 1 W the radio drops off the USB bus at key-up, and every write then spins 500 ms
    (usb_vcom.c), after which the radio reset. Lines and module writes go through pbVcom, which
    skips a suspended bus and, after a write times out, waits for the host or a retry time"""
    code = _pager_added()
    assert code.count("vcom_writeBlock(") == 1          # only inside pbVcom
    body = code[code.index("static bool pbVcom("):code.index("static void pbSay(")]
    assert "OTG_DSTS" in body and "pbSayFail" in body and "SAY_RETRY_MS" in body
    say = code[code.index("static void pbSay("):code.index("static void pbWrite(")]
    assert "if(pbVcom(s, strlen(s)))" in say
    om = code[code.index("static void omWrite("):code.index("static void omLine(")]
    assert "pbVcom(b, n);" in om and "pbHostMs = " in om   # a module's reads count as the host


def test_guard_kicker_outranks_and_leaves_breadcrumbs():
    """the kicker runs at miosix's top priority, and the guard records its state in RTC backup
    registers, which "guard" prints with RCC_CSR and then clears (RMVF)"""
    code = _pager_added()
    arm = code[code.index("static void txgArm("):code.index("static void txGuardOn(")]
    assert "pthread_attr_setschedparam(&attr, &sp);" in arm
    assert "TXG_PRIO  = 3;" in code
    assert "TXG_BKP   = 0x40002890u;" in code          # RTC_BKP16R, clear of rtc.c
    on = code[code.index("static void txGuardOn("):code.index("static void txGuardOff(")]
    assert "txgCrumb(TXG_ST_ON, d);" in on
    assert "txgCrumb(TXG_ST_STOP, d);" in code
    rep = code[code.index("txGuardReport(char *out, size_t n)\n{"):]
    assert "(1u << 24)" in rep
    assert 'strcmp(pbCmd, "guard") == 0' in code


def _patch_added(path):
    patch = open(os.path.join(ROOT, "firmware", "uv380-openrtx", "uv380-blowfang.patch")).read()
    start = patch.index("+++ b/" + path)
    end = patch.find("\ndiff --git", start)
    part = patch[start:end if end >= 0 else len(patch)]
    return "\n".join(ln[1:] for ln in part.splitlines() if ln.startswith("+"))


def test_usb_fifo_empty_interrupt_cannot_storm():
    """11-16 17:16Z (C7): after a 1 W keying the kicks stopped as USB re-enumerated and the
    watchdog reset the radio. ST's USB library only clears an IN endpoint's FIFO-empty interrupt
    mask on transfer complete, so a write abandoned when the host went away fires that interrupt
    without end once the endpoint is open again. The patch clears it when nothing is left to write
    (as ST's later HAL does), at every bus reset, and when a vcom write times out"""
    dcd = _patch_added("platform/mcu/STM32F4xx/drivers/usb/usb_dcd_int.c")
    assert "if((ep->xfer_len == 0) || (ep->xfer_count >= ep->xfer_len))" in dcd
    assert "DIEPEMPMSK, 0x1u << epnum, 0);" in dcd
    assert "USB_OTG_WRITE_REG32(&pdev->regs.DREGS->DIEPEMPMSK, 0);" in dcd
    vcom = _patch_added("platform/mcu/STM32F4xx/drivers/usb_vcom.c")
    assert "0x1u << (CDC_IN_EP & 0x7Fu), 0);" in vcom
    assert "in_ep[CDC_IN_EP & 0x7Fu].xfer_len = 0;" in vcom


def test_headers_write_only_what_they_say():
    """w25sec.h sends only the read-security-register command; at1846.h never writes the
    frequency (0x29, 0x2A), TX (0x30) or power registers"""
    w = open(os.path.join(MOD, "w25sec.h")).read()
    assert w.count("w25_byte(0x") == 1 and "w25_byte(0x48)" in w
    a = open(os.path.join(MOD, "at1846.h")).read()
    for reg in ("0x29", "0x2A", "0x30"):
        assert f"*reg = {reg}" not in a


def _sf_lines(levels, t):
    """levels -> c6dmrrx soft dump lines: per symbol L then 16 - |L|, 7-bit"""
    by = bytes(v for lv in levels for v in (int(lv) & 0x7F, (16 - abs(int(lv))) & 0x7F)).hex()
    return [f"sf {t} 0a8 {o:03x} {by[o * 2:o * 2 + 132]}" for o in range(0, 264, 66)]


def test_channel_fit_and_pre_equaliser():
    """the 11-16 bench's closed eye, modelled: soft dumps (slot type, sync and info symbols in the
    chip's orders) through a channel that kills the low end are placed in the stream, the fit
    finds the chain and the pre-equaliser reopens it"""
    import uv380_dmrburst as tool
    sym = np.asarray(tool.stream_symbols(ds.pack(rand_msg(30, seed=6), 2, "AG7EW")), float)
    h = np.array([-0.1, 0.3, -0.6, 1.2, -2.7, -1.3, 6.0, -2.5, 0.9, -0.9, 0.2, -0.3])   # lags -6..5
    lv = np.convolve(sym, h)[6:6 + len(sym)]
    rng = np.random.default_rng(7)
    lines = []
    lays = list(tool.LAYOUTS.values())
    for n in range(10):
        idx = int(rng.integers(1, len(sym) // 144 - 1)) * 144 + lays[n % len(lays)]
        lines += _sf_lines(np.clip(np.round(lv[idx] + rng.normal(0, 1, 132)), -63, 63), 1000 + 60 * n)
    dumps = tool.soft_dumps(lines)
    assert len(dumps) == 10
    fit = tool.fit_channel(sym, dumps)
    assert fit["placed"] == 10 and fit["sign"] == 1.0 and not fit["mixed"]
    assert np.allclose(fit["h"][:12], h, atol=0.3) and fit["ser"] > 0.2
    eq = tool.design_eq(fit)
    out = np.convolve(tool.equalise(sym, eq), h)[6:6 + len(sym)]
    dec = np.select([out < -16, out < 0, out < 16], [-3, -1, 1], 3)
    assert np.mean(dec[50:-50] != sym[50:-50]) < 0.05
    # a second pass through the equalised chain refines it (base in series with the residual)
    rest = np.convolve(tool.equalise(sym, eq), h)[6:6 + len(sym)] * 0.7
    lines = []
    for n in range(10):
        idx = int(rng.integers(1, len(sym) // 144 - 1)) * 144 + lays[n % len(lays)]
        lines += _sf_lines(np.clip(np.round(rest[idx] + rng.normal(0, 1, 132)), -63, 63), 2000 + 60 * n)
    eq2 = tool.compose_eq(eq, tool.fit_channel(sym, tool.soft_dumps(lines)))
    assert np.isclose(np.linalg.norm(eq2["taps"]), np.linalg.norm(eq["taps"]))
    out2 = np.convolve(tool.equalise(sym, eq2), h)[6:6 + len(sym)]
    out2 *= 24 / np.mean(np.abs(out2)[np.abs(sym) == 3])
    dec2 = np.select([out2 < -16, out2 < 0, out2 < 16], [-3, -1, 1], 3)
    assert np.mean(dec2[50:-50] != sym[50:-50]) <= np.mean(dec[50:-50] != sym[50:-50]) + 0.01


def test_rx_log_unpacks_the_message(tmp_path):
    """as uv380_i2sprobe logs it: every module line behind a "mod say" prefix"""
    import uv380_dmrburst as tool
    blocks = ds.pack(tool.test_message(), 1, "AG7EW")
    log = tmp_path / "rx.log"
    lines = ["mod say b 10 sc=2 dt=9 crc=0 cc=1 sy=0 se=0 fe=0 new=1 d=" + "00" * 24]
    lines += [f"mod say b {60 * i} sc=2 dt=7 crc=0 cc=1 sy=0 se=0 fe=0 new=1 d={b.hex()}{'00' * 12}"
              for i, b in enumerate(blocks)]
    log.write_text("\n".join(lines) + "\n")
    got = [b["payload"] for b in tool.rx_bursts(log.read_text().splitlines()) if b["dt"] == 7]
    assert got == blocks
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "uv380_dmrburst.py"), "rx", str(log)],
                       capture_output=True, text=True, check=True).stdout
    assert "23 of 23 sent" in r and "'lost': 0" in r
