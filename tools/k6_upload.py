"""Upload a K6 RAM module with ARQ: send it, listen for the K6's report, resend only what it lacks.

  python3 tools/k6_upload.py c/k6/mod/hello.c --key-file ~/.config/sotto/k6-mod.key
  python3 tools/k6_upload.py mod-ifcal.bin --api-level 4 --telemetry 4 --key-file ... --payload XL

On hamlet: the IC-7100 (rigctld on :4532) sends the module as F/1800 audio with magic 0xAE, so
the K6 answers. --via pluto: the ADALM-Pluto sends it (pluto_bench.py send, pre-emphasised, gain
--pluto-gain, its own MCW ID first and last) and the IC-7100 only listens. While the tool waits, it records the IC-7100's receive audio (parec) and decodes
the K6's 0xAF reports (modem/k6mod.py):

  partway   the K6 lists the chunks it lacks: they go again, same serial and transfer id
  ran       the module ran; its return value, run time, free stack and say lines (--telemetry)
  refused   bad header, wrong key, no key, or an old serial: no point resending
  nothing   no report heard: everything goes again (a K6 that ran it already answers a repeat)

Chunk size: XL (128-byte packets) unless --payload L. A K6 hears only the packet size it is set
to, and a wrong-size chunk fails its CRC, so a K6 set to the other size answers nothing at all.
When a send of every chunk brings no report and no report has come yet, the next send uses the
other size, as a new transfer (new serial: the chunks are numbered differently). Before
2026-11-13 a stray key 2 or 7 in VOICE MSG changed the K6's size and every XL chunk was rejected;
those keys are gone, but a control packet or an older image can still leave it at L.
--no-fallback keeps the one size.

The K6 waits 2 s after the last packet and for a clear channel, keys up on 440.850 only (its
tx_on takes about 1.4 s), sends the report twice, then its own CW ID. The tool sends the PC's CW
ID at the end. Before the first key-up the K6 needs an image from 2026-11-06 on (API level 4).
Never use ttyUSB2 (the K6's PTT line); this tool opens no serial port.

The rate ladder (images with PR #138: CFG and ARQ reports carry rates=F12,F18,D24; docs/k6-fsk24.md,
the 2400 plan): over a flat transmitter (--via pluto) the chunks can go as plain 2-FSK at 2400 baud
(D/2400, a third more throughput; sweep 3: as sensitive as F/1800 or D/1200 to within 1 dB). Each
such send is one transmission: an F/1800 control record (phy=D baud=2400, pre-emphasised as any
F/1800 Pluto send) and then the chunks at D/2400, flat, 2.2 kHz peak. The K6 replies at F/1800 as
before and falls back to F/1800 by itself (3 bad CRCs of 8, 10 s with no good packet after the
switch, 30 s quiet), so every send asks again. Images before 8a78fb5 (PR #153) read D/2400 by
time and lost long uploads (11-17: 3-11 of 25 chunks); 8a78fb5 reads by interrupt and took 25/25 in
one send from K6 RSSI -74 to -88 dBm (edge about -94). --rate auto (the default) asks only once a report
has shown the K6 takes D24 and only for an image of D24_MIN_CHUNKS (16) chunks or more (asking
costs about 3 s a send, so a smaller one is quicker at F/1800), and keeps per-transmitter state in $BLOWFANG_WORK/state/k6-rate.json:
a send at D/2400 that loses over 15 % of its chunks, or a report that says the K6 fell back on
CRC or revert, sends the rest at F/1800 (the first such send led by a D/2400 record asking for
F/1800, for a K6 still on D/2400) and marks the path F18; two clean uploads after that
(under 5 % lost) mark it D24 again. The IC-7100's audio can't carry D/2400: --via ic7100 is
always F/1800.
SPDX-License-Identifier: MIT
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from modem import k6mod  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k6_bench as bench  # noqa: E402  (the PTT lock and the CW ID keeper's state)

TOOL = "k6_upload"

FS = 48000
PLAY = "alsa_output.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.analog-stereo"
REC = "alsa_input.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.analog-stereo"


def next_step(st, count, xfer):
    """The K6's newest status report (or None) -> ("send", chunk list) | ("done", why) |
    ("stop", why). count: the image's chunks; xfer: this upload's transfer id."""
    if st is None:
        return "send", list(range(count))
    if st["xfer"] != xfer:
        return "stop", f"the K6 reported transfer {st['xfer']}, not {xfer} (another upload?)"
    if st["state"] == "ran":
        return "done", f"ran: returned {st['ret']} in {st['ms']} ms, {st['stack_free']} B stack free"
    if st["state"] == "refused":
        return "stop", f"refused: {k6mod.RESULTS.get(st['r'], st['r'])}"
    if not st["missing"]:
        return "send", list(range(count))
    return "send", st["missing"]


def other_size(payload):
    """the chunk size to try when the K6 heard none of this one"""
    return "L" if payload == "XL" else "XL"


def switch_size(st, heard, fallback):
    """after a send: whether the next one should change chunk size. st: the report heard for
    it (None: none); heard: whether any report for this size came earlier in the run. Only a
    silence with nothing ever heard at this size means the K6 may be set to the other one: a
    report (even partway) shows the size works, and a silence after one is a lost channel."""
    return fallback and st is None and not heard


# ---- the rate ladder (the sender's side) ----

D24_PEAK_HZ = 2200.0      # D/2400's peak deviation from the Pluto (fsk24 sweeps; run 3 read 1.5-4.5 kHz)
D24_GAP_S = 0.6           # carrier between the control record and the D/2400 chunks: the K6 leaves its
                          # burst 250 ms after the last packet (NEXT_SYNC_WAIT_TICKS), then retunes
STEP_DOWN_LOST = 0.15     # a D/2400 send losing more than this share of its chunks: F/1800 for the rest
CLEAN_LOST = 0.05         # an upload losing less than this overall is clean
CLEAN_TO_STEP_UP = 2      # clean uploads at F/1800 before the path goes back to D/2400
D24_MIN_CHUNKS = 16       # auto asks for D/2400 only for an image this big: each D/2400 send carries
                          # about 3 s more (the F/1800 control record and the gap) and saves 0.3 s an
                          # XL chunk; hello.c (4 chunks, 11-17 04:51Z): 28 s wall at D/2400, 24 at F/1800


def rate_path():
    return os.path.join(bench.work(), "state", "k6-rate.json")


def load_rates(path=None):
    try:
        d = json.load(open(path or rate_path()))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_rates(d, path=None):
    p = path or rate_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w") as f:
        json.dump(d, f, indent=1, sort_keys=True)
    os.replace(p + ".tmp", p)


def path_state(d, via):
    """one transmitter's state: rung (where its next upload starts, if the K6 takes D24), clean
    (clean uploads in a row at F/1800 since a step down), k6_d24 (a report showed rates ..D24)"""
    return dict({"rung": "D24", "clean": 0, "k6_d24": False}, **d.get(via, {}))


def start_rung(want, via, ps, count=None):
    """the rung for an upload's first send: ic7100 always F18; --rate F18/D24 as asked; auto: D24
    when the K6 is known to take it, the path is marked D24 and the image has at least
    D24_MIN_CHUNKS chunks (count; None: no limit)"""
    if via != "pluto":
        if want == "D24":
            raise SystemExit("--rate D24 needs a flat transmitter (--via pluto): the IC-7100's audio can't carry it")
        return "F18"
    if want in ("F18", "D24"):
        return want
    if count is not None and count < D24_MIN_CHUNKS:
        return "F18"
    return "D24" if ps["k6_d24"] and ps["rung"] == "D24" else "F18"


def lost_share(sent, have_before, have_after):
    """the share of a send's chunks the K6 didn't get (0..1); None when nothing was sent"""
    if not sent:
        return None
    return min(1.0, max(0.0, 1.0 - (have_after - have_before) / sent))


def after_send(rung, st, lost, want):
    """-> the rung for the next send in this upload, and why it changed (or None). A D/2400 send
    with no report, over STEP_DOWN_LOST lost, or a K6 that fell back on crc/revert, steps down."""
    if rung != "D24" or want == "D24":
        return rung, None
    if st is None:
        return "F18", "no report after a D/2400 send"
    if st.get("fell") in ("crc", "revert"):
        return "F18", f"the K6 fell back from D/2400 ({st['fell']})"
    if lost is not None and lost > STEP_DOWN_LOST:
        return "F18", f"{lost:.0%} of the D/2400 chunks lost"
    return rung, None


def end_upload(ps, stepped_down, lost_total, done):
    """the path's state after an upload: a step down marks it F18; clean uploads at F18 count up
    and CLEAN_TO_STEP_UP of them mark it D24 again"""
    ps = dict(ps)
    if stepped_down:
        ps.update(rung="F18", clean=0)
    elif done and lost_total is not None and lost_total < CLEAN_LOST:
        if ps["rung"] == "F18":
            ps["clean"] += 1
            if ps["clean"] >= CLEAN_TO_STEP_UP:
                ps.update(rung="D24", clean=0)
    else:
        ps["clean"] = 0
    return ps


def preemph(x, fs):
    """750 us pre-emphasis shelved at 3 kHz, 0 dB at 1 kHz: pluto_bench --preemph's filter (and
    modem/fsk24.py's), here so the tool needs no modem import beyond k6mod (ci-radios scope)"""
    from scipy import signal as sps
    w1, w2 = 1.0 / 750e-6, 2 * np.pi * 3000.0
    b, a = sps.bilinear([1.0 / w1, 1.0], [1.0 / w2, 1.0], fs)
    _, h = sps.freqz(b, a, [1000.0], fs=fs)
    return sps.lfilter(b, a, np.asarray(x, float)) / abs(h[0])


