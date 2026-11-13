"""RAM modules for the UV-380's OpenRTX image (firmware/uv380-openrtx/ortx_mod.h): the loader
(ortx_mod.c with c/common/mod_core.c) on an emulated STM32F405 (QEMU netduinoplus2,
make -C radios/uv380 omqemu), fed the same "mod ..." lines tools/uv380_i2sprobe.py sends over USB
serial. A module built and signed by modem.uv380mod must load, run from the heap arena against the
OpenRTX table and say what it should; Ctrl-C must end it; a wrong key, the bare-metal port's
magic, a newer API level, a replay, a short or out-of-order upload and an unkeyed image must be
refused. The tool's own side (the lines it sends, waiting, Ctrl-C) runs over a pty.

The tests use a throwaway key, never ~/.config/sotto/k6-mod.key.
Needs arm-none-eabi-gcc, qemu-system-arm, clang, ld.lld and llvm-objcopy.
SPDX-License-Identifier: MIT"""
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELF = os.path.join(ROOT, "radios", "uv380", "build", "ortxmod-qemu.elf")
MOD = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod")
QEMU = shutil.which("qemu-system-arm")
TOOLS = all(shutil.which(t) for t in ("clang", "ld.lld", "llvm-objcopy", "llvm-nm"))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
from modem import k6mod, uv380mod  # noqa: E402
import uv380_i2sprobe as tool  # noqa: E402

KEY = secrets.token_bytes(16)
emu = pytest.mark.skipif(not (QEMU and TOOLS and shutil.which("arm-none-eabi-gcc")),
                         reason="needs qemu-system-arm, arm-none-eabi-gcc and clang/lld/llvm")


@pytest.fixture(scope="module")
def elf():
    subprocess.run(["make", "-C", os.path.join(ROOT, "radios", "uv380"), "omqemu"], check=True,
                   capture_output=True)
    return open(ELF, "rb").read()


@pytest.fixture(scope="module")
def hello():
    return uv380mod.build(os.path.join(MOD, "hello.c"))


def run(elf, lines, key=KEY, usb=b""):
    """the loader on QEMU, keyed with key (None: unkeyed), fed lines: its output lines"""
    if key is not None:
        elf = k6mod.key_image(elf, key)
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "fw.elf"), "wb").write(elf)
        open(os.path.join(d, "in.txt"), "w").write("\n".join(lines) + "\n")
        if usb:
            open(os.path.join(d, "usb.bin"), "wb").write(usb)
        r = subprocess.run([QEMU, "-M", "netduinoplus2", "-nographic", "-monitor", "none",
                            "-serial", "none", "-semihosting-config", "enable=on,target=native",
                            "-kernel", "fw.elf"], cwd=d, capture_output=True, text=True, timeout=120)
    out = (r.stdout + r.stderr).splitlines()
    assert r.returncode == 0 and "om_qemu: done" in out, "\n".join(out)
    return out


def img(code_entry, serial=1, key=KEY, **kw):
    code, entry = code_entry
    return k6mod.image(code, entry, serial, key, **{"magic": b"U3R", **kw})


def test_table_is_picked_from_the_source():
    assert uv380mod.source_table(os.path.join(MOD, "hello.c")) == "openrtx"
    assert uv380mod.source_table(os.path.join(ROOT, "radios", "uv380", "mod", "hello.c")) == "uv380"


def test_images_name_their_table(hello):
    code, entry = hello
    assert uv380mod.image(code, entry, 1, KEY, table="openrtx")[:4] == b"U3R\x01"
    assert uv380mod.image(code, entry, 1, KEY)[:4] == b"U3M\x01"
    with pytest.raises(ValueError):
        uv380mod.image(code, entry, 1, KEY, level=2, table="openrtx")


def test_mod_lines():
    data = bytes(range(256)) + b"xyz"
    lines = tool.mod_lines(data)
    assert lines[0] == "mod begin 259" and lines[-1] == "mod run"
    got = b""
    for ln in lines[1:-1]:
        _, _, off, hx = ln.split()
        assert int(off, 16) == len(got) and len(bytes.fromhex(hx)) <= tool.HEX_MAX
        got += bytes.fromhex(hx)
    assert got == data


