"""UV-380 RAM modules: build a module for the Cortex-M4F, sign it, write the image
(radios/uv380/src/uv_api.h, c/common/mod_core.h).

  python -m modem.uv380mod radios/uv380/mod/hello.c hello.u3m --key-file ~/.config/sotto/k6-mod.key
  python -m modem.uv380mod firmware/uv380-openrtx/mod/hello.c hello.u3r --key-file ~/.config/sotto/k6-mod.key

Same image and SipHash-2-4 tag as a K6 module (modem.k6mod), built with clang for thumbv7em hard
float (-fropi) and checked position-independent the same way (linked at two addresses, the bytes
must match). The UV-380 has two API tables, each with its own header magic so a module only runs
against the table it was built for:
  uv380    the bare-metal port (radios/uv380/src/uv_api.h), magic "U3M"
  openrtx  the OpenRTX image (firmware/uv380-openrtx/ortx_mod.h), magic "U3R", loaded over USB
           serial by tools/uv380_i2sprobe.py mod
A source that includes ortx_mod.h is built for openrtx, any other for uv380 (--table overrides).
SPDX-License-Identifier: MIT
"""
import argparse
import os
import re
import sys
import time

from . import picmod

MAGIC = b"U3M"
LEVEL = 1
ARENA = 16384            # UVM_ARENA
# table -> (magic, its newest API level, arena bytes)
TABLES = {
    "uv380": (MAGIC, LEVEL, ARENA),
    "openrtx": (b"U3R", 5, 16384),      # firmware/uv380-openrtx/ortx_mod.h: OM_MAGIC, OM_LEVEL, OM_ARENA
}
ENTRY = "uv_main"
ROOT = picmod.ROOT
LINK = os.path.join(ROOT, "radios", "uv380", "mod", "module.ld")
CFLAGS = [
    "--target=thumbv7em-none-eabihf",
    "-mcpu=cortex-m4",
    "-mfpu=fpv4-sp-d16",
    "-mfloat-abi=hard",
    *picmod.COMMON_CFLAGS,
    "-I" + os.path.join(ROOT, "radios", "uv380", "src"),
    "-I" + os.path.join(ROOT, "firmware", "uv380-openrtx"),
]


def source_table(src):
    """The table a module's C source is for: "openrtx" if it includes ortx_mod.h, else "uv380"."""
    return "openrtx" if re.search(r'^\s*#\s*include\s*"ortx_mod\.h"', open(src).read(), re.M) else "uv380"


def build(src):
    """C source -> (code bytes, entry offset)."""
    return picmod.build(src, cflags=CFLAGS, link=LINK, entry_sym=ENTRY)


def source_level(src):
    """The API level a module's C source declares (#define UV_MODULE_LEVEL n), or 1."""
    return picmod.source_int(src, "UV_MODULE_LEVEL", LEVEL)


def image(code, entry, serial, key, level=LEVEL, table="uv380"):
    magic, newest, arena = TABLES[table]
    if level > newest:
        raise ValueError(f"API level {level}: the {table} table is at level {newest}")
    code = bytes(code)
    if entry >= len(code) or entry & 1 or len(code) > 0xFFFF:
        raise ValueError("bad entry offset or code length")
    img = picmod.signed_image(code, entry, serial, key, magic, level=level)
    if len(img) > arena:
        raise ValueError(f"{len(img)} bytes is more than the {table} module arena ({arena})")
    return img


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m modem.uv380mod", description=__doc__.splitlines()[0])
    p.add_argument("source", help="module C source")
    p.add_argument("output", help="signed image")
    p.add_argument("--key-file", required=True, help="e.g. ~/.config/sotto/k6-mod.key")
    p.add_argument("--serial", type=int, help="default: the time in seconds (must grow every run)")
    p.add_argument("--table", choices=sorted(TABLES), help="default: from the source's includes")
    a = p.parse_args(argv)
    table = a.table or source_table(a.source)
    code, entry = build(a.source)
    serial = a.serial if a.serial is not None else int(time.time())
    img = image(code, entry, serial, picmod.read_key(a.key_file), source_level(a.source), table)
    with open(a.output, "wb") as f:
        f.write(img)
    print(f"{a.output}: {table} table, {len(code)} B code, entry +{entry}, serial {serial}, "
          f"{len(img)} B image", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
