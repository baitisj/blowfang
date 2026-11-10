#!/usr/bin/env python3
"""The hamlet bench's transmit rules in one place: the PTT lock, the CW ID keeper, and the state
they share. Every tool that keys the IC-7100 goes through here (docs/hamlet-permissions.md).

  python3 tools/k6_bench.py paths              where everything lives (BLOWFANG_WORK, default ~/blowfang-work)
  python3 tools/k6_bench.py status             last CW ID, last data TX, the keeper, the lock
  python3 tools/k6_bench.py send WAV           key and play one WAV on 70 cm (sh tools/k6_send.sh)
         [--k6log LOG | --freq HZ]             ... and only if the rig matches the K6's last CFG / HZ
  python3 tools/k6_bench.py id [WHY]           a CW ID now
  python3 tools/k6_bench.py idkeeper           the CW ID keeper (the k6-idkeeper user unit runs it)
  python3 tools/k6_bench.py wavs               (re)make the ID and K6 reset WAVs (needs the checkout)
  python3 tools/k6_bench.py install [--unit]   copy this file and the reset trigger into
                                               $BLOWFANG_WORK/bin, make the WAVs, write the unit
  python3 tools/k6_bench.py stop [--freq HZ]   stop a running K6 module that listens for it
                                               (c/k6/mod/k6_stop.h): two PSK31 'AG7EW STOP' bursts
  python3 tools/k6_bench.py owner              who holds the bench, until when, doing what
  python3 tools/k6_bench.py claim WHO MIN WHAT  hold the bench for MIN minutes (WHO: a short name,
                                               such as the thread's; again by WHO extends it)
  python3 tools/k6_bench.py release WHO        give it back (--force: whoever holds it)
  python3 tools/k6_bench.py serial-hold WHO -- CMD...   run the one /dev/ttyUSB2 reader (a logger) as
                                               WHO, until it ends or someone asks for the port
  python3 tools/k6_bench.py serial-owner       who reads /dev/ttyUSB2 (WHO, pid, command, since)
  python3 tools/k6_bench.py serial-release WHO   ask the reader to let go and wait until it has
                                               (--wait S, 30): no kill across sessions needed

What they share, under $BLOWFANG_WORK/state/ (it outlives reboots, unlike a session's /tmp):
  ptt.lock          flock: one keyer at a time, data or CW ID
  ptt.lock.last_tx  when the last data transmission ended (epoch s)
  ptt.lock.last_id  when the last CW ID ended
  txlog.txt         one line per data transmission: start, end, what
  cwid.wav          'AG7EW SOTTO TEST', MCW 800 Hz, 20 wpm, level 0.4
  reset.wav         the K6 reset control packet (modem.k6 ctl --phy F --seq 201 --action reset), XL then L
  stop.wav          one burst of the module stop signal: PSK31 'AG7EW STOP', 1000 Hz, level 0.3
  bench.owner       who holds the bench and until when (JSON: who, what, since, until; epoch s)
  serial.lock       flock, held by serial-hold for as long as its reader runs
  serial.owner      that reader (JSON: who, pid, cmd, since); stale once serial.lock is free
  serial.release    a request to let go of the port (JSON: by, at); serial-hold watches for it
and $BLOWFANG_WORK/logs/k6-cwid.txt, one line per CW ID (~/k6-cwid.txt links there).

The bench owner: a session that will use the radios for a while claims the bench first. While a
claim runs, a data key-up through this file (send, the reset trigger, k6_upload, k6_ifcap,
k6_afsweep, k6_rxmap) is refused unless $K6_BENCH_AS (or send's --as) names the holder. The CW ID keeper never
asks: an ID is the law, whoever holds the bench. A claim ends by itself at its time, so a session
that dies leaves nothing stuck. With no claim the bench is free to anyone, as before.

The keeper (FCC 97.119): a session is active while a data TX ended less than 10 min ago. While
active, it IDs when 9 min have passed since the last ID; 2 min after the last data TX it sends a
final ID unless one came after it (but not for a TX over 30 min old, such as one from before a
reboot). It only keys on 70 cm or 2 m, never anywhere else.

The K6 cable's one reader: /dev/ttyUSB2 takes one reader (two split the bytes) and k6_update
needs it free to flash. A thread runs its logger under serial-hold; another thread that needs the
port (a flash, its own logger) runs serial-release, and the holder's own serial-hold stops its
child and exits, so no session ever has to kill another session's process (which a session's
permission check refuses). The holder's log file stays where it is.

Standard library only, so the copy in $BLOWFANG_WORK/bin keeps working whatever commit ~/blowfang has
checked out. Opens no serial port: PTT goes through rigctld (/dev/ttyUSB0), never /dev/ttyUSB2.
SPDX-License-Identifier: MIT
"""
import argparse
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import wave

