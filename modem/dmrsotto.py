"""sotto over the HR_C6000's DMR modem: a message as rate 1/2 data blocks (docs/uv380-c6000-modem.md,
"Sotto in DMR data bursts"). The C version for the radio is c/common/dmr_sotto.h; tests check the
two agree byte for byte.

A rate 1/2 data burst carries 12 bytes, BPTC(196,96) coded by the chip, one burst per 60 ms in
direct mode: 9 payload bytes a burst is 1200 bit/s, exactly sotto 1200's rate (6 bytes per 40 ms
superframe). Each block:

  byte 0      bits 7-5 kind (0 data, 1 head, 2 text, 3 end), bits 4-0 sequence number (mod 32)
  bytes 1-9   payload
  bytes 10-11 CRC-16-CCITT (poly 0x1021, init 0xFFFF, no final xor) of bytes 0-9, big endian

A message is: one head block (msg id, codec, superframe count (2 bytes), text block count, 4 zero
bytes), the text blocks (9 ASCII bytes each: the station's ident, at least the callsign), the
superframes back to back in data blocks (the last one zero padded), one end block (msg id, byte
count (2 bytes)). A receiver places each block by the sequence numbers' steps from the head
(mod 32, so up to 31 blocks lost in a row), never by counting the blocks it got. The rate 1/2 blocks carry no
CRC of their own in DMR (the packet's CRC-32 covers them), so the per-block CRC here is what lets
a receiver drop a bad block and conceal that one superframe and a half.

  python3 -m modem.dmrsotto pack MSG.bit OUT.blk [--id N] [--text AG7EW]
  python3 -m modem.dmrsotto unpack IN.blk OUT.bit

MIT licence."""
import argparse

KIND_DATA, KIND_HEAD, KIND_TEXT, KIND_END = 0, 1, 2, 3
CODEC_SOTTO1200 = 0x12
SF_BYTES = 6
PAYLOAD = 9
BLOCK = 12


def crc16(by):
    c = 0xFFFF
    for b in by:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def block(kind, seq, payload):
    p = bytes(payload) + bytes(PAYLOAD - len(payload))
    assert len(p) == PAYLOAD
    b = bytes([(kind & 7) << 5 | (seq & 31)]) + p
    c = crc16(b)
    return b + bytes([c >> 8, c & 255])


def check(blk):
    """-> (kind, seq, payload) or None (CRC fails)"""
    blk = bytes(blk)
    if len(blk) != BLOCK or crc16(blk[:10]) != (blk[10] << 8 | blk[11]):
        return None
    return blk[0] >> 5, blk[0] & 31, blk[1:10]


def pack(msg, msg_id=1, text="AG7EW", codec=CODEC_SOTTO1200):
    """superframe bytes (a multiple of 6) -> [12-byte blocks]"""
    msg = bytes(msg)
    assert len(msg) % SF_BYTES == 0
    nsf = len(msg) // SF_BYTES
    t = text.encode("ascii")
    tb = [t[i:i + PAYLOAD] for i in range(0, len(t), PAYLOAD)] or [b""]
    out = [block(KIND_HEAD, 0, bytes([msg_id & 255, codec, nsf >> 8 & 255, nsf & 255, len(tb)]))]
    for c in tb:
        out.append(block(KIND_TEXT, len(out), c))
    for i in range(0, len(msg), PAYLOAD):
        out.append(block(KIND_DATA, len(out), msg[i:i + PAYLOAD]))
    out.append(block(KIND_END, len(out), bytes([msg_id & 255, len(msg) >> 8 & 255, len(msg) & 255])))
    return out


def unpack(blocks):
    """[12-byte blocks, or None for a burst heard with errors] in air order -> dict: id, codec,
    text, msg (the superframe bytes; a lost block's 9 bytes are 0xFF), lost (data blocks
    missing), bad (blocks whose CRC failed), end (the end block came); None with no head block"""
    head, pos, last, got, bad, end = None, 0, 0, {}, 0, False
    for blk in blocks:
        r = None if blk is None else check(blk)
        if r is None:
            bad += blk is not None
            continue
        kind, seq, p = r
        if kind == KIND_HEAD:
            head, pos, last, got, end = p, 0, seq, {}, False
            continue
        if head is None:
            continue
        step = (seq - last) % 32
        if step == 0:
            continue                    # the same block again
        pos += step
        last = seq
        if kind == KIND_END:
            end = True
        else:
            got[pos] = (kind, p)
    if head is None:
        return None
    nsf, ntext = head[2] << 8 | head[3], head[4]
    nbytes = nsf * SF_BYTES
    text = b"".join(got[k][1].rstrip(b"\0") for k in range(1, ntext + 1) if k in got)
    msg, lost = bytearray(), 0
    for i in range((nbytes + PAYLOAD - 1) // PAYLOAD):
        k = 1 + ntext + i
        if k in got and got[k][0] == KIND_DATA:
            msg += got[k][1]
        else:
            msg += b"\xff" * PAYLOAD
            lost += 1
    return {"id": head[0], "codec": head[1], "text": text.decode("ascii", "replace"),
            "msg": bytes(msg[:nbytes]), "lost": lost, "bad": bad, "end": end}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("bit")
    p.add_argument("out")
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--text", default="AG7EW")
    u = sub.add_parser("unpack")
    u.add_argument("blk")
    u.add_argument("out")
    a = ap.parse_args()
    if a.cmd == "pack":
        msg = open(a.bit, "rb").read()
        msg = msg[:len(msg) - len(msg) % SF_BYTES]
        open(a.out, "wb").write(b"".join(pack(msg, a.id, a.text)))
    else:
        raw = open(a.blk, "rb").read()
        r = unpack([raw[i:i + BLOCK] for i in range(0, len(raw), BLOCK)])
        if r is None:
            raise SystemExit("no head block")
        open(a.out, "wb").write(r["msg"])
        print({k: v for k, v in r.items() if k != "msg"})


if __name__ == "__main__":
    main()
