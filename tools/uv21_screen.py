"""The UV-21 Pro's screen, read back from its ST7735S by the scrdump RAM module
(radios/uv21/mod/scrdump.c), as a PNG.

    python3 tools/uv21_screen.py uv21mod.log -o screen.png [--scale 3] [--ref other.png]

The module prints the raw bytes clocked after each row's RAMRD, dummy bits and all, so this finds
the bit offset itself: the controller sends 18-bit pixels as three bytes with the colour in the
top six bits, so at the right offset the two low bits of every byte are zero. On the radio
(2026-11-28) the pixels start 9 bits in: one dummy clock, then a dummy byte. The low-bit test
can't tell 1 from 9 (or 17), so the tool takes 9 and says how well the low bits agree. --ref compares
with another PNG of the same size (an emulator render, say) and prints the rows that differ.
Standard library only. SPDX-License-Identifier: MIT
"""
import argparse
import re
import struct
import sys
import zlib

W, H = 128, 160
ROW = re.compile(r"scr r ([0-9a-f]{2}) ([0-9a-f]{2}) ([0-9a-f]+)")
ID = re.compile(r"scr id ([0-9a-f]{8})")


def parse(text):
    """{row: bytes} and the RDDID word, from the module's lines"""
    pieces, ident = {}, None
    for ln in text.splitlines():
        m = ID.search(ln)
        if m:
            ident = int(m.group(1), 16)
        m = ROW.search(ln)
        if m:
            pieces.setdefault(int(m.group(1), 16), {})[int(m.group(2), 16)] = bytes.fromhex(m.group(3))
    rows = {y: b"".join(p[k] for k in sorted(p)) for y, p in pieces.items()}
    return rows, ident


def shifted(raw, bit):
    """raw read `bit` bits later"""
    v = int.from_bytes(raw, "big") << bit
    return (v & ((1 << (8 * len(raw))) - 1)).to_bytes(len(raw), "big")


DUMMY_BITS = 9      # RAMRD on the radio: a dummy clock and a dummy byte before the first pixel


def align(rows, bit=DUMMY_BITS):
    """(bit, the share of bytes with their two low bits clear there): near 1.0 when the read worked"""
    clear = sum(1 for raw in rows.values() for b in shifted(raw, bit)[:3 * W] if not b & 3)
    total = sum(min(len(r) - 2, 3 * W) for r in rows.values())
    return bit, clear / max(total, 1)


def pixels(rows, bit):
    """{row: [rgb565] * W}"""
    out = {}
    for y, raw in rows.items():
        s = shifted(raw, bit)
        px = []
        for x in range(W):
            r, g, b = s[3 * x] >> 3, s[3 * x + 1] >> 2, s[3 * x + 2] >> 3
            px.append(r << 11 | g << 5 | b)
        out[y] = px
    return out


def rgb(c):
    return ((c >> 11 & 31) * 255 // 31, (c >> 5 & 63) * 255 // 63, (c & 31) * 255 // 31)


def png(path, px, scale=1):
    """rows missing from the dump come out dark purple"""
    raw = bytearray()
    for y in range(H):
        line = bytearray()
        for x in range(W):
            c = rgb(px[y][x]) if y in px else (40, 0, 40)
            line += bytes(c) * scale
        for _ in range(scale):
            raw += b"\0" + line

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W * scale, H * scale, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def read_png(path):
    """an 8-bit RGB(A) PNG without interlace -> rows of (r, g, b)"""
    data = open(path, "rb").read()
    pos, idat = 8, b""
    w = h = depth = ctype = None
    while pos < len(data):
        n, t = struct.unpack(">I4s", data[pos:pos + 8])
        d = data[pos + 8:pos + 8 + n]
        if t == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", d[:10])
        elif t == b"IDAT":
            idat += d
        pos += 12 + n
    bpp = {2: 3, 6: 4}[ctype]
    raw, rows, prev = zlib.decompress(idat), [], bytearray(w * bpp)
    for y in range(h):
        f, line = raw[y * (w * bpp + 1)], bytearray(raw[y * (w * bpp + 1) + 1:(y + 1) * (w * bpp + 1)])
        for i in range(len(line)):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append([tuple(line[x * bpp:x * bpp + 3]) for x in range(w)])
        prev = line
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("--ref", help="a PNG of the same screen at --scale to compare with")
    a = ap.parse_args(argv)
    rows, ident = parse(open(a.log, errors="replace").read())
    if not rows:
        print("no 'scr r' lines")
        return 1
    bit, frac = align(rows)
    px = pixels(rows, bit)
    png(a.out, px, a.scale)
    print(f"id {ident:08x}" if ident is not None else "id -")
    print(f"rows {len(rows)} of {H}, bit offset {bit}, {100 * frac:.1f}% of bytes with clear low bits")
    print(f"{a.out}: {W * a.scale}x{H * a.scale}")
    if a.ref:
        ref = read_png(a.ref)
        diff = []
        for y in sorted(px):
            bad = sum(1 for x in range(W) if rgb(px[y][x]) != ref[y * a.scale][x * a.scale])
            if bad:
                diff.append((y, bad))
        print(f"vs {a.ref}: {len(diff)} rows differ" + (": " + ", ".join(f"{y}({n})" for y, n in diff[:40]) if diff else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
