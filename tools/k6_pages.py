#!/usr/bin/env python3
"""EXPLORE's code pages for the 8 kHz voice + EXPLORE image (c/k6/kp.h, docs/k6-voice-explore.md).

  k6_pages.py imports OBJ... -o EXTERN.x
      The image's symbols the pages call (their undefined symbols, less what they define
      themselves), as a linker script of EXTERN lines, so the image keeps them although nothing
      in it calls them (explore.c's ex_on_raster, cwid.c's cw_feed, ...). build.rs links it in
      (K6_PAGE_IMPORTS).

  k6_pages.py link --elf IMAGE --page sweep|listen -o PAGE.bin OBJ...
      One page, linked at the image's decoder pool + the module header (c/k6/kp.ld) against the
      image's symbols (ld.lld --just-symbols), then its table checked, then the image's build ID
      appended. PAGE.bin goes into the K6's SPI library with
      `python3 -m modem.k6 mod PAGE.bin --install SLOT --page ...` (sweep: slot 10, listen: 11,
      table: 9).

SPDX-License-Identifier: MIT"""
import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LD = os.environ.get("LD", "ld.lld")
NM = os.environ.get("NM", "llvm-nm")
OBJCOPY = os.environ.get("OBJCOPY", "llvm-objcopy")
READELF = os.environ.get("READELF", "llvm-readelf")
OBJDUMP = os.environ.get("OBJDUMP", "llvm-objdump")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k6_frames  # noqa: E402

KM_HDR = 16
KM_TAG = 8
KP_ID_BYTES = 8
PAGES = {"sweep": 1, "listen": 2, "table": 3}
# hal::arena: the decoder pool starts after the store (4,096) and the 8 kHz PWM ring (1,280)
DEC_OFF = 4096 + 1280


def run(*cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def symbols(objs):
    """(defined, undefined) global symbol names of the objects"""
    defined, undef = set(), set()
    for ln in run(NM, "-g", *objs).splitlines():
        w = ln.split()
        if len(w) == 2 and w[0] == "U":
            undef.add(w[1])
        elif len(w) == 3:
            defined.add(w[2])
    return defined, undef - defined


def build_id(elf):
    m = re.search(r"Build ID:\s*([0-9a-f]+)", run(READELF, "-n", elf))
    if not m:
        sys.exit(f"{elf} has no build ID (link it with --build-id)")
    b = bytes.fromhex(m.group(1))
    if len(b) < KP_ID_BYTES:
        sys.exit(f"{elf}'s build ID is shorter than {KP_ID_BYTES} bytes")
    return b[:KP_ID_BYTES]


def sym(elf, name):
    """(address, size) of a symbol of the image"""
    for ln in run(NM, "-S", elf).splitlines():
        w = ln.split()
        if len(w) == 4 and w[3] == name:
            return int(w[0], 16), int(w[1], 16)
    sys.exit(f"{elf} has no symbol {name}")


def cmd_imports(a):
    _, need = symbols(a.objs)
    with open(a.output, "w") as f:
        f.write("/* the image's symbols that EXPLORE's code pages call (tools/k6_pages.py) */\n")
        for s in sorted(need):
            f.write(f"EXTERN({s})\n")
    print(f"{a.output}: {len(need)} symbols the pages call: {' '.join(sorted(need))}")


def cmd_link(a):
    arena, arena_bytes = sym(a.elf, "k6_arena")
    cap = arena_bytes - DEC_OFF                 # the decoder pool: the page's header, code, tag
    org = arena + DEC_OFF + KM_HDR
    with tempfile.TemporaryDirectory() as d:
        elf = os.path.join(d, "page.elf")
        bin_ = os.path.join(d, "page.bin")
        run(LD, "-T", os.path.join(ROOT, "c", "k6", "kp.ld"), f"--defsym=KP_ORG={org:#x}",
            f"--just-symbols={a.elf}", "--gc-sections", "-e", "kp_tab", "-o", elf, *a.objs)
        run(OBJCOPY, "-O", "binary", "--only-section=.page", elf, bin_)
        code = open(bin_, "rb").read()
        names = {int(w[0], 16): w[2] for w in (ln.split() for ln in run(NM, elf).splitlines()) if len(w) == 3}
        dis = run(OBJDUMP, "-d", "-C", "--no-show-raw-insn", elf).splitlines()
    # the table: magic, its own address, then each function inside the page (Thumb: odd)
    magic, here = struct.unpack_from("<II", code)
    pid, n = magic >> 16 & 0xFF, magic >> 24
    if magic & 0xFFFF != 0x504B or pid != PAGES[a.page] or here != org:
        sys.exit(f"page {a.page}: bad table head {magic:#x} {here:#x} (want id {PAGES[a.page]} at {org:#x})")
    deep = (0, "")
    for i, f in enumerate(struct.unpack_from(f"<{n}I", code, 8)):
        if not (f & 1 and org <= f - 1 < org + len(code)):
            sys.exit(f"page {a.page}: function {i} at {f:#x} is outside the page")
        # its stack: the deepest chain of direct calls inside the page (k6_frames.py), which the
        # image's --deep report can't see (it calls the page through a pointer)
        d = k6_frames.deepest(dis, names[f - 1])[0]
        deep = max(deep, (d, names[f - 1]))
    code += build_id(a.elf)
    if KM_HDR + len(code) + KM_TAG > cap:
        sys.exit(f"page {a.page}: {len(code)} B, more than the decoder pool holds "
                 f"({cap - KM_HDR - KM_TAG} B of code)")
    with open(a.output, "wb") as f:
        f.write(code)
    print(f"{a.output}: page {a.page}, {n} functions, {len(code)} B at {org:#x} "
          f"({cap - KM_HDR - KM_TAG - len(code)} B of the pool to spare), image build ID {code[-8:].hex()}; "
          f"stack: {deep[0]} B under {deep[1]} (direct calls)")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("imports")
    i.add_argument("objs", nargs="+")
    i.add_argument("-o", "--output", required=True)
    k = sub.add_parser("link")
    k.add_argument("--elf", required=True, help="the voice + EXPLORE image the page is for")
    k.add_argument("--page", required=True, choices=PAGES)
    k.add_argument("-o", "--output", required=True)
    k.add_argument("objs", nargs="+", help="the page's objects: kp_tab for the page first")
    a = p.parse_args()
    {"imports": cmd_imports, "link": cmd_link}[a.cmd](a)


if __name__ == "__main__":
    main()
