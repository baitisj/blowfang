"""What can the K6's broadcast FM chip (RDA5807M) do? Patch and read the fmprobe module
(c/k6/mod/fmprobe.c, docs/k6-fm-chip.md). Receive only: the K6 transmits nothing but its usual
upload report on 440.850.

  python3 tools/k6_fmprobe.py patch IN.bin OUT.bin [--quick-ms 150] [--rds1-s 65] ...
  python3 tools/k6_fmprobe.py report LOG...            the findings from a serial log (k6.log)

The report turns the module's say lines back into numbers: the sweep and its peaks, the LNA
port that works, the RSSI's settling time (to 90 % of the last reading), the station's width on
the expert shape (points within 3 and 10 RSSI units of its peak, so the SURF lens can be sized),
where the expert sweep puts the broadcast band (a check of OE3HBW's 50 MHz base), verbose mode,
RDS per station and each clock group against the log's own time stamp.
The UV-21 Pro's cable module (radios/uv21/mod/fmprobe.c) says the same lines, plus its settings
(P), every address that answers (A), raw reads (b..) and the chip ID (i); report reads them too.

hamlet has no clang: patch the CI-built mod-fmprobe.bin. Never uses ttyUSB2.
SPDX-License-Identifier: MIT
"""
import argparse
import datetime
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "c", "k6", "mod", "fmprobe.c")
MAGIC = b"FMPROBE1"
FMT = "<8s7H7B3x"   # par_t in fmprobe.c, 32 bytes
FIELDS = ["half_us", "quick_ms", "settle_ms", "rds1_s", "rds_s", "sw_lo", "sw_hi", "sw_step", "n_rds",
          "shape_khz", "shape_n", "ex_lo", "ex_hi", "flags"]
DEFAULTS = dict(half_us=10, quick_ms=150, settle_ms=600, rds1_s=65, rds_s=20, sw_lo=119, sw_hi=319,
                sw_step=2, n_rds=3, shape_khz=5, shape_n=40, ex_lo=50, ex_hi=115, flags=0)
PFIELDS = ["sw_lo", "sw_hi", "sw_step", "shape_khz", "shape_n", "ex_lo", "ex_hi", "clk", "part"]
SAY = re.compile(r"K6 MOD say (.*)$")


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
        raise ValueError("not the fmprobe module (no single FMPROBE1 block)")
    return code[:at] + blk + code[at + len(blk):]


def chars(s):
    """'0' + RSSI / 2 per char -> RSSI (even values)"""
    return [(ord(ch) - 48) * 2 for ch in s]


def says(text):
    """(unix time or None, say text) for each say line; bare lines (an emulator's) pass as they are"""
    out = []
    for ln in text.splitlines():
        m = SAY.search(ln)
        if m:
            t = ln.split("\t", 1)[0]
            try:
                out.append((float(t), m.group(1)))
            except ValueError:
                out.append((None, m.group(1)))
        elif ln and not ln.startswith(("K6 ", "#")) and "\t" not in ln:
            out.append((None, ln))
    return out


def decode(text, par=None):
    par = {**DEFAULTS, **(par or {})}
    r = {"rows": {}, "dump": {"d": {}, "D": {}}, "peaks": [], "rds": {}, "ps": {}, "ct": [], "groups": {}}
    for t, s in says(text):
        tag, _, rest = s.partition(" ")
        if tag == "P":                       # the UV-21 module's settings line
            v = [int(x) for x in rest.split()]
            r["par"] = dict(zip(PFIELDS, v))
            par.update({k: x for k, x in r["par"].items() if k in DEFAULTS})
        elif tag == "A":
            r["answer"] = rest.split()
        elif tag[:1] == "b" and len(tag) == 3:
            r.setdefault("raw", {})[tag[1:]] = rest
        elif tag == "i":
            r["id"] = rest
        elif tag == "a":
            r["ack"] = {k: v == "1" for k, v in (x.split("=") for x in rest.split())}
        elif tag[:1] in "dD" and len(tag) == 3 and re.fullmatch(r"[0-9a-f]{2}", tag[1:]):
            base = int(tag[1:], 16)
            for i, w in enumerate(rest.split()):
                r["dump"][tag[0]][base + i] = None if w == "----" else int(w, 16)
        elif tag[:1] in "wfhxk" and len(tag) <= 2:
            r["rows"].setdefault(tag[0], []).append((tag, rest))
        elif tag == "s":
            f, q, rr, fl = rest.split()
            r["peaks"].append((round(float(f) * 1000), int(q[2:]), int(rr[2:]), fl))
        elif tag == "l":
            r["lna"] = [int(x) for x in rest.split()]
        elif tag == "c":
            stc, row = rest.split(" ", 1)
            r["settle"] = (int(stc), chars(row))
        elif tag == "v":
            r["verbose"] = rest
        elif tag == "r":
            f, *kv = rest.split()
            r["rds"][round(float(f) * 1000)] = {re.match(r"[a-z]+", x).group(0): int(re.sub(r"^[a-z]+", "", x))
                                                for x in kv}
        elif tag == "g":
            r["groups"][len(r["groups"])] = rest
        elif tag == "p":
            f, pi, ps = rest.split(" ", 2)
            r["ps"][round(float(f) * 1000)] = (pi[3:], ps[3:])
        elif tag == "t":
            m = re.match(r"(\S+) mjd=(\d+) (\d\d):(\d\d) o=(-?\d+) e=(\d{4})", rest)
            f, mjd, hh, mm, off, e = m.groups()
            r["ct"].append(dict(khz=round(float(f) * 1000), mjd=int(mjd), hh=int(hh), mm=int(mm), off=int(off),
                                e=e, log=t))
        elif tag == "E":
            r["end"] = {k: int(v) for k, v in (x.split("=") for x in rest.split())}
    for k, parts in r["rows"].items():
        txt = "".join(p for _, p in sorted(parts))
        r[k] = txt if k == "f" else chars(txt)
    if "w" in r:
        r["sweep"] = [(76000 + (par["sw_lo"] + i * par["sw_step"]) * 100, v, r.get("f", "")[i:i + 1])
                      for i, v in enumerate(r["w"])]
    return r