SINK = "alsa_output.usb-Burr-Brown_from_TI_USB_Audio_CODEC-00.analog-stereo"
PAGER_HZ = 440_850_000
BAND_70CM = (430_010_000, 449_990_000)        # north of Line A: nothing at 420-430
BANDS_ID = (BAND_70CM, (144_010_000, 147_990_000))
ID_TEXT = os.environ.get("CWID", "AG7EW SOTTO TEST")
SESSION_S, ID_EVERY_S, END_AFTER_S = 600, 540, 120
END_STALE_S = 1800                            # no "end of session" ID for a TX older than this
                                              # (state left from before a reboot or a keeper outage)
ID_FIRST_S = 510                              # send: ID first when the last one is older (8.5 min)
LOCK_WAIT_S = 90.0
STOP_TEXT = "AG7EW STOP"                      # each of the stop signal's two bursts (k6_stop.h)
STOP_GAP_S = 2.0                              # between them (the K6 takes 1-5 s; the rig's
                                              # read-backs before the second key-up add ~0.3 s)
HERE = os.path.dirname(os.path.abspath(__file__))


# ---- where things live ----

def work():
    """$BLOWFANG_WORK, default ~/blowfang-work: every bench file outside the checkout goes under it"""
    return os.path.abspath(os.path.expanduser(os.environ.get("BLOWFANG_WORK") or "~/blowfang-work"))


def path(name):
    """a bench file by name: lock, last_tx, last_id, txlog, cwid_wav, reset_wav, cwid_log,
    keeper (the keeper's pid file), bin"""
    w = work()
    st = os.path.join(w, "state")
    return {"lock": os.path.join(st, "ptt.lock"), "last_tx": os.path.join(st, "ptt.lock.last_tx"),
            "last_id": os.path.join(st, "ptt.lock.last_id"), "txlog": os.path.join(st, "txlog.txt"),
            "cwid_wav": os.path.join(st, "cwid.wav"), "reset_wav": os.path.join(st, "reset.wav"),
            "keeper": os.path.join(st, "idkeeper.pid"), "owner": os.path.join(st, "bench.owner"),
            "cwid_log": os.path.join(w, "logs", "k6-cwid.txt"), "stop_wav": os.path.join(st, "stop.wav"),
            "bin": os.path.join(w, "bin"), "serial_lock": os.path.join(st, "serial.lock"),
            "serial_owner": os.path.join(st, "serial.owner"),
            "serial_release": os.path.join(st, "serial.release")}[name]


def repo():
    """the blowfang checkout (for modem.k6): this file's parent when it is in one, else $BLOWFANG_REPO
    or ~/blowfang"""
    up = os.path.dirname(HERE)
    if os.path.isdir(os.path.join(up, "modem")):
        return up
    return os.path.abspath(os.path.expanduser(os.environ.get("BLOWFANG_REPO") or "~/blowfang"))


def _read(name):
    try:
        return float(open(path(name)).read())
    except (OSError, ValueError):
        return 0.0


def _write(name, value):
    p = path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"{value:.3f}")
    os.replace(tmp, p)


def _append(name, line):
    p = path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a") as f:
        f.write(line + "\n")


def utc(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t))


def note_tx(t0, t1, what):
    _write("last_tx", t1)
    _append("txlog", f"{t0:.3f} {t1:.3f} {what}")


def note_id(t0, t1, why, text=ID_TEXT):
    _write("last_id", t1)
    _append("cwid_log", f"{utc(t0)} UTC  CW ID '{text}' ({why}), {t1 - t0:.1f} s")


# ---- the bench owner ----

