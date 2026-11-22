#!/usr/bin/env python3
"""Read pluto_chan -f's stream (squelch-gated HamFLAC overs, see flacout.cpp) and write each over
as OUTDIR/<MHz>_<UTC>.flac, plus OUTDIR/levels.json (each channel's level each 0.1 s, dBFS) and
OUTDIR/overs.json (channel, start, length, bytes).

  pluto_chan.py STREAM OUTDIR --hz 442250000,442300000,... [--t0 EPOCH]
STREAM is a file, or - for stdin (ssh root@pluto 'iio_readdev ... | pluto_chan -f ...' | ...).
"""
import argparse
import json
import os
import struct
import sys
import time


def read_stream(f):
    """-> yields ('L', nch, tick, [[10 levels] per channel]), ('O', ch, tick, flac), ('E', 0, tick)"""
    while True:
        h = f.read(6)
        if len(h) < 6:
            return
        kind, a, tick = chr(h[0]), h[1], struct.unpack("<I", h[2:6])[0]
        if kind == "L":
            b = f.read(10 * a)
            yield "L", a, tick, [list(struct.unpack("10b", b[10 * j:10 * j + 10])) for j in range(a)]
        elif kind == "O":
            n = struct.unpack("<I", f.read(4))[0]
            yield "O", a, tick, f.read(n)
        elif kind == "E":
            yield "E", 0, tick, None
            return
        else:
            raise SystemExit(f"pluto_chan.py: bad record {h!r}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stream")
    ap.add_argument("outdir")
    ap.add_argument("--hz", required=True, help="the channels, in pluto_chan's order")
    ap.add_argument("--t0", type=float, default=None, help="the stream's start, epoch s (default: now)")
    a = ap.parse_args(argv)
    hzs = [int(float(v)) for v in a.hz.split(",")]
    t0 = time.time() if a.t0 is None else a.t0
    os.makedirs(a.outdir, exist_ok=True)
    f = sys.stdin.buffer if a.stream == "-" else open(a.stream, "rb")
    levels, overs, total = [[] for _ in hzs], [], 0
    for kind, x, tick, body in read_stream(f):
        if kind == "L":
            for j, lv in enumerate(body[: len(hzs)]):
                levels[j].extend(lv)
            total += 6 + 10 * x
        elif kind == "O":
            ts = time.strftime("%Y%m%dT%H%M%S", time.gmtime(t0 + tick / 10)) + f"{(tick % 10)}Z"
            name = f"{hzs[x] / 1e6:.4f}_{ts}.flac"
            with open(os.path.join(a.outdir, name), "wb") as o:
                o.write(body)
            overs.append({"ch": x, "hz": hzs[x], "tick": tick, "file": name, "bytes": len(body)})
            total += 10 + len(body)
            print(f"{name}: {len(body)} bytes", flush=True)
        else:
            total += 6
            secs = tick / 10
            print(f"end: {secs:.1f} s, {len(overs)} overs, {total} bytes ({total / max(secs, 0.1):.0f} B/s"
                  f" for {len(hzs)} channels; raw 8 kHz audio would be {16000 * len(hzs)} B/s)")
    with open(os.path.join(a.outdir, "levels.json"), "w") as o:
        json.dump({"hz": hzs, "t0": t0, "levels_dbfs_100ms": levels}, o)
    with open(os.path.join(a.outdir, "overs.json"), "w") as o:
        json.dump(overs, o, indent=1)


if __name__ == "__main__":
    main()
