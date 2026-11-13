"""Read the UV-380 I2S probe's lines (firmware/uv380-openrtx/i2sprobe.h) from its USB serial log
and say what they show: the pin census, and the captures ranked by how much of their power sits
at the test tone (the IC-7100's PSK31 "AG7EW TEST" at 1000 Hz, or PHY R's 600 and 1200 Hz).

  python3 tools/uv380_i2sprobe.py report LOG [--tone 1000] [--top 12]
  python3 tools/uv380_i2sprobe.py listen /dev/ttyACM0 LOG [--seconds 120]
  python3 tools/uv380_i2sprobe.py cmd /dev/ttyACM0 "auto 0" "cap SPI3 s P16 8000" "raw 64" [--log LOG]

cmd sends each command line in turn (i2sprobe.h, i2sp_command: "help" lists them), prints what
comes back and waits for that command's "ok" or "err" line ("pass" may take minutes). Stop any
listener on the port first: two readers split its lines.

A capture "hears" the radio when a channel's strongest tone is the test tone (within one bin,
fs / 64) and holds a good share of its power, and the same channel with no signal does not.
MIT licence."""
import argparse
import re
import sys
import time

CH = re.compile(r"(\w\d?):nz=(\d+) ch=(\d+) m=(-?\d+) r=(\d+) pk=(\d+)/(\d+)")


def parse(lines):
    """-> (start lines, census lines, captures [dict], other lines)"""
    starts, census, caps, other = [], [], [], []
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("i2sprobe start"):
            starts.append(ln)
        elif ln.startswith("census "):
            census.append(ln)
        elif ln.startswith("c ") or ln.startswith("adc "):
            w = ln.split()
            cap = {"line": ln, "head": " ".join(w[:7]) if w[0] == "c" else " ".join(w[:3]), "ch": {}}
            m = re.search(r" n=(\d+)", ln)
            cap["n"] = int(m.group(1)) if m else 0
            m = re.search(r" x=(\d+)", ln)
            cap["x"] = int(m.group(1)) if m else None
            for tag, nz, chg, mean, rms, hz, pm in CH.findall(ln):
                cap["ch"][tag] = dict(nz=int(nz), changes=int(chg), mean=int(mean), rms=int(rms),
                                      hz=int(hz), pm=int(pm))
            cap["fs"] = int(w[6]) if w[0] == "c" and len(w) > 6 and w[6].isdigit() else 8000
            caps.append(cap)
        elif ln.startswith("skip ") or ln.startswith("i2sprobe"):
            other.append(ln)
    return starts, census, caps, other


def score(cap, tone):
    """the best channel's share at the tone (per mille), and that channel"""
    best, tag = 0, None
    for t, c in cap["ch"].items():
        if c["rms"] and abs(c["hz"] - tone) <= cap["fs"] / 64:
            if c["pm"] > best:
                best, tag = c["pm"], t
    return best, tag


def report(lines, tone=1000, top=12):
    starts, census, caps, other = parse(lines)
    out = []
    out += starts[:1]
    out += census[-2:]
    out += other
    live = [c for c in caps if any(ch["nz"] for ch in c["ch"].values())]
    out.append(f"{len(caps)} captures, {len(live)} with any nonzero samples")
    ranked = sorted(caps, key=lambda c: -score(c, tone)[0])
    hits = [c for c in ranked if score(c, tone)[0] >= 300]
    out.append(f"captures with the {tone} Hz tone as their strongest, 30% of the power or more: {len(hits)}")
    for c in ranked[:top]:
        s, t = score(c, tone)
        if t is None:
            continue
        ch = c["ch"][t]
        out.append(f"  {c['head']}: {t} {s / 10:.1f}% at {ch['hz']} Hz, rms {ch['rms']}, nonzero {ch['nz']}")
    adc = [c for c in caps if c["line"].startswith("adc ")]
    for c in adc[-1:]:
        ch = c["ch"].get("a", {})
        out.append(f"PC3 ADC: rms {ch.get('rms')}, strongest {ch.get('hz')} Hz {ch.get('pm', 0) / 10:.1f}%")
    return out


def listen(dev, log, seconds):
    import termios
    import tty
    with open(dev, "rb", buffering=0) as f, open(log, "ab") as g:
        try:
            tty.setraw(f.fileno())
        except termios.error:
            pass
        t0 = time.time()
        while time.time() - t0 < seconds:
            b = f.read(256)
            if b:
                g.write(b)
                g.flush()


def command(dev, cmds, log=None, timeout=10.0, pass_timeout=240.0, out=print):
    """send each command, print its lines; -> the number of commands that ended in "err" or
    timed out"""
    import os
    import termios
    import tty
    import contextlib
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY)
    bad = 0
    with contextlib.ExitStack() as st:
        g = st.enter_context(open(log, "ab")) if log else None
        st.callback(os.close, fd)
        try:
            tty.setraw(fd)
            at = termios.tcgetattr(fd)
            at[6][termios.VMIN], at[6][termios.VTIME] = 0, 2
            termios.tcsetattr(fd, termios.TCSANOW, at)
        except termios.error:
            pass
        buf = b""
        for c in cmds:
            os.write(fd, c.encode() + b"\n")
            t0, wait, done = time.time(), pass_timeout if c.split()[:1] == ["pass"] else timeout, None
            while done is None and time.time() - t0 < wait:
                b = os.read(fd, 512)
                if g and b:
                    g.write(b)
                    g.flush()
                buf += b
                while b"\n" in buf:
                    ln, buf = buf.split(b"\n", 1)
                    ln = ln.decode(errors="replace").strip()
                    if not ln:
                        continue
                    out(ln)
                    if ln.startswith(("ok ", "err ")):
                        done = ln
            if done is None:
                out(f"timeout: {c}")
            if done is None or done.startswith("err "):
                bad += 1
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("report")
    q.add_argument("log")
    q.add_argument("--tone", type=int, default=1000)
    q.add_argument("--top", type=int, default=12)
    q = sub.add_parser("listen")
    q.add_argument("dev")
    q.add_argument("log")
    q.add_argument("--seconds", type=float, default=120.0)
    q = sub.add_parser("cmd")
    q.add_argument("dev")
    q.add_argument("cmds", nargs="+")
    q.add_argument("--log")
    q.add_argument("--timeout", type=float, default=10.0)
    a = ap.parse_args(argv)
    if a.cmd == "report":
        with open(a.log, errors="replace") as f:
            print("\n".join(report(f, a.tone, a.top)))
    elif a.cmd == "cmd":
        return 1 if command(a.dev, a.cmds, a.log, a.timeout) else 0
    else:
        listen(a.dev, a.log, a.seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