def mjd_date(mjd):
    return datetime.date(1858, 11, 17) + datetime.timedelta(days=mjd)


def width(row, step_khz, floor_drop):
    """kHz of the row within floor_drop of its peak (contiguous around the peak)"""
    if not row:
        return None
    pk = max(range(len(row)), key=lambda i: row[i])
    lo = hi = pk
    while lo > 0 and row[lo - 1] >= row[pk] - floor_drop:
        lo -= 1
    while hi < len(row) - 1 and row[hi + 1] >= row[pk] - floor_drop:
        hi += 1
    return (hi - lo) * step_khz, pk


def report(r, par=None):
    par = {**DEFAULTS, **{k: v for k, v in r.get("par", {}).items() if k in DEFAULTS}, **(par or {})}
    out = []
    if "answer" in r:
        out.append(f"I2C addresses that answer: {' '.join(r['answer']) or 'none'}"
                   + (f", chip id {r['id']}" if "id" in r else ""))
    if "ack" in r:
        out.append(f"bus: {r['ack']}  chip id {r['dump']['d'].get(0, 'n/a') if r['dump']['d'] else 'n/a'}"
                   .replace("n/a", "?"))
    if r.get("sweep"):
        heard = [f for f, v, fl in r["sweep"] if fl in "tS"]
        out.append(f"sweep {len(r['sweep'])} channels, FM_TRUE on {len(heard)}")
    for f, q, s, fl in r["peaks"]:
        out.append(f"  peak {f / 1000:.1f} MHz quick {q} settled {s} {fl}")
    if "lna" in r:
        out.append(f"LNA ports 0..3 settled RSSI {r['lna']} (Aura uses 2, LNAP)")
    if "settle" in r:
        stc, row = r["settle"]
        last = row[-1]
        t90 = next((20 * (i + 1) for i, v in enumerate(row) if v >= 0.9 * last), None)
        out.append(f"settle: STC after {stc} ms, RSSI at 90 % of {last} after {t90} ms")
    if r.get("h"):
        w3 = width(r["h"], par["shape_khz"], 3)
        w10 = width(r["h"], par["shape_khz"], 10)
        centre = (w3[1] - par["shape_n"]) * par["shape_khz"]
        out.append(f"expert shape: peak at {centre:+d} kHz, {w3[0]} kHz within 3, {w10[0]} kHz within 10")
    if r.get("k"):
        out.append(f"channel shape (50 kHz steps, -200..+200): {r['k']}")
    if r.get("x"):
        x = r["x"]
        band = [par["ex_lo"] + i for i, v in enumerate(x) if v >= max(x) - 20]
        out.append(f"expert sweep {par['ex_lo']}-{par['ex_hi']} MHz: loud at {band} MHz")
    if "verbose" in r:
        out.append(f"verbose (REG 0x56): {r['verbose']}")
    for f, d in r["rds"].items():
        pi, ps = r["ps"].get(f, ("?", "?"))
        out.append(f"RDS {f / 1000:.1f}: {d}  PI {pi} PS '{ps}'")
    for g in r["groups"].values():
        out.append(f"  groups {g}")
    for ct in r["ct"]:
        d = mjd_date(ct["mjd"])
        utc = datetime.datetime(d.year, d.month, d.day, ct["hh"], ct["mm"], tzinfo=datetime.timezone.utc)
        line = f"clock {ct['khz'] / 1000:.1f}: {utc:%Y-%m-%d %H:%M} UTC, offset {ct['off'] / 2:+g} h, BLER {ct['e']}"
        if ct["log"]:
            line += f", log says {ct['log'] - utc.timestamp():+.2f} s after the minute"
        out.append(line)
    if "end" in r:
        out.append(f"end {r['end']}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("patch")
    p.add_argument("inp")
    p.add_argument("out")
    for f in FIELDS:
        p.add_argument("--" + f.replace("_", "-"), type=int, default=DEFAULTS[f])
    p = sub.add_parser("report")
    p.add_argument("logs", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "patch":
        code = open(a.inp, "rb").read()
        blk = pack(**{f: getattr(a, f) for f in FIELDS})
        open(a.out, "wb").write(patch(code, blk))
        print(f"{a.out}: {unpack(blk)}")
    else:
        text = "\n".join(open(f, errors="replace").read() for f in a.logs)
        print(report(decode(text)))


if __name__ == "__main__":
    sys.exit(main())
