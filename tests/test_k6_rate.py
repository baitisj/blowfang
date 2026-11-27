"""The K6 pager's rate ladder (c/k6/k6_rate.c; docs/k6-fsk24.md, "2400 baud in the image"; Jeff
2026-11-16): D/2400 for a session a control packet asks for, F/1800 otherwise, and the K6's own
fallback on CRC failures, on no good packet after the switch and on silence. The policy on this
machine (the same C the image links), the control record the PC sends for it, the ARQ report's
rate bits both ways, the link numbers for the PLAY line, and the Aura patch's D/2400 recipe
against fsk24rx's run 3 (the Pluto, 234 of 234). Also: k6_rate.c builds for the M0 with no
library calls and no division.
SPDX-License-Identifier: MIT"""
import ctypes
import os
import re
import shutil
import struct
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from modem import k6mod, pager  # noqa: E402

K6 = os.path.join(ROOT, "c", "k6")
SRC = os.path.join(K6, "k6_rate.c")
PAGER_RS = os.path.join(ROOT, "radios", "k6", "fw", "src", "app", "pager.rs")
F18, D24 = 0, 1
NONE, CRC, QUIET, REVERT = range(4)
S = 10_000  # 100 us ticks a second


class Kr(ctypes.Structure):
    _fields_ = [("hist", ctypes.c_uint8), ("n", ctypes.c_uint8), ("good", ctypes.c_uint8),
                ("why", ctypes.c_uint8), ("quiet", ctypes.c_uint32), ("syncs", ctypes.c_uint16),
                ("bad", ctypes.c_uint16), ("rssi_min", ctypes.c_int16), ("bins", ctypes.c_uint8 * 32)]


