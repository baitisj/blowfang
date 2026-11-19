"""The browser installer (web/k6-installer/k6web.js, Web Serial) against the same emulated radio
as tools/k6_install.py (tests/test_k6_install.py: the cable session answered by the real module
loader and SPI library in c/k6/kl_host, Aura's first-boot dump, the stock bootloader). Node runs
the page's steps (tests/k6_web_driver.cjs); its port and prompts come here over stdio.

Checks that the JavaScript signs and keys byte for byte as modem/k6mod.py does, that a bundle
(folder or CI zip) loads into the right slots, that a wrong key writes nothing, that pages for
another image are skipped, that lost or damaged answers are sent again, that an image is keyed
before it is flashed, the new-radio install with its backup at FORMAT FLASH?, the no-hands update,
the backup and the status. Needs node and make -C c/k6 kl_host.
SPDX-License-Identifier: MIT"""
import json
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_k6_install import KEY, ROOT, Radio, bundle, keyfile, out_dir, slot_info  # noqa: E402,F401
from modem import k6mod  # noqa: E402

DRIVER = os.path.join(HERE, "k6_web_driver.cjs")
NODE = shutil.which("node")
pytestmark = [
    pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "c", "k6", "kl_host")), reason="needs make -C c/k6 kl_host"),
    pytest.mark.skipif(NODE is None, reason="needs node"),
]