def owner(now=None):
    """the running claim as a dict (who, what, since, until), or None when the bench is free"""
    try:
        o = json.load(open(path("owner")))
        if (now or time.time()) < float(o["until"]):
            return o
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def me():
    """who this process is on the bench: $K6_BENCH_AS, or None"""
    return os.environ.get("K6_BENCH_AS") or None


def owner_line(o, now=None):
    now = now or time.time()
    return (f"{o['who']} until {utc(float(o['until']))} UTC ({(float(o['until']) - now) / 60:.0f} min left)"
            + (f": {o['what']}" if o.get("what") else ""))


def check_owner(who=None):
    """SystemExit unless the bench is free or `who` (default $K6_BENCH_AS) holds it"""
    o = owner()
    who = who or me()
    if o and o["who"] != who:
        raise SystemExit(f"refusing: the bench is held by {owner_line(o)}; "
                         f"{'you are ' + who if who else 'set K6_BENCH_AS to the holder'}"
                         f" (k6_bench.py owner)")


def claim(who, minutes, what="", now=None):
    """hold the bench for `minutes` from now; the holder can extend or shorten its own claim
    (at most 12 h: a forgotten claim must not hold the bench for days)"""
    if not 0 < minutes <= 720:
        raise SystemExit("claim: MIN is 1 to 720")
    if not who or any(c.isspace() for c in who):
        raise SystemExit("claim: WHO is one short word, such as the thread's name")
    now = now or time.time()
    p = path("owner")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        o = owner(now)
        if o and o["who"] != who:
            raise SystemExit(f"the bench is held by {owner_line(o, now)}")
        new = {"who": who, "what": what, "since": o["since"] if o else round(now, 3),
               "until": round(now + 60 * minutes, 3)}
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(new, f)
        os.replace(tmp, p)
        _append("txlog", f"{now:.3f} {now:.3f} bench claimed by {who} for {minutes:g} min: {what}")
    return new


def release(who, force=False, now=None):
    """end `who`'s claim (force: anyone's); True when one ended"""
    now = now or time.time()
    p = path("owner")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        o = owner(now)
        if not o:
            return False
        if o["who"] != who and not force:
            raise SystemExit(f"release: the bench is held by {owner_line(o, now)}, not {who}")
        os.remove(p)
        _append("txlog", f"{now:.3f} {now:.3f} bench released by {who} (held by {o['who']})")
    return True


# ---- the K6 cable's one reader (/dev/ttyUSB2) ----

SERIAL_POLL_S = 0.5


def _json(name):
    try:
        return json.load(open(path(name)))
    except (OSError, ValueError):
        return None


def _put_json(name, value):
    p = path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(value, f)
    os.replace(tmp, p)


def _unlink(name):
    with contextlib.suppress(OSError):
        os.remove(path(name))


def serial_owner():
    """the running reader as a dict (who, pid, cmd, since), or None (no reader, or a stale file
    from one that died: serial.lock is free then)"""
    p = path("serial_lock")
    if not os.path.exists(p):
        return None
    with open(p, "a") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            for _ in range(20):                 # a new holder writes its file just after the lock
                o = _json("serial_owner")
                if o:
                    return o
                time.sleep(0.05)
            return {"who": "?", "pid": 0, "cmd": "?", "since": 0}
        fcntl.flock(lk, fcntl.LOCK_UN)
    return None


