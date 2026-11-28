"""A radio's screen from its serial console, as a PNG: the shared screen-grab format
(c/common/scrgrab.h: "scr 1 ..." header, LZ4-packed bands, CRC-32), which any radio's module or
firmware prints, and the UV-21 scrdump module's raw RAMRD lines (-D SD_RAW=1, "scr r ...").

    python3 tools/screen_grab.py console.log -o screen.png [--scale 3] [--ref other.png]

--ref compares with another PNG of the same screen at the same --scale (an emulator render, say)
and prints the rows that differ. The packed bands decode with modem/lz4.py (the radios' lz4d.c,
byte for byte). Exit status 1 when no screen is found or its CRC is wrong.

Raw RAMRD lines: the ST7735S sends 18-bit pixels as three bytes with the colour in the top six bits,
after a dummy clock and a dummy byte (measured on the UV-21 2026-11-28: 9 bits); the tool reports
how many bytes have their two low bits clear at that offset, near 100% when the read worked.
SPDX-License-Identifier: MIT
"""
import argparse
import os
import re
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modem import lz4  # noqa: E402

W, H = 128, 160        # the UV-21's raw RAMRD lines
SCR_RGB565, SCR_MONO, SCR_ST7565 = 1, 2, 3
INK, PAPER = 0x0000, 0xFFFF    # formats 2 and 3: a set bit is a lit, dark pixel
HDR = re.compile(r"scr 1 (\d+) (\d+) (\d+) (\d+) (\d+)")
BAND = re.compile(r"scr b (\d+) (\d+) (\d+)")
DATA = re.compile(r"scr d ([0-9a-f]+)")
END = re.compile(r"scr e ([0-9a-f]{8}) (\d+)")
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


class Bad(Exception):
    pass


def grab(text):
    """the last whole screen in scrgrab's format: (w, h, fmt, {row: rgb565 list}); Bad if broken"""
    hdr, bands, cur, out = None, [], None, None
    for ln in text.splitlines():
        m = HDR.search(ln)
        if m:
            hdr, bands, cur = tuple(int(g) for g in m.groups()), [], None
            continue
        if hdr is None:
            continue
        m = BAND.search(ln)
        if m:
            cur = [int(m.group(2)), int(m.group(3)), bytearray()]
            bands.append(cur)
            continue
        m = DATA.search(ln)
        if m and cur is not None:
            cur[2] += bytes.fromhex(m.group(1))
            continue
        m = END.search(ln)
        if m:
            out = (hdr, bands, int(m.group(1), 16), int(m.group(2)))
            hdr = None
    if out is None:
        raise Bad("no whole 'scr 1' ... 'scr e' screen")
    (w, h, fmt, row_bytes, band_rows), bands, crc, nb = out
    if nb != len(bands):
        raise Bad(f"{len(bands)} bands, the end line says {nb}")
    data = bytearray()
    for raw, packed, b in bands:
        if packed:
            if len(b) != packed:
                raise Bad(f"band {len(data) // max(row_bytes * band_rows, 1)}: {len(b)} bytes, said {packed}")
            data += lz4.decompress(bytes(b), raw)
        else:
            if len(b) != raw:
                raise Bad(f"raw band: {len(b)} bytes, said {raw}")
            data += b
    if zlib.crc32(bytes(data)) & 0xFFFFFFFF != crc:
        raise Bad(f"CRC {zlib.crc32(bytes(data)) & 0xFFFFFFFF:08x}, the radio said {crc:08x}")
    rows = (h + 7) // 8 if fmt == SCR_ST7565 else h
    if len(data) != row_bytes * rows:
        raise Bad(f"{len(data)} bytes for {rows} rows of {row_bytes}")
    px = {}
    for y in range(h):
        if fmt == SCR_ST7565:
            r = data[(y >> 3) * row_bytes:((y >> 3) + 1) * row_bytes]
            px[y] = [INK if r[x] >> (y & 7) & 1 else PAPER for x in range(w)]
            continue
        r = data[y * row_bytes:(y + 1) * row_bytes]
        if fmt == SCR_RGB565:
            px[y] = [r[2 * x] << 8 | r[2 * x + 1] for x in range(w)]
        elif fmt == SCR_MONO:
            px[y] = [INK if r[x >> 3] >> (7 - (x & 7)) & 1 else PAPER for x in range(w)]
        else:
            raise Bad(f"format {fmt}")
    return w, h, fmt, px


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


def png(path, px, scale=1, w=W, h=H):
    """rows missing from the dump come out dark purple"""
    raw = bytearray()
    for y in range(h):
        line = bytearray()
        for x in range(w):
            c = rgb(px[y][x]) if y in px else (40, 0, 40)
            line += bytes(c) * scale
        for _ in range(scale):
            raw += b"\0" + line

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w * scale, h * scale, 8, 2, 0, 0, 0))
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
    text = open(a.log, errors="replace").read()
    m = ID.search(text)
    print(f"id {m.group(1)}" if m else "id -")
    if HDR.search(text):
        try:
            w, h, fmt, px = grab(text)
        except Bad as e:
            print(f"bad screen: {e}")
            return 1
        print(f"screen {w}x{h} format {fmt}, CRC good")
    else:
        rows, _ = parse(text)
        if not rows:
            print("no 'scr' lines")
            return 1
        w, h = W, H
        bit, frac = align(rows)
        px = pixels(rows, bit)
        print(f"raw rows {len(rows)} of {H}, bit offset {bit}, {100 * frac:.1f}% of bytes with clear low bits")
    png(a.out, px, a.scale, w, h)
    print(f"{a.out}: {w * a.scale}x{h * a.scale}")
    if a.ref:
        ref = read_png(a.ref)
        diff = []
        for y in sorted(px):
            bad = sum(1 for x in range(w) if rgb(px[y][x]) != ref[y * a.scale][x * a.scale])
            if bad:
                diff.append((y, bad))
        print(f"vs {a.ref}: {len(diff)} rows differ" + (": " + ", ".join(f"{y}({n})" for y, n in diff[:40]) if diff else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
