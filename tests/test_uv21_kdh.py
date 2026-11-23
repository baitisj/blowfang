"""tools/uv21_kdh.py: the UV-21 Pro's KDH bootloader (against a model of the UV-5RM bootloader's
command loop, as disassembled for radios/uv21/README.md) and the dumper's frames."""
import argparse
import hashlib
import json
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import uv21_kdh as kdh  # noqa: E402


def unxor_like_the_bootloader(off, count, page):
    """the bootloader's write path: decrypt 0x800 <= off < count * 0x400 - 0x800"""
    i = off // 0x400
    if 0x800 <= off < count * 0x400 - 0x800 and i % 3:
        return kdh.kdh_xor(page, b"KDHT" if i % 3 == 1 else b"RBGI")
    return page


class FakeBootloader:
    """the 5RM bootloader's loop: 01 handshake; 04 region 1 + count (resets the offset); 03 one
    1 KB package whose number * 0x400 must equal the offset, below 0xF000, erasing and writing
    that page only; 05 region 2; 45 end. Replies AA cmd args len16 crc16 EF."""

    def __init__(self, app=None, k6=False, fail_at=None, boot_after=None):
        self.app = bytearray(app or hashlib.sha256(b"stock").digest() * (0xF000 // 32))
        self.stock = bytes(self.app)
        self.state, self.region, self.off, self.count = 0, None, 0, 0
        self.sent, self.out, self.cmds, self.fail_at = [], bytearray(), [], fail_at
        if k6:
            self.out += b"K6 CFG build=abc\r\n"
        self.ended = False
        self.boot_after = boot_after                # command 42s before the "power-on"

    def reply(self, cmd, code):
        body = bytes([cmd, code, 0, 0])
        self.out += b"\xAA" + body + struct.pack(">H", kdh.crc16_xmodem(body)) + b"\xEF"

    def write(self, b):
        self.sent.append(bytes(b))
        assert b[0] == 0xAA and b[-1] == 0xEF
        cmd, args, n = b[1], b[2], struct.unpack(">H", b[3:5])[0]
        data = b[5:5 + n]
        assert struct.unpack(">H", b[5 + n:7 + n])[0] == kdh.crc16_xmodem(b[1:5 + n])
        self.cmds.append(cmd)
        if cmd == 0x42:
            if self.boot_after is None or self.boot_after > 0:
                if self.boot_after:
                    self.boot_after -= 1
                return                              # the stock app ignores it
            self.state = 0
            return self.reply(cmd, 6)
        if cmd == 0x01:
            self.state = 1
            return self.reply(cmd, 6)
        if cmd == 0x04 and self.state == 1:
            self.count, self.off, self.state, self.region = data[0] if data[0] >= 2 else 5, 0, 2, 1
            return self.reply(cmd, 6)
        if cmd == 0x05 and self.state == 2:
            self.region, self.off = 2, 0
            return self.reply(cmd, 6)
        if cmd == 0x03 and self.state == 2:
            if n != 0x400:
                return self.reply(3, 0xE1)
            if args * 0x400 != self.off or self.off >= 0xF000:
                return self.reply(cmd, 0xE2)
            if self.fail_at == args:
                return self.reply(cmd, 0xE3)
            assert self.region == 1, "region 2 must never be written"
            self.app[self.off:self.off + 0x400] = unxor_like_the_bootloader(self.off, self.count, data)
            self.off += 0x400
            return self.reply(cmd, 6)
        if cmd == 0x45:
            self.ended = True
            return self.reply(cmd, 6)
        self.reply(cmd, 0xE5)

    def read(self, n, timeout=None, gap=None):
        r, self.out = bytes(self.out[:n]), self.out[n:]
        return r

    def drain(self, seconds):
        return self.read(1 << 16)

    def close(self):
        pass


def app_image(n_bytes, seed=b"blowfang"):
    body = bytearray((hashlib.sha256(seed).digest() * (n_bytes // 32 + 1))[:n_bytes])
    body[0:8] = struct.pack("<II", 0x20004000, 0x08001101)
    return bytes(body)


def ns(tmp_path, **kw):
    d = dict(port="fake", out=str(tmp_path / "run"), listen_s=0.0, wait_boot_s=0.0)
    d.update(kw)
    return argparse.Namespace(**d)


def flash(tmp_path, img, radio, sha=None):
    p = tmp_path / "img.bin"
    p.write_bytes(img)
    a = ns(tmp_path, image=str(p), sha256=sha or hashlib.sha256(img).hexdigest())
    return kdh.cmd_flash(a, port=radio, log=lambda s: None)


def test_crypt_matches_the_published_bf_decoder():
    # a .BF decrypts pages 2 .. n-3 (KDHT for page % 3 == 1, RBGI for 2): ours is the inverse
    img = app_image(0xF000)
    count, pk = kdh.packages(img)
    assert count == 60
    for i, page in pk:
        assert unxor_like_the_bootloader(i * 0x400, count, page) == img[i * 0x400:(i + 1) * 0x400]
    assert pk[0][1] == img[:0x400] and pk[59][1] == img[-0x400:]       # first / last go plain
    assert pk[4][1] != img[0x1000:0x1400]                                # page 4: KDHT


def test_one_page_costs_exactly_page_0(tmp_path):
    r = FakeBootloader()
    img = app_image(815)
    assert flash(tmp_path, img, r) == 0
    assert r.cmds == [0x01, 0x04, 0x03, 0x45] and r.ended
    assert r.sent[1][5] == 2                                     # declared 2, not 1 (read as 5)
    assert bytes(r.app[:0x400]) == img + b"\xff" * (0x400 - len(img))
    assert bytes(r.app[0x400:]) == r.stock[0x400:]
    assert json.load(open(tmp_path / "run" / "flash.json"))["result"] == "ok"


def test_a_whole_app_lands_decrypted(tmp_path):
    r = FakeBootloader()
    img = app_image(0xEC00)
    assert flash(tmp_path, img, r) == 0
    assert bytes(r.app[:0xEC00]) == img and bytes(r.app[0xEC00:]) == r.stock[0xEC00:]
    assert 0x05 not in r.cmds


def test_refuses_an_image_that_was_not_approved(tmp_path):
    r = FakeBootloader()
    assert flash(tmp_path, app_image(815), r, sha="00" * 32) == 5
    assert r.sent == []


@pytest.mark.parametrize("img", [b"", app_image(0xF001), bytes(1024),
                                 struct.pack("<II", 0x20004000, 0x08000101) + bytes(1016)])
def test_refuses_what_the_app_area_cant_take(tmp_path, img):
    with pytest.raises(ValueError):
        kdh.packages(img)


def test_reports_a_failed_page(tmp_path):
    r = FakeBootloader(fail_at=1)
    assert flash(tmp_path, app_image(0x1000), r) == 3
    res = json.load(open(tmp_path / "run" / "flash.json"))
    assert res["sent"] == 1 and "write failed" in res["result"]
    assert 0x45 not in r.cmds


def test_refuses_when_the_k6_is_on_the_cable(tmp_path):
    r = FakeBootloader(k6=True)
    p = tmp_path / "img.bin"
    p.write_bytes(app_image(815))
    a = ns(tmp_path, image=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest(), listen_s=0.5)
    assert kdh.cmd_flash(a, port=r, log=lambda s: None) == 4 and r.sent == []


def test_probe_only_shakes_hands(tmp_path):
    r = FakeBootloader()
    assert kdh.cmd_probe(ns(tmp_path), port=r, log=lambda s: None) == 0
    assert r.cmds == [0x01] and bytes(r.app) == r.stock
    assert json.load(open(tmp_path / "run" / "probe.json"))["handshake"] == "ack"


def test_probe_with_no_bootloader(tmp_path, monkeypatch):
    class Silent(FakeBootloader):
        def write(self, b):
            self.sent.append(bytes(b))
    monkeypatch.setattr(kdh, "REPLY_S", 0.01)
    r = Silent()
    assert kdh.cmd_probe(ns(tmp_path), port=r, log=lambda s: None) == 2
    assert len(r.sent) == 5
    assert json.load(open(tmp_path / "run" / "probe.json"))["handshake"] == "command 0x01: no reply"


def test_probe_waits_for_a_power_on(tmp_path, monkeypatch):
    monkeypatch.setattr(kdh, "BOOT_EVERY_S", 0.001)
    r = FakeBootloader(boot_after=20)
    assert kdh.cmd_probe(ns(tmp_path, wait_boot_s=5.0), port=r, log=lambda s: None) == 0
    assert r.cmds.count(0x42) == 21 and r.cmds[-1] == 0x01 and bytes(r.app) == r.stock
    p = json.load(open(tmp_path / "run" / "probe.json"))
    assert p["boot"] is True and p["handshake"] == "ack"


def test_probe_gives_up_with_no_power_on(tmp_path, monkeypatch):
    monkeypatch.setattr(kdh, "BOOT_EVERY_S", 0.001)
    r = FakeBootloader()
    assert kdh.cmd_probe(ns(tmp_path, wait_boot_s=0.05), port=r, log=lambda s: None) == 2
    assert set(r.cmds) == {0x42}
    assert json.load(open(tmp_path / "run" / "probe.json"))["handshake"] == "no power-on seen"


# ---- dump: a dumper simulated in Python (tests/test_uv21_dumper.py runs the real one) ----

def frame(t, addr, data=b""):
    body = t.encode() + struct.pack("<IH", addr, len(data)) + data
    return b"\xA5\x5A" + body + struct.pack(">H", kdh.crc16_xmodem(body))


def chunks(t, base, data):
    return b"".join(frame(t, base + o, data[o:o + 256]) for o in range(0, len(data), 256))


FLASH = hashlib.sha256(b"f").digest() * 2048
INFO = struct.pack("<H", 64) + bytes(18)
SPI = bytes(range(256)) * 512                      # 128 KB, JEDEC capacity 0x11


def taste(chip=True):
    j = 0x114020 if chip else 0xFFFFFF                 # no chip: MISO floats high
    d = SPI[:256] if chip else b"\xFF" * 256
    return frame("T", 3 | j << 8, d) + frame("T", 0 | j << 8, d)


def one_pass(n, flash=FLASH, v=2):
    return (chunks("F", 0x08000000, flash) + chunks("F", 0x1FFFE400, bytes(0x1000))
            + frame("F", 0x1FFFF7E0, INFO) + frame("F", 0x1FFFF800, bytes(16))
            + (taste() if v == 2 else b"")
            + frame("J", 0x114020) + chunks("S", 0, SPI) + frame("E", n))


class FakeDumper:
    """streams the tail of what it sent at boot, then a taste per 'T' (dumper 2) and a pass per
    'D'"""

    def __init__(self, boot=b"", flaky=None, v=2, chip=True):
        self.out, self.n, self.flaky, self.writes = bytearray(boot), 2, flaky, []
        self.v, self.chip = v, chip

    def write(self, b):
        self.writes.append(bytes(b))
        if b == b"T" and self.v == 2:
            self.out += taste(self.chip) + frame("E", 0)
        if b == b"D":
            self.n += 1
            p = bytearray(one_pass(self.n, FLASH if self.flaky != self.n else FLASH[:-1] + b"!", self.v))
            self.out += p

    def read(self, n, timeout=None, gap=None):
        r, self.out = bytes(self.out[:n]), self.out[n:]
        return r

    def close(self):
        pass


def dump_args(tmp_path):
    return ns(tmp_path, private=str(tmp_path / "priv"), passes=2, timeout_s=60.0)


def test_dump_asks_for_two_full_passes(tmp_path):
    tail = one_pass(2)[-5000:]                      # joined mid-pass: thrown away
    r = FakeDumper(boot=tail)
    assert kdh.cmd_dump(dump_args(tmp_path), port=r, log=lambda s: None) == 0
    assert r.writes == [b"T", b"D", b"D"]                # taste first, then the full passes
    s = json.load(open(tmp_path / "run" / "summary.json"))
    assert s["complete"] and s["passes"] == 2 and s["partial_passes"] == 1 and s["flash_kb"] == 64
    assert s["taste"][0] == {"mode": 3, "jedec": "20 40 11", "answered": True, "all_ff": False,
                             "all_00": False} and s["tastes"] == 3
    assert (tmp_path / "priv" / "flash.bin").read_bytes() == FLASH
    assert (tmp_path / "priv" / "spi.bin").read_bytes() == SPI


def test_a_taste_with_no_chip_ends_the_dump(tmp_path):
    boot = frame("B", 0, b"blowfang uv21 dumper 2") + taste(chip=False) + frame("E", 0)
    r = FakeDumper(boot=boot, chip=False)
    assert kdh.cmd_dump(dump_args(tmp_path), port=r, log=lambda s: None) == 4
    assert r.writes == []                                # the boot taste said it all: no passes
    s = json.load(open(tmp_path / "run" / "summary.json"))
    assert s["stopped"] == "no SPI flash answered the taste" and s["passes"] == 0
    assert [t["answered"] for t in s["taste"]] == [False, False] and s["taste"][1]["all_ff"]
    assert (tmp_path / "priv" / "taste-mode0.bin").read_bytes() == b"\xFF" * 256


def test_dumper_1_still_gets_its_passes(tmp_path):
    r = FakeDumper(boot=frame("B", 0, b"blowfang uv21 dumper 1"), v=1)
    assert kdh.cmd_dump(dump_args(tmp_path), port=r, log=lambda s: None) == 0
    assert r.writes == [b"D", b"D"]
    assert "taste" not in json.load(open(tmp_path / "run" / "summary.json"))


def test_dump_keeps_the_radio_out_of_the_run_folder(tmp_path):
    kdh.cmd_dump(dump_args(tmp_path), port=FakeDumper(), log=lambda s: None)
    run = b"".join((tmp_path / "run" / f).read_bytes() for f in os.listdir(tmp_path / "run"))
    assert FLASH[:32] not in run and SPI[:64] not in run
    assert hashlib.sha256(SPI).hexdigest().encode() not in run      # no hash of the channels either


def test_passes_that_disagree_are_not_complete(tmp_path):
    a = dump_args(tmp_path)
    assert kdh.cmd_dump(a, port=FakeDumper(flaky=4), log=lambda s: None) == 3
    s = json.load(open(tmp_path / "run" / "summary.json"))
    assert not s["complete"] and s["flash_pages_differ"] == ["0xfc00"]


def test_a_corrupt_frame_spoils_only_its_pass():
    raw = bytearray(one_pass(1) + one_pass(2) + one_pass(3))
    raw[100] ^= 0xFF                                  # inside pass 1's first flash frame
    frames, bad = kdh.parse_frames(bytes(raw))
    d = kdh.assemble(frames)
    assert bad == 1 and d["partial"] == 1 and [p["n"] for p in d["passes"]] == [2, 3]
    assert kdh.summary(d, bad)["complete"]


class FakeConsole:
    """the bring-up image on the cable: answers i with its report"""

    def __init__(self):
        self.sent, self.pending = b"", b"blowfang uv21 bringup 3\r\n"

    def write(self, b):
        self.sent += b
        if b == b"i":
            self.pending += b"blowfang uv21 bringup 3 build=abc board=uv21\r\nrf id=4819\r\n"

    def read(self, n, timeout=None, gap=None):
        out, self.pending = self.pending[:n], self.pending[n:]
        return out

    def drain(self, seconds):
        return self.read(1 << 16)

    def close(self):
        pass


def test_console_sends_only_read_only_commands(tmp_path):
    port = FakeConsole()
    a = argparse.Namespace(out=str(tmp_path), send="i", seconds=0.1, listen_s=0.1)
    assert kdh.cmd_console(a, port=port, log=lambda *_: None) == 0
    assert port.sent == b"i"
    text = (tmp_path / "console.txt").read_text()
    assert "rf id=4819" in text
    res = json.loads((tmp_path / "console.json").read_text())
    assert res["banner"] == "blowfang uv21 bringup 3"
    for bad in ("O", "R", "iO"):
        port = FakeConsole()
        a.send = bad
        assert kdh.cmd_console(a, port=port, log=lambda *_: None) == 5 and port.sent == b""
