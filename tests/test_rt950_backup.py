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


def test_summary_gets_hashes_only_and_the_calibration_regions(radio, tmp_path):
    r = radio("stock")
    out, summ = tmp_path / "private", tmp_path / "run"
    assert rb.main(["read", r.path, str(out), "--addr", "2", "--summary", str(summ)]) == 0
    assert sorted(os.listdir(summ)) == ["SHA256SUMS", "manifest.json"]
    man = json.loads(read(summ / "manifest.json"))
    regions = man["ranges"][0]["regions"]
    assert regions["cal_f000"] == rb.sha(r.spi[0xF000:0xF080])
    assert set(regions) == {n for n, _, _ in rb.STOCK_REGIONS}


def test_stock_read_refuses_another_model(radio, tmp_path, monkeypatch):
    r = radio("stock")
    monkeypatch.setattr(rb, "STOCK_MODEL", "UV-K6")
    assert rb.main(["read", r.path, str(tmp_path), "--addr", "2"]) == 2
    assert not (tmp_path / "cps64k.bin").exists() and r.writes == 0


def test_compare_names_regions_and_pages_never_bytes(tmp_path, capsys):
    a = bytes(range(256)) * 256
    b = bytearray(a)
    b[0xF010] ^= 0xFF          # inside cal_f000
    b[0x9001] ^= 0x55          # inside system_config
    pa, pb, pj = tmp_path / "a.bin", tmp_path / "b.bin", tmp_path / "c.json"
    pa.write_bytes(a)
    pb.write_bytes(bytes(b))
    assert rb.main(["compare", str(pa), str(pb), "--json", str(pj)]) == 0
    res = json.loads(pj.read_text())
    assert res["differ"] == 2 and not res["same"]
    assert res["regions"]["cal_f000"] == {"differ": 1, "first": "0xF010", "last": "0xF010"}
    assert res["regions"]["cal_e000"] == {"differ": 0}
    assert res["pages_4k"] == {"0x9000": 1, "0xF000": 1}
    assert "cal_e000         same" in capsys.readouterr().out


def test_compare_keys_finds_a_region_scrambled_by_each_sessions_key(tmp_path):
    raw = bytes((i * 37 + 11) % 251 + 1 for i in range(0x10000))
    a, b = bytearray(raw), bytearray(raw)
    f0 = slice(0xF000, 0xF080)          # sent raw by the radio, "decoded" with each session's key
    a[f0] = rb.xor(raw[f0], rb.XOR_KEYS[3])
    b[f0] = rb.xor(raw[f0], rb.XOR_KEYS[11])
    res = rb.compare(bytes(a), bytes(b), keys=True)
    assert res["regions"]["cal_f000"]["differ"] and not res["regions"]["cal_e000"]["differ"]
    assert [3, 11] in res["key_pairs"]["cal_f000"]
    assert set(res["key_pairs"]) == {"cal_f000"}


def test_manifest_records_the_session_key(radio, tmp_path):
    r = radio("stock")
    assert rb.main(["read", r.path, str(tmp_path), "--addr", "2"]) == 0
    man = json.loads(read(tmp_path / "manifest.json"))
    assert 0 <= man["key_index"] < len(rb.XOR_KEYS)


def test_one_pass_only_for_a_split_read(radio, tmp_path):
    r = radio("stock")
    assert rb.main(["read", r.path, str(tmp_path), "--addr", "2", "--passes", "1"]) == 0
    man = json.loads(read(tmp_path / "manifest.json"))
    rng = man["ranges"][0]
    assert len(rng["passes"]) == 1 and rng["single_pass"] and rng["agreed_sha256"] == rng["passes"][0]["sha256"]
    assert read(tmp_path / "cps64k.bin") == r.spi[:0x10000]


@pytest.mark.parametrize("block", [32, 128])
def test_stock_calibration_comes_back_as_sent_whatever_the_session_key(radio, tmp_path, block):
    r = radio("stock")
    outs = []
    for i in range(2):
        out = tmp_path / f"r{i}"
        assert rb.main(["read", r.path, str(out), "--addr", "2", "--block", str(block)]) == 0
        outs.append(read(out / "cps64k.bin"))
    assert outs[0] == outs[1] == r.spi[:0x10000]
    man = json.loads(read(tmp_path / "r1" / "manifest.json"))
    assert man["raw_regions"] == ["0xF000+0x80", "0xF200+0x80"]
