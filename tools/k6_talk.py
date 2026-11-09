"""Live talk-through: speak into a microphone on hamlet and the K6 plays it a few seconds later,
for as long as you talk (modem/k6live.py has how).

  python3 tools/k6_talk.py                       talk until Ctrl-C (at most --seconds, 120)
         [--seconds S] [--source PULSE_SOURCE]   the mic (default: PulseAudio's default source)
         [--codec 1200|600|400] [--depth 4]      sotto 1200 at depth 4: speech ~6.3 s later
         [--freq 440850000] [--no-id]
  python3 tools/k6_talk.py --in speech.wav       the same from a file, paced as if spoken
  python3 tools/k6_talk.py --in speech.wav --out send.wav    no radio: the whole send as audio
  python3 tools/k6_talk.py --sot msg.sot --out send.wav      ... from sotto frames already made

On air, all on one key-up through tools/k6_bench.py (PTT lock, bench owner, 70 cm only, and
only on --freq): a stream-on control packet, then the speech as one open-ended stream message
(total 0xFFFF), each interleave group as soon as the encoder has filled it, filler and repeats
in between. A CW ID ('AG7EW SOTTO TEST', k6_bench) goes first unless one went out in the last
minute, and again at the end, so a session of up to 9 minutes is never unidentified for more
than 10. The first Ctrl-C ends the talk cleanly (the group being filled goes out padded with
silence, then the tail and the ID); a second one unkeys at once.

The K6: a voice image with streaming (vlr/vlrh), XL and F/1800 (both power-on defaults), on
440.850. It plays the stream as it arrives and stops after the last group. Images built before
this change keep waiting after the last group, silent, until EXIT or the next message.

Needs: parec and pacat (PulseAudio), stdbuf, and sotto's float encoder (the fixed-point one has
no sotto 1200 encoder): --enc PATH, $SOTTO_ENC, sotto/c/sotto_enc (make -C sotto codec2, then
make -C sotto/c sotto_enc), ~/sotto/c/sotto_enc (hamlet's codec checkout), or on PATH. The run is saved to $BLOWFANG_WORK/runs/talk-<UTC>/: the frames sent
(talk.sot) and the timeline (log.txt).
SPDX-License-Identifier: MIT
"""
import argparse
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from modem import fsk, k6live, pager  # noqa: E402

MAX_S = 540                # a session never runs past the 9-minute ID rule (k6_bench)
ID_RECENT_S = 60           # no ID first if one went out this recently
AHEAD_S = 0.15             # audio written ahead of real time
FS_ENC = 8000
CODEC_FLAG = {"1200": "-1200", "600": "-600", "400": "-400"}
QUIET = {"start_new_session": True}   # Ctrl-C is ours: the player and the mic must outlive it


def say(*a):
    print(time.strftime("%H:%M:%S", time.gmtime()), *a, flush=True)


def encoder(path=None):
    for p in (path, os.environ.get("SOTTO_ENC"), os.path.join(ROOT, "sotto", "c", "sotto_enc"),
              os.path.expanduser("~/sotto/c/sotto_enc"), shutil.which("sotto_enc")):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    raise SystemExit("no sotto encoder: make -C sotto/c sotto_enc, or give --enc PATH")


def read_pcm8k(path):
    """a WAV/FLAC (any rate) or raw s16le 8 kHz file -> int16 at 8 kHz"""
    if path.endswith(".raw"):
        return np.fromfile(path, "<i2")
    import soundfile
    x, fs = soundfile.read(path, dtype="float64", always_2d=True)
    x = fsk.resample(x[:, 0], fs, FS_ENC)
    return (np.clip(x, -1, 1) * 32767).astype("<i2")


def encode(pcm, codec, enc):
    """int16 8 kHz -> 6-byte frames, through sotto_enc"""
    out = subprocess.run([enc, CODEC_FLAG[codec], "-", "-"], input=pcm.astype("<i2").tobytes(),
                         capture_output=True, check=True).stdout
    return [out[i:i + 6] for i in range(0, len(out) - 5, 6)]


def check(a):
    if a.codec not in CODEC_FLAG:
        raise SystemExit(f"refusing: sotto {a.codec} can't stream live (its packets are slower on air "
                         f"than its speech: {k6live.air_ratio(a.codec, a.depth):.2f}x); use 1200, 600 or 400")
    r = k6live.air_ratio(a.codec, a.depth, parity=a.parity)
    if r > k6live.MAX_RATIO:
        raise SystemExit(f"refusing: {r:.2f} s on air per second of speech (over {k6live.MAX_RATIO}); "
                         f"drop --parity or use depth 8")
    if a.seconds > MAX_S:
        raise SystemExit(f"refusing: --seconds {a.seconds:.0f} is over {MAX_S} (the 9-minute ID rule)")
    return r


