"""The K6's RDS module (c/k6/mod/fmrds.c, docs/k6-fm-chip.md "RDS module"): patch its settings
and read its serial lines back. Receive only: the K6 transmits nothing but its usual upload
report on 440.850.

  python3 tools/k6_fmrds.py patch IN.bin OUT.bin [--clk-s 65] [--n-clk 4] [--flags 1] ...
  python3 tools/k6_fmrds.py report LOG...           stations, names, text, clocks and the vote
  python3 tools/k6_fmrds.py check LOG...            PASS / FAIL for an unattended run (exit 0 / 1)

The report lists each station (frequency, signal, PI, programme type by name, the name it
sends), the radio text the clock listens caught, every clock group with its distance from the
vote and, when the log carries time stamps (hamlet's logger), from the log's own clock, and the
voted clock against the log. Station names, text and frequencies are what the stations
broadcast where the radio is: keep reports out of Git (site-specific).

hamlet has no clang: patch the CI-built mod-fmrds.bin. Never uses ttyUSB2.
SPDX-License-Identifier: MIT
"""
import argparse
import datetime
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from k6_fmprobe import mjd_date, says  # noqa: E402,F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "c", "k6", "mod", "fmrds.c")
MAGIC = b"FMRDS001"
FMT = "<8s9H6B"   # par_t in fmrds.c, 32 bytes
FIELDS = ["half_us", "quick_ms", "settle_ms", "scan_ms", "clk_s", "sw_lo", "sw_hi", "agree_ms", "w_ms",
          "sw_step", "n_clk", "lat_ms", "flags", "min_ok", "lead_s"]
DEFAULTS = dict(half_us=10, quick_ms=150, settle_ms=400, scan_ms=5000, clk_s=65, sw_lo=119, sw_hi=319,
                agree_ms=1500, w_ms=500, sw_step=2, n_clk=4, lat_ms=88, flags=0, min_ok=8, lead_s=0)

# programme types: RBDS (North America, ECC a0-a6) and RDS (IEC 62106)
PTY_RBDS = ["none", "News", "Information", "Sports", "Talk", "Rock", "Classic Rock", "Adult Hits",
            "Soft Rock", "Top 40", "Country", "Oldies", "Soft Music", "Nostalgia", "Jazz", "Classical",
            "R&B", "Soft R&B", "Language", "Religious Music", "Religious Talk", "Personality", "Public",
            "College", "Spanish Talk", "Spanish Music", "Hip Hop", "", "", "Weather", "Emergency Test",
            "Emergency"]
PTY_RDS = ["none", "News", "Current Affairs", "Information", "Sport", "Education", "Drama", "Culture",
           "Science", "Varied", "Pop Music", "Rock Music", "Easy Listening", "Light Classical",
           "Serious Classical", "Other Music", "Weather", "Finance", "Children's", "Social Affairs",
           "Religion", "Phone-In", "Travel", "Leisure", "Jazz", "Country", "National Music", "Oldies",
           "Folk", "Documentary", "Alarm Test", "Alarm"]


def pack(**kw):
    v = {**DEFAULTS, **kw}
    return struct.pack(FMT, MAGIC, *[v[f] for f in FIELDS])


def unpack(blk):
    v = struct.unpack(FMT, blk[:struct.calcsize(FMT)])
    assert v[0] == MAGIC
    return dict(zip(FIELDS, v[1:]))


def patch(code, blk):
    at = code.find(MAGIC)
    if at < 0 or code.find(MAGIC, at + 1) >= 0:
        raise ValueError("not the fmrds module (no single FMRDS001 block)")
    return code[:at] + blk + code[at + len(blk):]


def khz(s):
    return round(float(s) * 1000)


def pty_name(pty, ecc):
    if ecc and 0xA0 <= ecc <= 0xA6:
        return PTY_RBDS[pty]
    if ecc:
        return PTY_RDS[pty]
    return f"{PTY_RBDS[pty]} (RBDS) / {PTY_RDS[pty]} (RDS)" if pty else "none"


