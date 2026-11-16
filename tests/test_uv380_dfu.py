"""Hands-free flash mode for the UV-380 (docs/uv380-dfu.md): the dfuboot and bootinfo RAM modules
(firmware/uv380-openrtx/mod) and tools/uv380_dfu.py.

- dfuboot's erase routine is self-contained (no calls out of the module: OpenRTX's code is partly in
  the sector it erases) and gives only the one sector-3 erase command;
- both modules build for the OpenRTX table (QEMU's netduinoplus2 has no flash interface or
  system memory to read, so they run only on the radio: tools/uv380_dfu.py info / enter --dry);
- the tool answers dfuboot's nonce ("erase N" or "dry N"), waits for 0483:df11 in sysfs, and its
  flash refuses a wrong sha256 and more than one DFU device. The radio side runs over a pty.

No key is needed: nothing here signs a module.
SPDX-License-Identifier: MIT"""
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "firmware", "uv380-openrtx", "mod")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modem import uv380mod  # noqa: E402
import uv380_dfu as dfu  # noqa: E402
import uv380_i2sprobe as probe  # noqa: E402

TOOLS = all(shutil.which(t) for t in ("clang", "ld.lld", "llvm-objcopy", "llvm-nm", "llvm-objdump"))
tools = pytest.mark.skipif(not TOOLS, reason="needs clang/lld/llvm")


@tools
def test_erase_routine_stays_in_the_module(tmp_path):
    obj = tmp_path / "dfuboot.o"
    subprocess.run(["clang", *uv380mod.CFLAGS, "-c", os.path.join(MOD, "dfuboot.c"), "-o", str(obj)],
                   check=True)
    dis = subprocess.run(["llvm-objdump", "-d", "--no-show-raw-insn", str(obj)], check=True,
                         capture_output=True, text=True).stdout
    body = dis.split("<erase_and_reset>:", 1)[1].split("\n\n", 1)[0]
    insns = [ln.split(None, 1)[1] for ln in body.strip().splitlines() if ":" in ln and len(ln.split(None, 1)) > 1]
    assert not [i for i in insns if re.match(r"(bl|blx|bx)\b", i)], body
    assert "cpsid\ti" in body.replace(" ", "\t") or "cpsid i" in body
    # CR = PSIZE x32 | SNB 3 | SER, then the same with STRT; nothing else is ever written to CR
    # but LOCK
    assert re.search(r"#0x21a\b", body), body
    relocs = subprocess.run(["llvm-objdump", "-r", str(obj)], check=True, capture_output=True,
                            text=True).stdout
    assert "[.text.erase_and_reset]" not in relocs


@tools
def test_modules_build_for_the_openrtx_table():
    for m in ("bootinfo.c", "dfuboot.c"):
        path = os.path.join(MOD, m)
        assert uv380mod.source_table(path) == "openrtx"
        code, entry = uv380mod.build(path)
        assert 0 < len(code) < 4096


def _fake_radio(m, script, sent):
    """answer upload lines with ok; on "mod run" play script(write, readline)"""
    buf = [b""]

    def readline():
        while b"\n" not in buf[0]:
            b = os.read(m, 512)
            if not b:
                return None
            buf[0] += b
        ln, buf[0] = buf[0].split(b"\n", 1)
        return ln.decode()

    def w(s):
        os.write(m, (s + "\r\n").encode())

    while True:
        ln = readline()
        if ln is None:
            return
        sent.append(ln)
        if ln.startswith("mod hex"):
            w(f"ok mod hex {ln.split()[2]} 0")
        elif ln.startswith("mod begin"):
            w(f"ok {ln}")
        elif ln == "mod run":
            script(w, readline)
            return


def _handshake(script, dry):
    import pty
    m, s = pty.openpty()
    sent, got = [], []
    t = threading.Thread(target=_fake_radio, args=(m, script, sent))
    t.start()
    r = dfu.handshake(os.ttyname(s), bytes(200), dry=dry, out=got.append, timeout=5)
    t.join(5)
    return r, sent, got


def _armed(answers):
    def script(w, readline):
        w("mod say dfuboot armed nonce=0badf00d rdp=1 boot pc=08000a01")
        ans = readline()
        answers.append(ans)
        if ans == "erase 0badf00d":
            w("mod say dfuboot erasing sector 3, then reset into TYT's bootloader")
        elif ans == "dry 0badf00d":
            w("mod say dfuboot dry: would erase sector 3 (cr=0000021a) at 0800c000, then aircr=05fa0004")
            w("ok mod run ret=68 ms=12")
        else:
            w("mod say dfuboot wrong answer: nothing written")
            w("ok mod run ret=87 ms=12")
    return script