def flat_track(parts, fs, gap_s=D24_GAP_S):
    """[(audio, peak Hz, pre-emphasise)] joined with gap_s of carrier between -> (audio scaled to
    +-1, full-scale deviation Hz) for pluto_bench send --dev without --preemph. Each part's audio
    peak becomes its peak Hz; a pre-emphasised part gets it before emphasis, as pluto_bench
    --preemph sends F/1800 (the 1 kHz level kept)"""
    out = []
    for x, peak, emph in parts:
        if out:
            out.append(np.zeros(int(gap_s * fs)))
        y = np.asarray(x, float) / max(1e-9, np.max(np.abs(x))) * peak
        out.append(preemph(y, fs) if emph else y)
    hz = np.concatenate(out)
    full = float(np.max(np.abs(hz))) * 1.02
    return hz / full, full


def d24_track(ctl_x, d24_x, fs, ctl_peak=None, d24_peak=D24_PEAK_HZ, gap_s=D24_GAP_S):
    """an F/1800 control record (pre-emphasised, ctl_peak Hz before it) then gap_s of carrier then
    D/2400 chunks (flat, d24_peak Hz)"""
    return flat_track([(ctl_x, ctl_peak or PLUTO_PEAK_HZ, True), (d24_x, d24_peak, False)], fs, gap_s)


