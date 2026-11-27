"""K6 RAM modules: build a module, sign it, and cut it into 0xAB packets (c/k6/k6_mod.h).

  python -m modem.k6 modkey ~/.config/sotto/k6-mod.key     a new random 16-byte key (hex)
  K6_MOD_KEY_FILE=~/.config/sotto/k6-mod.key sh firmware/k6-aura/build.sh
                                                           an image that accepts modules
  python -m modem.k6 mod hello.wav c/k6/mod/hello.c --key-file ~/.config/sotto/k6-mod.key \\
      --phy F --payload XL                                 the module as transmit audio

The module is C built with clang for the M0 with -fropi, so its code and constants are reached
PC-relative and it runs wherever the K6 puts it. build() links it at two addresses and refuses
it unless both come out byte for byte the same. Image layout and packet format: k6_mod.h and
docs/k6-modules.md. SPDX-License-Identifier: MIT
"""
import os
import struct

import numpy as np

from . import pager, picmod

MAGIC = 0xAB
MAGIC_ARQ = 0xAE         # the same chunk, reply wanted: the K6 answers with 0xAF reports
MAGIC_REPORT = 0xAF
ABI = picmod.ABI
LEVEL = 1
TLM_MAX = 12             # telemetry parts a module may ask for
ARENA_BYTES = 11776     # the block a module runs in (SPI-codebook images, 2026-11-08 on)
ARENA_BYTES_12 = 14976  # the same block in the images with module API level 12 (2026-11-19, hal::ram0)
ARENA_BYTES_13 = 15408  # level 13 (later 2026-11-19: sotto's C statics and EXPLORE's state in hal::ram0)
ARENA_BYTES_16 = 16688  # level 16 (leaner pager-only App + boot_init_app + FX_SYNTH_STATE on hiss: +1,280 B)
LEVEL_BIG_ARENA = 12    # a module that needs more than ARENA_BYTES declares this level (or 13 for
                        # more than ARENA_BYTES_12): older images refuse it (header level > their
                        # KM_LEVEL) instead of running it
TLM_BYTES = 696         # its top, where the K6 keeps the say lines for telemetry while a module runs
TLM_ROOM = ARENA_BYTES - TLM_BYTES   # an image (header + code + tag) longer than this, with telemetry
                                     # on, has its tail written over by its own say lines (11-18:
                                     # c4ftm's size table at 11,080-11,182, c4sotto's last call)


def arena_bytes(level):
    """The module block a module of this API level runs in on every image that takes it."""
    if level >= 16:
        return ARENA_BYTES_16
    if level >= 13:
        return ARENA_BYTES_13
    return ARENA_BYTES_12 if level >= LEVEL_BIG_ARENA else ARENA_BYTES


def tlm_room(level):
    """TLM_ROOM for a module of this API level."""
    return arena_bytes(level) - TLM_BYTES
LIB_FLAG = 0x01          # header byte 13: keep it in the SPI module library (c/k6/k6_lib.h)
PAGE_FLAG = 0x02         # with LIB_FLAG: a code page the image loads itself, never run (kl_page)
CARDS_FLAG = 0x04        # with LIB_FLAG: a deck of help cards, never run
LZ4_FLAG = 0x08          # the body is packed (API level 15 on, docs/k6-lz4.md): the unpacked code
                         # length (u16 LE) and one LZ4 block of the code (modem/lz4.py)
LEVEL_LZ4 = 15           # the API level a packed image declares, so older images refuse it
LZ4_MARGIN = 8           # k6_mod.h KM_LZ4_MARGIN: room km_unpack wants beyond the unpacked image
LIB_SLOTS = 32           # library slots, slot n at 0x110000 + n * 16 KB (12 before the release plan)
NAME_MAGIC = b"K6NM"     # a module's name block (K6_MODULE_NAME, k6_mod.h): first in its code
NAME_LEN = 12
LIB_SLOT_BYTES = 0x4000
TLM_PART = 57
HDR = picmod.HDR
TAG = picmod.TAG
ROOT = picmod.ROOT
LINK = os.path.join(ROOT, "c", "k6", "mod", "module.ld")
CFLAGS = ["--target=thumbv6m-none-eabi", "-mcpu=cortex-m0", "-mfloat-abi=soft", *picmod.COMMON_CFLAGS,
          "-I" + os.path.join(ROOT, "c", "k6"),
          "-isystem", os.path.join(ROOT, "c", "k6", "mod", "libc")]   # string.h for C written against a libc (m0rt.h)
