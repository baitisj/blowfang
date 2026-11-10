"""The UV-380 encodes sotto 1200 itself: the QEMU build of radios/uv380 (an emulated STM32F405,
QEMU's netduinoplus2) reads 8 kHz speech from msg.raw on the host, runs the fixed-point sotto 1200
encoder from the sotto submodule on it (state in the CCM-first arena, src/sotto_arena.c), and sends
the superframes as K6 pager packets in FFSK. The K6 receiver model (modem.fsk's F receiver) must
get back exactly the bits the x86 build of the same encoder writes (sotto_enc_fx -1200).

QEMU is not cycle-accurate, so this checks the bits, the memory and the whole chain, not the speed;
the speed is sotto's Cortex-M4 bench (sotto tools/cm4_lr_check.sh).

Needs make -C radios/uv380 qemu, make -C sotto/c fixed and qemu-system-arm.
SPDX-License-Identifier: MIT"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELF = os.path.join(ROOT, "radios", "uv380", "build", "uv380-qemu.elf")
ENC = os.path.join(ROOT, "sotto", "c", "fixed", "sotto_enc_fx")
RAW = os.path.join(ROOT, "sotto", "codec", "codec2", "raw")
QEMU = shutil.which("qemu-system-arm")
sys.path.insert(0, ROOT)
from modem import fsk, k6, pager  # noqa: E402

pytestmark = pytest.mark.skipif(not (QEMU and os.path.exists(ELF) and os.path.exists(ENC)),
                                reason="needs qemu-system-arm, make -C radios/uv380 qemu and make -C sotto/c fixed")

CCM_ARENA = 60 * 1024


def run_firmware(pcm, d):
    with open(os.path.join(d, "msg.raw"), "wb") as f:
        f.write(pcm)
    r = subprocess.run([QEMU, "-M", "netduinoplus2", "-nographic", "-monitor", "none",
                        "-serial", "none", "-semihosting-config", "enable=on,target=native",
                        "-kernel", ELF], cwd=d, capture_output=True, text=True, timeout=600)
    log = r.stdout + r.stderr
    assert r.returncode == 0 and "uv380: sent" in log, log
    vals = dict((k, int(v)) for k, v in re.findall(r"uv380: ([a-z ]+)=(-?\d+)", log))
    with open(os.path.join(d, "audio.raw"), "rb") as f:
        return np.frombuffer(f.read(), "<i2").astype(float) / 32768, vals


def x86_bits(pcm, d):
    src, out = os.path.join(d, "x86.raw"), os.path.join(d, "x86.bit")
    with open(src, "wb") as f:
        f.write(pcm)
    subprocess.run([ENC, "-1200", src, out], check=True, capture_output=True)
    with open(out, "rb") as f:
        return f.read()


@pytest.mark.parametrize("clip", ["hts1a.raw", "ve9qrp_10s.raw"])
def test_uv380_encodes_what_x86_encodes(clip):
    with open(os.path.join(RAW, clip), "rb") as f:
        pcm = f.read()
    with tempfile.TemporaryDirectory() as d:
        x, vals = run_firmware(pcm, d)
        ref = x86_bits(pcm, d)
    n = len(ref) // 6
    assert n > 0 and vals["enc frames"] == n
    # the encoder's state sits in the CCM, all of it freed after the message
    assert 0 < vals["enc ccm"] <= CCM_ARENA and vals["enc sram"] == 0 and vals["arena live"] == 0
    prof = pager.profile("XL", "1200")
    asm, times = fsk.receive(x, 48000, "F", 1800, prof)
    assert sum(ok for _, ok, _, _ in times) == prof.air_packets(n) and asm.bad == 0
    (mid,) = asm.messages
    got, good = asm.frames(mid)
    assert got == ref and good.all()
    assert mid == k6.msg_id_for(ref)