PLUTO_PEAK_HZ = 3000.0   # the upload's peak deviation before pre-emphasis, whatever --level: the
                         # F/1800 ladders' (f1800.wav peaks at 0.81 under pluto_bench --dev 3700, so
                         # about 3 kHz; 11-16: 11-12/16 packets from TX gain -40 to -58). It was
                         # 1850 Hz until 11-17, 4 dB less, and on a weak path every upload chunk failed


def d24_send(a, d, rnd, serial, xfer, todo, payload, back=False):
    """one transmission with a rate change in it -> (WAV path, full-scale Hz for pluto_bench --dev,
    modem.k6's note). back=False: the F/1800 control record asking for D/2400, the gap, the chunks
    at D/2400. back=True: a D/2400 control record asking for F/1800 (a K6 that may still be on
    D/2400 after a step down hears only that), the gap, the chunks at F/1800"""
    import soundfile as sf
    c = os.path.join(d, f"ctl{rnd}.wav")
    m = os.path.join(d, f"chunks{rnd}.wav")
    ctl_wav(a, c, payload, back)
    note = mod_wav(a, m, serial, xfer, todo, payload, rung="F18" if back else "D24")
    cx, cfs = sf.read(c)
    mx, mfs = sf.read(m)
    if cfs != mfs:
        raise SystemExit(f"control record at {cfs} Hz, chunks at {mfs} Hz")
    pk = getattr(a, "pluto_peak", PLUTO_PEAK_HZ)
    if back:
        x, full = flat_track([(cx, D24_PEAK_HZ, False), (mx, pk, True)], cfs)
    else:
        x, full = d24_track(cx, mx, cfs, pk)
    w = os.path.join(d, f"up{rnd}.wav")
    sf.write(w, x, cfs, subtype="PCM_16")
    return w, full, note