def decode(text):
    r = {"st": {}, "ct": [], "rt": {}, "y": {}, "d": []}
    for t, s in says(text):
        tag, _, rest = s.partition(" ")
        if tag == "a":
            r["ack"] = {k: v == "1" for k, v in (x.split("=") for x in rest.split())}
        elif tag == "v":
            r["verbose"] = rest
        elif tag[:1] in "wf" and len(tag) == 2 and tag[1].isdigit():
            r.setdefault(tag[0], []).append((tag, rest))
        elif tag == "n":
            m = re.match(r"(\S+) q(\d+) g(\d+) ok(\d+) pi=(\w{4}) pty=(\d+) f=(\w*) ps=(.{8})$", rest)
            f, q, g, ok, pi, pty, fl, ps = m.groups()
            r["st"][khz(f)] = dict(q=int(q), g=int(g), ok=int(ok), pi=pi, pty=int(pty), fl=fl, ps=ps)
        elif tag == "t":
            m = re.match(r"(\S+) mjd=(\d+) (\d\d):(\d\d) o=(-?\d+) e=(\S{3}) at=(\d+)", rest)
            f, mjd, hh, mm, off, e, at = m.groups()
            r["ct"].append(dict(khz=khz(f), mjd=int(mjd), hh=int(hh), mm=int(mm), off=int(off), e=e,
                                at=int(at), log=t))
        elif tag in ("x0", "x1"):
            f, _, txt = rest.partition(" ")
            r["rt"].setdefault(khz(f), ["", ""])[int(tag[1])] = txt
        elif tag == "y":
            m = re.match(r"(\S+) ecc=(\w\w) gt=(.*)$", rest)
            r["y"][khz(m.group(1))] = dict(ecc=int(m.group(2), 16), gt=m.group(3).split())
        elif tag == "c":
            m = re.match(r"(\d\d):(\d\d):(\d\d)\.(\d{3}) v=(\d+) w=(\d+) d=(\d{4})-(\d\d)-(\d\d) o=(-?\d+)", rest)
            hh, mi, ss, ms, v, w, y, mo, dd, off = (int(x) for x in m.groups())
            r["clock"] = dict(utc=datetime.datetime(y, mo, dd, hh, mi, ss, ms * 1000, tzinfo=datetime.timezone.utc),
                              v=v, w=w, off=off, log=t)
        elif tag == "d":
            f, e = rest.split()
            r["d"].append((khz(f), int(e)))
        elif tag == "k":
            e, w = rest.split()
            r["radio"] = dict(err=int(e), w=int(w[2:]))
        elif tag == "E":
            r["end"] = {k: int(v) for k, v in (x.split("=") for x in rest.split())}
    for k in ("w", "f"):
        if k in r:
            r[k] = "".join(p for _, p in sorted(r[k]))
    return r