ENTRY = "k6_main"

siphash24 = picmod.siphash24
new_key = picmod.new_key
read_key = picmod.read_key


def build(src, cc="clang", ld="ld.lld", objcopy="llvm-objcopy", nm="llvm-nm", cflags=None, link=None,
          entry_sym=None):
    """C source -> (code bytes, entry offset). Refuses code that isn't position-independent.
    cflags, link, entry_sym: another radio's (modem.uv380mod); the K6's by default."""
    return picmod.build(src, cflags=cflags or CFLAGS, link=link or LINK, entry_sym=entry_sym or ENTRY,
                        cc=cc, ld=ld, objcopy=objcopy, nm=nm)


def bin_entry(code):
    """a built module .bin's entry offset: module.ld puts k6_main first, after the name block when
    the module has one (K6_MODULE_NAME). A .bin carries no symbols, so this is how to find it:
    taking 0 for a named module jumps into its name (the K6 hung that way on 2026-11-14)."""
    return 4 + NAME_LEN if code[:4] == NAME_MAGIC else 0


def module_name(code):
    """The name a module's code carries (K6_MODULE_NAME), or None"""
    if len(code) < 4 + NAME_LEN or code[:4] != NAME_MAGIC:
        return None
    return code[4:4 + NAME_LEN].split(b"\0")[0].decode("ascii", "replace")


def source_level(src):
    """The API level a module's C source declares (#define K6_MODULE_LEVEL n), or 1."""
    return picmod.source_int(src, "K6_MODULE_LEVEL", LEVEL)


source_opt = picmod.source_opt


def lz4_body(code):
    """A packed image's body (LZ4_FLAG): the code's length, then the code as one LZ4 block."""
    from . import lz4
    code = bytes(code)
    block = lz4.compress(code)
    if lz4.decompress(block, len(code)) != code:
        raise AssertionError("LZ4 round trip")
    # km_unpack decodes in place, its packed bytes above its output: the proven bound it sizes
    # its margin by (k6_mod.h KM_LZ4_MARGIN) holds for this stream
    if lz4.inplace_excess(block) > len(code) - len(block) + 3 + len(block) // 255:
        raise AssertionError("LZ4 stream outside km_unpack's in-place bound")
    return struct.pack("<H", len(code)) + block


def unpacked_room(code_len, packed_len):
    """The RAM km_unpack needs for a packed image: the unpacked image and its margin"""
    return HDR + code_len + TAG + LZ4_MARGIN + (packed_len >> 7)


