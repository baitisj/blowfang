#!/usr/bin/env python3
"""Send the Epistle of James to the K6 as 14 sotto 400 hiss messages (vlrh image), one at a time.

  cd ~/blowfang && python3 demos/james/send_james.py RUNDIR [--start N] [--k6log LOG] [--dry-run]

Each message: build the WAV with modem.k6 tx (F/1800, XL, codec 400, its own msg_id 100+N, CW ID
"AG7EW SOTTO TEST" at the end), check the IC-7100 reads RF POWER 0 (stub antenna), key it through
k6_bench.py send (PTT lock, simplex check, 440.850 only), then wait until the K6 has played it
before sending the next. With --k6log (a read-only modem.k6_harness log of /dev/ttyUSB2), it waits
for that message's K6 PLAY line and compares pcm= with EXPECTED; otherwise it waits speech + 6 s.

Source: World English Bible, read by David Williams (public domain, AudioTreasure). Encoded with
sotto main e246c3b: c/sotto_enc -16 -400, cut at pauses into pieces under 68 s, each fits the
640-slot store (76.8 s of sotto 400) whole, so streaming never overruns.
SPDX-License-Identifier: MIT
"""
import argparse
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FREQ = 440_850_000
CWID = "AG7EW SOTTO TEST"
# msg_id: (pcm, samples) from tools/k6_pcmhash.py james-NN.sot --codec 400 --payload XL --hiss --msg-id 100+NN
EXPECTED = {
    1: ("e96b586d", 1025280), 2: ("8c20d2c0", 812160), 3: ("218d1591", 1013760),
    4: ("7f190210", 844800), 5: ("be81947a", 1044480), 6: ("85658bec", 762240),
    7: ("a9dd5ab6", 789120), 8: ("0b903851", 812160), 9: ("99c314ca", 912000),
    10: ("694c46aa", 825600), 11: ("fc76675a", 733440), 12: ("614d62eb", 739200),
    13: ("1df0c27a", 967680), 14: ("66ee3739", 405120),
}


def log(msg):
    print(time.strftime("%H:%M:%SZ ", time.gmtime()) + msg, flush=True)


def rfpower():
    out = subprocess.run(["rigctl", "-m", "2", "l", "RFPOWER"], capture_output=True, text=True, check=True)
    return float(out.stdout.split()[0])


def k6_frames(k6log):
    """frames= from the K6's last CFG line in the log ("L" or "XL"), or None"""
    with open(k6log, errors="replace") as f:
        hits = re.findall(r"K6 CFG .*?frames=(\w+)", f.read())
    return hits[-1] if hits else None


def wait_play(k6log, msg_id, since, timeout):
    """the K6 PLAY line for msg_id written after byte offset since, or None"""
    pat = re.compile(r"K6 PLAY id=%02x .*" % msg_id)
    t_end = time.time() + timeout
    while time.time() < t_end:
        with open(k6log, errors="replace") as f:
            f.seek(since)
            m = pat.search(f.read())
        if m:
            return m.group(0)
        time.sleep(1.0)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("rundir")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--stop", type=int, default=14)
    ap.add_argument("--k6log")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.rundir, exist_ok=True)
    results = []
    for n in range(a.start, a.stop + 1):
        sot = os.path.join(HERE, f"james-{n:02d}.sot")
        wav = os.path.join(a.rundir, f"james-{n:02d}.wav")
        mid = 100 + n
        tx = subprocess.run([sys.executable, "-m", "modem.k6", "tx", sot, wav, "--phy", "F", "--payload", "XL",
                             "--codec", "400", "--msg-id", str(mid), "--cw-id", CWID],
                            cwd=ROOT, capture_output=True, text=True, check=True)
        m = re.search(r"([\d.]+) s of speech.*?([\d.]+) s on air", tx.stdout + tx.stderr)
        speech, air = float(m.group(1)), float(m.group(2))
        log(f"james-{n:02d}: msg_id {mid}, {speech:.1f} s of speech, {air:.1f} s on air")
        if a.dry_run:
            continue
        p = rfpower()
        if p > 0.0:
            raise SystemExit(f"refusing: IC-7100 RFPOWER reads {p}, must be 0 (stub antenna)")
        if a.k6log and k6_frames(a.k6log) not in (None, "XL"):
            raise SystemExit(f"refusing: the K6's last CFG says frames={k6_frames(a.k6log)}; these are XL "
                             "packets (key 2 on the K6, or modem.k6 ctl --set frames=XL)")
        since = os.path.getsize(a.k6log) if a.k6log else 0
        t0 = time.time()
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "k6_bench.py"), "send", wav,
                        "--freq", str(FREQ), "--max-s", "45"], check=True)
        if a.k6log:
            line = wait_play(a.k6log, mid, since, speech + air + 30)
            if not line:
                log(f"james-{n:02d}: no K6 PLAY line for id {mid:02x}")
                results.append((n, "no PLAY line"))
                continue
            pcm = re.search(r"pcm=(\w+)", line).group(1)
            missing = re.search(r"missing=(\S+)", line).group(1)
            ok = "MATCH" if pcm == EXPECTED[n][0] else ("MISMATCH" if missing in ("-", "") else f"missing {missing}")
            log(f"james-{n:02d}: {ok}  {line}")
            results.append((n, ok))
            time.sleep(2.0)
        else:
            rest = t0 + air + speech + 6 - time.time()
            log(f"james-{n:02d}: sent; waiting {max(rest, 0):.0f} s for it to play")
            time.sleep(max(rest, 0))
    for n, r in results:
        print(f"james-{n:02d} {r}")


if __name__ == "__main__":
    main()