def serial_hold(who, cmd, poll=SERIAL_POLL_S, term_s=5.0):
    """run `cmd` (the reader) as `who` until it exits or a serial-release request comes; returns
    its exit code, or 0 when it was asked to let go"""
    if not who or any(c.isspace() for c in who):
        raise SystemExit("serial-hold: WHO is one short word, such as the thread's name")
    if not cmd:
        raise SystemExit("serial-hold: give the reader's command after --")
    p = path("serial_lock")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    lk = open(p, "a")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        o = serial_owner() or {}
        lk.close()
        raise SystemExit(f"serial-hold: {o.get('who', '?')} reads the port (pid {o.get('pid', '?')}); "
                         f"ask with: k6_bench.py serial-release {who}")
    _unlink("serial_release")                   # a request made before we started is not for us
    now = time.time()
    _put_json("serial_owner", {"who": who, "pid": os.getpid(), "cmd": " ".join(cmd), "since": round(now, 3)})
    try:
        child = subprocess.Popen(cmd)
    except OSError:
        _unlink("serial_owner")
        fcntl.flock(lk, fcntl.LOCK_UN)
        lk.close()
        raise
    _put_json("serial_owner", {"who": who, "pid": child.pid, "cmd": " ".join(cmd), "since": round(now, 3)})
    _append("txlog", f"{now:.3f} {now:.3f} serial reader started by {who}: {' '.join(cmd)}")
    rc, why = None, "ended"
    try:
        while rc is None:
            rc = child.poll()
            if rc is None and os.path.exists(path("serial_release")):
                r = _json("serial_release") or {}
                why = f"released for {r.get('by', '?')}"
                child.terminate()
                try:
                    child.wait(term_s)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                rc = 0
                break
            if rc is None:
                time.sleep(poll)
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait()
        _unlink("serial_owner")
        _unlink("serial_release")
        now = time.time()
        _append("txlog", f"{now:.3f} {now:.3f} serial reader of {who} stopped ({why})")
        fcntl.flock(lk, fcntl.LOCK_UN)
        lk.close()
    return rc


def serial_release(by, wait=30.0, poll=0.2):
    """ask the running reader to let go and wait until it has; True when the port is free"""
    if serial_owner() is None:
        _unlink("serial_release")
        return True
    _put_json("serial_release", {"by": by, "at": round(time.time(), 3)})
    end = time.time() + wait
    while time.time() < end:
        if serial_owner() is None:
            return True
        time.sleep(poll)
    return False


# ---- the PTT lock ----

_held = 0


@contextlib.contextmanager
def ptt_lock(wait=LOCK_WAIT_S):
    """one keyer at a time across every process on the bench. Re-entrant within a process."""
    global _held
    if _held:
        _held += 1
        try:
            yield
        finally:
            _held -= 1
        return
    p = path("lock")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a") as lk:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise SystemExit(f"the PTT lock ({p}) was held for {wait:.0f} s: another keyer is running")
                time.sleep(0.1)
        _held = 1
        try:
            yield
        finally:
            _held = 0
            fcntl.flock(lk, fcntl.LOCK_UN)


def lock_free():
    """True when nobody holds the PTT lock (a probe; it takes and drops it at once)"""
    p = path("lock")
    if not os.path.exists(p):
        return True
    with open(p, "a") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(lk, fcntl.LOCK_UN)
        return True


# ---- the rig ----

def rigctl(rigctld=None):
    """rig(*args) -> rigctl's first output line, through rigctld (rigctl -m 2)"""
    base = ["rigctl", "-m", "2"] + (["-r", rigctld] if rigctld else [])

    def rig(*args):
        out = subprocess.run(base + [str(v) for v in args], check=True, capture_output=True, text=True)
        return out.stdout.strip().split("\n")[0].strip()
    return rig


def rig_hz(rig):
    f = rig("f")
    if not f.isdigit():
        raise SystemExit(f"refusing: rigctl f gave '{f}'")
    return int(f)


def in_band(hz, bands=(BAND_70CM,)):
    return any(lo <= hz <= hi for lo, hi in bands)


def simplex(rig):
    """transmit where the rig listens: VFO A, split off, no repeater shift, set and read back right
    before PTT. The IC-7100's auto repeater turns DUP on by itself in the 70 cm repeater sub-bands
    (the CW recorder parks there), and on 2026-11-08 a CW ID went out shifted after that."""
    rig("V", "VFOA")
    rig("S", 0, "VFOA")
    rig("R", "0")
    shift = (rig("r").splitlines() or [""])[0]
    split = (rig("s").splitlines() or [""])[0]
    if shift != "None" or split != "0":
        raise SystemExit(f"refusing to key: repeater shift {shift!r}, split {split!r} after clearing them")


