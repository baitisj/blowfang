"""Codec2's memory on the UV-380 OpenRTX image (firmware/uv380-openrtx/c2_alloc.h, 11-18): the
first Codec2 call to the speaker reset the radio (11-18 10:33Z), because codec2_create's 31 KB and
the decoder thread's 16 KB stack didn't fit the 38 KB of heap left beside the module arena. Codec2's
malloc, calloc and free now go to the sotto encoder's static arena (CCM, idle while a module runs)
first and the heap after. Host gcc, with codec2_create(3200)'s blocks as the radio's image makes
them (read from its ELF): most go to the arena, the rest to the heap, every block goes back where it
came from, and the arena is whole again after codec2_destroy. On the host, real Codec2 built with
c2_alloc.h decoded the same samples as a plain build (25,264 B arena, 6,044 B heap).
SPDX-License-Identifier: MIT"""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, "firmware", "uv380-openrtx")

# codec2_create(CODEC2_MODE_3200) on the radio, in order (11-18, the image's disassembly): struct
# CODEC2, Pn, Sn_, w, Sn, fft_fwd, fftr_fwd, fftr_inv, nlp, nlp's fft, bpf_buf
BLOCKS = (4400, 640, 640, 1280, 1280, 4360, 5396, 5396, 1756, 4360, 1684)

HARNESS = r"""
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>
size_t heap_now, heap_n;
static void *base[64]; static size_t sz[64];
void *__real_malloc(size_t); void *__real_calloc(size_t, size_t); void __real_free(void *);
void *__wrap_malloc(size_t s) { void *p = __real_malloc(s); for (int i = 0; i < 64; i++)
  if (!base[i]) { base[i] = p; sz[i] = s; break; } heap_now += s; heap_n++; return p; }
void *__wrap_calloc(size_t a, size_t b) { void *p = __wrap_malloc(a * b); memset(p, 0, a * b);
  return p; }
void __wrap_free(void *p) { if (!p) return; for (int i = 0; i < 64; i++) if (base[i] == p) {
  heap_now -= sz[i]; base[i] = 0; __real_free(p); return; } printf("BAD FREE\n"); }
#define SOTTO8K_ENC_ARENA_IMPL 1
#include "sotto8k_enc_arena.h"
#undef calloc
#undef free
#include "c2_alloc.h"     /* as Codec2's sources see it */
static const unsigned blocks[] = {BLOCKS};
#define NB (sizeof blocks / sizeof blocks[0])
int main(void) {
  for (int round = 0; round < 3; round++) {
    void *p[NB];
    unsigned in_arena = 0;
    for (unsigned i = 0; i < NB; i++) {
      p[i] = (i & 1) ? calloc(1, blocks[i]) : malloc(blocks[i]);
      memset(p[i], 0x5a, blocks[i]);
      in_arena += enc_owns(p[i]);
    }
    printf("round %d arena %u blocks %u heap %zu\n", round, enc_used, in_arena, heap_now);
    for (unsigned i = 0; i < NB; i++)     /* codec2_destroy's order isn't create's */
      free(p[(i * 7) % NB]);
    printf("after arena %u live %u heap %zu\n", enc_used, enc_live, heap_now);
  }
  /* the arena mostly taken by someone else: what doesn't fit comes from the heap */
  void *r = sotto8k_enc_calloc(1, 22000);
  void *q = malloc(4400);
  printf("taken %d heap %zu\n", enc_owns(q), heap_now);
  free(q);
  sotto8k_enc_free(r);
  printf("end arena %u heap %zu\n", enc_used, heap_now);
  return 0;
}
"""


@pytest.mark.skipif(not shutil.which("gcc"), reason="needs gcc")
def test_codec2_blocks_go_to_the_arena_first_and_come_back(tmp_path):
    src = tmp_path / "h.c"
    src.write_text(HARNESS.replace("{BLOCKS}", "{" + ", ".join(map(str, BLOCKS)) + "}"))
    exe = tmp_path / "h"
    subprocess.run(["gcc", "-O1", "-std=gnu99", "-w",
                    f"-I{FW}", f"-I{os.path.join(FW, 'sotto8k')}", str(src), "-o", str(exe),
                    "-Wl,--wrap=malloc,--wrap=calloc,--wrap=free"], check=True)
    out = subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout
    assert "BAD FREE" not in out, out
    lines = out.splitlines()
    # 25,160 B of the arena (8-byte rounding) for the first nine, 6,044 B of heap for the last two
    for r in range(3):
        assert lines[2 * r] == f"round {r} arena 25160 blocks 9 heap 6044", out
        assert lines[2 * r + 1] == "after arena 0 live 0 heap 0", out
    assert lines[6] == "taken 0 heap 4400", out
    assert lines[7] == "end arena 0 heap 0", out


def test_the_image_build_puts_c2_alloc_in_front_of_codec2():
    patch = open(os.path.join(FW, "uv380-blowfang.patch")).read()
    assert "diff --git a/subprojects/packagefiles/codec2/meson.build" in patch
    assert ("+add_project_arguments('-include', meson.current_source_dir() / 'c2_alloc.h', "
            "language : 'c')") in patch
    build = open(os.path.join(FW, "build.sh")).read()
    assert 'cp "$HERE/c2_alloc.h" "$WORK/subprojects/packagefiles/codec2/"' in build
    # c2_start sees that the decoder thread's stack and the rest of Codec2 fit before it starts
    assert "void *stk = malloc(CODEC2_THREAD_STKSIZE);" in patch
