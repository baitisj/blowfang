"""LZ4 blocks for the K6 (Jeff, 2026-11-14 and 2026-11-20; docs/k6-lz4.md): a packer that writes
the standard LZ4 block format, tuned for small blocks (an optimal parse, not the fast greedy
one), and the reference decoder that mirrors the radio's c/k6/lz4d.c byte for byte.

  compress(data)        -> one LZ4 block (no frame, no size header)
  decompress(block, n)  -> the n bytes it holds (ValueError if it doesn't decode to exactly n)
  inplace_excess(block) -> how far the radio's write position runs ahead of its read position
                           at worst, decoding in place (see k6_mod.h KM_LZ4_MARGIN)

Every block follows the format's end rules (the last 5 bytes are literals, the last match starts
12 or more bytes before the end), so the reference lz4 tools decode it too.
SPDX-License-Identifier: MIT"""

MIN_MATCH = 4
LAST_LITERALS = 5
MF_LIMIT = 12
MAX_OFFSET = 65535
CHAIN = 512           # match candidates looked at per position


def _ext(n):
    """extension bytes for a length nibble value n (literals: the run; matches: length - 4)"""
    return 0 if n < 15 else 1 + (n - 15) // 255


def _matches(data):
    """For each position, the longest match (length, offset) with an earlier position, or (0, 0)."""
    n = len(data)
    best = [(0, 0)] * n
    head = {}
    prev = [-1] * n
    limit = n - LAST_LITERALS          # a match may not run into the last 5 bytes
    for i in range(n - MIN_MATCH + 1):
        key = data[i:i + 4]
        j = head.get(key, -1)
        prev[i] = j
        head[key] = i
        if i >= n - MF_LIMIT:
            continue
        bl, bo, depth = 0, 0, CHAIN
        while j >= 0 and depth and i - j <= MAX_OFFSET:
            if data[j + bl:j + bl + 1] == data[i + bl:i + bl + 1] or bl == 0:
                k = 0
                while i + k < limit and data[j + k] == data[i + k]:
                    k += 1
                if k > bl:
                    bl, bo = k, i - j
                    if i + k >= limit:
                        break
            j = prev[j]
            depth -= 1
        if bl >= MIN_MATCH:
            best[i] = (bl, bo)
    return best


def compress(data):
    """data (bytes) -> an LZ4 block. An optimal parse over each position's longest match: the price
    of a sequence is its token, literals, their extension bytes, the offset and the match's
    extension bytes."""
    data = bytes(data)
    n = len(data)
    if n == 0:
        return b"\x00"
    best = _matches(data)
    INF = float("inf")
    price = [INF] * (n + 1)
    lit = [0] * (n + 1)              # literals since the last match, on the best path to here
    back = [None] * (n + 1)          # (start, length, offset) of the match ending here, or None
    price[0] = 0
    for i in range(n):
        p = price[i]
        if p == INF:
            continue
        # one more literal (its run's extension bytes grow at 15, 270, 525 ...)
        r = lit[i] + 1
        c = p + 1 + _ext(r) - _ext(r - 1)
        if c < price[i + 1] or (c == price[i + 1] and back[i + 1] is not None):
            price[i + 1], lit[i + 1], back[i + 1] = c, r, None
        bl, bo = best[i]
        if bl < MIN_MATCH:
            continue
        lens = set(range(MIN_MATCH, min(bl, 18) + 1)) | {bl}
        for length in lens:
            c = p + 1 + 2 + _ext(length - 4)
            j = i + length
            if c < price[j]:
                price[j], lit[j], back[j] = c, 0, (i, length, bo)
    # walk back: the matches on the best path, in order
    seqs, i = [], n
    while i > 0:
        if back[i] is None:
            i -= 1
        else:
            s, length, off = back[i]
            seqs.append((s, length, off))
            i = s
    seqs.reverse()
    out = bytearray()
    pos = 0
    for s, length, off in seqs:
        _sequence(out, data[pos:s], length, off)
        pos = s + length
    _sequence(out, data[pos:], 0, 0)
    return bytes(out)


def _sequence(out, lits, length, off):
    r = len(lits)
    m = length - 4 if length else 0
    out.append(min(r, 15) << 4 | (min(m, 15) if length else 0))
    if r >= 15:
        x = r - 15
        while x >= 255:
            out.append(255)
            x -= 255
        out.append(x)
    out += lits
    if not length:
        return
    out += bytes([off & 0xFF, off >> 8])
    if m >= 15:
        x = m - 15
        while x >= 255:
            out.append(255)
            x -= 255
        out.append(x)


def _decode(block, cap):
    """lz4d.c in Python: (bytes written, max(write pos - read pos) over the block, ok)"""
    s, e, out = 0, len(block), bytearray()
    worst = 0

    def more(s, length):
        while True:
            if s >= e:
                return None, length
            b = block[s]
            s += 1
            length += b
            if b != 255:
                return s, length

    while True:
        if s >= e:
            return out, worst, False
        t = block[s]
        s += 1
        length = t >> 4
        if length == 15:
            s, length = more(s, length)
            if s is None:
                return out, worst, False
        for _ in range(length):
            if s >= e or len(out) >= cap:
                return out, worst, False
            out.append(block[s])
            s += 1
        worst = max(worst, len(out) - s)
        if s == e:
            return out, worst, True
        if e - s < 2:
            return out, worst, False
        off = block[s] | block[s + 1] << 8
        s += 2
        if not off or off > len(out):
            return out, worst, False
        length = t & 15
        if length == 15:
            s, length = more(s, length)
            if s is None:
                return out, worst, False
        length += 4
        for _ in range(length):
            if len(out) >= cap:
                return out, worst, False
            out.append(out[-off])
        worst = max(worst, len(out) - s)


def decompress(block, n):
    """The n bytes an LZ4 block holds, decoded as the radio decodes it."""
    out, _, ok = _decode(bytes(block), n)
    if not ok or len(out) != n:
        raise ValueError(f"LZ4 block doesn't decode to {n} bytes")
    return bytes(out)


def inplace_excess(block):
    """The largest (bytes written - bytes read) at any point while decoding: decoding in place is
    safe when the packed block starts at least this many bytes after the output's start."""
    _, worst, ok = _decode(bytes(block), 1 << 30)
    if not ok:
        raise ValueError("not an LZ4 block")
    return worst