def rig(a, *args):
    cmd = ["rigctl", "-m", "2", "-r", a.rigctld] + [str(v) for v in args]
    if a.dry_run:
        print("dry run:", " ".join(cmd))
        return "0"
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def key_and_play(a, path, kind="data", text=bench.ID_TEXT, flat_dev=None):
    """the bench's keying sequence under its PTT lock (tools/k6_bench.py); kind "id" for a CW ID.
    --via pluto: the ADALM-Pluto sends it instead (tools/pluto_bench.py send, AG7EW in MCW first
    and last, so a separate ID isn't needed); the IC-7100 only listens. flat_dev: a WAV already
    shaped (d24_track): full scale = flat_dev Hz, no pre-emphasis added"""
    if a.via == "pluto":
        if kind == "id":
            return
        dev = flat_dev or getattr(a, "pluto_peak", PLUTO_PEAK_HZ) / a.level
        cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pluto_bench.py"),
               "send", path, "--hz", str(a.freq)] + ([] if flat_dev else ["--preemph"]) + \
              ["--gain", str(a.pluto_gain), "--dev", f"{dev:.0f}"]
        if a.dry_run:
            print("dry run:", " ".join(cmd))
            return
        subprocess.run(cmd, check=True)
        return
    bench.key_and_play(lambda *v: rig(a, *v), path, a.device, a.dry_run, kind,
                       f"{TOOL} {os.path.basename(path)}", text)