def summary(snd, log=None):
    play, waited = k6live.k6_schedule(snd.groups(), snd.packer)
    ends = snd.packet_ends()
    gap = max(np.diff(ends)) if len(ends) > 1 else 0.0
    reps = sum(1 for _, _, w in snd.log if w.startswith("repeat"))
    s = (f"{snd.sent} groups ({snd.sent * snd.packer.fpg * snd.packer.prof.frame_s:.1f} s of speech), "
         f"{reps} repeats, {snd.t:.1f} s on air; a real-time K6 starts at {play[0] if play else 0:.1f} s, "
         f"waits {waited:.2f} s, longest gap between packets {gap:.2f} s")
    if log:
        with open(log, "w") as f:
            f.write(s + "\n")
            for t0, t1, w in snd.log:
                if w != "fill":
                    f.write(f"{t0:9.3f} {t1:9.3f} {w}\n")
    return s


def control(a):
    seq = a.msg_id if a.msg_id is not None else int(time.time()) & 0xFF
    return pager.control_packet(seq, None, "stream-on", 128)


def cmd_offline(a):
    if a.sot:
        b = open(a.sot, "rb").read()
        frames = [b[i:i + 6] for i in range(0, len(b) - 5, 6)]
    else:
        frames = encode(read_pcm8k(a.inp), a.codec, encoder(a.enc))
    if len(frames) * pager.CODECS[a.codec][1] > a.seconds:
        frames = frames[:int(a.seconds / pager.CODECS[a.codec][1])]
    mid = a.msg_id if a.msg_id is not None else int(time.time()) & 0xFF
    x, snd = k6live.offline(frames, a.codec, a.depth, mid, a.slack, 0.12, a.level, control(a),
                            fsk.FS, a.parity, not a.no_repeat)
    import soundfile
    soundfile.write(a.out, np.clip(x, -1, 1).astype(np.float32), fsk.FS, subtype="PCM_16")
    say(f"wrote {a.out}: " + summary(snd))
    return 0