def image(code, entry, serial, key, level=LEVEL, tlm=0, lib=None, page=False, magic=b"K6M", cards=False,
          lz4=False):
    """Header + code + SipHash tag (k6_mod.h). tlm: text parts of say lines the K6 sends back
    after the module runs, when the upload asks for a reply (packets(..., arq=True)). lib: a
    slot of the K6's SPI module library (c/k6/k6_lib.h, 0..LIB_SLOTS-1): the K6 keeps the
    module there (flag LIB_FLAG) instead of running it, and runs it later on control action lib.
    page (with lib): a code page (PAGE_FLAG, tools/k6_pages.py): kept in the slot, never run as a
    module; the image that it was linked against loads it itself. magic: the radio whose API
    table the module is for (b"U3M" the UV-380, modem.uv380mod). cards (with lib): a deck of
    help cards (CARDS_FLAG), kept in the slot and never run. lz4: packed (LZ4_FLAG, API level
    15 at least): smaller over the air, and a module or page stays packed in its library slot,
    unpacking into RAM each time it loads; a deck or a module that runs at once unpacks on
    arrival."""
    code = bytes(code)
    if entry >= len(code) or entry & 1 or len(code) > 0xFFFF:
        raise ValueError("bad entry offset or code length")
    if not 0 <= tlm <= TLM_MAX:
        raise ValueError(f"telemetry parts 0..{TLM_MAX}")
    if lib is not None and not 0 <= lib < LIB_SLOTS:
        raise ValueError(f"library slot 0..{LIB_SLOTS - 1}")
    body = lz4_body(code) if lz4 else code
    if lz4 and magic != b"K6M":
        raise ValueError("packed images are the K6's")
    if lz4:
        level = max(level, LEVEL_LZ4)
        if unpacked_room(len(code), len(body)) > arena_bytes(level):
            raise ValueError(f"{len(code)} bytes of code unpacked is more than the module block takes")
    if lib is not None and 16 + len(body) + 8 > LIB_SLOT_BYTES:
        raise ValueError(f"{16 + len(body) + 8} bytes is more than a library slot ({LIB_SLOT_BYTES})")
    if (page or cards) and lib is None:
        raise ValueError("a page or a deck goes into a library slot")
    if page and cards:
        raise ValueError("a page or a deck, not both")
    flags, slot = (0, 0) if lib is None else (LIB_FLAG | (PAGE_FLAG if page else 0) | (CARDS_FLAG if cards else 0), lib)
    flags |= LZ4_FLAG if lz4 else 0
    return picmod.signed_image(body, entry, serial, key, magic, level=level, flags=flags, tlm=tlm,
                               slot=slot, abi=ABI)


def unpack(img, key):
    """A packed image as the K6 holds it once km_unpack has run: header (code length unpacked,
    LZ4_FLAG cleared), code, and the tag over them. An image that isn't packed comes back as it
    is."""
    img = bytes(img)
    if not img[13] & LZ4_FLAG:
        return img
    from . import lz4
    n = struct.unpack_from("<H", img, 4)[0]
    raw = struct.unpack_from("<H", img, HDR)[0]
    code = lz4.decompress(img[HDR + 2:HDR + n], raw)
    hdr = img[:4] + struct.pack("<H", raw) + img[6:13] + bytes([img[13] & ~LZ4_FLAG]) + img[14:16]
    return hdr + code + struct.pack("<Q", siphash24(key, hdr + code))


def data_bytes(length):
    return length - 6


