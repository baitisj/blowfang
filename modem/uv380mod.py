"""UV-380 RAM modules: build a module for the Cortex-M4F, sign it, write the image
(radios/uv380/src/uv_api.h, c/common/mod_core.h).

  python -m modem.uv380mod radios/uv380/mod/hello.c hello.u3m --key-file ~/.config/sotto/k6-mod.key

Same image and SipHash-2-4 tag as a K6 module (modem.k6mod), with header magic "U3M" so it only
runs against the UV-380's table, built with clang for thumbv7em hard float (-fropi) and checked
position-independent the same way (linked at two addresses, the bytes must match).
SPDX-License-Identifier: MIT
"""
import argparse
import os
import re
import sys
import time

from . import k6mod

MAGIC = b"U3M"
LEVEL = 1
ARENA = 16384            # UVM_ARENA
ENTRY = "uv_main"
ROOT = k6mod.ROOT
LINK = os.path.join(ROOT, "radios", "uv380", "mod", "module.ld")
CFLAGS = [
    "--target=thumbv7em-none-eabihf",
    "-mcpu=cortex-m4",
    "-mfpu=fpv4-sp-d16",
    "-mfloat-abi=hard",
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
    "-I" + os.path.join(ROOT, "radios", "uv380", "src"),
]


def build(src):
    """C source -> (code bytes, entry offset)."""
    return k6mod.build(src, cflags=CFLAGS, link=LINK, entry_sym=ENTRY)


def source_level(src):
    """The API level a module's C source declares (#define UV_MODULE_LEVEL n), or 1."""
    m = re.search(r"^\s*#\s*define\s+UV_MODULE_LEVEL\s+(\d+)", open(src).read(), re.M)
    return int(m.group(1)) if m else LEVEL


def image(code, entry, serial, key, level=LEVEL):
    img = k6mod.image(code, entry, serial, key, level, magic=MAGIC)
    if len(img) > ARENA:
        raise ValueError(f"{len(img)} bytes is more than the UV-380's module arena ({ARENA})")
    return img


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m modem.uv380mod", description=__doc__.splitlines()[0])
    p.add_argument("source", help="module C source")
    p.add_argument("output", help="signed image")
    p.add_argument("--key-file", required=True, help="e.g. ~/.config/sotto/k6-mod.key")
    p.add_argument("--serial", type=int, help="default: the time in seconds (must grow every run)")
    a = p.parse_args(argv)
    code, entry = build(a.source)
    serial = a.serial if a.serial is not None else int(time.time())
    img = image(code, entry, serial, k6mod.read_key(a.key_file), source_level(a.source))
    with open(a.output, "wb") as f:
        f.write(img)
    print(f"{a.output}: {len(code)} B code, entry +{entry}, serial {serial}, {len(img)} B image",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
