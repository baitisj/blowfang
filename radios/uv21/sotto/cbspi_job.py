"""[S12] Bench-runner jobs that put sotto's codebook into the UV-21 Pro's SPI flash with the
cbspi RAM modules (radios/uv21/mod/cbspi.c), from this tree's sotto-cb.bin
(radios/uv21/sotto/build.sh).

  python3 radios/uv21/sotto/cbspi_job.py check ID OUT.json    read only: what the window holds
  python3 radios/uv21/sotto/cbspi_job.py write ID OUT.json    check, erase the window, the three
                                                              parts, check again

The job's checkout is this tree. Jeff, 2026-11-28: "Codebook in SPI is fine". Nothing is flashed;
the modules write only the 32 KB window at 0x1F0000.  SPDX-License-Identifier: MIT
"""
import json
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
IMG = os.path.join(ROOT, "radios", "uv21", "build", "sotto", "sotto-cb.bin")


def defines():
    d = open(IMG, "rb").read()
    assert d[:4] == b"K6CB" and len(d) == 32 + 30720, "build sotto-cb.bin first (radios/uv21/sotto/build.sh)"
    hdr = "CB_HDR=" + ",".join("0x%08x" % w for w in struct.unpack("<8I", d[:32]))
    return hdr, "CB_IMG_CRC=0x%08x" % zlib.crc32(d)


def job(kind, ident):
    hdr, crc = defines()
    check = {"do": "uv21.mod", "args": ["cbspi", "CB_OP=0", hdr, crc]}
    steps = [{"do": "uv21.mod", "args": ["hello"]}, check]
    if kind == "write":
        steps += [
            {"do": "uv21.mod", "args": ["cbspi", "CB_OP=1"]},
            {"do": "uv21.mod", "args": ["cbspi", "CB_OP=2", "CB_PART=0", hdr]},
            {"do": "uv21.mod", "args": ["cbspi", "CB_OP=2", "CB_PART=1"]},
            {"do": "uv21.mod", "args": ["cbspi", "CB_OP=2", "CB_PART=2"]},
            dict(check),
        ]
        # the first check fails (no image yet) by design: the job goes on to the erase
        steps[1] = dict(check, continue_on_error=True)
    return {
        "id": ident,
        "who": "s12",
        "freq_mhz": "bench",
        "minutes": 10,
        "note": f"[S12] UV-21 RAM modules only, no flash, no transmit: cbspi {kind} of the sotto codebook "
                "window 0x1F0000-0x1F7FFF in SPI flash (Jeff 2026-11-28: codebook in SPI is fine)",
        "checkout": ROOT,
        "take_serial": True,
        "steps": steps,
    }


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ("check", "write"):
        sys.exit(__doc__)
    json.dump(job(sys.argv[1], sys.argv[2]), open(sys.argv[3], "w"), indent=1)
    print(sys.argv[3])


if __name__ == "__main__":
    main()
