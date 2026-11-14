"""The K6 station editor's logic (c/k6/sted.c, the release plan's editor, Jeff 2026-11-13) on this
machine: a new station typed from nothing, the frequency's range, multi-tap text (a call keeps
only what a call can hold), tones by UP/DOWN and by digits, 4 copying the other tone, * deleting
the digits of a saved frequency, the offset's steps and a custom one in kHz, EXIT inside
a field putting it back, the ask before leaving with changes, Save's checks and what sted_commit
marks. Then its key trial module (c/k6/mod/edtrial.c) on an emulated Cortex-M0 with the real
keypad matrix and a table the host library wrote: it edits and "saves" a station and writes
nothing. Also: sted.c builds for the M0 with no library calls and no writable data.
SPDX-License-Identifier: MIT"""
import ctypes
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_rt import BASE, K6, Rec, Table, lib  # noqa: E402,F401  (lib: the rt host fixture)

SRC = os.path.join(K6, "sted.c")
TRIAL = os.path.join(K6, "mod", "edtrial.c")
ARENA = 11776
SF_RX, SF_OFF, SF_RXT, SF_TXT, SF_NAME, SF_CALL, SF_GRP, SF_WIDE, SF_DMR = range(9)
NONE, SAVE, EXIT, MENU = range(4)
RT_F_WIDE, RT_F_NAME_USER, RT_F_CALL_USER, RT_F_TONE_USER, RT_F_AUTO = 0x01, 0x08, 0x10, 0x40, 0x80
RT_C_FROM_CW, RT_C_TWICE = 0x08, 0x10


class Sted(ctypes.Structure):
    _fields_ = [("r", Rec), ("was", Rec), ("fwas", Rec), ("tap_ms", ctypes.c_uint32), ("msg", ctypes.c_char_p),
                ("f", ctypes.c_uint8), ("open", ctypes.c_uint8), ("ask", ctypes.c_uint8), ("nd", ctypes.c_uint8),
                ("tap_k", ctypes.c_uint8), ("tap_i", ctypes.c_uint8), ("fresh", ctypes.c_uint8), ("neg", ctypes.c_uint8),
                ("cust", ctypes.c_uint8), ("dig", ctypes.c_char * 10)]