@pytest.fixture(scope="module")
def L(tmp_path_factory):
    if not shutil.which("cc"):
        pytest.skip("needs a C compiler")
    so = str(tmp_path_factory.mktemp("rate") / "libkr.so")
    subprocess.run(["cc", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-o", so, SRC], check=True)
    lib = ctypes.CDLL(so)
    lib.kr_control.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    lib.kr_packet.argtypes = [ctypes.c_int]
    lib.kr_tick.argtypes = [ctypes.c_uint32]
    lib.kr_link.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.kr_state.restype = ctypes.POINTER(Kr)
    return lib


def st(L):
    return L.kr_state().contents


def ctl(**settings):
    """(mask, value) of the control record the PC sends (modem/pager.py), as the K6 reads it"""
    rec = pager.parse_control(pager.control_packet(7, settings, length=128))
    return rec[2], rec[3]


def test_control_record_asks_for_d2400_only_with_both_bits(L):
    assert ctl(phy="D", baud=2400) == (5, 4)
    for cur in (F18, D24):
        assert L.kr_control(cur, *ctl(phy="D", baud=2400)) == D24
        # back to the default: phy=F, or baud=1200, with or without the other
        assert L.kr_control(cur, *ctl(phy="F")) == F18
        assert L.kr_control(cur, *ctl(baud=1200)) == F18
        assert L.kr_control(cur, *ctl(phy="F", baud=2400)) == F18
        assert L.kr_control(cur, *ctl(phy="D", baud=1200)) == F18
        # neither said: the rung stays; a lone baud=2400 (the old FFSK 2400 ask) or phy=D too
        assert L.kr_control(cur, *ctl(frames="XL", auto=1)) == cur
        assert L.kr_control(cur, *ctl(baud=2400)) == cur
        assert L.kr_control(cur, *ctl(phy="D")) == cur


def test_three_crc_failures_in_eight_send_it_back(L):
    L.kr_enter()
    assert st(L).n == 0 and st(L).good == 0
    # two failures among good packets: stays
    for ok in (1, 0, 1, 1, 0, 1, 1, 1):
        assert L.kr_packet(ok) == NONE
    assert st(L).n == 8 and st(L).good == 1
    # the first of those failures is now 5 packets back; a third failure inside the window falls
    assert L.kr_packet(0) == CRC
    assert L.kr_take_why() == CRC and L.kr_take_why() == NONE
    # a fresh session forgets them; failures that slide out of the window don't count
    L.kr_enter()
    for ok in (0, 0) + (1,) * 7:
        assert L.kr_packet(ok) == NONE
    assert L.kr_packet(0) == NONE  # the two old ones are 8 and 9 back: only one in the window
    assert L.kr_packet(0) == NONE
    assert L.kr_packet(0) == CRC


def test_no_good_packet_after_the_switch_reverts_in_10_s(L):
    L.kr_enter()
    for _ in range(99):
        assert L.kr_tick(S // 10) == NONE
    assert L.kr_tick(S // 10) == REVERT
    assert L.kr_take_why() == REVERT
    # bad packets don't hold it there
    L.kr_enter()
    L.kr_packet(0)
    assert L.kr_tick(10 * S) == REVERT


def test_silence_after_a_good_packet_ends_the_session_at_30_s(L):
    L.kr_enter()
    assert L.kr_tick(9 * S) == NONE
    L.kr_packet(1)
    assert L.kr_tick(29 * S) == NONE
    L.kr_packet(1)  # a good packet starts the 30 s again
    assert L.kr_tick(29 * S) == NONE
    assert L.kr_tick(S) == QUIET
    assert L.kr_take_why() == QUIET
    # a long main-loop pass (a module running for minutes) saturates, it doesn't wrap
    L.kr_enter()
    L.kr_packet(1)
    L.kr_tick(0xFFFF_FFF0)
    assert L.kr_tick(0x100) == QUIET and st(L).quiet == 0xFFFF_FFFF


def test_link_numbers_for_the_play_line(L):
    L.kr_link_reset()
    assert L.kr_rssi_median() == 0 and st(L).rssi_min == 0
    for ok, r in [(1, -80), (1, -79), (0, -110), (1, -81), (1, -60)]:
        L.kr_link(ok, r)
    s = st(L)
    assert (s.syncs, s.bad, s.rssi_min) == (5, 1, -110)
    # the median's 3 dB bin, its middle: -80 sits in -82..-80 (from -136 in 3s)
    assert L.kr_rssi_median() == -81
    # out of range ends in the end bins
    L.kr_link_reset()
    L.kr_link(1, -170)
    assert L.kr_rssi_median() == -135
    L.kr_link_reset()
    L.kr_link(1, -10)
    assert L.kr_rssi_median() == -136 + 31 * 3 + 1
    # a bin saturates at 255 and the median still comes from the bins
    L.kr_link_reset()
    for _ in range(300):
        L.kr_link(1, -100)
    L.kr_link(1, -50)
    assert st(L).syncs == 301 and L.kr_rssi_median() == -99  # the bin -100..-98


def report(state_byte):
    """a status report as the K6 sends it (k6_mod.c km_report_status's layout), whitened"""
    d = bytearray(64)
    d[0], d[1], d[2], d[3], d[4] = k6mod.MAGIC_REPORT, k6mod.REP_STATUS, 3, 0, 1
    d[5], d[6], d[7], d[9] = 2, 10, 10, state_byte
    d[46:51] = b"AG7EW"
    c = pager.crc16_ccitt(bytes(d[:62]))
    d[62], d[63] = c >> 8, c & 0xFF
    return pager._whiten(bytes(d))


def test_arq_report_carries_the_rung():
    old = k6mod.parse_report(report(1))  # an image before the ladder: no high bits
    assert old["state"] == "ran" and old["rates"] == "F12,F18" and old["rung"] == "F18" and old["fell"] is None
    r = k6mod.parse_report(report(1 | 0x10 | 0x20))
    assert r["state"] == "ran" and r["rates"] == "F12,F18,D24" and r["rung"] == "D24" and r["fell"] is None
    r = k6mod.parse_report(report(0 | 0x10 | CRC << 6))
    assert r["state"] == "partway" and r["rung"] == "F18" and r["fell"] == "crc"
    assert k6mod.parse_report(report(2 | 0x10 | REVERT << 6))["fell"] == "revert"
    # the C header says the same bits
    h = open(os.path.join(K6, "k6_mod.h")).read()
    assert re.search(r"#define KM_RATE_D24 0x10u", h) and re.search(r"#define KM_RATE_AT_D24 0x20u", h)
    assert re.search(r"#define KM_RATE_WHY_SHIFT 6", h)


def test_image_d2400_recipe_is_run3s():
    """REG_58, REG_72 and REG_2B for D/2400 in the image are fsk24rx's setting 2 (run 3 read every
    packet the Pluto sent in it), the report bits match k6_mod.h, and kr_t's layout matches."""
    a = open(PAGER_RS).read()
    rx = open(os.path.join(K6, "mod", "fsk24rx.c")).read()
    assert "{0x00E9, 24779, 1067, 0x0400}," in rx
    assert re.search(r"const CFG_D_2400: u16 = 0x00E9;", a)
    assert re.search(r"const BAUD_2400: u16 = 0x60CB;", a) and 0x60CB == 24779
    assert re.search(r"const AF_HPF_OFF: u16 = 0x0400;", a)
    for name, v in [("KM_RATE_D24", "0x10"), ("KM_RATE_AT_D24", "0x20"), ("KM_RATE_WHY_SHIFT", "6"),
                    ("KR_D24", "1"), ("KR_WHY_CRC", "1"), ("KR_WHY_QUIET", "2")]:
        assert re.search(rf"const {name}: \w+ = {v};", a), name
    # FFI layout: the Rust KrState fields are kr_t's, in order
    m = re.search(r"pub struct KrState \{(.*?)\}", a, re.S)
    fields = re.findall(r"pub (\w+): ([\w\[\]; 0-9]+),", m.group(1))
    assert [f for f, _ in fields] == [f for f, _ in Kr._fields_]
    assert dict(fields)["bins"] == "[u8; 32]"
    # the old FFSK 1200/2400 variants are gone; replies stay at F/1800
    assert "RX2400_VARIANTS" not in a and "f2400_var" not in a
    assert "app.pager.d2400 = false;\n    configure(app, syst);" in a


def test_builds_for_the_m0_with_no_calls_out():
    if not shutil.which("clang") or not shutil.which("llvm-nm"):
        pytest.skip("needs clang and llvm-nm")
    out = subprocess.run(["clang", "--target=thumbv6m-none-eabi", "-mcpu=cortex-m0", "-mthumb", "-Oz",
                          "-ffreestanding", "-Wall", "-Werror", "-c", SRC, "-o", "/dev/stdout"],
                         check=True, capture_output=True).stdout
    nm = subprocess.run(["llvm-nm", "-u", "-"], input=out, capture_output=True, check=True).stdout.decode()
    assert nm.split() == []  # no __aeabi_*div, no memset
    sz = subprocess.run(["llvm-size", "-"], input=out, capture_output=True, check=True).stdout.decode()
    text = int(sz.splitlines()[1].split()[0])
    assert text < 600, sz