@contextlib.contextmanager
def keyed(rig, kind="data", what="", text=ID_TEXT, dry_run=False, lead=0.2):
    """PTT on for the with-body, under the lock, then off (always). Afterwards the keeper's state:
    kind "data" sets last_tx and a txlog line, kind "id" sets last_id and a k6-cwid.txt line.
    dry_run: no lock and no state (rig is the caller's, which prints instead).
    A data key-up checks the bench owner first (check_owner); a CW ID never does."""
    if kind != "id" and not dry_run:
        check_owner()
    with (contextlib.nullcontext() if dry_run else ptt_lock()):
        t = rig("t")
        if t != "0":
            raise SystemExit(f"the IC-7100 is already keyed (t={t}) by something outside the PTT lock")
        if not dry_run:
            simplex(rig)
        t0 = time.time()
        try:
            rig("T", 1)
            time.sleep(lead)
            yield
        finally:
            rig("T", 0)
            if not dry_run:
                (note_id(t0, time.time(), what or "CW ID", text) if kind == "id"
                 else note_tx(t0, time.time(), what or "data"))


def play(wav, device=SINK, dry_run=False, max_s=None):
    if dry_run:
        print("dry run: paplay", wav)
        return
    cmd = ["paplay", f"--device={device}", wav]
    if max_s:
        r = subprocess.run(["timeout", str(int(max_s))] + cmd).returncode
        if r:
            print(f"paplay ended with {r} (124: hit {max_s:.0f} s)", file=sys.stderr)
    else:
        subprocess.run(cmd, check=True)


def key_and_play(rig, wav, device=SINK, dry_run=False, kind="data", what="", text=ID_TEXT, tail=0.05,
                 max_s=None):
    """the bench's keying sequence: lock, t must read 0, T 1, 0.2 s, play, tail, T 0, record"""
    with keyed(rig, kind, what or os.path.basename(wav), text, dry_run):
        play(wav, device, dry_run, max_s)
        time.sleep(tail)


def wav_seconds(p):
    with wave.open(p) as w:
        return w.getnframes() / w.getframerate()


def cwid_wav():
    p = path("cwid_wav")
    if not os.path.exists(p):
        make_wavs(only_id=True)
    return p


def send_id(rig, why, device=SINK, dry_run=False):
    key_and_play(rig, cwid_wav(), device, dry_run, kind="id", what=why)


def id_due_first(now=None):
    """send's rule: the last CW ID is older than 8.5 min"""
    return (now or time.time()) - _read("last_id") > ID_FIRST_S


def keeper_due(now, last_tx, last_id):
    """why the keeper should ID now, or None"""
    if now - last_tx < SESSION_S and now - last_id >= ID_EVERY_S:
        return "9-min periodic"
    if last_tx > last_id and END_AFTER_S <= now - last_tx < END_STALE_S:
        return "end of session"
    return None


def k6_cfg_hz(log):
    """the freq= of the last 'K6 CFG' line in a harness log, or None"""
    import re
    hz = None
    for ln in open(log, errors="replace"):
        if "K6 CFG" in ln:
            m = re.search(r"freq=(\d+)", ln)
            hz = int(m.group(1)) if m else hz
    return hz


# ---- the WAVs ----

def write_wav(p, x, fs=48000):
    import numpy as np
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def stop_wav():
    p = path("stop_wav")
    if not os.path.exists(p):
        make_wavs(only_id=True)
    return p


def make_wavs(only_id=False):
    r = repo()
    if not os.path.isdir(os.path.join(r, "modem")):
        raise SystemExit(f"no blowfang checkout at {r} to make the WAVs from: set BLOWFANG_REPO")
    sys.path.insert(0, r)
    from modem import k6
    write_wav(path("cwid_wav"), k6.cw(ID_TEXT, 48000, 20, 800.0, 0.4))
    print("wrote", path("cwid_wav"), f"'{ID_TEXT}'")
    from modem import psk31
    write_wav(path("stop_wav"), psk31.modulate(STOP_TEXT, 48000, 1000.0, 0.3, lead_s=0.3, tail_s=0.1))
    print("wrote", path("stop_wav"), f"'{STOP_TEXT}' (PSK31)")
    if only_id:
        return
    # XL first (images from 2026-11-09 boot with frames=XL), then L for older images and for a
    # radio someone switched to L with key 2: the K6 resets on the first copy it reads
    import numpy as np
    parts = []
    for payload in ("XL", "L"):
        tmp = path("reset_wav") + f".{payload}.tmp.wav"
        subprocess.run([sys.executable, "-m", "modem.k6", "ctl", tmp, "--phy", "F", "--seq", "201",
                        "--action", "reset", "--payload", payload], cwd=r, check=True)
        with wave.open(tmp) as w:
            parts.append(np.frombuffer(w.readframes(w.getnframes()), "<i2") / 32767)
        os.remove(tmp)
    write_wav(path("reset_wav"), np.concatenate([parts[0], np.zeros(48000), parts[1]]))
    print("wrote", path("reset_wav"), "(XL, then L)")