@emu
def test_hello_runs(elf, hello):
    out = run(elf, ["mod info"] + tool.mod_lines(img(hello)) + ["mod info"])
    assert re.fullmatch(r"ok mod info magic=U3R level=1 arena=2[0-9a-f]{7} size=16384 loaded=0/0 "
                        r"key=yes serial=0", out[0]), out
    say = [ln[8:] for ln in out if ln.startswith("mod say ")]
    assert re.fullmatch(r"hello world from RAM at 0x2[0-9a-f]{7}", say[0])
    assert say[1] == "squares 0x9f31fc58 div 142857 float 2525"
    m = re.fullmatch(r"scratch (\d+) ok", say[2])
    assert m and 15000 < int(m.group(1)) < 16384
    assert re.fullmatch(r"c6000 E0=ba rssi -77 dBm after \d+ ms", say[3])
    assert any(re.fullmatch(r"ok mod run ret=85 ms=\d+", ln) for ln in out), out
    assert out[-2].endswith("serial=1")
    assert sum(ln.startswith("ok mod hex ") for ln in out) == (len(img(hello)) + 127) // 128


@emu
def test_ctrl_c_ends_it(elf, hello):
    out = run(elf, tool.mod_lines(img(hello)), usb=b"\x03")
    assert any(re.fullmatch(r"ok mod run ret=92 ms=\d+ \(Ctrl-C\)", ln) for ln in out), out
    assert len([ln for ln in out if ln.startswith("mod say ")]) == 3


@emu
def test_c6dump(elf):
    out = run(elf, tool.mod_lines(img(uv380mod.build(os.path.join(MOD, "c6dump.c")))))
    rows = [ln for ln in out if ln.startswith("mod say c6 ")]
    assert len(rows) == 16
    assert rows[0] == "mod say c6 00: " + " ".join(f"{r ^ 0x5a:02x}" for r in range(16))
    assert "mod say rssi -77 dBm" in out


def refused(out):
    m = [re.match(r"err mod run refused r=(-\d+)", ln) for ln in out]
    return [int(x.group(1)) for x in m if x]


@emu
def test_refusals(elf, hello):
    code, entry = hello
    assert refused(run(elf, tool.mod_lines(img(hello, key=secrets.token_bytes(16))))) == [-4]
    assert refused(run(elf, tool.mod_lines(img(hello, magic=b"U3M")))) == [-3]
    assert refused(run(elf, tool.mod_lines(img(hello, level=2)))) == [-3]
    bad = bytearray(img(hello))
    bad[40] ^= 1
    assert refused(run(elf, tool.mod_lines(bytes(bad)))) == [-4]
    # the same serial twice in one boot: a replay
    out = run(elf, tool.mod_lines(img(hello, serial=7)) + tool.mod_lines(img(hello, serial=7)))
    assert refused(out) == [-6]
    # unkeyed: everything refused
    out = run(elf, ["mod info"] + tool.mod_lines(img(hello)), key=None)
    assert "key=no" in out[0] and refused(out) == [-5]


@emu
def test_upload_errors(elf, hello):
    lines = tool.mod_lines(img(hello))
    out = run(elf, lines[:2] + ["mod run"])
    assert any(re.match(r"err mod run: 128 of \d+ bytes loaded", ln) for ln in out), out
    out = run(elf, [lines[0], lines[2]])
    assert any(ln.startswith("err mod hex 80: send the image in order") for ln in out), out
    out = run(elf, ["mod begin 99999", "mod begin 4", "mod hex 0 zz", "mod frob"])
    assert [ln.split(":")[0] for ln in out if ln.startswith("err")] == [
        "err mod begin 99999", "err mod begin 4", "err mod hex 0", "err mod frob"]
    # a line past the declared length
    out = run(elf, ["mod begin 30", "mod hex 0 " + "00" * 31])
    assert any(ln.startswith("err mod hex 0: past the image") for ln in out), out