def ctl_wav(a, path, payload=None, back=False):
    """a control record for a session (three copies), at the upload's packet size (the K6 hears
    only the size it is set to): sent at F/1800 asking for D/2400, or (back) sent at D/2400 asking
    for F/1800"""
    phy, baud, want = ("D", "2400", ["phy=F"]) if back else ("F", "1800", ["phy=D", "baud=2400"])
    cmd = [sys.executable, "-m", "modem.k6", "ctl", path, "--phy", phy, "--baud", baud,
           "--payload", payload or a.payload, "--level", str(a.level), "--set"] + want
    subprocess.run(cmd, check=True, capture_output=True, text=True,
                   cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def mod_wav(a, path, serial, xfer, only=None, payload=None, rung=None):
    phy, baud = ("D", 2400) if rung == "D24" else (a.phy, a.baud)
    cmd = [sys.executable, "-m", "modem.k6", "mod", path, a.source, "--key-file", a.key_file,
           "--serial", str(serial), "--xfer", str(xfer), "--phy", phy, "--baud", str(baud),
           "--payload", payload or a.payload, "--level", str(a.level), "--arq", "--telemetry", str(a.telemetry)]
    if a.api_level:
        cmd += ["--api-level", str(a.api_level)]
    if only is not None:
        cmd += ["--only", ",".join(map(str, only))]
    r = subprocess.run(cmd, check=True, capture_output=True, text=True,
                       cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    return r.stderr.strip()


def chunks_of(a, payload=None):
    """the image's chunk count at this payload size, from the same build `modem.k6 mod` makes"""
    if a.source.endswith(".bin"):
        code = open(a.source, "rb").read()
    else:
        code, _ = k6mod.build(a.source)
    n = k6mod.HDR + len(code) + k6mod.TAG
    dl = k6mod.data_bytes(128 if (payload or a.payload) == "XL" else 64)
    return -(-n // dl)


class Recorder:
    """parec of the IC-7100's receive audio into a raw file, read back while it grows"""

    def __init__(self, a, d):
        self.path = os.path.join(d, "rx.raw")
        self.p = None
        if not a.dry_run:
            self.p = subprocess.Popen(["parec", f"--device={a.rec_device}", "--format=s16le",
                                       f"--rate={FS}", "--channels=1", "--raw", self.path])

    def samples(self):
        """samples in the file so far: parec writes behind real time, so mark positions by this"""
        return os.path.getsize(self.path) // 2 if os.path.exists(self.path) else 0

    def audio(self, since=0):
        if not os.path.exists(self.path):
            return np.zeros(0)
        return np.fromfile(self.path, np.int16, offset=2 * since).astype(float) / 32768

    def stop(self):
        if self.p:
            self.p.terminate()
            self.p.wait()


TAIL_S = 60       # listen() decodes the last minute of the recording each pass: a report is ~10 s


def tail_reports(a, rec, since, reps):
    """reps (every report seen so far, [(s, report)]) with the recording's last TAIL_S decoded
    again: the reports that start before that window are kept, the window's are decoded fresh.
    Decoding all of a 30-minute recording each second was too slow to reach the report before
    the listen ran out (explorer-scan-6, 2026-11-07)."""
    w = max(since, rec.samples() - TAIL_S * FS)
    x = rec.audio(w)
    if len(x) < FS:              # under a second of new audio yet: nothing to decode
        return reps
    return [(t, r) for t, r in reps if t < w / FS] + [(t + w / FS, r) for t, r in k6mod.reports(x, FS, a.phy, a.baud)]


def listen(a, rec, since, xfer, seconds):
    """decode the recording from sample `since` on until a report for this transfer is complete
    (status and every text part) or seconds pass -> (status, text, every report seen)"""
    t_end = time.time() + seconds
    st, text, reps = None, "", []
    while time.time() < t_end:
        time.sleep(1.0)
        if a.dry_run:
            break
        reps = tail_reports(a, rec, since, reps)
        mine = [(t, r) for t, r in reps if r["kind"] == "text" or r.get("xfer") == xfer]
        st, text = k6mod.collect(mine)
        if st is not None and "lost]" not in text:
            if st["state"] != "ran" or st["parts"] <= 1 or text:
                time.sleep(4.0)          # its CW ID; then the channel is ours again
                break
    return st, text, reps


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("source", help="module C source or its built .bin (CI's mod-*.bin)")
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--api-level", type=int)
    ap.add_argument("--telemetry", type=int, default=4, help="say-line parts to send back (0-12, default 4)")
    ap.add_argument("--payload", choices=("L", "XL"), default="XL",
                    help="chunk size to start with (default XL); see --no-fallback")
    ap.add_argument("--no-fallback", dest="fallback", action="store_false",
                    help="keep --payload's size even when a whole send brings no report")
    ap.add_argument("--phy", choices="DF", default="F")
    ap.add_argument("--baud", type=float, default=1800)
    ap.add_argument("--level", type=float, default=0.3)
    ap.add_argument("--rounds", type=int, default=4, help="sends at most (default 4)")
    ap.add_argument("--run-s", type=float, default=10.0,
                    help="how long the module may run before its report (default 10 s)")
    ap.add_argument("--freq", type=int, default=440_850_000)
    ap.add_argument("--rigctld", default="localhost:4532")
    ap.add_argument("--device", default=PLAY)
    ap.add_argument("--rec-device", default=REC)
    ap.add_argument("--call", default="AG7EW")
    ap.add_argument("--keep", metavar="RAW", help="copy the recording here at the end (raw s16le mono 48 kHz)")
    ap.add_argument("--via", choices=("ic7100", "pluto"), default="ic7100",
                    help="the transmitter: the IC-7100 (default) or the ADALM-Pluto (tools/pluto_bench.py;"
                         " the IC-7100 still listens for the K6's reports, unkeyed)")
    ap.add_argument("--pluto-gain", type=float, default=-52.0,
                    help="--via pluto: TX gain, dB (default -52: an upload took 2 sends there, 11-16; at -40 the K6 bench copy heard nothing; -64 is past F/1800's edge)")
    ap.add_argument("--pluto-peak", type=float, default=PLUTO_PEAK_HZ,
                    help="--via pluto: peak deviation before pre-emphasis, Hz (default 3000, as the F/1800"
                         " ladders that decode)")
    ap.add_argument("--rate", choices=("auto", "F18", "D24"), default="auto",
                    help="--via pluto: the chunks' rate (auto, the default: D/2400 once a report showed the"
                         " K6 takes it, the image has 16 chunks or more and this transmitter's state says"
                         " D24, see above; F18 never asks; D24 always asks). --via ic7100 is always F/1800")
    ap.add_argument("--rate-state", metavar="JSON", help="the rate ladder's state file (default $BLOWFANG_WORK/state/k6-rate.json)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ladder = a.phy == "F" and a.baud == 1800     # the ladder starts from F/1800, as the K6 boots
    want = a.rate if ladder else "F18"
    rates = load_rates(a.rate_state)
    ps = path_state(rates, a.via)
    if a.freq != 440_850_000:
        sys.exit("the K6 answers on 440.850 only")
    f = rig(a, "f")
    if not a.dry_run and int(f) != a.freq:
        sys.exit(f"IC-7100 on {f} Hz, not {a.freq}: set both radios to 440.850 first")
    rig(a, "M", "PKTFM", 12000)      # the wide FM-D filter: passband 0 picks the 6 kHz one, half the deviation
    serial = int(time.time())
    xfer = serial & 0xFF
    payload = a.payload
    count = chunks_of(a, payload)
    rung = start_rung(want, a.via, ps, count)
    if want == "auto" and a.via == "pluto" and ps["k6_d24"] and ps["rung"] == "D24" and count < D24_MIN_CHUNKS:
        print(f"{count} chunks, under {D24_MIN_CHUNKS}: F/1800 is quicker than asking for D/2400", flush=True)
    todo = list(range(count))
    heard = False                    # a report has come for this chunk size
    have = 0                         # chunks the K6 had at its last report for this transfer
    sent_total = got_total = 0       # chunks sent, and how many of them the K6 newly had
    stepped_down = done = maybe_d24 = False
    with tempfile.TemporaryDirectory() as d:
        rec = Recorder(a, d)
        try:
            for rnd in range(1, a.rounds + 1):
                if rung == "D24" or maybe_d24:
                    w, full, note = d24_send(a, d, rnd, serial, xfer, todo, payload, back=rung != "D24")
                else:
                    w, full = os.path.join(d, f"up{rnd}.wav"), None
                    note = mod_wav(a, w, serial, xfer, todo, payload)
                print(f"{time.strftime('%H:%M:%S')} send {rnd}: {len(todo)} of {count} {payload} chunks "
                      f"at {'D/2400' if rung == 'D24' else f'{a.phy}/{a.baud:g}'}"
                      f"{' after a D/2400 record asking for F/1800' if maybe_d24 and rung != 'D24' else ''} "
                      f"(serial {serial}, xfer {xfer}); {note}", flush=True)
                sent_d24 = rung == "D24"
                key_and_play(a, w, flat_dev=full)
                since = rec.samples()
                print(f"{time.strftime('%H:%M:%S')} listening for the K6's report", flush=True)
                st, text, _ = listen(a, rec, since, xfer, 25.0 + a.run_s)
                step, arg = next_step(st, count, xfer)
                if st is not None:
                    print(f"{time.strftime('%H:%M:%S')} K6: {st['state']}, {st['have']}/{st['count']} "
                          f"chunks, r={st['r']} ({k6mod.RESULTS.get(st['r'], '?')}), rates {st.get('rates')}"
                          f" on {st.get('rung')}" + (f", fell back ({st['fell']})" if st.get("fell") else ""),
                          flush=True)
                    if st.get("rates"):
                        ps["k6_d24"] = "D24" in st["rates"]
                else:
                    print(f"{time.strftime('%H:%M:%S')} no report heard", flush=True)
                have_after = st["have"] if st is not None and st.get("xfer") == xfer else have
                lost = lost_share(len(todo), have, have_after)
                sent_total += len(todo)
                got_total += max(0, have_after - have)
                have = have_after
                rung, why = after_send(rung, st, lost, want)
                # after a D/2400 send the K6 may still be on D/2400 (its report says; no report:
                # unknown) and then hears no F/1800 record: the next F/1800 send says so at D/2400
                maybe_d24 = sent_d24 and rung != "D24" and (st is None or st.get("rung") != "F18")
                if why:
                    stepped_down = True
                    print(f"{time.strftime('%H:%M:%S')} {why}: the rest goes at F/1800", flush=True)
                if step == "send" and not why and switch_size(st, heard, a.fallback):
                    payload = other_size(payload)
                    serial = max(int(time.time()), serial + 1)   # a new transfer: the K6 takes a newer serial
                    xfer = serial & 0xFF
                    count = chunks_of(a, payload)
                    arg = list(range(count))
                    have = 0
                    print(f"{time.strftime('%H:%M:%S')} no report after a whole send: the K6 may be "
                          f"set to {payload} packets, so the next send is {count} {payload} chunks", flush=True)
                heard = heard or st is not None
                if step == "send":
                    todo = arg
                    continue
                print(arg)
                if text:
                    print("K6 telemetry:\n" + text, end="" if text.endswith("\n") else "\n")
                done = step == "done"
                break
            else:
                print(f"gave up after {a.rounds} sends")
        finally:
            rec.stop()
            if a.via == "pluto" and not a.dry_run:
                lost_total = 1 - got_total / sent_total if sent_total else None
                rates[a.via] = end_upload(ps, stepped_down, lost_total, done)
                save_rates(rates, a.rate_state)
                print(f"rate ladder ({a.via}): {rates[a.via]}"
                      + (f", {lost_total:.0%} of {sent_total} chunks sent were lost" if lost_total is not None else ""))
            if a.keep:
                import shutil
                shutil.copy(rec.path, a.keep)
                print(f"recording (s16le mono {FS} Hz) kept in {a.keep}")
            from modem import k6
            p = os.path.join(d, "id.wav")
            import soundfile as sf
            sf.write(p, k6.cw(a.call + " SOTTO TEST", FS), FS, subtype="PCM_16")
            print(f"{time.strftime('%H:%M:%S')} CW ID", flush=True)
            key_and_play(a, p, "id", a.call + " SOTTO TEST")


if __name__ == "__main__":
    main()
