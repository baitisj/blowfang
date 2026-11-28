"""The shared screen-grab format (c/common/scrgrab.h) and the LZ4 packer under it (c/common/lz4c.c),
built for the host: what the radio prints must come back byte for byte through tools/screen_grab.py
(modem/lz4.py decodes, as the radios' lz4d.c does), CRC and all, for screens that pack well, screens
that don't (raw bands), and the LZ4 format's edge cases."""
import os
import random
import subprocess
import sys
import zlib

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import screen_grab  # noqa: E402
from modem import lz4  # noqa: E402

HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include "scrgrab.c"
static void say(const char *l) { puts(l); }
int main(int argc, char **argv) {
  int w = atoi(argv[1]), h = atoi(argv[2]), fmt = atoi(argv[3]), rb = atoi(argv[4]), band = atoi(argv[5]);
  static uint8_t img[1 << 20], b[1 << 16], p[1 << 16], row[4096];
  static uint16_t tab[LZ4C_TAB];
  if (argc > 6) {                       /* lz4c alone: stdin -> one block on stdout */
    size_t n = fread(img, 1, sizeof img, stdin);
    int r = lz4c(img, (unsigned)n, p, sizeof p, tab);
    if (r < 0) return 2;
    fwrite(p, 1, (size_t)r, stdout);
    return 0;
  }
  scr_t s = {say, b, p, tab, 0, 0, 0, 0, 0};
  scr_begin(&s, w, h, fmt, (unsigned)rb, (unsigned)band);
  for (int y = 0; y < h; y++) {
    if (fread(row, 1, (size_t)rb, stdin) != (size_t)rb) return 3;
    scr_row(&s, row);
  }
  scr_end(&s);
  return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory):
    d = tmp_path_factory.mktemp("scrgrab")
    src = d / "h.c"
    src.write_text(HARNESS)
    out = d / "h"
    subprocess.run(["cc", "-O1", "-Wall", "-Werror", "-I", os.path.join(ROOT, "c", "common"), str(src), "-o", str(out)],
                   check=True)
    return str(out)


def run(exe, data, *args):
    r = subprocess.run([exe, *map(str, args)], input=data, capture_output=True, check=True)
    return r.stdout


def screen(w, h, rng, noise):
    px = []
    for y in range(h):
        for x in range(w):
            c = 0x18C3 if y < 12 else (0xFFFF if (x // 6 + y // 10) % 7 == 0 else 0)
            if rng.random() < noise:
                c = rng.randrange(65536)
            px.append(c)
    return b"".join(c.to_bytes(2, "big") for c in px)


@pytest.mark.parametrize("noise", [0.0, 0.05, 1.0])
def test_a_screen_comes_back_byte_for_byte(exe, noise):
    w, h = 128, 160
    data = screen(w, h, random.Random(7), noise)
    text = run(exe, data, w, h, 1, 2 * w, 8).decode()
    assert all(len(ln) <= 64 for ln in text.splitlines())            # the UV-21's say() limit
    gw, gh, fmt, px = screen_grab.grab(text)
    assert (gw, gh, fmt) == (w, h, 1)
    back = b"".join(c.to_bytes(2, "big") for y in range(h) for c in px[y])
    assert back == data
    if noise == 0.0:
        assert sum(len(ln) for ln in text.splitlines()) < len(data) // 4  # packs well
    if noise == 1.0:
        assert " 0" in "".join(ln[-2:] for ln in text.splitlines() if ln.startswith("scr b"))  # raw bands


def test_a_flipped_byte_fails_the_crc(exe):
    data = screen(128, 16, random.Random(1), 0.0)
    lines = run(exe, data, 128, 16, 1, 256, 16).decode().splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith("scr d"))
    v = lines[i][6:]
    lines[i] = "scr d " + ("0" if v[0] != "0" else "1") + v[1:]
    with pytest.raises((screen_grab.Bad, ValueError)):
        screen_grab.grab("\n".join(lines))


@pytest.mark.parametrize("data", [b"", b"a", b"abcdefghijklm", b"\0" * 13, b"\0" * 5000, bytes(range(256)) * 9,
                                  b"ab" * 40 + bytes(random.Random(3).randrange(256) for _ in range(300))])
def test_lz4c_blocks_decode_and_keep_the_end_rules(exe, data):
    block = run(exe, data, 0, 0, 0, 0, 0, "lz4")
    assert lz4.decompress(block, len(data)) == data
    last_match = None                   # (start, end) in the output of the last match
    i = pos = 0
    while i < len(block):
        tok = block[i]
        i += 1
        nlit = tok >> 4
        if nlit == 15:
            while block[i] == 255:
                nlit += 255
                i += 1
            nlit += block[i]
            i += 1
        i += nlit
        pos += nlit
        if i >= len(block):
            break
        i += 2
        m = (tok & 15) + 4
        if tok & 15 == 15:
            while block[i] == 255:
                m += 255
                i += 1
            m += block[i]
            i += 1
        last_match = (pos, pos + m)
        pos += m
    if last_match:
        assert last_match[0] <= len(data) - 12 and last_match[1] <= len(data) - 5
    if len(data) > 13:
        assert len(block) <= len(data) + len(data) // 255 + 16
    assert zlib.crc32(data) == zlib.crc32(lz4.decompress(block, len(data)))


def k6_lines(pages, noise_lines=()):
    """one K6 screen in format 3 (the ST7565's pages), a page a band, raw, each page's 128 bytes
    in one long "scr d" line (c/k6/mod/scrgrab.c packs it instead; the decoder takes either)"""
    out, crc = ["scr 1 128 64 3 128 1"], 0
    for n, p in enumerate(pages):
        out += [f"scr b {n} 128 0", "scr d " + p.hex()]
        out += list(noise_lines)
        crc = zlib.crc32(p, crc)
    out.append(f"scr e {crc:08x} 8")
    return "\r\n".join(out) + "\r\n"


def test_k6_pages_come_back_as_pixels():
    rnd = random.Random(7)
    pages = [bytes(rnd.randrange(256) for _ in range(128)) for _ in range(8)]
    # another line of the serial report between bands (a K6 MOD line) is skipped
    w, h, fmt, px = screen_grab.grab(k6_lines(pages, ["K6 MOD ret=0 (0x0) in 3 ms"]))
    assert (w, h, fmt) == (128, 64, screen_grab.SCR_ST7565)
    for y in range(64):
        for x in range(128):
            lit = pages[y >> 3][x] >> (y & 7) & 1
            assert px[y][x] == (screen_grab.INK if lit else screen_grab.PAPER)


def test_k6_grab_with_a_bad_crc_fails():
    pages = [bytes([k] * 128) for k in range(8)]
    text = k6_lines(pages).replace("scr d " + bytes([3] * 128).hex(), "scr d " + bytes([3] * 127 + [4]).hex())
    with pytest.raises(screen_grab.Bad):
        screen_grab.grab(text)