def test_tool_sends_a_module_over_a_pty():
    """the tool's mod: begin, hex lines, run; stops at the first err; Ctrl-C a module that runs on"""
    import pty
    import threading
    m, s = pty.openpty()
    got, sent = [], []

    def radio():
        buf = b""
        while True:
            while b"\n" not in buf:
                b = os.read(m, 512)
                if not b:
                    return
                buf += b
            ln, buf = buf.split(b"\n", 1)
            ln = ln.decode()
            sent.append(ln)
            if ln.startswith("mod hex"):
                os.write(m, f"ok mod hex {ln.split()[2]} 0\r\n".encode())
            elif ln.startswith("mod begin"):
                os.write(m, f"ok {ln}\r\n".encode())
            elif ln == "mod run":
                os.write(m, b"mod say spinning\r\n")
                while b"\x03" not in buf:
                    buf += os.read(m, 512)
                buf = buf.replace(b"\x03", b"")
                os.write(m, b"ok mod run ret=92 ms=1500 (Ctrl-C)\r\n")
                return

    t = threading.Thread(target=radio)
    t.start()
    data = bytes(300)
    bad = tool.command(os.ttyname(s), tool.mod_lines(data), out=got.append, timeout=5,
                       pass_timeout=1.0, stop_on_err=True)
    t.join(10)
    assert bad == 0
    assert sent == tool.mod_lines(data)
    assert got[-3:] == ["mod say spinning", "ctrl-c: mod run still running after 1 s",
                        "ok mod run ret=92 ms=1500 (Ctrl-C)"]


@emu
def test_c6sweep_drives_the_probe(elf):
    """each bit of each register flipped, a capture after each, the register put back"""
    out = run(elf, tool.mod_lines(img(uv380mod.build(os.path.join(MOD, "c6sweep.c")))))
    probe = [ln[6:] for ln in out if ln.startswith("probe ")]
    sweeps = [ln for ln in out if ln.startswith("mod say sweep ")]
    assert len(sweeps) == 13 * 8 and any(re.fullmatch(r"ok mod run ret=104 ms=\d+", ln) for ln in out), out
    assert probe[0] == probe[-1] == "cap SPI3 s S16 8000"
    assert len(probe) == 2 + 2 * 13 * 8
    # register 26 reads 26 ^ 5a = 7c on the fake: bit 0 flipped first
    assert "mod say sweep 26 7c->7d" in out
    i = probe.index("c6w 26 7d")
    assert probe[i + 1] == "cap SPI3 s S16 8000"


@emu
def test_c6codec_checks_after_each_flip(elf):
    out = run(elf, tool.mod_lines(img(uv380mod.build(os.path.join(MOD, "c6codec.c")))))
    assert "mod say c6codec reg00=5a" in out
    assert len([ln for ln in out if ln.startswith("mod say sweep e")]) == 6 * 8
    assert any(re.fullmatch(r"ok mod run ret=48 ms=\d+", ln) for ln in out), out
    probe = [ln[6:] for ln in out if ln.startswith("probe ")]
    assert probe.count("raw a 32") == 2 + 48


def test_mod_commands_end_on_their_own_line():
    assert "ok c6 06=21".startswith(tool.ends("mod run")) is False
    assert "ok mod run ret=0 ms=3".startswith(tool.ends("mod run"))
    assert "err mod run refused r=-6: x".startswith(tool.ends("mod run"))
    assert "ok mod hex 80 128".startswith(tool.ends("mod hex 80 00"))
    assert "ok buf".startswith(tool.ends("buf"))


def test_serials_always_grow(tmp_path):
    f = str(tmp_path / "serial")
    assert tool.next_serial(f, now=1000) == 1000
    assert tool.next_serial(f, now=1000) == 1001
    assert tool.next_serial(f, now=999) == 1002
    assert tool.next_serial(f, now=5000) == 5000


def test_c6listen_builds_for_the_openrtx_table():
    """it pokes GPIOD, so it can't run on the emulator's fake; it must build position-independent"""
    code, entry = uv380mod.build(os.path.join(MOD, "c6listen.c"))
    assert entry == 0 and len(code) < 2048
    assert uv380mod.source_table(os.path.join(MOD, "c6listen.c")) == "openrtx"
