"""[S12] sotto 1200 receive and decode for the UV-21 Pro (radios/uv21/sotto): the K6 voice pager's
library built for the AT32F421's Cortex-M4, with the codebook read from (stand-in) SPI flash.
On the emulated M4 it must play exactly what the K6's host build (c/k6/pager_host_lr) plays from
the same 0xA9 packets, fit the flash the port has left, and keep up with 120 MHz. Needs
sh radios/uv21/sotto/build.sh (clang, ld.lld, a host cc), make -C c/k6 pager_host_lr, and
unicorn, capstone and numpy. SPDX-License-Identifier: MIT"""
import os
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "radios", "uv21", "build", "sotto")
ELF = os.path.join(OUT, "bench.elf")
HOST_LR = os.path.join(ROOT, "c", "k6", "pager_host_lr")
sys.path.insert(0, os.path.join(ROOT, "radios", "uv21", "sotto"))

pytest.importorskip("unicorn")
pytest.importorskip("capstone")
pytestmark = pytest.mark.skipif(
    not (os.path.exists(ELF) and os.path.exists(HOST_LR)),
    reason="needs sh radios/uv21/sotto/build.sh and make -C c/k6 pager_host_lr")

import bench_uv21 as bench  # noqa: E402

# the flash the port pays for the library (codebook in SPI): port 21 is 26,104 B of the 61,440 B
# app area, and the firmware's own glue (FSK receive, PWM and DMA, the screen) needs room too
FLASH_BUDGET = 16 * 1024


def host_pcm(packets, xl):
    with tempfile.TemporaryDirectory() as d:
        open(f"{d}/p.bin", "wb").write(b"".join(packets))
        args = [HOST_LR] + (["-x"] if xl else []) + ["-b", "6144", f"{d}/p.bin", f"{d}/o.raw"]
        out = subprocess.run(args, check=True, capture_output=True, text=True).stdout.split()
        return dict(kv.split("=", 1) for kv in out), np.fromfile(f"{d}/o.raw", "<i2")


@pytest.mark.parametrize("profile,frames,lose", [("XL", 70, []), ("XL", 70, [1]), ("L", 40, [2])])
def test_the_m4_plays_what_the_k6_plays(profile, frames, lose):
    """bit for bit, a lost packet concealed alike"""
    res, pcm = bench.run(ELF, frames=frames, profile=profile, lose=lose)
    sent = [p for i, p in enumerate(bench.packets_of(bench.frames_of(frames), profile)) if i not in lose]
    st, ref = host_pcm(sent, profile == "XL")
    m4 = np.frombuffer(pcm, "<i2")
    assert int(st["total"]) == frames
    assert len(m4) == len(ref) == frames * 320
    assert np.array_equal(m4, ref)


def test_it_keeps_up_at_120_mhz():
    """the worst 40 ms slot well inside the budget with the radio's measured flash timing (job
    s12ws1: the prefetch hides the fetch wait states). With no prefetch at all (--ws 3, which the
    radio disproved) the worst slot would be about the whole budget, so that bound isn't held."""
    res, _ = bench.run(ELF, frames=70, lose=[1])
    assert res["load_pct_worst"] < 60, res
    # the RF chip's FSK FIFO holds 8 words, 71 ms of air at 1800 baud: the firmware empties it
    # from the decoder's yield hook, so no stretch between yields may come near that, even at
    # the slow bound
    slow, _ = bench.run(ELF, frames=20, ws=3, data_ws=3)
    assert slow["yield_gap_us"] < 20_000, slow


def test_it_fits_and_reads_its_codebook_from_spi():
    size = dict(kv.split("=") for kv in open(os.path.join(OUT, "size.txt")).read().split())
    assert int(size["flash"]) <= FLASH_BUDGET, size
    # the K6's image format and table (c/common/sp_cb.h): the same bytes the K6 reads at 0x100000
    img = open(os.path.join(OUT, "sotto-cb.bin"), "rb").read()
    assert img[:4] == b"K6CB" and len(img) == 32 + 30720
    nm = subprocess.run(["llvm-nm", os.path.join(OUT, "fit.elf")], capture_output=True, text=True).stdout
    assert "fx_cb0" not in nm and "sp_flash_read" in nm
    assert "sp_flash_write" not in nm and "sp_flash_erase4k" not in nm   # (no install path linked)
