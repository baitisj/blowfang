"""The UV-380 OpenRTX image's sotto 1200 encoder wrappers (firmware/uv380-openrtx/sotto8k): with
calloc and free from the static arena (sotto8k_enc_arena.h, CCM on the radio, since the heap
couldn't hold the encoder and its thread: 11-14 01:27Z), the encoder gives the same bytes as
sotto's own heap build, and a second encoder after the first is destroyed gives them again (the
arena is reset). Host gcc; the radio's struct sizes are checked by the arena's size comment.
SPDX-License-Identifier: MIT"""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOTTO = os.path.join(ROOT, "sotto", "c")
W = os.path.join(ROOT, "firmware", "uv380-openrtx", "sotto8k")
SPEECH = os.path.join(ROOT, "sotto", "codec", "codec2", "raw", "ve9qrp_10s.raw")
MAIN = os.path.join(ROOT, "tests", "c", "sotto8k_enc_twice.c")


@pytest.mark.skipif(not (shutil.which("gcc") and os.path.exists(SPEECH)),
                    reason="needs gcc and the sotto submodule")
def test_the_arena_encoder_matches_the_heap_one(tmp_path):
    t = tmp_path / "t"
    (t / "sotto" / "fixed").mkdir(parents=True)
    for f in os.listdir(SOTTO):
        if f.endswith((".h", ".inc")):
            shutil.copy(os.path.join(SOTTO, f), t / "sotto")
    for f in os.listdir(os.path.join(SOTTO, "fixed")):
        if f.endswith((".h", ".inc", ".c")):
            shutil.copy(os.path.join(SOTTO, "fixed", f), t / "sotto" / "fixed")
    for f in os.listdir(W):
        shutil.copy(os.path.join(W, f), t)
    arena = [str(t / f) for f in ("sotto8k_fx_enc.c", "sotto8k_fx_lr_enc.c", "sotto8k_fx_math.c",
                                  "sotto8k_fx_tables.c")]
    heap = [os.path.join(SOTTO, "fixed", f) for f in ("sotto_fx_enc.c", "sotto_fx_lr_enc.c",
                                                      "fx_math.c", "fx_tables.c")]
    # host pointers are 8 bytes: room for the bigger structs here
    subprocess.run(["gcc", "-O1", "-std=gnu99", "-w", "-DSOTTO8K_ENC_ARENA=40000u", f"-I{t}",
                    f"-I{t / 'sotto'}", MAIN, *arena, "-o", str(t / "a")], check=True)
    subprocess.run(["gcc", "-O1", "-std=gnu99", "-w", "-DSOTTO_V36", "-DSOTTO_PITCH_THIRD",
                    f"-I{SOTTO}", f"-I{t}", MAIN, *heap, "-o", str(t / "h")], check=True)
    out = subprocess.run([str(t / "a"), SPEECH, str(t / "a1"), str(t / "a2")], check=True,
                         capture_output=True, text=True).stdout
    assert out.split("\n")[:2] == ["round 0 frames 200", "round 1 frames 200"], out
    subprocess.run([str(t / "h"), SPEECH, str(t / "h1"), str(t / "h2")], check=True)
    a1, a2, h1 = ((t / n).read_bytes() for n in ("a1", "a2", "h1"))
    assert len(a1) == 1200 and a1 == a2 == h1