class Source:
    """frames as the encoder makes them: (monotonic time, 6 bytes) on a queue, from parec (or a
    file fed at real time) through sotto_enc with its output unbuffered"""

    def __init__(self, a):
        self.q = queue.Queue()
        self.procs = []
        enc = [encoder(a.enc), CODEC_FLAG[a.codec], "-", "-"]
        if shutil.which("stdbuf"):
            enc = ["stdbuf", "-o0"] + enc
        self.feed = None
        if a.inp:
            self.pcm = read_pcm8k(a.inp)
            self.enc = subprocess.Popen(enc, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0, **QUIET)
            self.feed = threading.Thread(target=self._feed, daemon=True)
        else:
            src = a.source or "@DEFAULT_SOURCE@"
            if "Burr-Brown" in src:
                raise SystemExit(f"refusing: {src} is the IC-7100's receive audio, not a microphone")
            rec = subprocess.Popen(["parec", f"--device={src}", f"--rate={FS_ENC}", "--channels=1",
                                    "--format=s16le", "--latency-msec=20"], stdout=subprocess.PIPE, **QUIET)
            self.enc = subprocess.Popen(enc, stdin=rec.stdout, stdout=subprocess.PIPE, **QUIET)
            rec.stdout.close()
            self.procs.append(rec)
        self.procs.append(self.enc)
        self.reader = threading.Thread(target=self._read, daemon=True)

    def start(self):
        if self.feed:
            self.feed.start()
        self.reader.start()

    def _feed(self):
        t0, n, step = time.monotonic(), 0, FS_ENC // 50
        try:
            while n < len(self.pcm):
                self.enc.stdin.write(self.pcm[n:n + step].tobytes())
                n += step
                time.sleep(max(0.0, t0 + n / FS_ENC - time.monotonic()))
            self.enc.stdin.close()
        except (BrokenPipeError, ValueError, OSError):
            pass

    def _read(self):
        while True:
            b = self.enc.stdout.read(6)
            if len(b) < 6:
                self.q.put(None)
                return
            self.q.put((time.monotonic(), b))

    def stop(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()


def cmd_live(a):
    import k6_bench as bench
    rig = bench.rigctl(a.rigctld)
    getattr(bench, "check_owner", lambda: None)()
    hz = bench.rig_hz(rig)
    if hz != a.freq or not bench.in_band(hz):
        raise SystemExit(f"refusing: IC-7100 on {hz} Hz, not {a.freq} (the K6's pager frequency)")
    device = a.device or bench.SINK
    d = os.path.join(bench.work(), "runs", time.strftime("talk-%Y%m%dT%H%M%SZ", time.gmtime()))
    os.makedirs(d, exist_ok=True)
    mid = a.msg_id if a.msg_id is not None else int(time.time()) & 0xFF
    packer = k6live.Packer(a.codec, a.depth, mid, parity=a.parity)
    snd = k6live.Sender(packer, slack_s=a.slack, control=control(a), repeat=not a.no_repeat)
    mod = k6live.Modulator(level=a.level)
    stop = threading.Event()

    def on_int(*_):
        say("stopping: the last group goes out, then the ID (Ctrl-C again unkeys now)")
        stop.set()
        signal.signal(signal.SIGINT, signal.default_int_handler)
    sent = []
    with bench.ptt_lock():
        if not a.no_id and time.time() - bench._read("last_id") > ID_RECENT_S:
            bench.send_id(rig, "before talk-through", device)
            time.sleep(0.5)
        src = Source(a)
        out = subprocess.Popen(["pacat", "--playback", f"--device={device}", f"--rate={fsk.FS}",
                                "--channels=1", "--format=s16le", "--latency-msec=50"],
                               stdin=subprocess.PIPE, **QUIET)
        old = signal.signal(signal.SIGINT, on_int)
        try:
            with bench.keyed(rig, what=f"talk-through sotto {a.codec} depth {a.depth} id={mid:02x}"):
                t0 = time.monotonic()
                src.start()
                say(f"TALK NOW (sotto {a.codec}, depth {a.depth}, at most {a.seconds:.0f} s; Ctrl-C ends)")
                eof = False
                while True:
                    now = time.monotonic() - t0
                    while not eof:
                        try:
                            item = src.q.get_nowait()
                        except queue.Empty:
                            break
                        if item is None:
                            eof = True
                        elif not snd.finished:
                            snd.push(item[1], item[0] - t0)
                            sent.append(item[1])
                    if not snd.finished and (eof or stop.is_set() or now >= a.seconds):
                        snd.finish(now)
                        src.stop()
                    while mod.seconds < now + AHEAD_S:
                        data = snd.step(mod.seconds)
                        if data is None:
                            break
                        out.stdin.write((mod(data, last=snd.done) * 32767).astype("<i2").tobytes())
                    if snd.done:
                        break
                    time.sleep(0.02)
                out.stdin.close()
                out.wait()
                time.sleep(a.hang)
        finally:
            signal.signal(signal.SIGINT, old)
            src.stop()
            if out.poll() is None:
                out.kill()
        if not a.no_id:
            time.sleep(0.5)
            bench.send_id(rig, "end of talk-through", device)
    with open(os.path.join(d, "talk.sot"), "wb") as f:
        f.write(b"".join(sent))
    say(summary(snd, os.path.join(d, "log.txt")))
    say(f"saved {d}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="k6_talk.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", help="speech from a file (WAV/FLAC, or .raw s16le 8 kHz) instead of the mic")
    ap.add_argument("--sot", help="sotto frames already encoded (with --out)")
    ap.add_argument("--out", help="write the send as a 48 kHz WAV instead of keying (no rig, no lock)")
    ap.add_argument("--seconds", type=float, default=120.0, help=f"at most this much speech (120; max {MAX_S})")
    ap.add_argument("--codec", default="1200", help="sotto 1200 (default), 600 or 400")
    ap.add_argument("--depth", type=int, default=4, choices=pager.DEPTHS,
                    help="packets per interleave group (4): smaller starts sooner, larger spreads a loss")
    ap.add_argument("--parity", action="store_true", help="a parity packet a group (depth 8 only keeps up)")
    ap.add_argument("--slack", type=float, default=0.5, help="hold group 0 back this long (s): the K6's margin")
    ap.add_argument("--no-repeat", action="store_true", help="filler only between groups, no repeats")
    ap.add_argument("--level", type=float, default=0.3, help="audio peak (0.3, as modem.k6's F sends)")
    ap.add_argument("--msg-id", type=lambda s: int(s, 0), help="message id (default: from the clock)")
    ap.add_argument("--source", help="PulseAudio source for the mic (default @DEFAULT_SOURCE@)")
    ap.add_argument("--enc", help="sotto_enc (default $SOTTO_ENC, sotto/c/sotto_enc, or on PATH)")
    ap.add_argument("--freq", type=int, default=440_850_000, help="refuse unless the IC-7100 reads this (Hz)")
    ap.add_argument("--device", help="the IC-7100's USB audio sink (k6_bench's)")
    ap.add_argument("--rigctld", help="host:port (default rigctl's own)")
    ap.add_argument("--hang", type=float, default=0.3, help="PTT held this long after the audio ends (s)")
    ap.add_argument("--no-id", action="store_true", help="no CW ID before and after (the keeper still IDs)")
    a = ap.parse_args(argv)
    if a.sot and not a.out:
        ap.error("--sot needs --out")
    check(a)
    if a.out:
        if not (a.sot or a.inp):
            ap.error("--out needs --in or --sot")
        return cmd_offline(a)
    return cmd_live(a)


if __name__ == "__main__":
    sys.exit(main())