@pytest.fixture(scope="module")
def S(tmp_path_factory):
    if not shutil.which("cc"):
        pytest.skip("needs a C compiler")
    so = str(tmp_path_factory.mktemp("sted") / "libsted.so")
    subprocess.run(["cc", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-o", so, SRC], check=True)
    L = ctypes.CDLL(so)
    L.sted_open.argtypes = [ctypes.POINTER(Sted), ctypes.POINTER(Rec)]
    L.sted_key.argtypes = [ctypes.POINTER(Sted), ctypes.c_int, ctypes.c_uint32]
    L.sted_changed.argtypes = [ctypes.POINTER(Sted)]
    L.sted_commit.argtypes = [ctypes.POINTER(Sted)]
    L.sted_saved.argtypes = [ctypes.POINTER(Sted), ctypes.POINTER(Rec)]
    L.sted_row.argtypes = [ctypes.POINTER(Sted), ctypes.c_int, ctypes.c_char_p]
    L.sted_hint.argtypes = [ctypes.POINTER(Sted), ctypes.c_char_p]
    L.sted_mhz.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_int]
    L.sted_mhz.restype = ctypes.c_void_p
    L.sted_tone.argtypes = [ctypes.c_char_p, ctypes.c_uint16]
    L.sted_tone.restype = ctypes.c_void_p
    return L


class Ed:
    """one editor: keys typed 300 ms apart unless a gap is given"""

    def __init__(self, L, rec=None):
        self.L, self.e, self.ms = L, Sted(), 10_000
        L.sted_open(ctypes.byref(self.e), ctypes.byref(rec) if rec is not None else None)

    def keys(self, ks, gap=300):
        out = None
        for k in ks:
            self.ms += gap
            out = self.L.sted_key(ctypes.byref(self.e), ord(k), self.ms)
        return out

    def to(self, f):
        """the cursor to field f in the list"""
        while self.e.f != f:
            self.keys("D")

    def row(self, f=None):
        b = ctypes.create_string_buffer(22)
        n = self.L.sted_row(ctypes.byref(self.e), self.e.f if f is None else f, b)
        assert n == len(b.value) <= 21
        return b.value.decode()

    def hint(self):
        b = ctypes.create_string_buffer(22)
        n = self.L.sted_hint(ctypes.byref(self.e), b)
        assert n == len(b.value) <= 21
        return b.value.decode()

    def changed(self):
        return self.L.sted_changed(ctypes.byref(self.e))


def lynden():
    return Rec(state=0x00FF, rx_hz=442_025_000, tx_off=5_000_000, rx_tone=1035, tx_tone=1035, name=b"LYNDEN",
               call=b"W7ABC", call_flags=RT_C_FROM_CW | RT_C_TWICE | 0x04, flags=RT_F_AUTO, id=7, group=2)


def test_builds_for_the_m0_without_library_calls(tmp_path):
    if not (shutil.which("clang") and shutil.which("llvm-nm")):
        pytest.skip("needs clang")
    o = str(tmp_path / "sted.o")
    subprocess.run(["clang", "--target=thumbv6m-none-eabi", "-mcpu=cortex-m0", "-mthumb", "-Oz", "-fropi",
                    "-ffreestanding", "-fno-builtin", "-nostdlib", "-ffunction-sections", "-fdata-sections",
                    "-Wall", "-Wextra", "-Werror", "-I" + K6, "-c", SRC, "-o", o], check=True)
    und = subprocess.run(["llvm-nm", "-u", o], capture_output=True, text=True, check=True).stdout.split()
    assert und == [], und                       # no divide helpers, no memcpy
    text, data, bss = map(int, subprocess.run(["llvm-size", o], capture_output=True, text=True,
                                              check=True).stdout.split("\n")[1].split()[:3])
    assert data == bss == 0                     # no writable statics: it can live in a code page
    assert text < 4400, text                    # 4,260 B (2026-11-14: tone copy, custom offset)


def test_text_helpers(S):
    b = ctypes.create_string_buffer(16)
    for hz, dec, want in [(442_025_000, 3, "442.025"), (146_520_000, 5, "146.52000"), (18_000_000, 1, "18.0"),
                          (600_000, 3, "0.600"), (5_000_000, 3, "5.000")]:
        S.sted_mhz(b, hz, dec)
        assert b.value.decode()[:len(want)] == want, (hz, b.value)
    for tone, want in [(1035, "103.5"), (670, "67.0"), (2541, "254.1"), (0x8000 | 0o023, "D023N"),
                       (0xC000 | 0o754, "D754I"), (0, "NONE")]:
        b = ctypes.create_string_buffer(16)
        S.sted_tone(b, tone)
        assert b.value.decode() == want


def test_a_new_station_typed_from_nothing(S):
    ed = Ed(S)
    assert ed.row(SF_RX) == "RX   ---" and ed.hint() == "#EDIT 5SAVE EXIT"
    assert ed.keys("5") == NONE and ed.hint() == "SET RX FIRST"      # nothing to save yet
    ed.keys("#4420")
    assert ed.e.open and ed.row() == "RX   442.0_" and ed.hint() == "DIGITS *DEL #OK"
    ed.keys("9*25")                             # * takes the last digit back
    assert ed.row() == "RX   442.025_"
    ed.keys("#")
    assert not ed.e.open and ed.e.r.rx_hz == 442_025_000 and ed.row() == "RX   442.02500"
    assert ed.changed()
    assert ed.keys("5") == SAVE                 # a new station with RX set saves


def test_the_frequency_stays_in_the_receivers_range(S):
    ed = Ed(S, lynden())
    ed.keys("#001#")                            # 1 MHz
    assert ed.e.open and ed.hint() == "18-630 MHZ ONLY" and ed.e.r.rx_hz == 442_025_000
    ed.keys("***7#")                            # 700 MHz
    assert ed.e.open and ed.hint() == "18-630 MHZ ONLY"
    ed.keys("*16#")                             # 160 MHz
    assert not ed.e.open and ed.e.r.rx_hz == 160_000_000


def test_multi_tap_name(S):
    ed = Ed(S)
    ed.to(SF_NAME)
    ed.keys("#")
    assert ed.row() == "NAM  _" and ed.hint() == "*DEL #OK"
    ed.keys("555")                              # L
    assert ed.row() == "NAM  L_" and ed.hint() == "5:JKL5 *DEL #OK"
    ed.keys("999")                              # Y
    ed.keys("66")                               # N
    ed.keys("3")                                # D
    ed.keys("3", gap=1200)                      # E: the same key after a second starts a new letter
    ed.keys("3")                                # ... and a quick second press moves it on
    ed.keys("66")                               # N
    assert ed.e.r.name == b"LYNDEN"
    ed.keys("0")                                # a space, then H after the second
    ed.keys("44", gap=200)
    assert ed.e.r.name == b"LYNDEN H"
    ed.keys("4", gap=1200)                      # the same key after a second: a new letter
    assert ed.e.r.name == b"LYNDEN HG"
    ed.keys("U")                                # UP steps the last letter
    assert ed.e.r.name == b"LYNDEN HH"
    ed.keys("*")
    assert ed.e.r.name == b"LYNDEN H"
    ed.keys("#")
    assert ed.row() == "NAM  LYNDEN H"


def test_a_name_fills_at_sixteen(S):
    ed = Ed(S)
    ed.to(SF_NAME)
    ed.keys("#")
    ed.keys("2" * 17, gap=1100)
    assert ed.e.r.name == b"A" * 16 and ed.hint() == "FULL"


def test_a_call_keeps_only_what_a_call_holds(S):
    ed = Ed(S)
    ed.to(SF_CALL)
    ed.keys("#")
    ed.keys("0")                                # key 0 has a space and 0: a call takes the 0
    assert ed.e.r.call == b"0"
    ed.keys("*111")                             # 1 / - . : a call cycles 1 and / only
    assert ed.e.r.call == b"1" and ed.hint() == "1:1/ *DEL #OK"
    ed.keys("*9")
    ed.keys("D")                                # DOWN from W: V (UP/DOWN skip a space too)
    assert ed.e.r.call == b"V"
    ed.keys("U" * 30)                           # through Z, the digits, / and round
    assert ed.e.r.call[0:1] in b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/"


def test_tones_by_up_down_and_by_digits(S):
    ed = Ed(S)
    ed.to(SF_RXT)
    ed.keys("#U")
    assert ed.e.r.rx_tone == 670 and ed.row() == "RXT  67.0_"
    ed.keys("D")
    assert ed.e.r.rx_tone == 0
    ed.keys("D")                                # below none: the last inverted DCS code
    assert ed.e.r.rx_tone == 0xC000 | 0o754 and ed.row() == "RXT  D754I_"
    assert ed.hint() == "UP/DN 4=TXT *OFF #OK"
    ed.keys("103")                              # digits pick the first CTCSS tone they start
    assert ed.e.r.rx_tone == 1035 and ed.hint() == "DIGITS *DEL #OK"
    ed.keys("*")                                # a digit off: the first tone "10" starts
    assert ed.e.r.rx_tone == 1000 and ed.row() == "RXT  100.0_"
    ed.keys("**")                               # the digits gone, the tone stays
    assert ed.e.r.rx_tone == 1000 and ed.hint() == "UP/DN 4=TXT *OFF #OK"
    ed.keys("*")                                # then * takes the tone off
    assert ed.e.r.rx_tone == 0
    ed.keys("3")
    assert ed.e.r.rx_tone == 0 and ed.hint() == "NO SUCH TONE"
    ed.keys("#D#U")                             # the TX tone apart from the RX tone
    assert ed.e.f == SF_TXT and ed.e.r.tx_tone == 670 and ed.e.r.rx_tone == 0
    # every tone in the cycle shows and comes back
    seen = set()
    for _ in range(259):
        ed.keys("U")
        seen.add(ed.e.r.tx_tone)
        assert ed.row().startswith("TXT  ")
    assert len(seen) == 259 and ed.e.r.tx_tone == 670
    # the DCS codes as text, against the standard list written as text (a decimal slip in the
    # table's octal shows here)
    shown = []
    for _ in range(259):
        ed.keys("U")
        row = ed.row()
        if row.endswith("N_"):
            shown.append(row[6:9])
    assert shown == DCS_TEXT.split()


DCS_TEXT = """023 025 026 031 032 036 043 047 051 053 054 065 071 072 073 074 114 115 116 122 125 131 132 134
143 145 152 155 156 162 165 172 174 205 212 223 225 226 243 244 245 246 251 252 255 261 263 265 266 271
274 306 311 315 325 331 332 343 346 351 356 364 365 371 411 412 413 423 431 432 445 446 452 454 455 462
464 465 466 503 506 516 523 526 532 546 565 606 612 624 627 631 632 654 662 664 703 712 723 731 732 734
743 754"""


def test_tone_copy(S):
    ed = Ed(S)
    ed.to(SF_RXT)
    assert ed.hint() == "#EDIT 4=TXT 5SAVE"
    ed.keys("#U#D")                             # RXT 67.0, then down to TXT
    assert ed.e.f == SF_TXT and ed.hint() == "#EDIT 4=RXT 5SAVE"
    ed.keys("4")                                # in the list: RXT copied in
    assert ed.e.r.tx_tone == 670 and not ed.e.open
    ed.keys("#U")
    assert ed.e.r.tx_tone == 693 and ed.hint() == "UP/DN 4=RXT *OFF #OK"
    ed.keys("4")                                # first in the open field: copied again
    assert ed.e.r.tx_tone == 670
    ed.keys("146")                              # after a digit 4 is a digit: 146.2
    assert ed.e.r.tx_tone == 1462
    ed.keys("#U4")                              # and back the other way
    assert ed.e.f == SF_RXT and ed.e.r.rx_tone == 1462


def test_star_deletes_a_saved_frequencys_digits(S):
    ed = Ed(S, lynden())
    ed.keys("#")                                # RX opens with its digits
    assert ed.row() == "RX   442.025_" and ed.hint() == "DIGITS *DEL #OK"
    ed.keys("**")
    assert ed.row() == "RX   442.0_"
    ed.keys("3#")
    assert ed.e.r.rx_hz == 442_030_000
    ed.keys("#1468#")                           # a digit typed first starts over
    assert ed.e.r.rx_hz == 146_800_000


def test_offset_steps_and_a_custom_one(S):
    ed = Ed(S)
    ed.to(SF_OFF)
    assert ed.row() == "OFS  SIMPLEX"
    ed.keys("#U")
    assert ed.e.r.tx_off == 600_000 and ed.row() == "OFS  +0.600_" and ed.hint() == "UP/DN *CUSTOM #OK"
    ed.keys("D")
    assert ed.e.r.tx_off == 0
    ed.keys("DD")                               # down past simplex: -5 MHz, then +5
    assert ed.e.r.tx_off == 5_000_000
    ed.keys("D#")
    assert ed.e.r.tx_off == -1_600_000 and ed.row() == "OFS  -1.600"
    # * starts a custom offset in kHz (Jeff 2026-11-14); it keeps the sign it had
    ed.keys("#*")
    assert ed.e.r.tx_off == 0 and ed.row() == "OFS  -_" and ed.hint() == "KHZ *SIGN #OK"
    ed.keys("*")                                # nothing typed: * flips the sign
    assert ed.row() == "OFS  +_"
    ed.keys("7600")
    assert ed.e.r.tx_off == 7_600_000 and ed.row() == "OFS  +7600_" and ed.hint() == "KHZ *DEL #OK"
    ed.keys("*")
    assert ed.e.r.tx_off == 760_000
    ed.keys("0#")
    assert ed.e.r.tx_off == 7_600_000 and ed.row() == "OFS  +7.600"
    ed.keys("#U#")                              # reopened: UP/DOWN steps the common ones again
    assert ed.e.r.tx_off == 600_000
    ed.keys("#*12000#")                         # 12 MHz (23 cm)
    assert ed.e.r.tx_off == 12_000_000
    ed.keys("#*99999")                          # five digits at most
    assert ed.keys("9") == NONE and ed.e.r.tx_off == 99_999_000


def test_group_wide_and_dmr(S):
    ed = Ed(S, lynden())
    ed.to(SF_GRP)
    assert ed.row() == "GRP  B"
    ed.keys("#DD#")
    assert ed.row() == "GRP  ALL"
    ed.keys("D")
    assert ed.e.f == SF_WIDE and ed.row() == "WIDE NO" and ed.hint() == "#FLIP 5SAVE EXIT"
    ed.keys("#")                                # flips, no open field
    assert not ed.e.open and ed.e.r.flags & RT_F_WIDE and ed.row() == "WIDE YES"
    ed.keys("D#")
    assert ed.row() == "DMR  YES"


def test_exit_inside_a_field_puts_it_back(S):
    ed = Ed(S, lynden())
    ed.to(SF_NAME)
    ed.keys("#******2")
    assert ed.e.r.name == b"A"
    ed.keys("X")
    assert not ed.e.open and ed.e.r.name == b"LYNDEN" and not ed.changed()
    ed.to(SF_RX)                                # RX typed, then EXIT: the frequency as it was
    ed.keys("#14#")                             # 140 MHz kept
    ed.keys("#2X")
    assert ed.e.r.rx_hz == 140_000_000


def test_leaving_with_changes_asks_once(S):
    ed = Ed(S, lynden())
    assert ed.keys("X") == EXIT                 # nothing changed: out at once
    ed = Ed(S, lynden())
    ed.to(SF_OFF)
    ed.keys("#*#")
    assert ed.keys("X") == NONE and ed.hint() == "EXIT AGAIN: DISCARD"
    assert ed.keys("D") == NONE and ed.hint() == "#EDIT 4=TXT 5SAVE"    # another key: asks again
    assert ed.keys("M") == NONE and ed.hint() == "MENU AGAIN: DISCARD"
    assert ed.keys("X") == NONE                 # a different leaving key asks for itself
    assert ed.keys("X") == EXIT


def test_save_and_what_commit_marks(S):
    ed = Ed(S, lynden())
    assert ed.keys("5") == NONE and ed.hint() == "NO CHANGES"
    ed.to(SF_CALL)
    ed.keys("#*")
    ed.keys("8")
    ed.keys("#")
    assert ed.e.r.call == b"W7ABT"
    assert ed.keys("5") == SAVE
    S.sted_commit(ctypes.byref(ed.e))
    r = ed.e.r
    assert r.flags & RT_F_CALL_USER and not r.flags & RT_F_AUTO
    assert not r.flags & (RT_F_NAME_USER | RT_F_TONE_USER)                 # only what was typed
    assert r.call_flags == 0x04                 # no longer a CW decode
    written = Rec.from_buffer_copy(bytes(r))
    written.seq, written.crc = 9, 0x1234
    S.sted_saved(ctypes.byref(ed.e), ctypes.byref(written))
    assert not ed.changed() and ed.e.r.crc == 0x1234 and ed.keys("5") == NONE
    # a tone change marks the tone
    ed.to(SF_TXT)
    ed.keys("#U#5")
    S.sted_commit(ctypes.byref(ed.e))
    assert ed.e.r.flags & RT_F_TONE_USER


# ---- the key trial (c/k6/mod/edtrial.c) on an emulated M0 ----

def taps(keys, t0, gap=250_000, hold=100_000):
    """keys pressed one after another from t0 (us): [(from, to, key)]"""
    return [(t0 + i * gap, t0 + i * gap + hold, k) for i, k in enumerate(keys)]


def _font4():
    """FONT_4X6 as edtrial.c carries it: {glyph bytes: char}"""
    import re
    src = open(TRIAL).read()
    body = src[src.index("FONT4[95][3] = {"):]
    body = body[:body.index("};")]
    gl = re.findall(r"\{(0x[0-9a-f]{2}), (0x[0-9a-f]{2}), (0x[0-9a-f]{2})\}", body)
    assert len(gl) == 95
    out = {}
    for i, g in enumerate(gl):
        out.setdefault(bytes(int(x, 16) for x in g), chr(32 + i))
    return out


def rows(frame):
    """the trial's five small rows (lcd_wf) as text, '>' before the inverted (selected) one"""
    font, wf, out = _font4(), frame["wf"], []
    assert len(wf) == 30
    for r in range(5):
        px = wf[r * 6:r * 6 + 6]
        nib = [[(px[y][i >> 1] >> (0 if i & 1 else 4)) & 15 for y in range(6)] for i in range(32)]
        inv = all(n == 15 for n in nib[31])
        t = ""
        for cell in nib:
            if inv:
                cell = [15 - n for n in cell]
            g = bytes((cell[0] << 4 | cell[1], cell[2] << 4 | cell[3], cell[4] << 4 | cell[5]))
            t += font.get(g, "?")
        out.append((">" if inv else " ") + t.rstrip())
    return out


def trial(lib, keys, stations=()):
    if not (shutil.which("clang") and shutil.which("ld.lld")):
        pytest.skip("needs clang and ld.lld")
    from test_k6_i2s import START, run
    from modem import k6mod
    code, entry = k6mod.build(TRIAL)
    assert k6mod.source_level(TRIAL) == 6 and 16 + len(code) + 8 <= ARENA, len(code)
    t = Table(lib)
    t.mount()
    for rx, name, kw in stations:
        t.put(rx, name, **kw)
    mem = bytearray(b"\xff" * (0x200000 - BASE))
    mem[:16 * 4096] = t.mem()
    fl, lcd = {"mem": mem}, {}
    ret, said, _, _ = run(START, code=(code, entry), level=6, lcd=lcd, keypad=keys, flash=fl,
                          spare=ARENA - 24 - len(code), count=3_000_000_000)
    return ret, said, fl, lcd["frames"]


def test_trial_edits_a_saved_station_and_writes_nothing(lib):
    stations = [(442_025_000, b"LYNDEN", dict(call=b"W7ABC", tx_off=5_000_000, rx_tone=1035, tx_tone=1035)),
                (146_940_000, b"", dict(call=b"K7XYZ"))]
    # DOWN to 442.025 (the list is by frequency), 8 edit, DOWN x4 to NAME, # open, * delete,
    # 2 2 (B), # close, 5 save, EXIT leaves the editor, EXIT the module
    keys = taps("D8DDDD#*22#5XX", 300_000)
    ret, said, fl, frames = trial(lib, keys, stations)
    assert ret == 1, said
    assert said[0] == "ed n=2"
    assert "ed save id=1 rx=442025000 off=5000000 t=1035/1035" in said, said
    assert "ed save g=0 fl=0008 LYNDEB/W7ABC" in said, said
    assert said[-1] == "ed end saves=1"
    assert fl["progs"] == fl["erases"] == 0     # a trial: nothing written
    first = frames[0]["text"]
    assert first[0].startswith("STATIONS 2") and first[5].startswith("8EDIT 0NEW EXIT")
    # the rows in the small font (Jeff 2026-11-14), the selected one inverted
    assert rows(frames[0])[:3] == [">146.940   S  K7XYZ", " 442.025 +5.0 LYNDEN", " "]
    assert first[1].startswith("K7XYZ ")        # line 1: the selected station's tones and call
    assert any(f["text"][1].startswith("103.5 W7ABC ") for f in frames)
    assert any(f["text"][1].startswith("NAM  LYNDEB_") for f in frames)     # the field, big
    assert any(f["text"][5].startswith("TRIAL: NOT WRITTEN") for f in frames)
    assert any(f["text"][0].startswith("EDIT 442.025 #1 *") for f in frames)
    assert any(rows(f)[2] == ">NAM  LYNDEB_" for f in frames)
    # the list after the trial save shows the new name
    assert any(rows(f)[1] == ">442.025 +5.0 LYNDEB" for f in frames)


def test_trial_new_station_and_an_empty_table(lib):
    keys = taps("0146520#5XX", 300_000)
    ret, said, fl, frames = trial(lib, keys)
    assert ret == 1 and said[0] == "ed n=0", said
    assert rows(frames[0])[0] == " NO SAVED STATIONS"
    assert "ed save id=1 rx=146520000 off=0 t=0/0" in said, said
    assert fl["progs"] == fl["erases"] == 0
