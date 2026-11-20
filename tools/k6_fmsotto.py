"""The K6's FM receiver that codes what it hears as sotto 1200 (c/k6/mod/fmsotto.c,
docs/k6-fm-sotto.md).

  python3 tools/k6_fmsotto.py deck out/fmsotto-cells.bin
      the slim encoder's codebook cells (sotto c/fixed/slim_cells.h) as the deck fmsotto reads
      from the SPI module library; install it once with
      python -m modem.k6 mod OUT.wav out/fmsotto-cells.bin --cards --install 14 --key-file KEY
      (or tools/k6_upload.py out/fmsotto-cells.bin --cards --install 14 ...)
  python3 tools/k6_fmsotto.py log K6.LOG OUT.bit [--wav OUT.wav]
      the superframes a run said ("s N hex" lines) back into a sotto 1200 .bit file, gaps
      filled with silence, and decoded by sotto's fixed-point decoder when --wav is given

SPDX-License-Identifier: MIT
"""
import argparse
import os
import re
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CELLS = os.path.join(ROOT, "sotto", "c", "fixed", "slim_cells.h")
DEC = os.path.join(ROOT, "sotto", "c", "fixed", "sotto_dec_fx")
SIZES = (512, 512, 256)          # the shape codebook's stages the cells cover
SF = 6                           # bytes a superframe
SILENT = bytes(SF)               # a superframe of zeros: unvoiced at the lowest energy


def parse_cells(path=CELLS):
    """slim_cells.h -> (defines, arrays): the #define values and every static array as a list"""
    text = re.sub(r"/\*.*?\*/", " ", open(path).read(), flags=re.S)
    defs = {m.group(1): int(m.group(2)) for m in re.finditer(r"#define\s+(\w+)\s+(-?\d+)", text)}
    arrays = {}
    for m in re.finditer(r"static const [\w ]+?\s(\w+)\[[^\]]*\]\s*=\s*\{([^}]*)\}", text):
        arrays[m.group(1)] = [int(v) for v in re.findall(r"-?\d+", m.group(2))]
    return defs, arrays


def cell_lists(defs, arrays, s):
    """stage s's cells as lists of vector numbers (what ivf_cell gives)"""
    off, idx = arrays[f"ivf_off{s}"], arrays[f"ivf_idx{s}"]
    out = []
    for c in range(defs[f"IVF_C{s}"]):
        n = off[c + 1] - off[c]
        lo = arrays[f"ivf_lo{s}"][c] if f"ivf_lo{s}" in arrays else n
        out.append([idx[off[c] + i] + (256 if i >= lo else 0) for i in range(n)])
    return out


def deck(path=CELLS):
    """the cell deck's bytes (layout: fmsotto.c, "The cell deck")"""
    defs, arrays = parse_cells(path)
    cd, cs, maxn = defs["IVF_CD"], [defs["IVF_C0"], defs["IVF_C1"], defs["IVF_C2"]], defs["IVF_MAXN"]
    cen = b"".join(struct.pack(f"{len(arrays[f'ivf_cen{s}'])}b", *arrays[f"ivf_cen{s}"]) for s in range(3))
    assert len(cen) == sum(cs) * cd
    starts, vec = [], []
    for s in range(3):
        lists = cell_lists(defs, arrays, s)
        assert sorted(v for l in lists for v in l) == list(range(SIZES[s])), f"stage {s}: not every vector once"
        assert max(map(len, lists)) <= maxn
        for l in lists:
            starts.append(len(vec))
            vec += l
        starts.append(len(vec))
    body = cen + struct.pack(f"<{len(starts)}H", *starts) + struct.pack(f"<{len(vec)}H", *vec)
    hdr = b"K6SC" + bytes([1, cd, cs[0], cs[1], cs[2], maxn, 0, 0]) + struct.pack("<I", sum(body) & 0xFFFFFFFF)
    return hdr + body


LINE = re.compile(r"(?:^|\s)s (\d+) ([0-9a-f]+)\s*$")


def frames_from_log(text):
    """{superframe number: 6 bytes} from a run's "s N hex" lines"""
    got = {}
    for ln in text.splitlines():
        m = LINE.search(ln.strip())
        if not m or len(m.group(2)) % (2 * SF):
            continue
        n, b = int(m.group(1)), bytes.fromhex(m.group(2))
        for i in range(0, len(b), SF):
            got[n + i // SF] = b[i:i + SF]
    return got


def bitstream(got):
    """the superframes in order from the first heard, gaps as silence; (bytes, gaps)"""
    if not got:
        return b"", 0
    lo, hi = min(got), max(got)
    return b"".join(got.get(n, SILENT) for n in range(lo, hi + 1)), sum(n not in got for n in range(lo, hi + 1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("deck", help="the cell deck for the SPI module library")
    d.add_argument("out")
    d.add_argument("--cells", default=CELLS)
    g = sub.add_parser("log", help="a run's superframes back into a .bit file")
    g.add_argument("log")
    g.add_argument("out")
    g.add_argument("--wav", help="also decode it to this WAV (8 kHz)")
    a = ap.parse_args(argv)
    if a.cmd == "deck":
        b = deck(a.cells)
        open(a.out, "wb").write(b)
        print(f"{a.out}: {len(b)} B")
        return 0
    got = frames_from_log(open(a.log, errors="replace").read())
    bits, gaps = bitstream(got)
    open(a.out, "wb").write(bits)
    print(f"{a.out}: {len(bits) // SF} superframes ({len(bits) // SF * 0.04:.1f} s), {gaps} missing")
    if a.wav:
        import numpy as np
        import soundfile as sf
        raw = a.out + ".raw"
        subprocess.run([DEC, "-1200", a.out, raw], check=True, capture_output=True)
        sf.write(a.wav, np.fromfile(raw, np.int16), 8000, subtype="PCM_16")
        os.remove(raw)
        print(f"{a.wav}: decoded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
