"""UV-21 Pro RAM modules: build one, wrap it in its header, and upload it over the cable
(radios/uv21/fw/src/umod.rs, radios/uv21/mod/uv21_mod.h).

  python -m modem.uv21mod build radios/uv21/mod/hello.c -o hello.um21
  python -m modem.uv21mod build radios/uv21/mod/regsweep.c -D SW_REG=0x1F -D SW_VALS=0x1657,0xC65A \\
      -D SW_HZ=439500000 -o sweep.um21
  python -m modem.uv21mod run /dev/ttyUSB2 radios/uv21/mod/hello.c --log run.txt

Built with clang for the Cortex-M4 without FPU, -fropi, and checked position-independent the
same way as the K6's and the UV-380's (linked at two addresses, the bytes must match). Unsigned:
the cable that carries it already reaches the bootloader, which takes whole images.
SPDX-License-Identifier: MIT
"""
import argparse
import os
import re
import struct
import sys
import time
import zlib

from . import picmod

ABI = 2
MAGIC = b"UM21"
HDR = 12
ROOM = 12288              # umod.rs ROOM: header, code and the module's RAM
CHUNK = 48               # bytes a "um w" line carries
ENTRY = "um_main"
ROOT = picmod.ROOT
MOD_DIR = os.path.join(ROOT, "radios", "uv21", "mod")
LINK = os.path.join(MOD_DIR, "module.ld")
CFLAGS = [
    "--target=thumbv7em-none-eabi",
    "-mcpu=cortex-m4",
    "-mfloat-abi=soft",
    *picmod.COMMON_CFLAGS,
    "-I" + MOD_DIR,
]
# a -D setting: a C name, then a number or a list of up to 32 numbers (hex or decimal)
DEFINE = re.compile(r"[A-Z][A-Z0-9_]{0,23}=(0x[0-9A-Fa-f]{1,8}|[0-9]{1,10})(,(0x[0-9A-Fa-f]{1,8}|[0-9]{1,10})){0,31}")


def build(src, defines=()):
    """C source and -D settings ("NAME=VALUE") -> (code bytes, entry offset)."""
    for d in defines:
        if not DEFINE.fullmatch(d):
            raise ValueError(f"-D {d}: NAME=number or NAME=n1,n2,... (hex or decimal)")
    return picmod.build(src, cflags=CFLAGS + ["-D" + d for d in defines], link=LINK, entry_sym=ENTRY)


def image(code, entry):
    """The header (UM21, ABI, entry offset, length, 0) and the code"""
    n = HDR + len(code)
    if n > ROOM - 256:
        raise ValueError(f"module {n} bytes: {ROOM - 256} at most (the rest is its RAM)")
    return MAGIC + struct.pack("<HHHH", ABI, HDR + entry, n, 0) + bytes(code)


def crc16_xmodem(data):
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = (crc << 1 ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def lines(img):
    """The upload, as the console lines umod.rs reads (each wants its "um ok" before the next)"""
    out = [f"um b {len(img):04x}"]
    for off in range(0, len(img), CHUNK):
        part = img[off:off + CHUNK]
        out.append(f"um w {off:04x} {part.hex()} {crc16_xmodem(part):04x}")
    out.append(f"um r {zlib.crc32(img) & 0xFFFFFFFF:08x}")
    return out


class Failed(Exception):
    pass


def upload(port, img, log=print, run_timeout=120.0, line_timeout=2.0, tries=3):
    """Sends the image and runs it; returns (the module's lines, its return value). port: an open
    pyserial port at 115200 (the radio in SURF)."""
    def answer(want, timeout):
        end, buf = time.time() + timeout, b""
        while time.time() < end:
            buf += port.read(port.in_waiting or 1)
            while b"\n" in buf:
                ln, buf = buf.split(b"\n", 1)
                t = ln.decode("ascii", "replace").strip()
                if t.startswith("um "):
                    return t
        return None

    ups = lines(img)
    for ln in ups[:-1]:
        for _ in range(tries):
            port.write((ln + "\n").encode())
            got = answer("um ", line_timeout)
            if got and got.startswith("um ok"):
                break
            log(f"retry: {ln[:16]} -> {got}")
        else:
            raise Failed(f"no 'um ok' for {ln[:24]}")
    port.write((ups[-1] + "\n").encode())
    got = answer("um ", line_timeout)
    if got != "um run":
        raise Failed(f"run refused: {got}")
    out, end, buf = [], time.time() + run_timeout, b""
    while time.time() < end:
        buf += port.read(port.in_waiting or 1)
        while b"\n" in buf:
            ln, buf = buf.split(b"\n", 1)
            t = ln.decode("ascii", "replace").strip()
            if t.startswith("um end "):
                return out, int(t[7:], 16)
            if t:
                out.append(t)
                log(t)
    raise Failed(f"no 'um end' in {run_timeout} s")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("source")
    b.add_argument("-D", dest="defines", action="append", default=[])
    b.add_argument("-o", "--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("port")
    r.add_argument("source", help="a .c module, or a built .um21 image")
    r.add_argument("-D", dest="defines", action="append", default=[])
    r.add_argument("--log")
    r.add_argument("--run-timeout", type=float, default=120.0)
    a = ap.parse_args(argv)
    if a.source.endswith(".um21"):
        img = open(a.source, "rb").read()
    else:
        img = image(*build(a.source, a.defines))
    if a.cmd == "build":
        open(a.out, "wb").write(img)
        print(f"{a.out}: {len(img)} bytes")
        return 0
    import serial
    log = open(a.log, "a") if a.log else None

    def say(t):
        print(t)
        if log:
            log.write(t + "\n")
            log.flush()
    with serial.Serial(a.port, 115200, timeout=0.1) as port:
        port.reset_input_buffer()
        out, ret = upload(port, img, log=say, run_timeout=a.run_timeout)
    say(f"um end {ret:08x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
