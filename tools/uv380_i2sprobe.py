"""Read the UV-380 I2S probe's lines (firmware/uv380-openrtx/i2sprobe.h) from its USB serial log
and say what they show: the pin census, and the captures ranked by how much of their power sits
at the test tone (the IC-7100's PSK31 "AG7EW TEST" at 1000 Hz, or PHY R's 600 and 1200 Hz).

  python3 tools/uv380_i2sprobe.py report LOG [--tone 1000] [--top 12]
  python3 tools/uv380_i2sprobe.py listen /dev/ttyACM0 LOG [--seconds 120]
  python3 tools/uv380_i2sprobe.py cmd /dev/ttyACM0 "auto 0" "cap SPI3 s P16 8000" "raw 64" [--log LOG]
  python3 tools/uv380_i2sprobe.py mod /dev/ttyACM0 MODULE.u3r|MODULE.c [--key-file K] [--log LOG]
  python3 tools/uv380_i2sprobe.py audio LOG OUT.raw

cmd sends each command line in turn (i2sprobe.h, i2sp_command: "help" lists them), prints what
comes back and waits for that command's "ok" or "err" line ("pass" may take minutes). Stop any
listener on the port first: two readers split its lines.

mod sends a signed RAM module (firmware/uv380-openrtx/ortx_mod.h) with "mod begin", "mod hex" lines
and "mod run", and prints what it says. A .c source is built and signed first (modem.uv380mod,
key ~/.config/sotto/k6-mod.key by default, serial the time in seconds, or one more than the last
this machine gave, kept in ~/.config/sotto/uv380-mod-serial). A module still running after
--run-timeout seconds gets a Ctrl-C byte, which ends it at its next stop() check.

A module's "a SEQ hhhh ..." audio lines (mod/rxrec.c) go to the log but not to the screen (a count
every 1000). audio turns them into 16-bit little-endian samples (a lost line: 64 zeros) and says
what the module's rate and raw lines show.

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


HEX_MAX = 128           # ortx_mod.h OM_HEX_MAX


def mod_lines(img):
    """the command lines that load and run a signed module image"""
    lines = [f"mod begin {len(img)}"]
    for off in range(0, len(img), HEX_MAX):
        lines.append(f"mod hex {off:x} {img[off:off + HEX_MAX].hex()}")
    return lines + ["mod run"]


def ends(c):
    """the prefixes of the line that ends command c: a "mod" command ends only on its own
    "ok mod <sub>" / "err mod <sub>" (a module's own probe commands print "ok ..." lines too),
    anything else on any "ok " or "err " line; "err busy" ends any"""
    w = c.split()
    if w[:1] == ["mod"]:
        sub = w[1] if len(w) > 1 else "info"
        return (f"ok mod {sub}", f"err mod {sub}", "err busy")
    return ("ok ", "err ")


def long_command(c):
    """a pass, a module run, or "mod begin" (the radio reads it once a pass in progress ends)"""
    return c.split()[:1] == ["pass"] or c.split()[:2] in (["mod", "run"], ["mod", "begin"])


def command(dev, cmds, log=None, timeout=10.0, pass_timeout=240.0, out=print, stop_on_err=False):
    """send each command, print its lines; -> the number of commands that ended in "err" or
    timed out. "pass" and "mod run" wait pass_timeout; a "mod run" still going then gets a Ctrl-C
    and 5 s more. stop_on_err: send nothing after the first error."""
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
            t0, wait, done = time.time(), pass_timeout if long_command(c) else timeout, None
            ctrl_c = c.split()[:2] == ["mod", "run"]
            while done is None:
                if time.time() - t0 >= wait:
                    if not ctrl_c:
                        break
                    out(f"ctrl-c: {c} still running after {wait:.0f} s")
                    os.write(fd, b"\x03")
                    t0, wait, ctrl_c = time.time(), 5.0, False
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
                    if ln.startswith(ends(c)):
                        done = ln
            if done is None:
                out(f"timeout: {c}")
            if done is None or done.startswith("err "):
                bad += 1
                if stop_on_err:
                    break
    return bad


SERIAL_FILE = "~/.config/sotto/uv380-mod-serial"


def next_serial(path=SERIAL_FILE, now=None):
    """a module serial newer than any this machine gave before: the time in seconds, or one more
    than the last if that is later (the radio refuses a serial not newer than the last it ran,
    so two modules built in the same second would otherwise clash)"""
    import os
    path = os.path.expanduser(path)
    now = int(time.time()) if now is None else now
    try:
        last = int(open(path).read().split()[0])
    except (OSError, ValueError, IndexError):
        last = 0
    serial = max(now, last + 1)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"{serial}\n")
    return serial


def module_image(path, key_file, serial_file=SERIAL_FILE):
    """a signed image: the file itself, or a .c source built and signed now"""
    if not path.endswith(".c"):
        return open(path, "rb").read()
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from modem import k6mod, uv380mod
    table = uv380mod.source_table(path)
    if table != "openrtx":
        raise SystemExit(f"{path}: not an OpenRTX module (include ortx_mod.h)")
    code, entry = uv380mod.build(path)
    return uv380mod.image(code, entry, next_serial(serial_file), k6mod.read_key(os.path.expanduser(key_file)),
                          uv380mod.source_level(path), table)


def audio_lines(lines):
    """rxrec's log -> (samples [int], lines lost, the rate line, the raw halfwords [int])"""
    out, lost, rate, raw, nxt = [], 0, None, [], None
    for ln in lines:
        w = ln.split()
        if len(w) > 2 and w[0] == "a" and w[1].isdigit():
            seq = int(w[1])
            try:
                v = [int(h, 16) for h in w[2:]]
            except ValueError:
                continue
            if nxt is not None and seq > nxt:
                lost += seq - nxt
                out += [0] * 64 * (seq - nxt)
            nxt = seq + 1
            out += [x - 65536 if x >= 32768 else x for x in v]
        elif "rate hw=" in ln:
            rate = ln[ln.index("rate"):].strip()
        elif "say raw " in ln:
            raw += [int(h, 16) for h in ln.split()[4:]]
    return out, lost, rate, raw


def audio_report(samples, lost, rate, raw):
    import math
    r = [f"{len(samples)} samples ({len(samples) / 8000:.1f} s at 8 kHz), {lost} lines lost"]
    if rate:
        m = re.search(r"hw=(\d+) ms=(\d+)", rate)
        if m and int(m.group(2)):
            hw, ms = int(m.group(1)), int(m.group(2))
            r.append(f"{rate}: {hw * 1000 / ms:.1f} halfwords a second, fs {hw * 500 / ms:.1f} "
                     "if two a sample")
    if len(raw) >= 4:
        odd = sum(raw[i] == raw[i + 1] for i in range(1, len(raw) - 1, 2))
        even = sum(raw[i] == raw[i + 1] for i in range(0, len(raw) - 1, 2))
        r.append(f"raw: {len(raw)} halfwords; pairs equal (2k-1, 2k) {odd}, (2k, 2k+1) {even}")
    if samples:
        rms = math.sqrt(sum(x * x for x in samples) / len(samples))
        r.append(f"rms {rms:.0f}, peak {max(abs(x) for x in samples)}")
    return r


def quiet(out=print):
    """out, but "a ..." audio lines only counted"""
    n = [0]

    def f(ln):
        if ln.startswith("a ") and ln[2:3].isdigit():
            n[0] += 1
            if n[0] % 1000 == 0:
                out(f"({n[0]} audio lines)")
            return
        out(ln)
    return f


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
    q = sub.add_parser("mod")
    q.add_argument("dev")
    q.add_argument("module", help="signed .u3r image, or a .c source to build and sign")
    q.add_argument("--key-file", default="~/.config/sotto/k6-mod.key")
    q.add_argument("--log")
    q.add_argument("--run-timeout", type=float, default=60.0)
    q = sub.add_parser("audio")
    q.add_argument("log")
    q.add_argument("out")
    a = ap.parse_args(argv)
    if a.cmd == "report":
        with open(a.log, errors="replace") as f:
            print("\n".join(report(f, a.tone, a.top)))
    elif a.cmd == "cmd":
        return 1 if command(a.dev, a.cmds, a.log, a.timeout) else 0
    elif a.cmd == "mod":
        img = module_image(a.module, a.key_file)
        return 1 if command(a.dev, mod_lines(img), a.log, pass_timeout=a.run_timeout,
                            out=quiet(), stop_on_err=True) else 0
    elif a.cmd == "audio":
        import array
        with open(a.log, errors="replace") as f:
            x, lost, rate, raw = audio_lines(f)
        with open(a.out, "wb") as g:
            array.array("h", x).tofile(g)
        print("\n".join(audio_report(x, lost, rate, raw)))
    else:
        listen(a.dev, a.log, a.seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
