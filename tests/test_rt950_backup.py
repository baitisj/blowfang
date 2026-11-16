"""The RT-950 Pro backup (docs/rt950-backup.md): radios/rt950/tools/rt950_backup.py against a fake
radio on a pseudo-terminal (radios/rt950/tools/fake_rt950.py), as the stock firmware and as the
dump-only image. Every byte comes back as the radio holds it, two passes agree before a file
counts, a dropped reply or a flipped bit costs a retry or a pass and not the backup, the 3-byte
reads refuse anything but the dump-only image, and no write opcode ever reaches the radio.
With the dump-only image built (make -C radios/rt950 backup-image JM=...), it is checked too.
Needs pyserial. SPDX-License-Identifier: GPL-3.0-or-later"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "radios", "rt950", "tools")
BACKUP = os.path.join(ROOT, "radios", "rt950", "backup")
BUILD = os.path.join(ROOT, "radios", "rt950", "build")
sys.path.insert(0, TOOLS)
sys.path.insert(0, BACKUP)
pytest.importorskip("serial")

import rt950_backup as rb          # noqa: E402
from fake_rt950 import FakeRadio   # noqa: E402
from fake_rt950 import image       # noqa: E402

SMALL = image(seed=7, spi_len=0x20000, mcu_len=0x3000)


@pytest.fixture
def radio():
    made = []

    def make(mode, **kw):
        r = FakeRadio(mode, **kw)
        r.start()
        made.append(r)
        return r

    yield make
    for r in made:
        r.close()


def read(path):
    with open(path, "rb") as f:
        return f.read()


def test_stock_reads_the_first_64k(radio, tmp_path):
    r = radio("stock")
    assert rb.main(["read", r.path, str(tmp_path), "--addr", "2"]) == 0
    assert read(tmp_path / "cps64k.bin") == r.spi[:0x10000]
    man = json.loads(read(tmp_path / "manifest.json"))
    assert man["ok"] and man["addr_bytes"] == 2 and man["model"] == "RT-950"
    assert len(man["ranges"][0]["passes"]) == 2
    assert r.writes == 0


def test_stock_cps_regions_only(radio, tmp_path):
    r = radio("stock")
    assert rb.main(["read", r.path, str(tmp_path), "--addr", "2", "--cps-regions"]) == 0
    for name, start, length in rb.STOCK_REGIONS:
        assert read(tmp_path / f"{name}.bin") == r.spi[start:start + length]
    assert r.writes == 0


def test_dump_image_reads_all_4mb_and_the_bootloader(radio, tmp_path):
    r = radio("backup")
    assert rb.main(["read", r.path, str(tmp_path), "--addr", "3"]) == 0
    assert read(tmp_path / "spi4m.bin") == r.spi              # (past 64 KB: the 3-byte addresses)
    assert read(tmp_path / "bootloader.bin") == r.mcu
    sums = read(tmp_path / "SHA256SUMS").decode()
    assert rb.sha(r.spi) + "  spi4m.bin" in sums
    assert r.writes == 0


def test_3_byte_reads_refuse_the_stock_firmware(radio, tmp_path):
    r = radio("stock")
    assert rb.main(["read", r.path, str(tmp_path), "--addr", "3"]) == 2
    assert not any(n.endswith(".bin") for n in os.listdir(tmp_path))
    assert r.reads == 0


def test_a_dropped_reply_is_retried_in_a_new_session(radio, tmp_path):
    r = radio("backup", spi=SMALL[0], mcu=SMALL[1], drop_at=0x10080)
    assert rb.main(["read", r.path, str(tmp_path), "--addr", "3", "--spi-len", "0x20000"]) == 0
    assert read(tmp_path / "spi4m.bin") == r.spi
    assert r.sessions == 2


def test_passes_that_disagree_take_another(radio, tmp_path):
    r = radio("backup", spi=SMALL[0], mcu=SMALL[1], corrupt_at=0x1F000)
    assert rb.main(["read", r.path, str(tmp_path), "--addr", "3", "--spi-len", "0x20000"]) == 0
    man = json.loads(read(tmp_path / "manifest.json"))
    spi = man["ranges"][0]
    assert len(spi["passes"]) == 3 and spi["passes"][0]["sha256"] != spi["agreed_sha256"]
    assert read(tmp_path / "spi4m.bin") == r.spi


def test_the_tool_cannot_send_a_write(radio):
    r = radio("backup", spi=SMALL[0], mcu=SMALL[1])
    rad = rb.Radio(r.path, 3)
    rad.handshake()
    for op in rb.NEVER_SEND:
        with pytest.raises(RuntimeError):
            rad.send(bytes([op, 0, 0, 0, 1, 0]))
    rad.close(True)
    assert r.writes == 0


@pytest.mark.skipif(not os.path.exists(os.path.join(BUILD, "rt950-backup-only.elf")),
                    reason="needs make -C radios/rt950 backup-image JM=...")
def test_the_built_dump_image_cannot_write_or_transmit():
    import check_image
    assert check_image.check(os.path.join(BUILD, "rt950-backup-only.elf"),
                             os.path.join(BUILD, "rt950-backup-only.bin")) == []


@pytest.mark.parametrize("block", [8, 32])
def test_smaller_reads_for_bluetooth(radio, tmp_path, block):
    r = radio("stock")
    assert rb.main(["read", r.path, str(tmp_path), "--addr", "2", "--block", str(block)]) == 0
    assert read(tmp_path / "cps64k.bin") == r.spi[:0x10000]
    assert r.reads == 2 * 0x10000 // block


def test_the_bluetooth_bridge_speaks_at_the_stock_rate():
    import rt950_ble_bridge as bb
    s = bb.auth_serial(5)
    assert len(s) == 20 and s[:4] == b"\x3f" * 4 and s[4] == 5 and max(s[5:]) <= 98
    assert bb.SELECTORS == tuple(range(9))
    assert bb.pace_s(5) >= 20 * 10 / 9600            # never faster than the module's 9600 baud UART
    assert bb.pace_s(2) < bb.pace_s(5)
