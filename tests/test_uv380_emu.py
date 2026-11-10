"""The UV-380 firmware on an emulated STM32F405 (QEMU's netduinoplus2): the QEMU build of
radios/uv380 reads msg.sot from the host, sends it as K6 pager packets in FFSK (XL, 1800 baud,
48 kHz) through the same board API the radio uses, and the audio it writes must decode with
modem.fsk's F receiver (the bench model of the K6's chip receiver) back into the message.

Needs make -C radios/uv380 qemu (arm-none-eabi-gcc) and qemu-system-arm.
SPDX-License-Identifier: MIT"""
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ELF = os.path.join(ROOT, "radios", "uv380", "build", "uv380-qemu.elf")
QEMU = shutil.which("qemu-system-arm")
sys.path.insert(0, ROOT)
from modem import fsk, k6, pager  # noqa: E402

pytestmark = pytest.mark.skipif(not (QEMU and os.path.exists(ELF)),
                                reason="needs qemu-system-arm and make -C radios/uv380 qemu")


def run_firmware(frames):
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "msg.sot"), "wb") as f:
            f.write(frames)
        r = subprocess.run([QEMU, "-M", "netduinoplus2", "-nographic", "-monitor", "none",
                            "-serial", "none", "-semihosting-config", "enable=on,target=native",
                            "-kernel", ELF], cwd=d, capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "uv380: sent" in r.stdout + r.stderr
        with open(os.path.join(d, "audio.raw"), "rb") as f:
            return np.frombuffer(f.read(), "<i2").astype(float) / 32768


@pytest.mark.parametrize("n", [1, 160, 333])
def test_k6_receiver_decodes_uv380(n):
    frames = bytes(np.random.default_rng(n).integers(0, 256, 6 * n, dtype=np.uint8))
    x = run_firmware(frames)
    prof = pager.profile("XL", "1200")
    asm, times = fsk.receive(x, 48000, "F", 1800, prof)
    assert sum(ok for _, ok, _, _ in times) == prof.air_packets(n) and asm.bad == 0
    (mid,) = asm.messages
    got, good = asm.frames(mid)
    assert mid == k6.msg_id_for(frames) and got == frames and good.all()
    # the burst is exactly modem.k6's: lead-in, 16 then 12 preamble bytes, tail
    burst = pager.Air(preamble_bytes=16, later_preamble_bytes=12).burst(prof.pack(frames, mid), 1800)
    assert abs(len(x) - len(burst) * 8 * 48000 / 1800 - 240) <= 2