def packets(img, xfer, length=128, arq=False):
    """The image as whitened module packets (length 64 = L, 128 = XL), chunk 0 first. arq: magic
    0xAE, so the K6 reports back over the air (images from 2026-11-06 on; older ones ignore
    the packets, as any unknown magic)."""
    dl = data_bytes(length)
    n = -(-len(img) // dl)
    if n > 255:
        raise ValueError(f"{len(img)} bytes is {n} chunks; 255 at most")
    out = []
    for i in range(n):
        d = bytes([MAGIC_ARQ if arq else MAGIC, xfer & 0xFF, i, n]) + img[i * dl:(i + 1) * dl].ljust(dl, b"\0")
        d += struct.pack(">H", pager.crc16_ccitt(d))
        out.append(pager._whiten(d))
    return out


def parse(payload):
    """(transfer id, chunk, count, data) of a whitened module packet, or None."""
    d = pager._whiten(payload)
    if d[0] not in (MAGIC, MAGIC_ARQ) or pager.crc16_ccitt(d[:-2]) != struct.unpack(">H", d[-2:])[0]:
        return None
    return d[1], d[2], d[3], d[4:-2]


# ---- the K6's reports (k6_mod.h, km_report_status / km_report_text) ----

REP_STATUS, REP_TEXT = 1, 2
STATES = {0: "partway", 1: "ran", 2: "refused"}
# byte 9's high bits (k6_mod.h KM_RATE_*, images from 2026-11-16): the rate ladder (c/k6/k6_rate.h)
RATE_D24, RATE_AT_D24, RATE_WHY_SHIFT = 0x10, 0x20, 6
RATE_WHYS = {0: None, 1: "crc", 2: "quiet", 3: "revert"}
RESULTS = {1: "chunk", 2: "repeat", 3: "ready", -1: "bad CRC", -2: "bad chunk", -3: "bad header",
           -4: "wrong key", -5: "no key in the image", -6: "replay (old serial)"}


def parse_report(payload):
    """A whitened 64-byte packet -> the K6's report as a dict, or None if it isn't one.
    kind "status": xfer, have, count, r, state, serial, missing (chunk numbers), call, ret, ms,
    stack_free, and the rate ladder: rates ("F12,F18,D24" from an image that takes D/2400, else
    "F12,F18"), rung (the K6's now: "D24" or "F18") and fell (why it last dropped from D/2400
    since the last report: "crc", "quiet", "revert" or None); kind "text": text. Both: seq,
    part, parts."""
    if len(payload) != 64:
        return None
    d = pager._whiten(bytes(payload))
    if d[0] != MAGIC_REPORT or pager.crc16_ccitt(d[:62]) != struct.unpack(">H", d[62:64])[0]:
        return None
    rep = dict(seq=d[2], part=d[3], parts=d[4])
    if d[1] == REP_STATUS:
        count = d[7]
        bits = int.from_bytes(d[14:46], "little")
        rep.update(kind="status", xfer=d[5], have=d[6], count=count, r=struct.unpack("b", d[8:9])[0],
                   state=STATES.get(d[9] & 0x0F, str(d[9] & 0x0F)), serial=struct.unpack("<I", d[10:14])[0],
                   rates="F12,F18,D24" if d[9] & RATE_D24 else "F12,F18",
                   rung="D24" if d[9] & RATE_AT_D24 else "F18",
                   fell=RATE_WHYS[d[9] >> RATE_WHY_SHIFT],
                   missing=[i for i in range(count) if bits >> i & 1],
                   call=d[46:52].split(b"\0")[0].decode("ascii", "replace"),
                   ret=struct.unpack("<i", d[52:56])[0], ms=struct.unpack("<I", d[56:60])[0],
                   stack_free=struct.unpack("<H", d[60:62])[0])
    elif d[1] == REP_TEXT:
        rep.update(kind="text", text=d[5:62].split(b"\0")[0].decode("ascii", "replace"))
    else:
        return None
    return rep


def reports(x, fs, phy="F", baud=1800, invert=False):
    """Audio (a recording of the K6, at any rate) -> [(time s, report dict)] for every 0xAF
    packet whose CRC checks, in order. The demodulator takes either polarity by itself."""
    from . import fsk
    d = fsk.Demodulator(phy, baud, fsk.FS, 64)
    y = fsk.resample(np.asarray(x, dtype=float), fs, fsk.FS)
    out = []
    if len(y) < fsk.FS // 2:          # shorter than one report: the filters need more than this
        return out
    for start, p, _, _ in d.demodulate(-y if invert else y):
        for q in (p, pager.swap_words(p)):
            r = parse_report(q)
            if r is not None:
                out.append((start / fsk.FS, r))
                break
    return out


def collect(reps):
    """Reports -> the newest status (dict, or None) and its telemetry text, its parts joined in
    order (a part heard once is enough; "[part N lost]" for one never heard)."""
    st = None
    for _, r in reps:
        if r["kind"] == "status":
            st = r
    if st is None:
        return None, ""
    parts = {r["part"]: r["text"] for _, r in reps if r["kind"] == "text" and r["seq"] == st["seq"]}
    text = "".join(parts.get(i, f"[part {i} lost]\n") for i in range(1, st["parts"]))
    return st, text


PLACEHOLDER = picmod.PLACEHOLDER
key_image = picmod.key_image