def run(cfg, radio=None, ask=None):
    """Run the driver; returns (done message, log text, {saved name: bytes}, results)"""
    cfg = dict(cfg)
    cfg.setdefault("key", KEY.hex())
    p = subprocess.Popen([NODE, DRIVER, json.dumps(cfg)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True, bufsize=1)
    log, saved, results = [], {}, {}
    try:
        for line in p.stdout:
            req = json.loads(line)
            op, ans = req["op"], {}
            if op == "write":
                radio.write(bytes.fromhex(req["hex"]))
            elif op == "read":
                ans = {"hex": radio.read(256).hex()}
            elif op == "clear":
                radio.reset_input_buffer()
            elif op == "log":
                log.append(req["text"])
            elif op == "ask":
                log.append(f"ASK {req['kind']}: {req['text']}")
                ans = {"answer": ask(req["kind"]) if ask else ""}
            elif op == "save":
                saved[req["name"]] = bytes.fromhex(req["hex"])
            elif op == "result":
                results.update({k: v for k, v in req.items() if k != "op"})
            p.stdin.write(json.dumps(ans) + "\n")
            if op == "done":
                return req, "\n".join(log), saved, results
    finally:
        p.stdin.close()
        p.wait(timeout=10)
    raise AssertionError("the driver stopped without a result:\n" + "\n".join(log))


def new_radio_ask(radio):
    """the owner's hands in a new-radio install: switch on, press MENU at FORMAT FLASH?, type YES"""
    asked = []

    def ask(kind):
        asked.append(kind)
        if kind == "poweron":
            radio.state, radio.inb = "boot", b""
        if kind == "menu":
            radio.formatted = True
            radio.state = "lean"
            radio.out += b"K6 CB r=2 mode=2 crc=d5856ecd ms=566.4\r\n"
            radio.spi[0x100000] = b"K6CB".ljust(64, b"\0")
        return "YES" if kind == "confirm" else ""
    return ask, asked


def test_signing_matches_k6mod(tmp_path):
    code = k6mod.NAME_MAGIC + b"SURF".ljust(12, b"\0") + bytes((i * 37 + 11) & 0xFF for i in range(301))
    image = b"\x01" * 100 + k6mod.PLACEHOLDER + b"\x02" * 100
    z = tmp_path / "b.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("dir/manifest.json", "{}", compress_type=zipfile.ZIP_STORED)
        f.writestr("dir/mod-x.bin", code * 20, compress_type=zipfile.ZIP_DEFLATED)
    done, log, _, res = run({"run": "vectors", "code": code.hex(), "serial": 0x89ABCDEF, "image": image.hex(), "zip": str(z)})
    assert done["result"] == 0, done
    v = res["vectors"]
    assert v["siphash"] == k6mod.siphash24(KEY, code).to_bytes(8, "little").hex()
    img = k6mod.image(code, k6mod.bin_entry(code), 0x89ABCDEF, KEY, 2, lib=7)
    assert v["image"] == img.hex()
    assert v["page"] == k6mod.image(code, 0, 0x89ABCDEF, KEY, lib=9, page=True).hex()
    assert v["cards"] == k6mod.image(code, 0, 0x89ABCDEF, KEY, lib=13, cards=True).hex()
    assert v["packets"] == [p.hex() for p in k6mod.packets(img, 0x5A, 64)]
    assert v["name"] == "SURF"
    assert v["keyed"] == k6mod.key_image(image, KEY).hex()
    assert v["zip"] == {"manifest.json": b"{}".hex(), "mod-x.bin": (code * 20).hex()}


def test_load(bundle, keyfile):
    radio = Radio()
    done, log, _, _ = run({"run": "load", "bundle": str(bundle), "args": {"verify": True}}, radio)
    assert done["result"] == 0, log + str(done)
    for slot, flags, name in ((9, 3, ""), (10, 3, ""), (13, 5, ""), (0, 1, "SURF"), (7, 1, "GROUNDWAVE")):
        i = slot_info(radio, slot)
        assert i["r"] == "0" and int(i["flags"]) == flags, (slot, i)
        assert bytes.fromhex(i["name"]).decode() == name
    assert slot_info(radio, 1) == {"r": "-1"}
    order = [ln.split()[1] for ln in log.splitlines() if ln.strip().startswith("slot ")]
    assert order == ["9", "10", "11", "12", "13", "0", "7"]
    assert "K6: K6 LIB put" in log
    assert radio.restarts == 1                               # the session's end
    tail = radio.shutdown()
    assert "RULE" not in tail and "erases=" in tail


def test_load_from_the_ci_zip(bundle):
    z = bundle.parent / "blowfang-k6-abc1234.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        for name in os.listdir(bundle):
            f.write(bundle / name, name)
    radio = Radio()
    done, log, _, _ = run({"run": "load", "zip": str(z), "args": {"only": [0, 13]}}, radio)
    assert done["result"] == 0, log + str(done)
    assert slot_info(radio, 0)["r"] == "0" and slot_info(radio, 13)["r"] == "0" and slot_info(radio, 9) == {"r": "-1"}
    radio.shutdown()


def test_damaged_bundle_is_refused(bundle):
    with open(bundle / "mod-surf.bin", "r+b") as f:
        f.seek(100)
        f.write(b"\xEE")
    done, _, _, _ = run({"run": "bundle", "bundle": str(bundle)})
    assert done["result"] == 1 and "doesn't match the manifest" in done["error"]


def test_wrong_key_writes_nothing(bundle):
    radio = Radio()
    done, log, _, _ = run({"run": "load", "bundle": str(bundle), "key": "ff" * 16}, radio)
    assert done["result"] == 1 and "another key" in done["error"]
    for slot in (0, 7, 9, 13):
        assert slot_info(radio, slot) == {"r": "-1"}
    radio.shutdown()


def test_the_placeholder_is_not_a_key(bundle):
    done, _, _, _ = run({"run": "bundle", "bundle": str(bundle), "key": k6mod.PLACEHOLDER.hex()})
    assert done["result"] == 1 and "placeholder" in done["error"]


def test_pages_for_another_image_are_skipped(bundle):
    radio = Radio(build_id=bytes(8))
    done, log, _, _ = run({"run": "load", "bundle": str(bundle)}, radio)
    assert done["result"] == 0 and "skipping EXPLORE's pages" in log
    assert slot_info(radio, 9) == {"r": "-1"}
    assert slot_info(radio, 0)["r"] == "0" and slot_info(radio, 13)["r"] == "0"
    radio.shutdown()


def test_lost_and_damaged_answers_are_sent_again(bundle):
    radio = Radio(drop=(5, 40), mangle=(9, 77))
    done, log, _, _ = run({"run": "load", "bundle": str(bundle), "opts": {"timeout": 300},
                           "args": {"only": [0, 13], "verify": True}}, radio)
    assert done["result"] == 0, log + str(done)
    assert slot_info(radio, 0)["r"] == "0" and slot_info(radio, 13)["r"] == "0"
    radio.shutdown()


def test_lost_answer_to_the_last_chunk(bundle):
    surf = os.path.getsize(bundle / "mod-surf.bin") + 24
    chunks = -(-surf // 58)
    radio = Radio(drop=(2 + chunks,))        # info, the key probe (1 chunk), then surf's chunks
    done, log, _, _ = run({"run": "load", "bundle": str(bundle), "opts": {"timeout": 300}, "args": {"only": [0]}}, radio)
    assert done["result"] == 0, log + str(done)
    assert slot_info(radio, 0)["r"] == "0"
    assert radio.answers == 2 + chunks + 1 + 48 + 1          # the same frames as the Python tool
    radio.shutdown()


def test_flash_over_the_cable(bundle):
    radio = Radio()
    img = (bundle / "aura-voice-explore.bin").read_bytes()
    done, log, _, _ = run({"run": "flash", "image": str(bundle / "aura-voice-explore.bin")}, radio)
    assert done["result"] == 0, log + str(done)
    assert radio.restarts == 1
    want = k6mod.key_image(img, KEY)
    assert radio.flashed[:len(want)] == want and not any(radio.flashed[len(want):])
    assert k6mod.PLACEHOLDER not in radio.flashed
    radio.shutdown()


@pytest.mark.parametrize("start", ["stock", "fresh"])
def test_install_on_a_new_radio(start, out_dir, tmp_path):
    d, mods = out_dir
    (d / "aura-pager-cbi.bin").write_bytes(b"CBI-IMAGE" + k6mod.PLACEHOLDER + bytes(2000))
    (d / "aura-pager-vlri.bin").write_bytes(b"VLRI-IMAGE" + k6mod.PLACEHOLDER + bytes(2000))
    b = tmp_path / "bundle"
    import k6_install as ki
    assert ki.main(["bundle", str(d), "-o", str(b), "--modules", str(mods)], log=lambda *x, **k: None) == 0
    radio = Radio()
    if start == "stock":
        radio.state = "stock"
    ask, asked = new_radio_ask(radio)
    done, log, saved, _ = run({"run": "install", "bundle": str(b), "opts": {"chipBytes": 0x10000, "pause": 0},
                               "args": {"fresh": start == "fresh"}}, radio, ask)
    assert done["result"] == 0, log + str(done)
    assert [f[:4] for f in radio.flashes] == [b"CBI-", b"VLRI", b"\0\0\0\0"]
    assert all(KEY in f and k6mod.PLACEHOLDER not in f for f in radio.flashes)
    assert asked == ["confirm", "poweron", "menu", "poweron", "poweron"]
    (name, data), = saved.items()
    assert name.startswith("k6-spiflash-") and len(data) == 0x10000
    assert slot_info(radio, 9)["r"] == "0" and slot_info(radio, 7)["r"] == "0"
    assert radio.restarts == 1
    radio.shutdown()


def test_new_radio_needs_yes(out_dir, tmp_path):
    d, mods = out_dir
    (d / "aura-pager-cbi.bin").write_bytes(b"CBI-IMAGE" + k6mod.PLACEHOLDER + bytes(2000))
    (d / "aura-pager-vlri.bin").write_bytes(b"VLRI-IMAGE" + k6mod.PLACEHOLDER + bytes(2000))
    b = tmp_path / "bundle"
    import k6_install as ki
    assert ki.main(["bundle", str(d), "-o", str(b), "--modules", str(mods)], log=lambda *x, **k: None) == 0
    radio = Radio()
    done, log, _, _ = run({"run": "install", "bundle": str(b), "args": {"fresh": True}}, radio, lambda kind: "no")
    assert done["result"] == 1 and "nothing was written" in log and not radio.flashes
    radio.shutdown()


@pytest.mark.parametrize("backup", ["have", "never", "auto"])
def test_update_needs_no_hands(backup, bundle):
    radio = Radio()
    radio.spi[0x100000] = b"K6CB".ljust(64, b"\0")
    radio.spi[0x108000] = b"K6VL".ljust(64, b"\0")
    asked = []
    done, log, saved, _ = run({"run": "install", "bundle": str(bundle), "opts": {"chipBytes": 0x10000, "pause": 0},
                               "args": {"backup": backup}}, radio, lambda kind: asked.append(kind) or "")
    assert done["result"] == 0, log + str(done)
    assert bool(saved) == (backup == "auto")
    assert not asked and len(radio.flashes) == 1 and radio.restarts == 2
    assert slot_info(radio, 10)["r"] == "0"
    radio.shutdown()


def test_update_stops_without_codebooks(bundle):
    radio = Radio()
    done, log, _, _ = run({"run": "install", "bundle": str(bundle), "args": {"backup": "never"}}, radio)
    assert done["result"] == 1 and "lacks" in done["error"] and not radio.flashes
    radio.shutdown()


def test_backup():
    radio = Radio()
    radio.kl_cmd("flip 0f010")
    done, log, saved, _ = run({"run": "backup", "opts": {"chipBytes": 0x20000}}, radio)
    assert done["result"] == 0, log + str(done)
    (data,) = saved.values()
    assert len(data) == 0x20000 and data[0xF010] == 0xFE and data.count(0xFF) == len(data) - 1
    radio.shutdown()


def test_status(bundle):
    radio = Radio()
    run({"run": "load", "bundle": str(bundle), "args": {"only": [0, 13]}}, radio)
    radio.state = "app"                                      # the restart after the load's end
    done, log, _, res = run({"run": "status"}, radio)
    assert done["result"] == 0, log + str(done)
    st = res["status"]
    assert st["info"]["buildId"] == "0123456789abcdef"
    assert {s["slot"]: (s["kind"], s["name"]) for s in st["slots"]} == {0: ("module", "SURF"), 13: ("cards", None)}
    assert len(st["missing"]) == 2
    radio.shutdown()