def report(r):
    out = []
    if "verbose" in r:
        out.append(f"chip and verbose mode: {r['verbose']}")
    if "f" in r:
        out.append(f"sweep: {len(r['f'])} channels, FM_TRUE on {r['f'].count('t')}")
    for f, s in r["st"].items():
        ecc = r["y"].get(f, {}).get("ecc", 0)
        flags = ",".join({"t": "TP", "a": "TA", "m": "music"}[c] for c in s["fl"])
        out.append(f"{f / 1000:6.1f} MHz  RSSI {s['q']:2d}  groups {s['g']:3d} clean {s['ok']:3d}  PI {s['pi']}  "
                   f"'{s['ps']}'  PTY {s['pty']} {pty_name(s['pty'], ecc)}" + (f"  {flags}" if flags else ""))
    for f, (a, b) in r["rt"].items():
        out.append(f"  text {f / 1000:.1f}: '{(a + b).rstrip()}'")
    for f, y in r["y"].items():
        out.append(f"  groups {f / 1000:.1f}: {' '.join(y['gt'])}" + (f", ECC {y['ecc']:02x}" if y["ecc"] else ""))
    dist = dict()
    for f, e in r["d"]:
        dist.setdefault(f, []).append(e)
    seen = {}
    for ct in r["ct"]:
        d = mjd_date(ct["mjd"])
        utc = datetime.datetime(d.year, d.month, d.day, ct["hh"], ct["mm"], tzinfo=datetime.timezone.utc)
        i = seen.get(ct["khz"], 0)
        seen[ct["khz"]] = i + 1
        line = (f"clock {ct['khz'] / 1000:.1f}: {utc:%Y-%m-%d %H:%M} UTC, offset {ct['off'] / 2:+g} h, "
                f"BLER B-D {ct['e']}")
        ds = dist.get(ct["khz"], [])
        if i < len(ds):
            line += f", {ds[i] / 1000:+.3f} s from the vote"
        if ct["log"]:
            line += f", log says {ct['log'] - utc.timestamp():+.2f} s after the minute"
        out.append(line)
    if "clock" in r:
        c = r["clock"]
        line = f"voted clock: {c['utc']:%Y-%m-%d %H:%M:%S.%f}"[:-3] + f" UTC, {c['v']} stations, bound {c['w']} ms"
        if c["log"]:
            line += f", log clock says {c['log'] - c['utc'].timestamp():+.2f} s"
        out.append(line)
    elif r["ct"]:
        out.append("no vote")
    if "radio" in r:
        out.append(f"radio's own clock before: {r['radio']['err'] / 1000:+.3f} s from the vote "
                   f"(its bound {r['radio']['w']} ms)")
    if "end" in r:
        out.append(f"end {r['end']}")
    return "\n".join(out)


def check(r):
    """(ok, reasons) for an unattended run: the module ran to its end without an error, the chip
    answered with verbose mode, the sweep heard stations, at least one station's name came in
    whole, and every clock group it reported is a possible time. A vote of one (no clock set) is
    not a failure: it depends on what the stations send."""
    bad = []
    if "end" not in r:
        bad.append("no end line (the module never finished, or the log lost it)")
    elif r["end"].get("n", -1) < 0:
        bad.append(f"the module returned {r['end']['n']}")
    v = r.get("verbose", "")
    if not v.startswith("58"):
        bad.append(f"chip line {v!r}: no RDA5807 (0x58xx)")
    elif not v.endswith(" ok"):
        bad.append("verbose mode refused: no error levels, clocks unchecked")
    if "f" in r and not r["f"].count("t"):
        bad.append("the sweep heard no station")
    named = [f for f, st in r["st"].items() if st["g"] and "." not in st["ps"]]
    if not named:
        bad.append("no station's name came in whole")
    for ct in r["ct"]:
        if ct["hh"] > 23 or ct["mm"] > 59 or abs(ct["off"]) > 28 or ct["e"] != "000":
            bad.append(f"clock group {ct} is not a clean time")
    return not bad, bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("patch")
    p.add_argument("inp")
    p.add_argument("out")
    for f in FIELDS:
        p.add_argument("--" + f.replace("_", "-"), type=int, default=DEFAULTS[f])
    for name in ("report", "check"):
        p = sub.add_parser(name)
        p.add_argument("logs", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "patch":
        code = open(a.inp, "rb").read()
        blk = pack(**{f: getattr(a, f) for f in FIELDS})
        open(a.out, "wb").write(patch(code, blk))
        print(f"{a.out}: {unpack(blk)}")
    else:
        text = "\n".join(open(f, errors="replace").read() for f in a.logs)
        r = decode(text)
        print(report(r))
        if a.cmd == "check":
            ok, bad = check(r)
            print("PASS" if ok else "FAIL: " + "; ".join(bad))
            return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
