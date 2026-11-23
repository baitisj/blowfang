"""tools/bench_kplug.py: the read-only Baofeng clone, against a simulated UV-17 Pro family radio
(no serial port), and the bench runner's kplug.read step."""
import importlib.util
import json
import os
import sys

import pytest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, os.path.dirname(__file__))
import test_bench_runner as tb  # noqa: E402
from test_bench_runner import work  # noqa: E402,F401  (the runner's fixture: BLOWFANG_WORK, K6_CABLE)
spec = importlib.util.spec_from_file_location("bench_kplug", os.path.join(TOOLS, "bench_kplug.py"))
kp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kp)


class FakeRadio:
    """a radio in programming mode: answers one ident, F / M / SEND, and R reads from its memory;
    records every byte written to it (a W must never appear)"""

    def __init__(self, mem, magic=kp.IDENTS["normal"], chatter=b"", dead_from=None):
        self.mem, self.magic, self.chatter, self.dead_from = mem, magic, chatter, dead_from
        self.rx = bytearray()           # what the tool sent
        self.out = bytearray(chatter)   # what the radio will send
        self.writes = []
        self.state = "idle"

    def write(self, b):
        self.writes.append(bytes(b))
        self.rx += b
        if bytes(b) in kp.IDENTS.values():
            if bytes(b) == self.magic:
                self.out += b"\x06"
                self.state = "id"
            return
        if self.state != "id":
            return
        if b == b"F":
            self.out += b"UV21NRFB\x00\x00YP07V0"
        elif b == b"M":
            self.out += b"\x00" * 15
        elif b == kp.SEND_MAGIC:
            self.out += b"\x06"
        elif b[:1] == b"R" and len(b) == 4:
            addr, n = int.from_bytes(b[1:3], "big"), b[3]
            if self.dead_from is not None and self.dead_from <= addr < 0xC000:
                return
            self.out += b[:4] + kp.scramble(self.mem.get(addr, b"\xff" * n)[:n])

    def read(self, n, timeout=None, gap=None):
        r, self.out = bytes(self.out[:n]), self.out[n:]
        return r

    def drain(self, seconds):
        return self.read(1 << 16)

    def close(self):
        pass


