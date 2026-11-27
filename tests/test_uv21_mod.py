"""UV-21 Pro RAM modules over the cable (radios/uv21/fw/src/umod.rs, modem/uv21mod.py), uploaded
into the emulated radio of sim/uv21/board.py while SURF runs (the operator, 2026-11-24: register
trials from RAM modules, automated). What must hold: an upload line by line, each answered; a
bad line changes nothing; a module runs once, its lines and return value on the console; the
register sweep reads a known signal and the noise beside it and puts the register back; and a
module's register writes pass the receive-only filter like any other."""
import os
import re
import sys

import pytest

pytest.importorskip("unicorn")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modem import uv21mod  # noqa: E402
from sim.uv21.board import Station, UV21Board, build  # noqa: E402
from test_uv21_surf import until, wait  # noqa: E402

MOD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "radios", "uv21", "mod")
PA_OFF = 0x0022


@pytest.fixture(scope="module")
def built():
    return build()


def radio(built, band=None):
    b = UV21Board(*built, band=band or [Station("a", 439_500_000, -70)])
    b.run(until="boot done", limit=300_000_000)
    wait(b, 1)
    return b


def send(b, line, want=r"um (ok|bad|run)[^\r\n]*", seconds=2):
    b.serial_in((line + "\n").encode())
    m, out = until(b, want, seconds)
    assert m, (line[:20], out[-300:])
    return m.group(0), out


def upload(b, img):
    for ln in uv21mod.lines(img)[:-1]:
        got, _ = send(b, ln)
        assert got.startswith("um ok"), (ln[:20], got)
    got, out = send(b, uv21mod.lines(img)[-1])
    assert got == "um run", got
    m = re.search(r"um end ([0-9a-f]{8})", out)
    if not m:
        m, more = until(b, r"um end ([0-9a-f]{8})", 120)
        out += more
    assert m, out[-500:]
    return int(m.group(1), 16), out


def test_hello_runs_once_and_says_its_line(built):
    b = radio(built)
    got, _ = send(b, "um ?", r"um abi=\S+ room=\S+")
    assert got == "um abi=2 room=3000"
    ret, out = upload(b, uv21mod.image(*uv21mod.build(os.path.join(MOD, "hello.c"))))
    assert ret == 0x4829, hex(ret)                                     # the BK4829's ID
    assert re.search(r"hello uv21 rf=4829 ram=2[0-9a-f]{3}", out), out[-300:]
    stk = re.search(r"um stk=([0-9a-f]{4})\r\num end", out)               # the stack never touched,
    assert stk and int(stk.group(1), 16) >= 1024, out[-300:]            # before `um end`'s line
    assert re.search(r"um end 00004829\r\n.*surf ram need=", out, re.S), out[-300:]   # SURF again
    got, _ = send(b, uv21mod.lines(b"x")[-1])                           # (once: no image now)
    assert got.startswith("um bad short"), got
    m, out = until(b, r"alive surf s=[0-9a-f]{4} stk=([0-9a-f]{4})", 12)   # SURF goes on, afresh
    assert m and int(m.group(1), 16) >= 1024, out[-200:]


def test_an_upload_that_goes_quiet_gives_surf_back(built):
    b = radio(built)
    send(b, uv21mod.lines(uv21mod.image(*uv21mod.build(os.path.join(MOD, "hello.c"))))[0])
    m, out = until(b, r"um idle", 15)                                   # 10 s with nothing on the line
    assert m, out[-300:]
    m, out = until(b, r"alive surf", 12)
    assert m, out[-200:]
    got, _ = send(b, uv21mod.lines(b"x")[-1])                           # (the upload is gone)
    assert got.startswith("um bad short"), got


def test_a_bad_line_changes_nothing(built):
    b = radio(built)
    img = uv21mod.image(*uv21mod.build(os.path.join(MOD, "hello.c")))
    ups = uv21mod.lines(img)
    send(b, ups[0])
    w = ups[1].rsplit(" ", 1)
    got, _ = send(b, w[0] + " " + format(int(w[1], 16) ^ 1, "04x"))     # a wrong CRC-16
    assert got.startswith("um bad crc"), got
    got, _ = send(b, ups[2])                                            # out of order
    assert got.startswith("um bad off"), got
    got, _ = send(b, ups[-1])                                           # run before it's whole
    assert got.startswith("um bad short"), got
    for ln in ups[1:-1]:
        assert send(b, ln)[0].startswith("um ok")
    got, _ = send(b, ups[-1][:-1] + ("0" if ups[-1][-1] != "0" else "1"))   # a wrong CRC-32
    assert got.startswith("um bad crc32"), got
    got, out = send(b, ups[-1])
    assert got == "um run"
    m = re.search(r"um end ([0-9a-f]{8})", out) or until(b, r"um end ([0-9a-f]{8})", 10)[0]
    assert m and int(m.group(1), 16) == 0x4829, out[-300:]