def test_tool_answers_the_nonce():
    answers = []
    r, sent, _ = _handshake(_armed(answers), dry=False)
    assert r == "erasing" and answers == ["erase 0badf00d"]
    assert sent[-1] == "mod run" and sent[0] == "mod begin 200"
    answers = []
    r, _, _ = _handshake(_armed(answers), dry=True)
    assert r == "dry" and answers == ["dry 0badf00d"]


def test_tool_reports_a_refusal():
    def script(w, readline):
        w("mod say dfuboot refused: app vectors (boot ...)")
        w("ok mod run ret=82 ms=1")
    r, _, _ = _handshake(script, dry=False)
    assert r.startswith("refused: app vectors")


def _usb(root, name, vid, pid):
    d = root / name
    d.mkdir()
    (d / "idVendor").write_text(vid + "\n")
    (d / "idProduct").write_text(pid + "\n")


def test_dfu_devices_reads_sysfs(tmp_path):
    _usb(tmp_path, "1-1", "0483", "5740")
    assert dfu.dfu_devices(str(tmp_path)) == []
    _usb(tmp_path, "1-2", "0483", "df11")
    assert dfu.dfu_devices(str(tmp_path)) == ["1-2"]


def test_enter_waits_for_the_bootloader(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "module_image", lambda path, key: bytes(10))

    def fake(dev, img, dry, out, log):
        threading.Timer(0.5, _usb, (tmp_path, "1-1", "0483", "df11")).start()
        return "erasing"
    monkeypatch.setattr(dfu, "handshake", fake)
    got = []
    assert dfu.enter("/dev/null", "k", out=got.append, sysfs=str(tmp_path), wait=5) == 0
    assert got[-1].startswith("in DFU after") and got[-1].endswith("1-1")
    # already there: nothing sent
    monkeypatch.setattr(dfu, "handshake", lambda *a: pytest.fail("sent a module while in DFU"))
    assert dfu.enter("/dev/null", "k", out=got.append, sysfs=str(tmp_path)) == 0


def test_enter_stops_when_not_erased(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "module_image", lambda path, key: bytes(10))
    monkeypatch.setattr(dfu, "handshake", lambda *a: "cancelled: nothing written")
    got = []
    assert dfu.enter("/dev/null", "k", out=got.append, sysfs=str(tmp_path), wait=1) == 1
    assert "nothing was written" in got[-1]


def test_flash_checks_sha_and_one_device(tmp_path, monkeypatch):
    img = tmp_path / "x_wrap.bin"
    img.write_bytes(b"image")
    sysfs = tmp_path / "usb"
    sysfs.mkdir()
    ran, got = [], []
    run = lambda argv: ran.append(argv) or subprocess.CompletedProcess(argv, 0)  # noqa: E731
    assert dfu.flash("/dev/null", str(img), "00" * 32, "rt", "k", out=got.append, sysfs=str(sysfs), run=run) == 1
    assert ran == [] and got[-1].startswith("refused:")
    _usb(sysfs, "1-1", "0483", "df11")
    _usb(sysfs, "1-2", "0483", "df11")
    assert dfu.flash("/dev/null", str(img), dfu.sha256(str(img)), "rt", "k", out=got.append,
                     sysfs=str(sysfs), run=run) == 1
    assert ran == [] and "exactly one" in got[-1]


def test_flash_runs_radio_tool_then_leave(tmp_path, monkeypatch):
    img = tmp_path / "x_wrap.bin"
    img.write_bytes(b"image")
    sysfs = tmp_path / "usb"
    sysfs.mkdir()
    _usb(sysfs, "1-1", "0483", "df11")
    acm = tmp_path / "ttyACM0"
    ran, got = [], []

    def run(argv):
        ran.append(argv)
        if argv[-1] == "usb-leave":
            shutil.rmtree(sysfs / "1-1")
            acm.write_text("")
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(probe, "command", lambda dev, cmds, log=None, out=print, **kw: 0)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    r = dfu.flash(str(acm), str(img), dfu.sha256(str(img)), "rt", "k", out=got.append, sysfs=str(sysfs),
                  run=run, wait_acm=2)
    assert r == 0, got
    assert ran == [["rt", "-d", "0", "-f", "-i", str(img)], ["rt", "usb-leave"]]
