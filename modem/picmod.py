"""Shared position-independent Thumb RAM module builder and SipHash-2-4 signer.

Used by the per-radio module tools (modem.k6mod, modem.uv21mod, modem.uv380mod). Compiles C with
clang -fropi, links at two base addresses to prove position independence, extracts the entry
symbol offset, and (for radios using c/common/mod_core.h) signs the 16-byte header + body with
SipHash-2-4 under the radio's 16-byte module key. Stdlib-only.
SPDX-License-Identifier: MIT
"""
import os
import re
import secrets
import struct
import subprocess
import tempfile

ABI = 1
HDR = 16
TAG = 8
PLACEHOLDER = b"sotto-K6MOD-key!"    # unkeyed firmware image placeholder (k6_mod.c / mod_core.c)
ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))

COMMON_CFLAGS = [
    "-mthumb",
    "-Os",
    "-fropi",
    "-ffreestanding",
    "-fno-builtin",
    "-nostdlib",
    "-ffunction-sections",
    "-fdata-sections",
    "-fomit-frame-pointer",
    "-Wall",
    "-Werror",
]

_M = (1 << 64) - 1


def _rotl(x, b):
    return ((x << b) | (x >> (64 - b))) & _M


def _rounds(v, n):
    for _ in range(n):
        v[0] = (v[0] + v[1]) & _M; v[1] = _rotl(v[1], 13) ^ v[0]; v[0] = _rotl(v[0], 32)
        v[2] = (v[2] + v[3]) & _M; v[3] = _rotl(v[3], 16) ^ v[2]
        v[0] = (v[0] + v[3]) & _M; v[3] = _rotl(v[3], 21) ^ v[0]
        v[2] = (v[2] + v[1]) & _M; v[1] = _rotl(v[1], 17) ^ v[2]; v[2] = _rotl(v[2], 32)


def siphash24(key, m):
    """SipHash-2-4 of bytes m under a 16-byte key, as an int (compared little-endian on radio)."""
    k0, k1 = struct.unpack("<QQ", bytes(key))
    v = [k0 ^ 0x736F6D6570736575, k1 ^ 0x646F72616E646F6D,
         k0 ^ 0x6C7967656E657261, k1 ^ 0x7465646279746573]
    m = bytes(m)
    n = len(m) - len(m) % 8
    for i in range(0, n, 8):
        mi = struct.unpack_from("<Q", m, i)[0]
        v[3] ^= mi; _rounds(v, 2); v[0] ^= mi
    b = (len(m) & 0xFF) << 56 | int.from_bytes(m[n:], "little")
    v[3] ^= b; _rounds(v, 2); v[0] ^= b
    v[2] ^= 0xFF; _rounds(v, 4)
    return v[0] ^ v[1] ^ v[2] ^ v[3]


def new_key(path):
    """Write a random 16-byte key as 32 hex digits, readable by you only."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_hex(16) + "\n")


def read_key(path):
    """Read a 16-byte key stored as 32 hex digits."""
    k = bytes.fromhex(open(os.path.expanduser(path)).read().strip())
    if len(k) != 16:
        raise ValueError(f"{path}: want 32 hex digits (16 bytes), got {len(k)} bytes")
    return k


def key_image(data, key):
    """Write a 16-byte key over the unkeyed firmware image's single PLACEHOLDER."""
    if len(key) != 16:
        raise ValueError("want a 16-byte key")
    n = data.count(PLACEHOLDER)
    if n != 1:
        raise ValueError(f"the key placeholder is in the image {n} times, want once "
                         "(an image built with K6_MOD_KEY_FILE, or without the module loader?)")
    return data.replace(PLACEHOLDER, bytes(key))


def source_int(src, macro, default=1):
    """Read an integer macro (#define <macro> <n>) from a module's C source, or default."""
    m = re.search(rf"^\s*#\s*define\s+{re.escape(macro)}\s+(\d+)", open(src).read(), re.M)
    return int(m.group(1)) if m else default


def source_opt(src, macro="K6_MODULE_OPT"):
    """Read an optimisation override (#define <macro> s|z) from a module's C source, or 's'."""
    m = re.search(rf"^\s*#\s*define\s+{re.escape(macro)}\s+([sz])\b", open(src).read(), re.M)
    return m.group(1) if m else "s"


def build(src, *, cflags, link, entry_sym, cc="clang", ld="ld.lld", objcopy="llvm-objcopy",
          nm="llvm-nm", opt_macro="K6_MODULE_OPT"):
    """C source -> (code bytes, entry offset). Refuses code that isn't position-independent."""
    with tempfile.TemporaryDirectory() as d:
        obj = os.path.join(d, "m.o")
        opt = source_opt(src, opt_macro) if opt_macro else "s"
        flags = [("-O" + opt) if f == "-Os" else f for f in cflags]
        subprocess.run([cc, *flags, "-c", src, "-o", obj], check=True)
        bins = []
        for base in (0, 0x1000):
            elf = os.path.join(d, f"m{base}.elf")
            subprocess.run([ld, "-T", link, "--defsym", f"MODULE_BASE={base}", "--gc-sections",
                            "--no-undefined", obj, "-o", elf], check=True)
            out = os.path.join(d, f"m{base}.bin")
            subprocess.run([objcopy, "-O", "binary", "--only-section=.text", elf, out], check=True)
            bins.append(open(out, "rb").read())
            if base == 0:
                syms = subprocess.run([nm, elf], check=True, capture_output=True, text=True).stdout
        if bins[0] != bins[1]:
            raise ValueError(f"{src}: not position-independent (linked at 0 and 0x1000 the bytes differ)")
        entry = next(int(ln.split()[0], 16) for ln in syms.splitlines() if ln.split()[-1] == entry_sym)
    return bins[0], entry


def signed_image(body, entry, serial, key, magic, level=1, flags=0, tlm=0, slot=0, abi=ABI):
    """Build a mod_core.h image: 16-byte header + body + 8-byte SipHash-2-4 tag."""
    body = bytes(body)
    if len(magic) != 3:
        raise ValueError("the magic is three bytes")
    if len(body) > 0xFFFF:
        raise ValueError("bad entry offset or code length")
    hdr = bytes(magic) + bytes([abi]) + struct.pack(
        "<HHIBBBB", len(body), entry, serial & 0xFFFFFFFF, level, flags, tlm, slot
    )
    signed = hdr + body
    return signed + struct.pack("<Q", siphash24(key, signed))
