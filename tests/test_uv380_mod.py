"""UV-380 RAM modules on an emulated STM32F405 (QEMU's netduinoplus2): the QEMU build of
radios/uv380, keyed as a K6 image is (modem.k6mod.key_image over the placeholder), takes a module
signed by modem.uv380mod from mod.bin on the host, checks it with c/common/mod_core.c and runs it
from SRAM against the UV-380's API table (radios/uv380/src/uv_api.h). The hello module's say
lines, return value and the 1200 Hz tone it writes through the audio calls must come back; a
module with the wrong key, a changed byte, the K6's magic, a newer API level, flags, an old serial
or a short image must be refused, and an image without a key refuses everything.

The tests use a throwaway key, never ~/.config/sotto/k6-mod.key.
Needs make -C radios/uv380 qemu (arm-none-eabi-gcc), qemu-system-arm, clang, ld.lld and llvm-objcopy.
SPDX-License-Identifier: MIT"""
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELF = os.path.join(ROOT, "radios", "uv380", "build", "uv380-qemu.elf")
HELLO = os.path.join(ROOT, "radios", "uv380", "mod", "hello.c")
QEMU = shutil.which("qemu-system-arm")
TOOLS = all(shutil.which(t) for t in ("clang", "ld.lld", "llvm-objcopy", "llvm-nm"))
sys.path.insert(0, ROOT)
from modem import k6mod, uv380mod  # noqa: E402

pytestmark = pytest.mark.skipif(not (QEMU and TOOLS and os.path.exists(ELF)),
                                reason="needs qemu-system-arm, clang/lld/llvm and make -C radios/uv380 qemu")

KEY = secrets.token_bytes(16)


@pytest.fixture(scope="module")
def hello():
    return uv380mod.build(HELLO)


def run(img, key=KEY):
    """Boot the QEMU image (keyed with key, or unkeyed if None) with img as mod.bin:
    (r, ret, say lines, audio samples)."""
    elf = open(ELF, "rb").read()
    if key is not None:
        elf = k6mod.key_image(elf, key)
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "fw.elf"), "wb").write(elf)
        open(os.path.join(d, "mod.bin"), "wb").write(img)
        r = subprocess.run([QEMU, "-M", "netduinoplus2", "-nographic", "-monitor", "none",
                            "-serial", "none", "-semihosting-config", "enable=on,target=native",
                            "-kernel", "fw.elf"], cwd=d, capture_output=True, text=True, timeout=120)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        m = re.search(r"uv380: mod r=(-?\d+) ret=(-?\d+)", out)
        assert m, out
        say = re.findall(r"UV380 MOD say (.*)", out)
        raw = os.path.join(d, "audio.raw")
        audio = np.fromfile(raw, "<i2") if os.path.exists(raw) else np.zeros(0, np.int16)
    return int(m.group(1)), int(m.group(2)), say, audio


def test_hello_runs(hello):
    code, entry = hello
    assert entry == 0 and len(code) < 2048
    r, ret, say, audio = run(uv380mod.image(code, entry, 1, KEY))
    assert (r, ret) == (3, 0x55)
    assert re.fullmatch(r"hello world from RAM at 0x2[0-9a-f]{7}", say[0])
    assert say[1] == "squares 0x9f31fc58 div 142857 float 2525"
    assert re.fullmatch(r"scratch \d+ ok", say[2]) and int(say[2].split()[1]) > 14000
    assert say[3].startswith("tone 1200 Hz 4800 samples")
    assert len(audio) == 4800 and 15000 < np.abs(audio).max() <= 16100
    spec = np.abs(np.fft.rfft(audio.astype(float)))
    assert abs(np.argmax(spec) * 48000 / len(audio) - 1200) <= 10


def image_bytes(hello, **kw):
    code, entry = hello
    return k6mod.image(code, entry, kw.pop("serial", 1), kw.pop("key", KEY), **{"magic": b"U3M", **kw})


@pytest.mark.parametrize("case, want", [
    ("wrong key", -4),
    ("changed byte", -4),
    ("K6 magic", -3),
    ("API level 2", -3),
    ("library flag", -3),
    ("serial 0", -6),
    ("short", -3),
])
def test_refused(hello, case, want):
    img = {
        "wrong key": lambda: image_bytes(hello, key=secrets.token_bytes(16)),
        "changed byte": lambda: (lambda b: b[:40] + bytes([b[40] ^ 1]) + b[41:])(image_bytes(hello)),
        "K6 magic": lambda: image_bytes(hello, magic=b"K6M"),
        "API level 2": lambda: image_bytes(hello, level=2),
        "library flag": lambda: image_bytes(hello, lib=0),
        "serial 0": lambda: image_bytes(hello, serial=0),
        "short": lambda: image_bytes(hello)[:-1],
    }[case]()
    r, ret, say, audio = run(img)
    assert (r, ret, say, len(audio)) == (want, 0, [], 0)


def test_unkeyed_image_refuses(hello):
    r, ret, say, _ = run(image_bytes(hello), key=None)
    assert (r, ret, say) == (-5, 0, [])


def test_hello_is_position_independent():
    # uv380mod.build links at 0 and 0x1000 and refuses the module if the bytes differ; a module
    # with a writable static doesn't link at all
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "bad.c")
        open(src, "w").write('#include "uv_api.h"\nstatic int n;\n'
                             "int uv_main(const uv380_api_t *api) { (void)api; return ++n; }\n")
        with pytest.raises(subprocess.CalledProcessError):
            uv380mod.build(src)
