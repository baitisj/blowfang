"""Reads a UV-21 Pro clk120 run (radios/uv21/mod/clk120.c: the MCU clock moved from a RAM module and
put back) from its console log, says whether it passed, and writes its screen grabs as PNGs.

    python3 radios/uv21/mod/clk120.py uv21mod.log [-o outdir] [--scale 3] [--ref surf.png]

A pass: the module returned 0 (every bit of "um end" / "clk done fails=" clear), it reached the new
clock and came home, the RF registers, the flash CRC and the test card read back the same at both
clocks, the soak saw no mismatch, and SURF's frame grabbed at home and at the new clock is the same
(both CRCs good). The CPU and call timings, the battery ADC and the RSSI levels are reported, not
judged. --ref compares the home grab of SURF with another PNG (an emulator render, say). Exit
status 0 on a pass, 1 otherwise.
SPDX-License-Identifier: MIT
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "tools"))
import screen_grab  # noqa: E402

FAILS = {0x001: "refused (a clock setup the module doesn't know)",
         0x002: "the PLL didn't lock at the new clock (went home)",
         0x004: "an RF register read differently",
         0x008: "the flash CRC changed",
         0x010: "the test card read back wrong at the new clock",
         0x020: "the test card read back wrong back home",
         0x040: "the soak saw a mismatch",
         0x080: "the way home failed (the deadman resets the radio)",
         0x100: "a clock register didn't read back as set"}
GRAB = re.compile(r"clk grab (.+?)\s*$")
STATE = re.compile(r"clk (home|new|back) mhz=(\d+) c=([0-9a-f]{8}) p=([0-9a-f]{3}) b=([0-9a-f]{4}) "
                   r"s=([0-9a-f]{3}) t=([0-9a-f]{4})")
CHECK = re.compile(r"clk (rf|fl|pat) (home|new|back) (.*)$")
CPU = re.compile(r"clk cpu (home|new) ram=(\d+) call=(\d+)")
ADC = re.compile(r"clk adc (home|new) batt=(\d+)")
RSSI = re.compile(r"clk rssi (home|new)((?: -?\d+)+)")
SOAK = re.compile(r"clk soak s=(\d+) rounds=(\d+) bad=(\d+)")
DONE = re.compile(r"clk done fails=([0-9a-f]{3})")
END = re.compile(r"um end ([0-9a-f]{8})")


def grabs(text):
    """{tag: (w, h, fmt, px)} for each screen after a "clk grab TAG" line (Bad if one is broken)"""
    out, tag, buf = {}, None, []
    for ln in text.splitlines() + ["clk grab -"]:
        m = GRAB.search(ln)
        if m or (tag and "clk " in ln and "scr " not in ln):
            if tag and buf:
                out[tag] = screen_grab.grab("\n".join(buf))
            tag, buf = (m.group(1) if m and m.group(1) != "-" else None), []
            continue
        if tag:
            buf.append(ln)
    return out


def verdict(text, ret=None):
    """what the run says, and whether it passed"""
    v = {"states": {}, "checks": {}, "cpu": {}, "adc": {}, "rssi": {}, "problems": []}
    for ln in text.splitlines():
        m = STATE.search(ln)
        if m:
            v["states"][m.group(1)] = {"mhz": int(m.group(2)), "cfg": m.group(3), "psr": m.group(4),
                                       "baudr": int(m.group(5), 16), "spi2": m.group(6),
                                       "tmr14_div": int(m.group(7), 16)}
        m = CHECK.search(ln)
        if m:
            v["checks"][f"{m.group(1)} {m.group(2)}"] = m.group(3)
        m = CPU.search(ln)
        if m:
            v["cpu"][m.group(1)] = (int(m.group(2)), int(m.group(3)))
        m = ADC.search(ln)
        if m:
            v["adc"][m.group(1)] = int(m.group(2))
        m = RSSI.search(ln)
        if m:
            v["rssi"][m.group(1)] = [int(t) for t in m.group(2).split()]
        m = SOAK.search(ln)
        if m:
            v["soak"] = {"s": int(m.group(1)), "rounds": int(m.group(2)), "bad": int(m.group(3))}
        m = DONE.search(ln)
        if m:
            v["fails"] = int(m.group(1), 16)
        m = END.search(ln)
        if m and ret is None:
            ret = int(m.group(1), 16)
    v["deadman"] = "clk deadman on" in text
    p = v["problems"]
    if ret is None:
        p.append("no 'um end' (the module didn't return: the deadman resets the radio)")
    elif ret:
        p += [why for bit, why in FAILS.items() if ret & bit] or [f"returned {ret:#x}"]
    st = v["states"]
    if "new" not in st or "back" not in st or "home" not in st:
        p.append("missing a home / new / back state line")
    elif st["new"]["mhz"] == st["home"]["mhz"]:
        p.append("never left the home clock")
    elif st["back"] != dict(st["home"]):
        p.append("back home, the clock registers differ from before")
    for k in ("rf new", "rf back", "fl new", "fl back", "pat new", "pat back"):
        c = v["checks"].get(k)
        if c is None:
            p.append(f"no '{k}' line")
        elif not (c.endswith(" ok") or re.search(r"\bbad=0\b", c)):
            p.append(f"{k}: {c}")
    if v.get("soak", {}).get("bad"):
        p.append(f"soak: {v['soak']['bad']} mismatches")
    try:
        g = grabs(text)
    except screen_grab.Bad as e:
        g = {}
        p.append(f"a screen grab is broken: {e}")
    v["grabs"] = sorted(g)
    if "surf home" in g and "surf new" in g and g["surf home"][3] != g["surf new"][3]:
        bad = [y for y in g["surf home"][3] if g["surf home"][3][y] != g["surf new"][3].get(y)]
        p.append(f"SURF's frame differs between the clocks in {len(bad)} rows")
    if "home" in v["cpu"] and "new" in v["cpu"]:
        v["speedup"] = [round(a / b, 2) if b else None for a, b in zip(v["cpu"]["home"], v["cpu"]["new"])]
    v["pass"] = not p
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log")
    ap.add_argument("-o", "--out", help="a folder for the grabs' PNGs")
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--ref", help="a PNG of SURF at --scale to compare the home grab with")
    a = ap.parse_args(argv)
    text = open(a.log, errors="replace").read()
    v = verdict(text)
    st = v["states"]
    print(" -> ".join(f"{k} {st[k]['mhz']} MHz" for k in ("home", "new", "back") if k in st) or "no states")
    for k in sorted(v["checks"]):
        print(f"  {k}: {v['checks'][k]}")
    if "soak" in v:
        print(f"  soak: {v['soak']['rounds']} rounds in {v['soak']['s']} s, {v['soak']['bad']} bad")
    if "speedup" in v:
        print(f"  speed-up at the new clock: RAM loop x{v['speedup'][0]}, calls into flash x{v['speedup'][1]}")
    if v["adc"]:
        print("  battery ADC: " + ", ".join(f"{k} {n}" for k, n in v["adc"].items()))
    for k, levels in v["rssi"].items():
        print(f"  rssi {k}: {levels}")
    print(f"  deadman: {'on' if v['deadman'] else 'off'}; grabs: {', '.join(v['grabs']) or 'none'}")
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        try:
            g = grabs(text)
        except screen_grab.Bad:
            g = {}
        for tag, (w, h, _fmt, px) in g.items():
            path = os.path.join(a.out, tag.replace(" ", "-") + ".png")
            screen_grab.png(path, px, a.scale, w, h)
            print(f"  {path}")
        if a.ref and "surf home" in g:
            ref = screen_grab.read_png(a.ref)
            w, h, _fmt, px = g["surf home"]
            diff = [y for y in sorted(px)
                    if any(screen_grab.rgb(px[y][x]) != ref[y * a.scale][x * a.scale] for x in range(w))]
            print(f"  surf home vs {a.ref}: {len(diff)} rows differ")
    print("PASS" if v["pass"] else "FAIL: " + "; ".join(v["problems"]))
    return 0 if v["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