def memory(channels=((0, 446.0e6), (1, 146.52e6), (2, 446.5e6))):
    mem = {}
    chan = bytearray(b"\xff" * (kp.CHANNELS * kp.CHAN_SIZE))
    for i, hz in channels:
        v = int(hz // 10)
        bcd = bytes(((v // 10 ** (2 * k + 1) % 10) << 4) | (v // 10 ** (2 * k) % 10) for k in range(4))
        chan[i * 32:i * 32 + 4] = bcd
        chan[i * 32 + 20:i * 32 + 32] = b"HOME RPT\x00\x00\x00\x00"
    for a in range(0, len(chan), 0x40):
        mem[a] = bytes(chan[a:a + 0x40])
    mem[0x8000] = b"SETTINGS-BLOCK" + bytes(50)
    mem[0xD000] = b"FW V0.07 UV21PRO" + bytes(48)
    mem[0xB000] = b"CAL" + bytes(range(61))
    return mem


def args(tmp_path, *extra):
    import argparse
    ns = argparse.Namespace(port="fake", out=str(tmp_path / "run"), private=str(tmp_path / "priv"),
                            ident=None, model="UV-21ProV2", extra=None, listen_s=0.0)
    for k, v in zip(extra[::2], extra[1::2]):
        if k == "extra":
            ns.extra = (ns.extra or []) + [v]
        else:
            setattr(ns, k, v)
    return ns


def test_scramble_is_its_own_inverse():
    data = bytes(range(256)) * 2
    assert kp.scramble(kp.scramble(data)) == data
    # key "CO 7": 0x00 / 0xFF pass, the key's space passes, the rest are XORed
    assert kp.scramble(b"\x00\xffAA") == b"\x00\xffA" + bytes([0x41 ^ 0x37])
    assert kp.scramble(b"AAAA") == bytes([0x41 ^ 0x43, 0x41 ^ 0x4F, 0x41, 0x41 ^ 0x37])


def test_reads_the_codeplug_and_never_writes(tmp_path):
    r = FakeRadio(memory())
    assert kp.cmd_read(args(tmp_path), port=r, log=lambda s: None) == 0
    sent = [w for w in r.writes if w[:1] == b"R" and len(w) == 4]
    assert len(sent) == sum(n for _, n in kp.REGIONS) // 0x40
    assert not any(w[:1] == b"W" for w in r.writes)
    img = (tmp_path / "priv" / "clone.img").read_bytes()
    assert len(img) == 0x8380 + len(b"UV-21ProV2") and img.endswith(b"UV-21ProV2")
    assert img[0x8000:0x800e] == b"SETTINGS-BLOCK"
    assert img[0x8340:0x8350] == b"FW V0.07 UV21PRO"          # 0xD000 lands last, as in CHIRP's image
    s = json.load(open(tmp_path / "run" / "summary.json"))
    assert s["channels"] == {"programmed": 3, "bands": {"2 m 136-174": 1, "70 cm 400-480": 2}}
    assert json.load(open(tmp_path / "run" / "ident.json"))["ident"] == "normal"


def test_channel_list_stays_out_of_the_run_folder(tmp_path):
    kp.cmd_read(args(tmp_path), port=FakeRadio(memory()), log=lambda s: None)
    run = b"".join((tmp_path / "run" / f).read_bytes() for f in os.listdir(tmp_path / "run"))
    assert b"HOME RPT" not in run and b"\x00\x46\x04" not in run      # names, 446.0 MHz in BCD
    assert b"HOME RPT" in (tmp_path / "priv" / "clone.img").read_bytes()
    assert b"FW V0.07 UV21PRO" in (tmp_path / "run" / "region_d000.bin").read_bytes()


def test_tries_the_color_ident_next(tmp_path):
    r = FakeRadio(memory(), magic=kp.IDENTS["color"])
    assert kp.cmd_read(args(tmp_path), port=r, log=lambda s: None) == 0
    assert json.load(open(tmp_path / "run" / "ident.json"))["ident"] == "color"


def test_no_answer(tmp_path):
    r = FakeRadio(memory(), magic=b"PROGRAMSOMETHING")
    assert kp.cmd_read(args(tmp_path), port=r, log=lambda s: None) == 2
    assert not any(w[:1] == b"R" for w in r.writes)


def test_refuses_when_the_k6_is_on_the_cable(tmp_path):
    r = FakeRadio(memory(), chatter=b"K6 CFG build=abc\r\n")
    assert kp.cmd_read(args(tmp_path), port=r, log=lambda s: None) == 4
    assert r.writes == []


def test_extra_blocks_skip_a_silent_range(tmp_path):
    r = FakeRadio(memory(), dead_from=0xB080)        # silent 0xB080-0xBFFF
    a = args(tmp_path, "extra", "0xB000:0xB100", "extra", "0xC000:0xC080")
    assert kp.cmd_read(a, port=r, log=lambda s: None) == 0
    s = json.load(open(tmp_path / "run" / "summary.json"))
    assert [(e["addr"], e["status"]) for e in s["extra"]] == [
        ("0xb000", "ok"), ("0xb040", "ok"), ("0xb080", "0 bytes"), ("0xc000", "ok"), ("0xc040", "ok")]
    assert (tmp_path / "run" / "region_b000.bin").read_bytes()[:3] == b"CAL"
    assert len((tmp_path / "run" / "region_b000.bin").read_bytes()) == 0x80
    assert (tmp_path / "run" / "region_c000.bin").exists()


@pytest.mark.parametrize("bad", ["0xB001", "0xB000:0xB000", "B000", "0x10000", "0xF000:0x10040"])
def test_extra_must_be_aligned_blocks(bad):
    with pytest.raises(ValueError):
        kp.parse_extra(bad)


# ---- the runner's kplug.read step ----

def test_runner_lists_match_the_tool():
    br = tb.br
    assert br.KPLUG_IDENTS == tuple(sorted(kp.IDENTS)) and br.KPLUG_MODELS == kp.MODELS
    assert br.KPLUG_EXTRA_MAX == kp.EXTRA_MAX_BLOCKS
    for s in ("0xB000", "0xB000:0xC000", "0x0:0x10000"):
        assert br.kplug_extra(s) == len(kp.parse_extra(s))


def test_runner_checks_kplug_args(work):
    a = ["--ident", "normal", "--model", "5RM", "--extra", "0xB000:0xC000", "--listen-s=2"]
    ok = tb.job("k", {"do": "kplug.read", "args": a})
    assert tb.problems(ok) == ""
    for bad in (["--write"], ["--port", "x"], ["--ident", "k6pro"], ["--extra", "0xB001"],
                ["--extra", "0x0:0x10000"], ["--listen-s", "60"], ["--model"]):
        assert tb.problems(tb.job("k", {"do": "kplug.read", "args": bad})), bad
    assert tb.problems(tb.job("k", {"do": "kplug.read", "background": True}))


def test_runner_command_keeps_the_codeplug_private(work):
    br = tb.br
    argv, _, bg = br.command(tb.job("kp1"), {"do": "kplug.read", "args": ["--ident", "normal"]}, str(work / "rd"))
    assert argv[1].endswith("bench_kplug.py") and argv[2:6] == ["read", "--port", "/dev/k6cable", "--out"]
    assert argv[argv.index("--private") + 1] == os.path.join(str(work), "private", "kplug", "kp1")
    assert argv[-2:] == ["--ident", "normal"] and not bg
    assert br.uses_cable({"do": "kplug.read"})          # a serial-hold logger must let go first