# ---- the commands ----

def cmd_paths(a):
    print("BLOWFANG_WORK", work())
    for n in (
            "bin",
            "lock",
            "last_tx",
            "last_id",
            "txlog",
            "cwid_wav",
            "reset_wav",
            "stop_wav",
            "keeper",
            "cwid_log",
            "owner",
    ):
        print(f"{n:9s} {path(n)}{'' if os.path.exists(path(n)) else '  (missing)'}")


def keeper_pid():
    """the running keeper's pid, or None"""
    p = path("keeper")
    if not os.path.exists(p):
        return None
    with open(p, "a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.seek(0)
            return f.read().strip() or "?"
        fcntl.flock(f, fcntl.LOCK_UN)
    return None


def cmd_status(a):
    now = time.time()
    tx, idt = _read("last_tx"), _read("last_id")
    age = lambda t: f"{utc(t)} UTC, {(now - t) / 60:.1f} min ago" if t else "never"
    print(f"last data TX  {age(tx)}")
    print(f"last CW ID    {age(idt)}")
    print(f"ID due now    {keeper_due(now, tx, idt) or 'no'}")
    pid = keeper_pid()
    print(f"keeper        {'running, pid ' + pid if pid else 'NOT running'}")
    print(f"PTT lock      {'free' if lock_free() else 'held (someone is keying)'}")
    so = serial_owner()
    print(f"serial reader {so['who'] + ' (pid ' + str(so['pid']) + ')' if so else 'none through serial-hold'}")
    o = owner(now)
    print(f"bench owner   {owner_line(o, now) if o else 'nobody (free)'}")


def cmd_owner(a):
    o = owner()
    print(owner_line(o) if o else "nobody holds the bench")
    if o and me() and me() != o["who"]:
        print(f"(you are {me()}: your data sends are refused until then)")


def cmd_claim(a):
    o = claim(a.who, a.minutes, " ".join(a.what))
    print("bench held by", owner_line(o))


def cmd_release(a):
    print("released" if release(a.who, a.force) else "nobody held the bench")


def cmd_serial_owner(a):
    o = serial_owner()
    print(f"{o['who']} (pid {o['pid']}, since {utc(float(o['since'] or 0))} UTC): {o['cmd']}" if o
          else "nobody reads /dev/ttyUSB2 through serial-hold")


def cmd_serial_hold(a):
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    return serial_hold(a.who, cmd)


def cmd_serial_release(a):
    o = serial_owner()
    if serial_release(a.who, a.wait):
        print(f"the port is free{' (' + o['who'] + ' let go)' if o else ''}")
        return 0
    print(f"{o['who'] if o else '?'} did not let go in {a.wait:g} s", file=sys.stderr)
    return 1


def cmd_send(a):
    if not os.path.isfile(a.wav):
        raise SystemExit(f"no such file: {a.wav}")
    dur = wav_seconds(a.wav)
    if dur > a.max_s:
        raise SystemExit(f"refusing: {a.wav} is {dur:.1f} s, over --max-s {a.max_s:.0f}")
    check_owner(a.who)
    if a.who:
        os.environ["K6_BENCH_AS"] = a.who     # for keyed() below
    rig = rigctl(a.rigctld)
    with ptt_lock():
        hz = rig_hz(rig)
        if not in_band(hz):
            raise SystemExit(f"refusing: IC-7100 on {hz} Hz, outside 430.010-449.990 MHz")
        if a.k6log:
            k6hz = k6_cfg_hz(a.k6log)
            if k6hz != hz:
                raise SystemExit(f"refusing: K6 on {k6hz} (last CFG in {a.k6log}) vs IC-7100 {hz}")
        elif a.freq and hz != a.freq:
            raise SystemExit(f"refusing: IC-7100 on {hz} Hz, not {a.freq}")
        if not a.no_id and (a.force_id or id_due_first()):
            send_id(rig, "before a data send", a.device)
            time.sleep(1.0)
        t0 = time.time()
        key_and_play(rig, a.wav, a.device, tail=a.tail, max_s=a.max_s)
    print(f"sent {a.wav} ({dur:.1f} s) on {hz} Hz from {utc(t0)} UTC; t0={t0:.3f}; PTT now {rig('t')}")


def send_stop(rig, device=SINK, gap=STOP_GAP_S, dry_run=False):
    """the module stop signal (c/k6/mod/k6_stop.h): two key-ups, each a PSK31 'AG7EW STOP' burst
    (about 3.5 s keyed; the K6 takes 2-6 s), `gap` s apart (1-5 s)"""
    wav = stop_wav()
    with (contextlib.nullcontext() if dry_run else ptt_lock()):
        for i in range(2):
            if i:
                time.sleep(gap)
            key_and_play(rig, wav, device, dry_run, what=f"module stop signal {i + 1}/2")


def cmd_stop(a):
    rig = rigctl(a.rigctld)
    check_owner()
    with ptt_lock():
        hz = rig_hz(rig)
        if not in_band(hz):
            raise SystemExit(f"refusing: IC-7100 on {hz} Hz, outside 430.010-449.990 MHz")
        if a.freq and hz != a.freq:
            raise SystemExit(f"refusing: IC-7100 on {hz} Hz, not {a.freq} (the module's frequency)")
        t0 = time.time()
        send_stop(rig, a.device, a.gap)
    print(f"stop signal on {hz} Hz from {utc(t0)} UTC, {time.time() - t0:.1f} s; PTT now {rig('t')}."
          " A module hears it only while it listens (a beacon: between frames); send it again if"
          " the serial log shows no stop")


def cmd_id(a):
    rig = rigctl(a.rigctld)
    with ptt_lock():
        hz = rig_hz(rig)
        if not in_band(hz, BANDS_ID):
            raise SystemExit(f"refusing: IC-7100 on {hz} Hz, not 70 cm or 2 m")
        send_id(rig, a.why, a.device)
    print(f"CW ID '{ID_TEXT}' on {hz} Hz ({a.why}); PTT now {rig('t')}")


def cmd_idkeeper(a):
    p = path("keeper")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    me = open(p, "a+")
    try:
        fcntl.flock(me, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        me.seek(0)
        raise SystemExit(f"a keeper is already running (pid {me.read().strip()})")
    me.seek(0)
    me.truncate()
    me.write(str(os.getpid()))
    me.flush()
    rig = rigctl(a.rigctld)
    cwid_wav()
    say = lambda s: print(f"{time.strftime('%H:%M:%S', time.gmtime())} UTC {s}", flush=True)
    say(f"idkeeper: '{ID_TEXT}', state in {os.path.dirname(path('lock'))}")
    while True:
        why = keeper_due(time.time(), _read("last_tx"), _read("last_id"))
        if why:
            try:
                with ptt_lock(300.0):
                    why = keeper_due(time.time(), _read("last_tx"), _read("last_id"))   # again, now we hold it
                    hz = rig_hz(rig) if why else 0
                    if why and not in_band(hz, BANDS_ID):
                        say(f"ID due ({why}) but the IC-7100 is on {hz} Hz, not 70 cm or 2 m: not keying")
                        time.sleep(50)
                    elif why:
                        send_id(rig, why, a.device)
                        say(f"CW ID ({why}) on {hz} Hz, PTT now {rig('t')}")
            except (SystemExit, subprocess.CalledProcessError, OSError) as e:
                say(f"no ID ({why}): {e}")
                time.sleep(20)
        time.sleep(a.every)


def cmd_wavs(a):
    make_wavs()


def unit_text():
    w = work()
    return f"""[Unit]
Description=sotto K6 bench CW ID keeper (AG7EW, FCC 97.119; tools/k6_bench.py)
After=rigctld.service
Wants=rigctld.service

[Service]
Environment=BLOWFANG_WORK={w}
ExecStart=/usr/bin/python3 {os.path.join(w, 'bin', 'k6_bench.py')} idkeeper
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""


def cmd_install(a):
    b = path("bin")
    for d in (b, os.path.dirname(path("lock")), os.path.dirname(path("cwid_log"))):
        os.makedirs(d, exist_ok=True)
    for f in ("k6_bench.py", "k6-reset-trigger.sh"):
        shutil.copy2(os.path.join(HERE, f), os.path.join(b, f))
        os.chmod(os.path.join(b, f), 0o755)
        print("installed", os.path.join(b, f))
    make_wavs()
    if a.unit:
        d = os.path.expanduser("~/.config/systemd/user")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "k6-idkeeper.service"), "w").write(unit_text())
        print("wrote", os.path.join(d, "k6-idkeeper.service"),
              "\nthen: systemctl --user daemon-reload && systemctl --user enable --now k6-idkeeper")
    else:
        print("(the keeper's unit: install --unit)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rigctld", help="host:port (default rigctl's own, localhost:4532)")
    ap.add_argument("--device", default=SINK, help="the IC-7100's USB audio sink")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("paths").set_defaults(fn=cmd_paths)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    s = sub.add_parser("send", help="key and play one WAV on 70 cm")
    s.add_argument("wav")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--k6log", help="the harness log: the rig must match the K6's last CFG freq=")
    g.add_argument("--freq", type=int, help="the rig must read exactly this (Hz)")
    s.add_argument("--max-s", type=float, default=180.0, help="refuse a longer WAV; cut play here (180)")
    s.add_argument("--tail", type=float, default=0.5, help="PTT hold after the audio ends, s (0.5)")
    s.add_argument("--no-id", action="store_true", help="no CW ID first even when one is due")
    s.add_argument("--force-id", action="store_true", help="a CW ID first in any case")
    s.add_argument("--as", dest="who", help="send as this bench holder (default $K6_BENCH_AS)")
    s.set_defaults(fn=cmd_send)
    i = sub.add_parser("id", help="a CW ID now")
    i.add_argument("why", nargs="?", default="by hand")
    i.set_defaults(fn=cmd_id)
    k = sub.add_parser("idkeeper", help="the CW ID keeper (runs until stopped)")
    k.add_argument("--every", type=float, default=10.0, help="check every so many s (10)")
    k.set_defaults(fn=cmd_idkeeper)
    sub.add_parser("wavs").set_defaults(fn=cmd_wavs)
    st = sub.add_parser("stop", help="the stop signal for a running module (k6_stop.h)")
    st.add_argument("--freq", type=int, default=PAGER_HZ, help="the rig must read this (Hz, 440850000)")
    st.add_argument("--gap", type=float, default=STOP_GAP_S, help="s between the two bursts (2)")
    st.set_defaults(fn=cmd_stop)
    sub.add_parser("owner", help="who holds the bench").set_defaults(fn=cmd_owner)
    c = sub.add_parser("claim", help="hold the bench for a while")
    c.add_argument("who", help="one word, such as the thread's name; set K6_BENCH_AS to it for your sends")
    c.add_argument("minutes", type=float)
    c.add_argument("what", nargs="*", help="what for, a few words")
    c.set_defaults(fn=cmd_claim)
    r = sub.add_parser("release", help="give the bench back")
    r.add_argument("who")
    r.add_argument("--force", action="store_true", help="end anyone's claim (Jeff, or a dead session's)")
    r.set_defaults(fn=cmd_release)
    sh = sub.add_parser("serial-hold", help="run the one /dev/ttyUSB2 reader until asked to let go")
    sh.add_argument("who")
    sh.add_argument("cmd", nargs=argparse.REMAINDER, help="-- then the reader's command")
    sh.set_defaults(fn=cmd_serial_hold)
    sub.add_parser("serial-owner", help="who reads /dev/ttyUSB2").set_defaults(fn=cmd_serial_owner)
    sr = sub.add_parser("serial-release", help="ask the reader to let go of /dev/ttyUSB2")
    sr.add_argument("who", help="who asks (logged)")
    sr.add_argument("--wait", type=float, default=30.0, help="s to wait for it (30)")
    sr.set_defaults(fn=cmd_serial_release)
    n = sub.add_parser("install")
    n.add_argument("--unit", action="store_true", help="also write ~/.config/systemd/user/k6-idkeeper.service")
    n.set_defaults(fn=cmd_install)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