def test_the_register_sweep_reads_signal_and_noise_and_puts_the_register_back(built):
    b = radio(built)
    old = b.reg_value(0x1F)
    assert old == 0x1657                                                # the stock init's
    img = uv21mod.image(*uv21mod.build(os.path.join(MOD, "regsweep.c"),
                                       ["SW_REG=0x1F", "SW_VALS=0x1657,0xC65A", "SW_HZ=439500000"]))
    ret, out = upload(b, img)
    assert ret == 2
    rows = re.findall(r"sw reg=1f val=([0-9a-f]{4}) sig=(-?[\d.]+) noise=(-?[\d.]+) snr=(-?[\d.]+)", out)
    assert [r[0] for r in rows] == ["1657", "c65a"], out[-500:]
    for _, sig, noise, snr in rows:
        assert abs(float(sig) + 70) <= 2, sig                           # the -70 dBm carrier
        assert float(noise) < -110, noise
        assert float(snr) > 40, snr
    assert "sw done" in out
    assert b.reg_value(0x1F) == old


def test_a_modules_writes_stay_receive_only(built):
    # a sweep of the PA register (REG_36): the firmware's filter sends only the off value
    b = radio(built)
    n = len(b.tx_watch)
    img = uv21mod.image(*uv21mod.build(os.path.join(MOD, "regsweep.c"),
                                       ["SW_REG=0x36", "SW_VALS=0xFFFF,0x7FBF", "SW_HZ=439500000", "SW_N=2"]))
    upload(b, img)
    pa = [v for r, v in b.tx_watch[n:] if r == 0x36]
    assert pa and set(pa) == {PA_OFF}, [hex(v) for v in pa]


def test_bad_settings_are_refused_before_building():
    with pytest.raises(ValueError):
        uv21mod.build(os.path.join(MOD, "regsweep.c"), ["SW_REG=0x1F; rm"])


def test_shared_radio_api_prefix(built, tmp_path):
    """um_api_t starts with c/common/radio_api.h's prefix (its layout checked at compile time), so
    portable code taking const radio_api_t * works unchanged on UV-21 (radios/uv21/mod/apiprobe.c):
    abi=2, level=1, ticks and ms, radio_rssi2, and rx_hz the K6's way: where the radio listens
    once the module returns (SURF's centre), not moved by the module's own tune."""
    src = tmp_path / "layout.c"
    src.write_text(
        '#include "uv21_mod.h"\n'
        '#define CHK(f, at) \\\n'
        '  _Static_assert(__builtin_offsetof(um_api_t, f) == __builtin_offsetof(radio_api_t, f), #f); \\\n'
        '  _Static_assert(__builtin_offsetof(um_api_t, f) == at, "ABI 2 " #f)\n'
        'CHK(abi, 0); CHK(level, 2); CHK(say, 4); CHK(ticks, 8); CHK(fd_read, 12);\n'
        'CHK(fd_write, 16); CHK(delay_us, 20); CHK(tune, 24); CHK(rx_hz, 28); CHK(ms, 32);\n'
        '_Static_assert(__builtin_offsetof(um_api_t, front_end) == sizeof(radio_api_t), "front_end");\n'
        'int um_main(const um_api_t *api, uint8_t *ram, uint32_t ram_len) { return 0; }\n'
    )
    uv21mod.build(str(src))
    b = radio(built)
    ret, out = upload(b, uv21mod.image(*uv21mod.build(os.path.join(MOD, "apiprobe.c"))))
    m = re.search(r"api 2/1 hz=439500000 moved=0 t16=\d+ ms=\d+ rssi=(-?[\d.]+)", out)
    assert m, out[-300:]
    assert ret == 0x55, (hex(ret), m.group(0))
    assert abs(float(m.group(1)) + 70) <= 2, m.group(0)
