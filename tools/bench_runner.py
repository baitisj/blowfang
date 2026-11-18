#!/usr/bin/env python3
"""The hamlet bench runner: one long-lived service that runs the threads' staged bench jobs under
Jeff's bench rules, so no thread needs Jeff's typed go for each flash or key-up (Jeff, 11-17).
docs/bench-runner.md has the job format and the rules.

  bench_runner.py submit JOB.json      check the job now, then queue it (exit 2 and the reasons when
                                       it breaks a rule; nothing is queued then)
  bench_runner.py check JOB.json       the same checks, no queueing
  bench_runner.py status               the runner, the running job, the queue, the last jobs
  bench_runner.py show ID              a job's result and the tail of its log
  bench_runner.py cancel ID [--by WHO] take a waiting job out, or stop the running one (SIGTERM:
                                       the keying tools unkey on it)
  bench_runner.py pause [WHY]          start no new job (the running one finishes); resume undoes it
  bench_runner.py hold-ic7100 [WHY]    Jeff uses the IC-7100 (HF, Ragtime): no IC-7100 step runs;
                                       free-ic7100 undoes it
  bench_runner.py serve                the runner itself (the blowfang-bench-runner user unit)
  bench_runner.py selftest             the rule checks against sample jobs, then this machine's
                                       setup (key, cable, rigctld, Pluto launcher, Aura)
  bench_runner.py install [--unit]     copy this file, bench_inbox.py and k6_bench.py into
                                       $BLOWFANG_WORK/bin; --unit also writes the user unit
  bench_runner.py inbox on|off         take jobs from cloud threads (the claude/bench-jobs branch,
                                       tools/bench_inbox.py) and push their results back (on)
  bench_runner.py autoupdate on|off    while idle, install main once main's own selftest passes (on)
  bench_runner.py rollback             put back the runner the last self-update replaced

A job is a JSON file: who (the thread, one word), a channel to claim, how long, a checkout, and
steps. Every step is one of a fixed set (k6.install, k6.flash, k6.update, k6.upload, pluto,
ic7100, ic7100.tune, ic7100.power0, logger, logger.stop, wait, uv380.flash, uv380.cmd,
uv380.send, uv380.m17, uv380.ysf, uv380.voice, uv380.sotto), turned into one
command of the bench's own tools. There is no step that runs a script: a script could do anything,
and the point is that the runner checks everything before it keys. The checks, in code:

  - transmit only in 430.010-449.990 MHz, inside the job's claimed channel (100 kHz guard);
    receive (pluto rx) anywhere
  - every K6 image keyed with ~/.config/sotto/k6-mod.key (or k6_install keys it with that key);
    every module signed with it (k6_upload gets --key-file from the runner, never from the job)
  - nothing on the never-flash list (acd06ba, a1a86f9, 35e982f), and each staged file's sha256
    matches the job's, checked again right before it is used
  - the K6 cable only through k6_install / k6_update / a serial-hold logger, the port found by
    its by-id name (USB numbering moves on reboot); no job argument may name a /dev path
  - Pluto gain at most -30 dB, from a checkout with the keeper fix (note_self_ided)
  - UV-380: only the bench commands (power 0, zero, listen, phy, trim, gain, emph, freq on its two
    channels), each read back; a send only at power 0 (read back first) on 441.850 or 440.850 inside
    the claim, through the checkout's own txreq.c module (the image sends ident first), or one of
    the checkout's own M17 modules (c6m17tx_bert/sms/voice.c: tx_tone's ident first), or its YSF
    modules (c6ysftx_dn/data.c: tx_tone's ident first), or one of
    its DMR voice-burst modules (c6vcheck.c and c6dvtx.c, which never key; c6dvtx_pn/live/msg.c:
    tx_tone's ident first), or its receive-only modules (c6fmtap.c, c6phyrx.c, c6m17rx.c: uv380.rx, which may
    run in the background while a Pluto send comes; the job waits for it before it ends)
  - a Pluto step may run in the background only to receive (rx)
  - IC-7100: never while Jeff holds it (hold-ic7100), RF power read back at most the job's limit
    (0 by default, 0.5 at most; the runner only ever lowers it, to 0), rig on 70 cm in the claim
  - one job at a time, under a k6_bench claim (the per-frequency claim other threads see)
  - staged files and outputs under ~/blowfang-work (or the checkout), checkouts clean
Ident is the tools' own: Pluto MCW first and last, IC-7100 sends through k6_bench (ID first when
due, the keeper), k6_upload's closing CW ID.

Standard library only, beside k6_bench.py in $BLOWFANG_WORK/bin. SPDX-License-Identifier: MIT
"""
import argparse
import contextlib
import datetime
import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import k6_bench as kb  # noqa: E402
try:                                   # the inbox (cloud threads' jobs) and self-update: optional
    import bench_inbox as bi  # noqa: E402
except ImportError:                    # an install from before it
    bi = None

NEVER_FLASH = ("acd06ba", "a1a86f9", "35e982f")
PLACEHOLDER = b"sotto-K6MOD-key!"            # modem/k6mod.py: an unkeyed image's key bytes
PLUTO_GAIN_MAX = -30.0
IC7100_POWER_CAP = 0.5                         # hamlet's own cap; 0 unless the job asks
POWER_SLACK = 0.02                             # the IC-7100 reads its lowest setting as ~0.01
LOGGER_MAX_S = 6 * 3600
WAIT_MAX_S = 3600
JOB_MAX_MIN = 240
CLAIM_RETRY_S = 30
STEP_TIMEOUT = {"k6.install": 900, "k6.flash": 600, "k6.update": 600, "k6.upload": 900,
                "pluto": 1800, "ic7100": 300, "ic7100.tune": 30, "ic7100.power0": 30,
                "logger": 30, "logger.stop": 60, "wait": WAIT_MAX_S + 30, "uv380.flash": 300,
                "uv380.cmd": 60, "uv380.send": 120, "uv380.m17": 120, "uv380.ysf": 120,
                "uv380.voice": 120, "uv380.sotto": 120, "uv380.rx": 240}
PLUTO_SUBS = ("info", "quiet", "send", "steps", "rx", "measure", "cal", "fsk24")
PLUTO_TX = ("send", "steps", "fsk24")
IC7100_SUBS = ("send", "id", "stop")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
WHO_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
# the UV-380's USB bench commands a job may send (OpMode_Pager.cpp tuneCommand, "phy", "listen"):
# nothing that writes memory or registers (the probe also takes wr, c6w, pin, ...), power only 0
UV380_CMD_RE = re.compile(r"^(tune|listen|listen [01]|power 0|zero [12]|phy (fsk|rng)|emph [01]|"
                          r"trim -?\d{1,4}|gain -?\d{1,2}|freq \d{6})$")
UV380_SEND_HZ = (441_850_000, 440_850_000)    # the image sends only on these (SEND_HZ, SEND_K6_HZ)
UV380_SEND_MODULE = ("firmware", "uv380-openrtx", "mod", "txreq.c")
UV380_M17_PLANS = ("bert", "sms", "voice")     # firmware/uv380-openrtx/mod/c6m17tx_PLAN.c
UV380_YSF_PLANS = ("dn", "data")               # firmware/uv380-openrtx/mod/c6ysftx_PLAN.c
# the radio's sotto message through the shared C6000 core (docs/uv380-multimode.md): uv380.sotto
# AIR [dry] -> the checkout's c6sotto_AIR.c or c6sotto_AIR_dry.c (dry never keys)
UV380_SOTTO_AIRS = ("m17", "ysf", "dmr")
# receive-only modules (docs/uv380-m17-rx.md): plan -> the checkout's module. None of them keys
# (no tx_tone, no 0x60 = 0x80); a step may run in the background so a Pluto send can come during it
UV380_RX_PLANS = {"tap": "c6fmtap.c", "tap1c": "c6fmtap_1c.c", "phy": "c6phyrx.c", "m17": "c6m17rx.c",
                  "m17tap": "c6m17rx_tap.c", "scan": "atscan.c", "ysf": "c6ysfrx.c",
                  "ysftap": "c6ysfrx_tap.c", "dmr": "c6dmrrx_long.c"}
UV380_RX_RUN_S = 200                           # Ctrl-C to a receive module still going after this


# DMR voice bursts (docs/uv380-c6000-modem.md, "Voice bursts (V0-V2)"): plan -> the checkout's module.
# check and dry never key (c6vcheck.c: the voice port only; c6dvtx.c: TX_DRY 1); the others key
UV380_VOICE_PLANS = {"check": "c6vcheck.c", "dry": "c6dvtx.c", "pn": "c6dvtx_pn.c",
                     "live": "c6dvtx_live.c", "msg": "c6dvtx_msg.c"}


def uv380_m17_module(plan):
    return ("firmware", "uv380-openrtx", "mod", f"c6m17tx_{plan}.c")


def uv380_ysf_module(plan):
    return ("firmware", "uv380-openrtx", "mod", f"c6ysftx_{plan}.c")


def uv380_voice_module(plan):
    return ("firmware", "uv380-openrtx", "mod", UV380_VOICE_PLANS[plan])


def uv380_sotto_module(args):
    dry = "_dry" if len(args) == 2 else ""
    return ("firmware", "uv380-openrtx", "mod", f"c6sotto_{args[0]}{dry}.c")


def uv380_rx_module(plan):
    return ("firmware", "uv380-openrtx", "mod", UV380_RX_PLANS[plan])

UV380_READBACK = {"power": "pwr", "zero": "zero", "trim": "trim", "gain": "gain", "emph": "emph",
                  "freq": "freq"}


class Refused(Exception):
    pass


STOPPING = []          # set by SIGTERM: the running step is stopped as if cancelled, then serve exits
BACKGROUND = []        # loggers started by jobs (reaped as they end)


# ---- where things live ----

def work():
    return kb.work()


def qdir(name=""):
    return os.path.join(work(), "queue", name) if name else os.path.join(work(), "queue")


def run_dir(job_id):
    return os.path.join(work(), "runs", "queue", job_id)


def key_path():
    return os.path.expanduser(os.environ.get("BLOWFANG_KEY") or "~/.config/sotto/k6-mod.key")


def aura_dir():
    return os.path.join(work(), "aura")


def hold_path():
    return os.path.join(work(), "state", "ic7100.hold")


def runner_log():
    return os.path.join(work(), "logs", "bench-runner.txt")


def default_checkout():
    return os.path.abspath(os.path.expanduser(os.environ.get("BLOWFANG_REPO") or "~/blowfang"))


def roots(checkout=None):
    """where a job's files may be: $BLOWFANG_WORK, ~/blowfang, and the job's checkout"""
    r = [work(), default_checkout()]
    if checkout:
        r.append(checkout)
    return [os.path.realpath(x) for x in r]


def under(p, rs):
    rp = os.path.realpath(os.path.expanduser(p))
    return any(rp == r or rp.startswith(r + os.sep) for r in rs)


def k6_cable():
    """the K6 cable (PL2303) by its by-id name, or $K6_CABLE"""
    if os.environ.get("K6_CABLE"):
        return os.environ["K6_CABLE"]
    hits = sorted(set(glob.glob("/dev/serial/by-id/*Prolific*") + glob.glob("/dev/serial/by-id/*PL2303*")))
    if len(hits) != 1:
        raise Refused(f"the K6 cable: {len(hits)} PL2303 ports under /dev/serial/by-id (want one;"
                      " set K6_CABLE in the unit)")
    return os.path.realpath(hits[0])


def uv380_port():
    """the UV-380 (OpenRTX's USB serial, 0483:5740) by the USB ids behind each by-id name, or
    $UV380_DEV. Never a guess: ttyACM0 on hamlet is the Pluto's console (by-id names carry the
    product and serial, not the ids, so a name glob found nothing and fell back to it)"""
    if os.environ.get("UV380_DEV"):
        return os.environ["UV380_DEV"]
    hits = []
    for p in sorted(glob.glob("/dev/serial/by-id/*")):
        dev = os.path.basename(os.path.realpath(p))
        usb = os.path.realpath(f"/sys/class/tty/{dev}/device/..")
        try:
            ids = (open(os.path.join(usb, "idVendor")).read().strip(),
                   open(os.path.join(usb, "idProduct")).read().strip())
        except OSError:
            continue
        if ids == ("0483", "5740"):
            hits.append(os.path.realpath(p))
    if len(set(hits)) != 1:
        raise Refused(f"the UV-380: {len(set(hits))} ports with USB ids 0483:5740 (want one: is it on"
                      " and on the probe image? UV380_DEV in the unit overrides)")
    return hits[0]


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def note(line):
    p = runner_log()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a") as f:
        f.write(f"{utcnow()} {line}\n")


# ---- the checks ----

def opts(args, name):
    """every value of --name V / --name=V in args"""
    out = []
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            out.append(args[i + 1])
        elif a.startswith(name + "="):
            out.append(a.split("=", 1)[1])
    return out


def as_hz(v):
    x = float(v)
    return int(round(x * 1e6)) if x < 10_000 else int(round(x))


def claim_hz(job):
    f = job.get("freq_mhz", 440.850)
    return None if f == "bench" else as_hz(f)


def check_tx_hz(job, hz, what, problems):
    if not kb.in_band(hz):
        problems.append(f"{what}: {hz} Hz is outside 430.010-449.990 MHz")
        return
    c = claim_hz(job)
    if c is not None and abs(hz - c) >= kb.GUARD_HZ:
        problems.append(f"{what}: {kb.mhz(hz)} is outside the job's claim ({kb.mhz(c)}, 100 kHz guard)")


def check_args(args, rs, what, problems):
    """no /dev paths, no keys, no rigctld elsewhere; every path-looking argument under rs"""
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        problems.append(f"{what}: args must be a list of strings")
        return False
    for a in args:
        v = a.split("=", 1)[1] if a.startswith("--") and "=" in a else a
        if "/dev/" in v or "ttyUSB" in v or "ttyACM" in v:
            problems.append(f"{what}: '{a}': no device paths in a job (the runner finds the ports)")
        elif v.startswith(("/", "~")) and not under(v, rs):
            problems.append(f"{what}: '{a}' is outside ~/blowfang-work and the checkout")
    for bad in ("--key-file", "--port", "--south-of-line-a", "--aura", "--trigger", "--manual", "--uri"):
        if any(a == bad or a.startswith(bad + "=") for a in args):
            problems.append(f"{what}: {bad} is the runner's to set, not the job's")
    for r in opts(args, "--rigctld"):
        if r not in ("localhost:4532", "127.0.0.1:4532"):
            problems.append(f"{what}: --rigctld {r}: the bench's rigctld only")
    return True


def staged_file(step, field, rs, what, problems, need_sha=True):
    p = step.get(field)
    if not isinstance(p, str) or not p:
        problems.append(f"{what}: needs {field}")
        return None
    p = os.path.realpath(os.path.expanduser(p))
    if not under(p, rs):
        problems.append(f"{what}: {field} {p} is outside ~/blowfang-work and the checkout (stage it there)")
        return None
    if not os.path.exists(p):
        problems.append(f"{what}: {field} {p} does not exist")
        return None
    if need_sha:
        want = str(step.get("sha256", "")).lower()
        if not HEX_RE.match(want):
            problems.append(f"{what}: needs sha256 (64 hex) of {field}")
        elif os.path.isfile(p) and sha256(p) != want:
            problems.append(f"{what}: {p} sha256 differs from the job's (a different file was staged)")
    return p


def never_flash(commit):
    c = (commit or "").lower()
    return any(c.startswith(n) or (len(c) >= 7 and n.startswith(c)) for n in NEVER_FLASH)


def check_commit(step, what, problems, also=()):
    c = step.get("commit")
    if not isinstance(c, str) or not re.match(r"^[0-9a-f]{7,40}$", c):
        problems.append(f"{what}: needs commit (the source commit, 7+ hex) so the never-flash list can be checked")
        return
    for x in (c,) + tuple(also):
        if x and never_flash(x.split("-")[0]):
            problems.append(f"{what}: {x} is on the never-flash list ({', '.join(NEVER_FLASH)})")


def read_key():
    """the module key's 16 bytes: the file holds 32 hex digits (modem/k6mod.py new_key/read_key)"""
    try:
        k = bytes.fromhex(open(key_path()).read().strip())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return k if len(k) == 16 else None


def check_image_keys(data, what, problems, want_keyed):
    """want_keyed: the image itself must carry our key (k6_update flashes it as it is);
    else k6_install keys it: our key, or the placeholder it writes our key over"""
    key = read_key()
    if key is None:
        problems.append(f"{what}: no module key (32 hex digits) at {key_path()}")
        return
    n_key, n_ph = data.count(key), data.count(PLACEHOLDER)
    if want_keyed:
        if n_ph or n_key != 1:
            problems.append(f"{what}: not the keyed build (key {n_key}x, placeholder {n_ph}x): key it with"
                            " modem.k6 keyimage first")
    elif not (n_key == 1 and n_ph == 0) and not (n_ph == 1 and n_key == 0):
        problems.append(f"{what}: keyed with another key or no module loader (key {n_key}x, placeholder {n_ph}x)")


def check_bundle(step, d, what, problems):
    try:
        m = json.load(open(os.path.join(d, "manifest.json")))
    except (OSError, ValueError) as e:
        problems.append(f"{what}: no readable manifest.json in {d} ({e})")
        return None
    want = str(step.get("manifest_sha256", "")).lower()
    if not HEX_RE.match(want):
        problems.append(f"{what}: needs manifest_sha256 (the bundle's manifest.json)")
    elif sha256(os.path.join(d, "manifest.json")) != want:
        problems.append(f"{what}: manifest.json sha256 differs from the job's")
    for name, h in (m.get("sha256") or {}).items():
        p = os.path.join(d, name)
        if not os.path.isfile(p) or sha256(p) != h:
            problems.append(f"{what}: {name} doesn't match the bundle's manifest")
    img = os.path.join(d, m.get("image", "aura-voice-explore.bin"))
    if os.path.isfile(img):
        check_image_keys(open(img, "rb").read(), what, problems, want_keyed=False)
    check_commit(step, what, problems, also=(m.get("build", ""),))
    b = (m.get("build") or "").split("-")[0]
    c = step.get("commit") or ""
    if b and c and not (b.startswith(c) or c.startswith(b)):
        problems.append(f"{what}: the bundle was built from {b}, the job says {c}")
    return m


def check_step(job, i, step, rs, problems):
    what = f"step {i + 1}"
    if not isinstance(step, dict) or step.get("do") not in STEP_TIMEOUT:
        problems.append(f"{what}: 'do' must be one of {', '.join(STEP_TIMEOUT)}")
        return
    do = step["do"]
    what = f"step {i + 1} ({do})"
    t = step.get("timeout", STEP_TIMEOUT[do])
    if not isinstance(t, (int, float)) or not 0 < t <= max(STEP_TIMEOUT[do], 3600) * 2:
        problems.append(f"{what}: timeout out of range")
    args = step.get("args", [])
    if not check_args(args, rs, what, problems):
        return
    if do == "k6.install":
        d = staged_file(step, "bundle", rs, what, problems, need_sha=False)
        if d:
            check_bundle(step, d, what, problems)
        for a in args:
            if a not in ("--verify", "--no-backup"):
                problems.append(f"{what}: only --verify and --no-backup (an update; new radios need Jeff's hands)")
    elif do in ("k6.flash", "k6.update"):
        p = staged_file(step, "image", rs, what, problems)
        if p and os.path.isfile(p):
            check_image_keys(open(p, "rb").read(), what, problems, want_keyed=do == "k6.update")
        check_commit(step, what, problems)
        if args:
            problems.append(f"{what}: takes no args")
        if do == "k6.update":
            check_tx_hz(job, kb.PAGER_HZ, what + " (the reset packet goes on 440.850)", problems)
            ic7100_limits(job, what, problems)
    elif do == "k6.upload":
        staged_file(step, "module", rs, what, problems, need_sha=bool(step.get("sha256")) or
                    not str(step.get("module", "")).endswith(".c"))
        for f in opts(args, "--freq") or [str(kb.PAGER_HZ)]:
            check_tx_hz(job, as_hz(f), what, problems)
        via = (opts(args, "--via") or ["ic7100"])[-1]
        if via == "pluto":
            for g in opts(args, "--pluto-gain"):
                if float(g) > PLUTO_GAIN_MAX:
                    problems.append(f"{what}: --pluto-gain {g} is above {PLUTO_GAIN_MAX:g} dB")
            check_pluto_checkout(job, what, problems)
        ic7100_limits(job, what, problems)
    elif do == "pluto":
        if not args or args[0] not in PLUTO_SUBS:
            problems.append(f"{what}: args[0] must be one of {', '.join(PLUTO_SUBS)}")
            return
        if "--as" in args:
            problems.append(f"{what}: --as is the runner's to set")
        if step.get("background") and args[0] != "rx":
            problems.append(f"{what}: only a Pluto rx (receive) may run in the background")
        if args[0] in PLUTO_TX:
            for f in opts(args, "--hz") or [str(kb.PAGER_HZ)]:
                check_tx_hz(job, as_hz(f), what, problems)
            for o in opts(args, "--offset-hz"):
                if abs(float(o)) > 50_000:
                    problems.append(f"{what}: --offset-hz {o} is over 50 kHz")
            gains = [float(g) for g in opts(args, "--gain")]
            for gs in opts(args, "--gains"):
                gains += [float(g) for g in gs.split(",") if g.strip()]
            for g in gains:
                if g > PLUTO_GAIN_MAX:
                    problems.append(f"{what}: gain {g:g} dB is above {PLUTO_GAIN_MAX:g} dB")
        check_pluto_checkout(job, what, problems)
    elif do == "ic7100":
        if not args or args[0] not in IC7100_SUBS:
            problems.append(f"{what}: args[0] must be one of {', '.join(IC7100_SUBS)}")
            return
        if "--no-id" in args or "--as" in args:
            problems.append(f"{what}: no --no-id or --as")
        if args[0] == "send":
            if len(args) < 2 or not under(args[1], rs) or not os.path.isfile(os.path.expanduser(args[1])):
                problems.append(f"{what}: send needs a staged WAV under ~/blowfang-work")
            fs = opts(args, "--freq")
            if not fs:
                problems.append(f"{what}: send needs --freq HZ (the rig must read it)")
            for m in opts(args, "--max-s"):
                if float(m) > 180:
                    problems.append(f"{what}: --max-s {m} is over 180")
        else:
            fs = opts(args, "--freq") or ([str(kb.PAGER_HZ)] if args[0] == "stop" else [])
        for f in fs:
            check_tx_hz(job, as_hz(f), what, problems)
        ic7100_limits(job, what, problems)
    elif do == "ic7100.tune":
        hz = step.get("hz")
        if not isinstance(hz, int):
            problems.append(f"{what}: needs hz (int)")
        else:
            check_tx_hz(job, hz, what, problems)
        if step.get("mode", "PKTFM") not in ("PKTFM", "FM"):
            problems.append(f"{what}: mode PKTFM or FM only")
        ic7100_limits(job, what, problems, keys=False)
    elif do == "ic7100.power0":
        pass
    elif do == "logger":
        s = step.get("seconds")
        if not isinstance(s, (int, float)) or not 0 < s <= LOGGER_MAX_S:
            problems.append(f"{what}: seconds 1..{LOGGER_MAX_S}")
        f = step.get("file", "k6.log")
        if not isinstance(f, str) or "/" in f or f.startswith("."):
            problems.append(f"{what}: file is a plain name in the job's run folder")
    elif do == "wait":
        if "pattern" in step:
            f = step.get("file", "k6.log")
            if not isinstance(f, str) or "/" in f:
                problems.append(f"{what}: file is a plain name in the job's run folder")
            try:
                re.compile(step["pattern"])
            except (re.error, TypeError):
                problems.append(f"{what}: pattern is not a regex")
        s = step.get("seconds", 0)
        if not isinstance(s, (int, float)) or not 0 <= s <= WAIT_MAX_S:
            problems.append(f"{what}: seconds 0..{WAIT_MAX_S}")
    elif do == "uv380.cmd":
        if not args:
            problems.append(f"{what}: args: the commands, such as [\"listen 0\", \"power 0\", \"zero 2\"]")
        for c in args:
            check_uv380_cmd(job, c, what, problems)
    elif do == "uv380.send":
        if args:
            problems.append(f"{what}: takes no args (the runner sends the checkout's txreq.c)")
        if not os.path.isfile(os.path.join(job_sources(job), *UV380_SEND_MODULE)):
            problems.append(f"{what}: the checkout has no {'/'.join(UV380_SEND_MODULE)}")
    elif do == "uv380.m17":
        if len(args) != 1 or args[0] not in UV380_M17_PLANS:
            problems.append(f"{what}: args: one plan, {' or '.join(UV380_M17_PLANS)} (the checkout's"
                            " c6m17tx_PLAN.c)")
        elif not os.path.isfile(os.path.join(job_sources(job), *uv380_m17_module(args[0]))):
            problems.append(f"{what}: the checkout has no {'/'.join(uv380_m17_module(args[0]))}")
    elif do == "uv380.ysf":
        if len(args) != 1 or not isinstance(args[0], str) or args[0] not in UV380_YSF_PLANS:
            problems.append(f"{what}: args: one plan, {' or '.join(UV380_YSF_PLANS)} (the checkout's"
                            " c6ysftx_PLAN.c)")
        elif not os.path.isfile(os.path.join(job_sources(job), *uv380_ysf_module(args[0]))):
            problems.append(f"{what}: the checkout has no {'/'.join(uv380_ysf_module(args[0]))}")
    elif do == "uv380.voice":
        if len(args) != 1 or not isinstance(args[0], str) or args[0] not in UV380_VOICE_PLANS:
            problems.append(f"{what}: args: one plan, {', '.join(UV380_VOICE_PLANS)} (the checkout's"
                            " c6vcheck.c / c6dvtx*.c)")
        elif not os.path.isfile(os.path.join(job_sources(job), *uv380_voice_module(args[0]))):
            problems.append(f"{what}: the checkout has no {'/'.join(uv380_voice_module(args[0]))}")
    elif do == "uv380.sotto":
        if not (1 <= len(args) <= 2 and args[0] in UV380_SOTTO_AIRS and args[1:] in ([], ["dry"])):
            problems.append(f"{what}: args: one of {', '.join(UV380_SOTTO_AIRS)}, then \"dry\" or nothing"
                            " (the checkout's c6sotto_AIR[_dry].c)")
        elif not os.path.isfile(os.path.join(job_sources(job), *uv380_sotto_module(args))):
            problems.append(f"{what}: the checkout has no {'/'.join(uv380_sotto_module(args))}")
    elif do == "uv380.rx":
        if len(args) != 1 or not isinstance(args[0], str) or args[0] not in UV380_RX_PLANS:
            problems.append(f"{what}: args: one plan, {', '.join(UV380_RX_PLANS)} (the checkout's"
                            " receive-only c6fmtap.c, c6fmtap_1c.c, c6phyrx.c, ...)")
        elif not os.path.isfile(os.path.join(job_sources(job), *uv380_rx_module(args[0]))):
            problems.append(f"{what}: the checkout has no {'/'.join(uv380_rx_module(args[0]))}")
    elif do == "uv380.flash":
        staged_file(step, "image", rs, what, problems)
        staged_file(step, "radio_tool", rs, what, problems, need_sha=False)
        check_commit(step, what, problems)
        if args:
            problems.append(f"{what}: takes no args")


def check_uv380_cmd(job, c, what, problems):
    if not UV380_CMD_RE.match(c):
        problems.append(f"{what}: '{c}' is not a UV-380 bench command (tune, listen [0|1], power 0,"
                        " zero 1|2, phy fsk|rng, emph 0|1, trim N, gain N, freq 441850|440850)")
        return
    k, _, v = c.partition(" ")
    if k == "trim" and abs(int(v)) > 8000:
        problems.append(f"{what}: '{c}': trim -8000..8000 Hz")
    elif k == "gain" and not -12 <= int(v) <= 15:
        problems.append(f"{what}: '{c}': gain -12..15 dB")
    elif k == "freq":
        if int(v) * 1000 not in UV380_SEND_HZ:
            problems.append(f"{what}: '{c}': the UV-380's channels are 441850 and 440850 kHz")
        else:
            check_tx_hz(job, int(v) * 1000, what, problems)


def ic7100_limits(job, what, problems, keys=True):
    p = job.get("ic7100_power_max", 0.0)
    if not isinstance(p, (int, float)) or not 0 <= p <= IC7100_POWER_CAP:
        problems.append(f"{what}: ic7100_power_max {p} is outside 0..{IC7100_POWER_CAP}")


def check_pluto_checkout(job, what, problems):
    co = job_checkout(job)
    kbp = os.path.join(co, "tools", "k6_bench.py")
    try:
        ok = "def note_self_ided" in open(kbp).read()
    except OSError:
        ok = False
    if not ok:
        problems.append(f"{what}: the checkout {co} predates the keeper fix (3a72067): Pluto sends would"
                        " make the keeper key the IC-7100")


def job_checkout(job):
    return os.path.realpath(os.path.expanduser(job.get("checkout") or default_checkout()))


def job_sources(job):
    """where the UV-380 module steps take their module source from: "sources" (a worktree, such as
    an inbox job's sources_commit) or the checkout. The tools always run from the checkout"""
    return os.path.realpath(os.path.expanduser(job["sources"])) if job.get("sources") else job_checkout(job)


def check_job(job):
    """the problems with a job (empty: it may run)"""
    problems = []
    if not isinstance(job, dict):
        return ["the job is not a JSON object"]
    if not ID_RE.match(str(job.get("id", ""))):
        problems.append("id: lower case letters, digits, . _ -, up to 64")
    if not WHO_RE.match(str(job.get("who", ""))):
        problems.append("who: one short word (the thread), the bench claim's name")
    f = job.get("freq_mhz", 440.850)
    if f != "bench":
        try:
            if not kb.in_band(as_hz(f)):
                problems.append(f"freq_mhz {f}: the claim must be a 70 cm channel (430.010-449.990) or \"bench\"")
        except (TypeError, ValueError):
            problems.append(f"freq_mhz {f!r}: MHz, such as 440.850, or \"bench\"")
    m = job.get("minutes")
    if not isinstance(m, (int, float)) or not 0 < m <= JOB_MAX_MIN:
        problems.append(f"minutes: 1..{JOB_MAX_MIN} (the claim, and the job's time limit)")
    w = job.get("wait_bench_min", 60)
    if not isinstance(w, (int, float)) or not 0 <= w <= 720:
        problems.append("wait_bench_min: 0..720")
    co = job_checkout(job)
    if not under(co, [os.path.realpath(os.path.join(work(), "worktrees")), os.path.realpath(default_checkout())]):
        problems.append(f"checkout {co}: a worktree under ~/blowfang-work/worktrees, or ~/blowfang")
    elif not (os.path.isdir(os.path.join(co, "tools")) and os.path.isdir(os.path.join(co, "modem"))):
        problems.append(f"checkout {co}: not a blowfang checkout (no tools/ and modem/)")
    if job.get("sources"):
        src = job_sources(job)
        if not under(src, [os.path.realpath(os.path.join(work(), "worktrees"))]):
            problems.append(f"sources {src}: a worktree under ~/blowfang-work/worktrees")
    if job.get("not_before"):
        try:
            parse_utc(job["not_before"])
        except (TypeError, ValueError):
            problems.append("not_before: UTC like 2026-11-17T10:00:00Z")
    r = job.get("repeat")
    if r is not None:
        try:
            ok = 10 <= float(r["every_min"]) <= 720 and parse_utc(r["until"]) - time.time() <= 86400
        except (KeyError, TypeError, ValueError):
            ok = False
        if not ok:
            problems.append("repeat: {\"every_min\": 10..720, \"until\": UTC within 24 h}")
    steps = job.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > 50:
        problems.append("steps: a list of 1..50 steps")
        return problems
    rs = roots(co)
    for i, s in enumerate(steps):
        check_step(job, i, s, rs, problems)
    return problems


# ---- the commands a job's steps become ----

def py():
    return sys.executable or "python3"


def bench_bin():
    return os.path.join(HERE, "k6_bench.py")


def command(job, step, rd):
    """(argv, cwd, background) for one step; None for the steps the runner does itself"""
    co, who, do = job_checkout(job), job["who"], step["do"]
    args = list(step.get("args", []))
    tool = lambda n: os.path.join(co, "tools", n)
    key = key_path()
    if do == "k6.install":
        return [py(), tool("k6_install.py"), "--port", k6_cable(), "--key-file", key, "install",
                os.path.realpath(os.path.expanduser(step["bundle"]))] + args, co, False
    if do == "k6.flash":
        return [py(), tool("k6_install.py"), "--port", k6_cable(), "--key-file", key, "flash",
                os.path.realpath(os.path.expanduser(step["image"]))], co, False
    if do == "k6.update":
        trig = f"sh {os.path.join(HERE, 'k6-reset-trigger.sh')}"
        return [py(), tool("k6_update.py"), os.path.realpath(os.path.expanduser(step["image"])),
                "--port", k6_cable(), "--aura", aura_dir(), "--trigger", trig, "--window", "0.6"], co, False
    if do == "k6.upload":
        return [py(), tool("k6_upload.py"), os.path.realpath(os.path.expanduser(step["module"])),
                "--key-file", key] + args, co, False
    if do == "pluto":
        return [py(), tool("pluto_bench.py"), "--as", who] + args, co, bool(step.get("background")) and \
            args[0] == "rx"
    if do == "ic7100":
        return [py(), bench_bin(), "--as", who] + args, co, False
    if do == "logger":
        log = os.path.join(rd, step.get("file", "k6.log"))
        return [py(), bench_bin(), "serial-hold", who, "--", "timeout", "-s", "INT", "--kill-after=10",
                str(int(step["seconds"])), py(), "-m", "modem.k6_harness", "listen", "--k6", k6_cable(),
                "--log", log], co, True
    if do == "logger.stop":
        return [py(), bench_bin(), "serial-release", who], co, False
    if do == "uv380.rx":
        return [py(), tool("uv380_i2sprobe.py"), "mod", uv380_port(),
                os.path.join(job_sources(job), *uv380_rx_module(args[0])), "--key-file", key,
                "--log", os.path.join(rd, "uv380.log"), "--run-timeout", str(UV380_RX_RUN_S)], co, \
            bool(step.get("background"))
    if do == "uv380.flash":
        return [py(), tool("uv380_dfu.py"), "flash", uv380_port(),
                os.path.realpath(os.path.expanduser(step["image"])), "--sha256", step["sha256"],
                "--radio-tool", os.path.realpath(os.path.expanduser(step["radio_tool"])),
                "--key-file", key, "--log", os.path.join(rd, "uv380.log")], co, False
    return None


IC7100_STEPS = ("ic7100", "ic7100.tune", "ic7100.power0")


def uses_ic7100(step):
    """k6_upload sets the IC-7100's mode and records its audio even when the Pluto sends"""
    return step["do"] in IC7100_STEPS or step["do"] in ("k6.update", "k6.upload")


def uses_cable(step):
    return step["do"] in ("k6.install", "k6.flash", "k6.update")


# ---- running ----

class Ctx:
    """one job's run: its log, the rig, cancel checks"""

    def __init__(self, job, rd, rig=None, cancelled=lambda: False):
        self.job, self.rd = job, rd
        self.rig = rig or kb.rigctl()
        self.cancelled = cancelled
        os.makedirs(rd, exist_ok=True)
        self.log = open(os.path.join(rd, "runner.log"), "a", buffering=1)

    def say(self, s):
        self.log.write(f"{utcnow()} {s}\n")


def rig_power(rig):
    return float(rig("l", "RFPOWER"))


def ic7100_ready(ctx, step):
    """the IC-7100 rules before a step that keys it: not held by Jeff, power at most the job's
    limit (lowered to 0 when it isn't), on 70 cm inside the claim (except ic7100.tune, which sets it)"""
    if os.path.exists(hold_path()):
        why = open(hold_path()).read().strip()
        raise Refused(f"the IC-7100 is held for Jeff ({why or 'hold-ic7100'}): no IC-7100 steps")
    if step["do"] == "ic7100.power0":
        return
    lim = float(ctx.job.get("ic7100_power_max", 0.0))
    p = rig_power(ctx.rig)
    if p > lim + POWER_SLACK:
        ctx.say(f"IC-7100 RF power {p:.2f} > {lim:.2f}: setting 0")
        ctx.rig("L", "RFPOWER", "0")
        p = rig_power(ctx.rig)
        if p > lim + POWER_SLACK:
            raise Refused(f"IC-7100 RF power reads {p:.2f} after setting 0")
    if step["do"] == "ic7100.tune":
        return
    hz = kb.rig_hz(ctx.rig)
    probs = []
    check_tx_hz(ctx.job, hz, "the IC-7100's frequency", probs)
    if probs:
        raise Refused(probs[0])


def recheck(ctx, step):
    """right before a step: the files are still the ones checked (nothing restaged since)"""
    probs = []
    check_step(ctx.job, 0, step, roots(job_checkout(ctx.job)), probs)
    if probs:
        raise Refused("; ".join(probs))


def free_cable(ctx, step):
    o = kb.serial_owner()
    if o is None:
        return
    if not (step.get("take_serial") or ctx.job.get("take_serial")):
        raise Refused(f"{o['who']} reads the K6 cable (pid {o['pid']}); set take_serial to ask it to let go")
    ctx.say(f"asking {o['who']}'s logger to let go of the cable")
    if not kb.serial_release(ctx.job["who"] + "-runner", 30.0):
        raise Refused(f"{o['who']}'s logger did not let go of the cable in 30 s")


def run_proc(ctx, argv, cwd, timeout, env):
    ctx.say("$ " + " ".join(argv))
    p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=ctx.log,
                         stderr=subprocess.STDOUT, start_new_session=True)
    end = time.monotonic() + timeout
    why = None
    while p.poll() is None:
        if ctx.cancelled():
            why = "cancelled"
        elif time.monotonic() > end:
            why = f"timeout {timeout:.0f} s"
        if why:
            ctx.say(f"stopping ({why}): SIGTERM to the step's process group")
            stop_group(p)
            break
        time.sleep(0.5)
    return p.returncode, why


def stop_group(p, grace=20.0):
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        p.wait(grace)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()


def wait_step(ctx, step):
    end = time.monotonic() + float(step.get("seconds", 0) or 0)
    pat = re.compile(step["pattern"]) if "pattern" in step else None
    if pat:
        end = time.monotonic() + float(step.get("seconds") or 600)
    f = os.path.join(ctx.rd, step.get("file", "k6.log"))
    while time.monotonic() < end:
        if ctx.cancelled():
            return 1, "cancelled"
        if pat and os.path.exists(f) and pat.search(open(f, errors="replace").read()):
            ctx.say(f"seen /{step['pattern']}/ in {f}")
            return 0, None
        time.sleep(0.5)
    return (1, f"no /{step['pattern']}/ in {f}") if pat else (0, None)


def module_tools(env):
    """a module compiler on the steps' PATH: uv380.send / uv380.m17 build their module from source at
    send time, and the service's PATH has no clang (hamlet has none on the host). $K6_ATFE, else
    the newest ATfE under BLOWFANG_WORK/toolchains, as tools/uv380_dfu.py finds it -> the dir added"""
    if shutil.which("clang", path=env.get("PATH")):
        return None
    dirs = [env["K6_ATFE"]] if env.get("K6_ATFE") else []
    dirs += sorted(glob.glob(os.path.join(work(), "toolchains", "ATfE-*", "bin")), reverse=True)
    for d in dirs:
        if os.path.exists(os.path.join(d, "clang")):
            env["PATH"] = d + os.pathsep + env.get("PATH", "")
            return d
    return None


def step_env(job):
    env = dict(os.environ)
    env["K6_BENCH_AS"] = job["who"]
    env["BLOWFANG_WORK"] = work()
    env["BLOWFANG_REPO"] = job_checkout(job)
    env.pop("K6LOG", None)
    env["PYTHONUNBUFFERED"] = "1"
    module_tools(env)
    return env


def checkout_clean(co):
    try:
        r = subprocess.run(["git", "-C", co, "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, timeout=30)
        head = subprocess.run(["git", "-C", co, "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if r.returncode:
        return False, r.stderr.strip() or "not a git checkout"
    return (not r.stdout.strip()), head if not r.stdout.strip() else "changed files: " + r.stdout.strip()[:200]


def uv380_probe(ctx, cmds, env, timeout=60.0):
    """send the UV-380 bench commands over USB (uv380_i2sprobe.py cmd); its lines, also in the log.
    tune, power and the like answer with a line of their own and no "ok", so the tool's exit code
    says nothing: the caller reads the lines back"""
    co = job_checkout(ctx.job)
    argv = [py(), os.path.join(co, "tools", "uv380_i2sprobe.py"), "cmd", uv380_port()] + list(cmds) + [
        "--timeout", "2", "--log", os.path.join(ctx.rd, "uv380.log")]
    ctx.say("$ " + " ".join(argv))
    r = subprocess.run(argv, cwd=co, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=timeout)
    out = (r.stdout or "") + (r.stderr or "")
    ctx.log.write(out)
    return out


def uv380_state(out):
    """the last tune line's fields and the last listen line's on= (str values), from probe output"""
    st = {}
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.startswith("tune "):
            st.update(dict(kv.split("=", 1) for kv in ln.split()[1:] if "=" in kv))
        m = re.match(r"listen on=([01])", ln)
        if m:
            st["listen"] = m.group(1)
    return st


def uv380_cmd_step(ctx, step, env):
    """the job's commands, then tune and listen read back: each setting must read what was set"""
    cmds = list(step["args"])
    out = uv380_probe(ctx, cmds + ["tune", "listen"], env)
    st = uv380_state(out)
    if "pwr" not in st:
        return 1, "no tune line came back (is the UV-380 on USB, on the probe image?)"
    for c in cmds:
        k, _, v = c.partition(" ")
        f = "listen" if k == "listen" and v else UV380_READBACK.get(k)
        if f and st.get(f) != v:
            return 1, f"'{c}' reads back {f}={st.get(f)}"
    ctx.say("UV-380 " + " ".join(f"{k}={v}" for k, v in st.items()))
    return 0, None


def uv380_send_step(ctx, step, env, timeout, module=UV380_SEND_MODULE, run_timeout=30):
    """one send of the radio's stored message, as PTT sends it (ident first): only at power 0 on one
    of its two channels inside the claim, checked on the radio's own read-back right before.
    uv380.m17, uv380.ysf and uv380.voice are the same with one of the checkout's M17, YSF or DMR
    voice-burst modules
    (tx_tone: the ident first); the same read-back even for the voice plans that never key"""
    st = uv380_state(uv380_probe(ctx, ["tune", "listen"], env))
    if "freq" not in st:
        raise Refused("the UV-380 reads no freq= (an image before the 441.850 move, or not on USB)")
    if st.get("pwr") != "0":
        raise Refused(f"the UV-380 reads pwr={st.get('pwr')}: sends only at power 0 (uv380.cmd \"power 0\")")
    hz = int(st["freq"]) * 1000
    probs = [] if hz in UV380_SEND_HZ else [f"the UV-380 is on {hz} Hz, not one of its two channels"]
    check_tx_hz(ctx.job, hz, "the UV-380's frequency", probs)
    if probs:
        raise Refused(probs[0])
    if st.get("listen") == "1":
        ctx.say("note: the UV-380's listener is on; it refuses a send while it hears a carrier")
    co = job_checkout(ctx.job)
    argv = [py(), os.path.join(co, "tools", "uv380_i2sprobe.py"), "mod", uv380_port(),
            os.path.join(job_sources(ctx.job), *module), "--key-file", key_path(),
            "--log", os.path.join(ctx.rd, "uv380.log"), "--run-timeout", str(run_timeout)]
    return run_proc(ctx, argv, co, timeout, env)


def run_job(job, rd, rig=None, cancelled=lambda: False, now=time.time):
    """run one job's steps under its claim (the caller holds it); the result as a dict"""
    ctx = Ctx(job, rd, rig, cancelled)
    res = {"id": job["id"], "who": job["who"], "started": utcnow(), "steps": [], "status": "done"}
    ok, head = checkout_clean(job_checkout(job))
    res["checkout"], res["head"] = job_checkout(job), head
    if not ok:
        res.update(status="refused", reason=f"checkout not clean: {head}")
        ctx.say(res["reason"])
        return res
    if job.get("sources"):
        ok, shead = checkout_clean(job_sources(job))
        res["sources"], res["sources_head"] = job_sources(job), shead
        if not ok:
            res.update(status="refused", reason=f"sources not clean: {shead}")
            ctx.say(res["reason"])
            return res
    ctx.say(f"job {job['id']} by {job['who']}, checkout {res['checkout']} at {head}"
            + (f", module sources {res['sources']} at {res['sources_head']}" if job.get("sources") else ""))
    env = step_env(job)
    deadline = now() + 60 * float(job["minutes"])
    mine = []                           # this job's background UV-380 receive modules
    for i, step in enumerate(job["steps"]):
        sr = {"n": i + 1, "do": step["do"], "started": utcnow()}
        res["steps"].append(sr)
        try:
            if cancelled():
                raise Refused("cancelled")
            left = deadline - now()
            if left <= 0:
                raise Refused(f"the job's {job['minutes']} min are used up")
            recheck(ctx, step)
            if uses_ic7100(step):
                ic7100_ready(ctx, step)
            if uses_cable(step):
                free_cable(ctx, step)
            timeout = min(float(step.get("timeout", STEP_TIMEOUT[step["do"]])), left)
            if step["do"] == "wait":
                rc, why = wait_step(ctx, step)
            elif step["do"] == "ic7100.tune":
                ctx.rig("F", step["hz"])
                ctx.rig("M", step.get("mode", "PKTFM"), 12000)
                ctx.rig("L", "RFPOWER", "0")
                got = kb.rig_hz(ctx.rig)
                ctx.say(f"IC-7100 F {got} M {ctx.rig('m')} RFPOWER {rig_power(ctx.rig):.2f}")
                rc, why = (0, None) if got == step["hz"] else (1, f"the rig reads {got}, not {step['hz']}")
            elif step["do"] == "uv380.cmd":
                rc, why = uv380_cmd_step(ctx, step, env)
            elif step["do"] == "uv380.send":
                rc, why = uv380_send_step(ctx, step, env, timeout)
            elif step["do"] == "uv380.m17":
                rc, why = uv380_send_step(ctx, step, env, timeout, uv380_m17_module(step["args"][0]), 45)
            elif step["do"] == "uv380.ysf":
                rc, why = uv380_send_step(ctx, step, env, timeout, uv380_ysf_module(step["args"][0]), 45)
            elif step["do"] == "uv380.voice":
                rc, why = uv380_send_step(ctx, step, env, timeout, uv380_voice_module(step["args"][0]), 45)
            elif step["do"] == "uv380.sotto":
                rc, why = uv380_send_step(ctx, step, env, timeout, uv380_sotto_module(step["args"]), 45)
            elif step["do"] == "ic7100.power0":
                ctx.rig("L", "RFPOWER", "0")
                p = rig_power(ctx.rig)
                ctx.say(f"IC-7100 RFPOWER {p:.2f}")
                rc, why = (0, None) if p <= POWER_SLACK else (1, f"RFPOWER reads {p:.2f}")
            else:
                argv, cwd, bg = command(job, step, rd)
                if bg:
                    ctx.say("$ (background) " + " ".join(argv))
                    out = "logger.out" if step["do"] == "logger" else step["do"] + ".out"
                    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                            stdout=open(os.path.join(rd, out), "a"),
                                            stderr=subprocess.STDOUT, start_new_session=True)
                    BACKGROUND.append(proc)
                    if step["do"] == "uv380.rx":
                        mine.append(proc)
                    time.sleep(2.0)
                    if step["do"] == "logger":     # the logger holds the K6 cable for the job
                        o = kb.serial_owner()
                        rc, why = (0, None) if o and o.get("who") == job["who"] else (1, "the logger did not start")
                    else:                           # a Pluto rx or receive module: still going (or done)
                        r = proc.poll()
                        rc, why = (0, None) if r in (None, 0) else (1, f"the background {step['do']} exited {r}")
                else:
                    rc, why = run_proc(ctx, argv, cwd, timeout, env)
        except Refused as e:
            rc, why = 1, f"refused: {e}"
        except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
                SystemExit) as e:
            rc, why = 1, f"{type(e).__name__}: {e}"
        if why and not rc:
            rc = 1                            # stopped (cancel, timeout) even if the tool exited 0
        sr.update(ended=utcnow(), rc=rc, **({"why": why} if why else {}))
        ctx.say(f"step {i + 1} ({step['do']}) rc={rc}" + (f" ({why})" if why else ""))
        if rc and not step.get("continue_on_error"):
            res.update(status="cancelled" if why == "cancelled" or cancelled() else "failed",
                       reason=f"step {i + 1} ({step['do']}): {why or f'exit {rc}'}")
            break
    for p in mine:                      # the UV-380's USB port stays the job's until they end
        end = time.monotonic() + STEP_TIMEOUT["uv380.rx"]
        while p.poll() is None and time.monotonic() < end and not cancelled():
            time.sleep(0.5)
        if p.poll() is None:
            ctx.say("stopping a background uv380.rx still running at the job's end")
            stop_group(p)
    res["ended"] = utcnow()
    ctx.say(f"job {job['id']} {res['status']}" + (f": {res.get('reason')}" if res.get("reason") else ""))
    return res


# ---- the queue ----

def ensure_dirs():
    for d in ("incoming", "done", "failed", "refused", "cancelled"):
        os.makedirs(qdir(d), exist_ok=True)


def put_json(p, v):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(v, f, indent=1)
    os.replace(tmp, p)


def get_json(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None


def known_ids():
    ids = set()
    for d in ("incoming", "done", "failed", "refused", "cancelled"):
        ids |= {f[:-5] for f in os.listdir(qdir(d)) if f.endswith(".json")}
    r = get_json(qdir("running.json"))
    if r:
        ids.add(r.get("id"))
    return ids


def submit(job):
    ensure_dirs()
    probs = check_job(job)
    if not probs and job["id"] in known_ids():
        probs.append(f"id {job['id']} is in the queue already (pick a new id)")
    if probs:
        return probs
    job = dict(job, submitted=utcnow(), submitted_at=round(time.time(), 3))
    put_json(os.path.join(qdir("incoming"), job["id"] + ".json"), job)
    note(f"queued {job['id']} by {job['who']}")
    return []


def waiting():
    out = []
    for f in os.listdir(qdir("incoming")):
        if f.endswith(".json"):
            j = get_json(os.path.join(qdir("incoming"), f))
            if j:
                out.append(j)
    return sorted(out, key=lambda j: (j.get("submitted_at", 0), j.get("id", "")))


def finish(job, res, where):
    rd = run_dir(job["id"])
    put_json(os.path.join(rd, "result.json"), res)
    put_json(os.path.join(qdir(where), job["id"] + ".json"), dict(job, result=res))
    with contextlib.suppress(FileNotFoundError):
        os.remove(os.path.join(qdir("incoming"), job["id"] + ".json"))
    note(f"{where} {job['id']} ({job['who']})" + (f": {res.get('reason')}" if res.get("reason") else ""))
    r = job.get("repeat")
    if r and where in ("done", "failed"):
        nb = time.time() + 60 * float(r["every_min"])
        if nb < parse_utc(r["until"]):
            base = re.sub(r"-r\d+$", "", job["id"])
            n = int((re.search(r"-r(\d+)$", job["id"]) or [0, 0])[1]) + 1
            nj = {k: v for k, v in job.items() if k not in ("result", "submitted", "submitted_at")}
            nid = f"{base}-r{n}"[:64]
            nj = retarget(nj, run_dir(job["id"]), run_dir(nid))
            nj.update(id=nid, not_before=datetime.datetime.fromtimestamp(
                nb, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            probs = submit(nj)
            if probs:
                note(f"repeat of {job['id']} not queued: {'; '.join(probs)}")


def retarget(job, old_rd, new_rd):
    """a repeat's copy of a job: paths inside the last run's folder (an inbox job's run:NAME, or a
    local job naming its own run folder) moved to the new run's folder, so each run keeps its own
    outputs (and an inbox job's come back in its results)"""
    job = json.loads(json.dumps(job))
    olds = {os.path.abspath(old_rd), os.path.realpath(old_rd)}   # as written, and with links resolved

    def fix(v):
        if not isinstance(v, str):
            return v
        pre, x = "", v
        if v.startswith("--") and "=" in v:
            pre, x = v.split("=", 1)
            pre += "="
        for o in olds:
            if x == o or x.startswith(o + os.sep):
                return pre + new_rd + x[len(o):]
        return v

    for s in job.get("steps") or []:
        for f in ("bundle", "image", "module", "radio_tool"):
            if f in s:
                s[f] = fix(s[f])
        if isinstance(s.get("args"), list):
            s["args"] = [fix(a) for a in s["args"]]
    return job


def cancel_path(job_id):
    return os.path.join(qdir(), f"cancel-{job_id}")


def serve_once(rig=None, now=time.time, claim=kb.claim, release=kb.release, on_start=None):
    """start the next job that may run now; True when one ran. on_start(job) once it has its claim"""
    ensure_dirs()
    if os.path.exists(qdir("PAUSE")) or STOPPING:
        return False
    for job in waiting():
        jid = job["id"]
        if os.path.exists(cancel_path(jid)):
            os.remove(cancel_path(jid))
            finish(job, {"id": jid, "status": "cancelled", "reason": "cancelled before it ran"}, "cancelled")
            continue
        if job.get("not_before") and parse_utc(job["not_before"]) > now():
            continue
        probs = check_job(job)
        if probs:
            finish(job, {"id": jid, "status": "refused", "reason": "; ".join(probs)}, "refused")
            continue
        try:
            claim(job["who"], float(job["minutes"]) + 2, f"bench runner job {jid}", hz=claim_hz(job))
        except SystemExit as e:
            waited = now() - float(job.get("submitted_at") or now())
            if job.get("not_before"):
                waited = now() - parse_utc(job["not_before"])
            if waited > 60 * float(job.get("wait_bench_min", 60)):
                finish(job, {"id": jid, "status": "failed", "reason": f"the bench stayed busy: {e}"}, "failed")
            continue                          # busy: try the next job (another channel may be free)
        put_json(qdir("running.json"), {"id": jid, "who": job["who"], "pid": os.getpid(), "since": utcnow(),
                                         "job": job})
        os.remove(os.path.join(qdir("incoming"), jid + ".json"))
        note(f"running {jid} ({job['who']})")
        if on_start:
            try:
                on_start(job)
            except Exception as e:            # the inbox's push must never stop the job
                note(f"on_start for {jid}: {type(e).__name__}: {e}")
        try:
            res = run_job(job, run_dir(jid), rig, now=now,
                          cancelled=lambda: bool(STOPPING) or os.path.exists(cancel_path(jid)))
        except Exception as e:                # the runner must outlive any one job
            res = {"id": jid, "status": "failed", "reason": f"runner error {type(e).__name__}: {e}"}
        finally:
            with contextlib.suppress(SystemExit):
                release(job["who"])
            with contextlib.suppress(FileNotFoundError):
                os.remove(qdir("running.json"))
        with contextlib.suppress(FileNotFoundError):
            os.remove(cancel_path(jid))
        finish(job, res, {"done": "done", "cancelled": "cancelled", "refused": "refused"}.get(res["status"], "failed"))
        return True
    return False


def cmd_serve(a):
    import fcntl
    ensure_dirs()
    lk = open(os.path.join(qdir(), "runner.lock"), "a")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("a bench runner is already running")
    stale = get_json(qdir("running.json"))
    if stale:                                 # a runner that died mid-job: say so, don't rerun it
        j = dict(stale.get("job") or {"id": stale["id"], "who": stale.get("who", "?")}, repeat=None)
        finish(j, {"id": stale["id"], "status": "failed", "reason": "the runner stopped during this job"}, "failed")
        os.remove(qdir("running.json"))
    note(f"runner started, pid {os.getpid()}")
    print(f"bench runner: queue {qdir()}, log {runner_log()}", flush=True)
    signal.signal(signal.SIGTERM, lambda *x: STOPPING.append(1))
    inbox = bi.Inbox() if bi else None
    if inbox:
        bi.bind(sys.modules.get(__name__))
        note("inbox " + ("on" if inbox.enabled() else "off (state/inbox.off)") + ", self-update "
             + ("on" if inbox.autoupdate() else "off (state/autoupdate.off)"))
    while not STOPPING:
        BACKGROUND[:] = [p for p in BACKGROUND if p.poll() is None]
        ran = serve_once(on_start=inbox.started if inbox else None)
        if inbox:
            inbox.finished()
            act = None if STOPPING else inbox.tick(idle=not ran)
            if act == "exec":                 # a newer runner is installed: become it (same pid)
                note("runner re-executing the newly installed runner")
                lk.close()
                os.execv(sys.executable, [sys.executable, os.path.join(work(), "bin", "bench_runner.py")]
                         + sys.argv[1:])
            if act == "restart":              # the unit changed: systemd restarts us on it
                note("runner exiting so systemd restarts it on the new unit")
                return 75
        if not ran:
            time.sleep(a.every)
    note("runner stopped (SIGTERM)")
    return 0


def load_job(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError) as e:
        raise SystemExit(f"{p}: {e}")


def cmd_check(a):
    probs = check_job(load_job(a.job))
    for p in probs:
        print("REFUSED:", p)
    if not probs:
        print("ok: the job keeps the bench rules")
    return 2 if probs else 0


def cmd_submit(a):
    job = load_job(a.job)
    probs = submit(job)
    for p in probs:
        print("REFUSED:", p)
    if probs:
        return 2
    print(f"queued {job['id']}: {len(waiting())} waiting; results in {run_dir(job['id'])}"
          f" (bench_runner.py show {job['id']})")
    return 0


def runner_pid():
    import fcntl
    p = os.path.join(qdir(), "runner.lock")
    if not os.path.exists(p):
        return False
    with open(p, "a") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lk, fcntl.LOCK_UN)
    return False


def cmd_status(a):
    ensure_dirs()
    print(f"runner     {'running' if runner_pid() else 'NOT running'}"
          + ("; PAUSED: " + open(qdir("PAUSE")).read().strip() if os.path.exists(qdir("PAUSE")) else ""))
    if os.path.exists(hold_path()):
        print("IC-7100    held for Jeff:", open(hold_path()).read().strip())
    r = get_json(qdir("running.json"))
    print("running   ", f"{r['id']} ({r['who']}) since {r['since']}" if r else "nothing")
    for j in waiting():
        print("waiting   ", j["id"], f"({j['who']})", ("not before " + j["not_before"]) if j.get("not_before") else "")
    done = []
    for d in ("done", "failed", "refused", "cancelled"):
        for f in os.listdir(qdir(d)):
            if f.endswith(".json"):
                done.append((os.path.getmtime(os.path.join(qdir(d), f)), d, f[:-5]))
    for t, d, jid in sorted(done)[-a.last:]:
        j = get_json(os.path.join(qdir(d), jid + ".json")) or {}
        why = (j.get("result") or {}).get("reason", "")
        print(f"{d:10s} {jid} {time.strftime('%H:%M:%SZ', time.gmtime(t))}" + (f": {why}" if why else ""))


def cmd_show(a):
    rd = run_dir(a.id)
    res = get_json(os.path.join(rd, "result.json"))
    if res is None:
        for d in ("incoming", "refused", "cancelled"):
            j = get_json(os.path.join(qdir(d), a.id + ".json"))
            if j:
                res = j.get("result") or {"status": "waiting" if d == "incoming" else d}
    print(json.dumps(res, indent=1) if res else f"no job {a.id}")
    lp = os.path.join(rd, "runner.log")
    if os.path.exists(lp):
        print(f"--- {lp} (last {a.lines} lines)")
        print("".join(open(lp, errors="replace").readlines()[-a.lines:]), end="")


def cmd_cancel(a):
    ensure_dirs()
    open(cancel_path(a.id), "w").write(a.by)
    note(f"cancel {a.id} asked by {a.by}")
    print(f"asked: {a.id} stops at once if running (SIGTERM), or never starts")


def cmd_pause(a):
    ensure_dirs()
    open(qdir("PAUSE"), "w").write(" ".join(a.why) or "paused")
    note("paused: " + (" ".join(a.why) or "-"))
    print("paused: no new job starts (the running one finishes; cancel stops it)")


def cmd_resume(a):
    with contextlib.suppress(FileNotFoundError):
        os.remove(qdir("PAUSE"))
    note("resumed")
    print("resumed")


def cmd_hold(a):
    os.makedirs(os.path.dirname(hold_path()), exist_ok=True)
    open(hold_path(), "w").write(" ".join(a.why) or f"held {utcnow()}")
    note("IC-7100 held: " + (" ".join(a.why) or "-"))
    print("IC-7100 held: no IC-7100 step runs until free-ic7100")


def cmd_switch(a):
    """inbox on|off, autoupdate on|off: a state file the runner reads each loop"""
    f = os.path.join(work(), "state", f"{a.cmd}.off")
    os.makedirs(os.path.dirname(f), exist_ok=True)
    if a.onoff == "off":
        open(f, "w").write(utcnow())
    else:
        with contextlib.suppress(FileNotFoundError):
            os.remove(f)
    note(f"{a.cmd} {a.onoff}")
    print(f"{a.cmd} {a.onoff}" + (" (the runner picks it up within seconds)" if runner_pid() else ""))


def cmd_rollback(a):
    """put back the runner files the last self-update replaced, and stop self-update"""
    b = kb.path("bin")
    prev = os.path.join(b, "previous")
    if not os.path.isdir(prev) or not os.listdir(prev):
        raise SystemExit("no previous runner in " + prev)
    for f in os.listdir(prev):
        shutil.copy2(os.path.join(prev, f), os.path.join(b, f))
        print("restored", os.path.join(b, f))
    os.makedirs(os.path.join(work(), "state"), exist_ok=True)
    open(os.path.join(work(), "state", "autoupdate.off"), "w").write(f"rollback {utcnow()}")
    note("rollback: previous runner files restored, self-update off")
    print("self-update is off (autoupdate on turns it back on); now: systemctl --user restart blowfang-bench-runner")


def cmd_free(a):
    with contextlib.suppress(FileNotFoundError):
        os.remove(hold_path())
    note("IC-7100 freed")
    print("IC-7100 free for the runner (it sets power 0 and checks the frequency before each key-up)")


# ---- install and self-test ----

def unit_text():
    w = work()
    return f"""[Unit]
Description=blowfang bench runner: the threads' staged bench jobs under Jeff's bench rules (tools/bench_runner.py)
After=rigctld.service k6-idkeeper.service

[Service]
Environment=BLOWFANG_WORK={w}
ExecStart=/usr/bin/python3 {os.path.join(w, 'bin', 'bench_runner.py')} serve
Restart=on-failure
RestartSec=30
KillMode=mixed
TimeoutStopSec=60

[Install]
WantedBy=default.target
"""


def cmd_install(a):
    b = kb.path("bin")
    os.makedirs(b, exist_ok=True)
    for f in ("bench_runner.py", "bench_inbox.py", "k6_bench.py", "k6-reset-trigger.sh"):
        src = os.path.join(HERE, f)
        if not os.path.exists(src):
            continue
        if os.path.realpath(src) != os.path.realpath(os.path.join(b, f)):
            shutil.copy2(src, os.path.join(b, f))
        os.chmod(os.path.join(b, f), 0o755)
        print("installed", os.path.join(b, f))
    head = subprocess.run(["git", "-C", HERE, "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.returncode == 0 and os.path.realpath(HERE) != os.path.realpath(b):
        put_json(os.path.join(b, ".installed.json"), {"commit": head.stdout.strip(), "at": utcnow(),
                                                       "why": f"install from {os.path.dirname(HERE)}"})
        print("from commit", head.stdout.strip())
    ensure_dirs()
    if a.unit:
        d = os.path.expanduser("~/.config/systemd/user")
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "blowfang-bench-runner.service"), "w").write(unit_text())
        print("wrote", os.path.join(d, "blowfang-bench-runner.service"),
              "\nthen: systemctl --user daemon-reload && systemctl --user enable --now blowfang-bench-runner")


def sample_jobs(tmp, co, key):
    """(name, job, should_pass) for the self-test, with files staged under tmp"""
    img_keyed = os.path.join(tmp, "keyed.bin")
    open(img_keyed, "wb").write(b"\0" * 100 + key + b"\0" * 100)
    img_plain = os.path.join(tmp, "plain.bin")
    open(img_plain, "wb").write(b"\0" * 100 + PLACEHOLDER + b"\0" * 100)
    img_other = os.path.join(tmp, "other.bin")
    open(img_other, "wb").write(b"\0" * 100 + bytes(16) + b"\1" * 100)
    wav = os.path.join(tmp, "x.wav")
    open(wav, "wb").write(b"RIFF")
    mod = os.path.join(tmp, "mod.bin")
    open(mod, "wb").write(b"\0" * 64)
    h = sha256
    base = dict(who="selftest", freq_mhz=440.850, minutes=10, checkout=co)
    J = lambda jid, *steps, **kw: dict(base, id=jid, steps=list(steps), **kw)
    return [
        ("OTA flash, keyed image", J("ok-update", {"do": "k6.update", "image": img_keyed, "sha256": h(img_keyed), "commit": "8a78fb5"}), True),
        ("cable flash, unkeyed (k6_install keys it)", J("ok-flash", {"do": "k6.flash", "image": img_plain, "sha256": h(img_plain), "commit": "8a78fb5"}), True),
        ("Pluto send at -60 on 440.850", J("ok-pluto", {"do": "pluto", "args": ["send", wav, "--gain", "-60"]}), True),
        ("module over the Pluto", J("ok-upload", {"do": "k6.upload", "module": mod, "sha256": h(mod), "args": ["--via", "pluto", "--pluto-gain", "-52", "--rounds", "1"]}), True),
        ("logger then wait for a line", J("ok-logger", {"do": "logger", "seconds": 900}, {"do": "wait", "pattern": "K6 BOOT", "seconds": 60}), True),
        ("IC-7100 send on 440.850", J("ok-ic", {"do": "ic7100", "args": ["send", wav, "--freq", "440850000"]}), True),
        ("Pluto receive on NOAA 162.550", J("ok-rx", {"do": "pluto", "args": ["rx", os.path.join(tmp, "n.wav"), "--hz", "162550000"]}), True),
        ("OTA flash of an unkeyed image", J("bad-unkeyed", {"do": "k6.update", "image": img_plain, "sha256": h(img_plain), "commit": "8a78fb5"}), False),
        ("flash of an image keyed with another key", J("bad-otherkey", {"do": "k6.flash", "image": img_other, "sha256": h(img_other), "commit": "8a78fb5"}), False),
        ("never-flash commit", J("bad-never", {"do": "k6.update", "image": img_keyed, "sha256": h(img_keyed), "commit": "acd06ba"}), False),
        ("sha256 that doesn't match", J("bad-sha", {"do": "k6.update", "image": img_keyed, "sha256": "0" * 64, "commit": "8a78fb5"}), False),
        ("Pluto send on 2 m", J("bad-2m", {"do": "pluto", "args": ["send", wav, "--hz", "146520000"]}), False),
        ("Pluto send at -20 dB", J("bad-gain", {"do": "pluto", "args": ["send", wav, "--gain", "-20"]}), False),
        ("Pluto ladder reaching -25", J("bad-gains", {"do": "pluto", "args": ["steps", wav, "--gains", "-70,-50,-25"]}), False),
        ("Pluto send off the claimed channel", J("bad-chan", {"do": "pluto", "args": ["send", wav, "--hz", "441850000"]}), False),
        ("claim on 2 m", J("bad-claim", {"do": "wait", "seconds": 1}, freq_mhz=146.52), False),
        ("anything naming ttyUSB2", J("bad-tty", {"do": "pluto", "args": ["rx", "/dev/ttyUSB2"]}), False),
        ("module with the job's own key file", J("bad-key", {"do": "k6.upload", "module": mod, "sha256": h(mod), "args": ["--key-file", key_path()]}), False),
        ("a file in /tmp", J("bad-tmp", {"do": "ic7100", "args": ["send", "/tmp/x.wav", "--freq", "440850000"]}), False),
        ("IC-7100 power 1.0", J("bad-power", {"do": "ic7100", "args": ["id"]}, ic7100_power_max=1.0), False),
        ("a script step", J("bad-script", {"do": "script", "args": ["go.sh"]}), False),
        ("rxmap's Line A override", J("bad-linea", {"do": "k6.upload", "module": mod, "sha256": h(mod), "args": ["--south-of-line-a"]}), False),
        ("checkout outside the worktrees", J("bad-co", {"do": "wait", "seconds": 1}, checkout="/tmp"), False),
        ("IC-7100 send with no --freq", J("bad-nofreq", {"do": "ic7100", "args": ["send", wav]}), False),
        ("UV-380 at 0 W, one send on 441.850", J("ok-uv380", {"do": "uv380.cmd", "args": ["listen 0", "power 0", "zero 2"]}, {"do": "uv380.send"}, freq_mhz=441.850), True),
        ("UV-380 power 1000", J("bad-uvpower", {"do": "uv380.cmd", "args": ["power 1000"]}, freq_mhz=441.850), False),
        ("UV-380 memory write", J("bad-uvwr", {"do": "uv380.cmd", "args": ["wr 40020018 1"]}, freq_mhz=441.850), False),
        ("UV-380 M17 BERT at 0 W on 441.850, the Pluto listening", J("ok-m17", {"do": "pluto", "args": ["rx", os.path.join(tmp, "m.cf32"), "--hz", "441900000", "--seconds", "40"], "background": True}, {"do": "uv380.cmd", "args": ["listen 0", "power 0", "zero 2", "trim -935"]}, {"do": "uv380.m17", "args": ["bert"]}, {"do": "uv380.cmd", "args": ["trim 5745"]}, freq_mhz=441.850), True),
        ("UV-380 M17 with no plan", J("bad-m17", {"do": "uv380.m17"}, freq_mhz=441.850), False),
        ("UV-380 YSF DN at 0 W on 441.850, the Pluto listening", J("ok-ysf", {"do": "pluto", "args": ["rx", os.path.join(tmp, "y.cf32"), "--hz", "441900000", "--seconds", "40"], "background": True}, {"do": "uv380.cmd", "args": ["listen 0", "power 0", "zero 2", "trim -580"]}, {"do": "uv380.ysf", "args": ["dn"]}, {"do": "uv380.cmd", "args": ["trim 6100"]}, freq_mhz=441.850), True),
        ("UV-380 YSF with a plan it doesn't have", J("bad-ysf", {"do": "uv380.ysf", "args": ["vw"]}, freq_mhz=441.850), False),
        ("UV-380 DMR voice test pattern at 0 W on 441.850, the Pluto listening", J("ok-dvoice", {"do": "pluto", "args": ["rx", os.path.join(tmp, "v.cf32"), "--hz", "441900000", "--seconds", "40"], "background": True}, {"do": "uv380.cmd", "args": ["listen 0", "power 0", "zero 2"]}, {"do": "uv380.voice", "args": ["pn"]}, freq_mhz=441.850), True),
        ("UV-380 FM tap receive in the background while the Pluto sends on 441.850", J("ok-uvrx", {"do": "uv380.rx", "args": ["tap"], "background": True}, {"do": "wait", "seconds": 30}, {"do": "pluto", "args": ["send", wav, "--hz", "441850000", "--gain", "-50"]}, freq_mhz=441.850), True),
        ("UV-380 receive with a plan it doesn't have", J("bad-uvrx", {"do": "uv380.rx", "args": ["../c6m17tx_bert.c"]}, freq_mhz=441.850), False),
        ("UV-380 sotto as YSF through the C6000 core at 0 W on 441.850, the Pluto listening", J("ok-sotto", {"do": "pluto", "args": ["rx", os.path.join(tmp, "s.cf32"), "--hz", "441850000", "--seconds", "40"], "background": True}, {"do": "uv380.cmd", "args": ["listen 0", "power 0", "zero 2", "trim -580"]}, {"do": "uv380.sotto", "args": ["ysf"]}, {"do": "uv380.cmd", "args": ["trim 6100"]}, freq_mhz=441.850), True),
        ("UV-380 sotto with a mode it doesn't have", J("bad-sotto", {"do": "uv380.sotto", "args": ["p25"]}, freq_mhz=441.850), False),
        ("UV-380 DMR voice with a module path", J("bad-dvoice", {"do": "uv380.voice", "args": ["../txreq.c"]}, freq_mhz=441.850), False),
        ("Pluto send in the background", J("bad-bgtx", {"do": "pluto", "args": ["send", wav, "--gain", "-60"], "background": True}), False),
        ("UV-380 to 440.850 under a 441.850 claim", J("bad-uvfreq", {"do": "uv380.cmd", "args": ["freq 440850"]}, freq_mhz=441.850), False),
    ]


def inbox_samples():
    """(name, job, should_pass) for the inbox's own rules (the runner's checks come after)"""
    J = lambda jid, *steps, **kw: dict(dict(id=jid, who="selftest", freq_mhz=440.850, minutes=10), steps=list(steps), **kw)
    return [
        ("Pluto send of a pushed WAV", J("in-ok", {"do": "pluto", "args": ["send", "job:x.wav", "--gain", "-60"]}), True),
        ("UV-380 M17 from a branch's module, Pluto capture in the run folder", J("in-m17", {"do": "pluto", "args": ["rx", "run:m.cf32", "--hz", "441900000"], "background": True}, {"do": "uv380.m17", "args": ["bert"]}, freq_mhz=441.850, sources_commit="519e64f"), True),
        ("an OTA flash", J("in-flash", {"do": "k6.update", "image": "job:k.bin", "sha256": "0" * 64, "commit": "8a78fb5"}), False),
        ("a K6 module as C source", J("in-c", {"do": "k6.upload", "module": "job:m.c"}), False),
        ("a hamlet path", J("in-path", {"do": "pluto", "args": ["send", "/home/baitisj/blowfang-work/x.wav"]}), False),
        ("a name climbing out", J("in-dots", {"do": "pluto", "args": ["send", "job:../x.wav"]}), False),
        ("its own checkout", J("in-co", {"do": "wait", "seconds": 1}, checkout="/home/baitisj/blowfang"), False),
    ]


def inbox_setup(say):
    """warnings only (the network is not the rules): can this machine fetch and push the inbox?"""
    ib = bi.Inbox()
    say(True, f"inbox {'on' if ib.enabled() else 'OFF'}, self-update {'on' if ib.autoupdate() else 'OFF'};"
        f" installed commit {bi.installed_commit(bi._br()) or 'unknown'}")
    if not ib.enabled():
        return
    url = ib.url()
    try:
        r = bi.git("ls-remote", url, "refs/heads/" + bi.BRANCH, check=False, timeout=60)
        say((r.returncode == 0 and bool(r.stdout.strip())) or None,
            f"inbox branch {bi.BRANCH} at {url}" + ("" if r.returncode == 0 and r.stdout.strip() else ": not readable"))
        src = ["--git-dir", ib.gd()] if os.path.isdir(ib.gd()) else ["-C", default_checkout()]
        ref = "refs/heads/main" if os.path.isdir(ib.gd()) else "HEAD"
        r = bi.git(*src, "push", "--dry-run", "--quiet", url, f"{ref}:refs/heads/{bi.BRANCH}-authcheck",
                   check=False, timeout=60)
        say(r.returncode == 0 or None, "inbox push (dry run)" + ("" if r.returncode == 0 else
            ": " + r.stderr.decode(errors="replace").strip()[-160:] + " (results can't come back)"))
    except (OSError, subprocess.TimeoutExpired) as e:
        say(None, f"inbox: {e}")


def cmd_selftest(a):
    import tempfile
    bad = 0
    say = lambda ok, s: print(("ok    " if ok is True else "FAIL  " if ok is False else "warn  ") + s)
    key = read_key()
    say(key is not None or None, f"module key {key_path()}" + ("" if key else " missing: K6 flash/upload jobs refuse"))
    co = os.path.realpath(a.checkout or default_checkout())
    stage = os.path.join(work(), "runs", "queue", ".selftest")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    k = key or b"0123456789abcdef"
    if key is None:
        os.environ["BLOWFANG_KEY"] = os.path.join(stage, "k")
        open(os.environ["BLOWFANG_KEY"], "w").write(k.hex() + "\n")
    for name, job, want in sample_jobs(stage, co, k):
        probs = check_job(job)
        ok = (not probs) == want
        bad += not ok
        say(ok, f"{'runs' if want else 'refused'}: {name}" + ("" if ok else f"  -> got {probs or 'no problems'}"))
    shutil.rmtree(stage, ignore_errors=True)
    if key is None:
        os.environ.pop("BLOWFANG_KEY")
    if bi:
        for name, job, want in inbox_samples():
            probs = bi.remote_rules(job, job["id"])
            ok = (not probs) == want
            bad += not ok
            say(ok, f"inbox {'takes' if want else 'refuses'}: {name}" + ("" if ok else f"  -> got {probs or 'no problems'}"))
        inbox_setup(say)
    else:
        say(None, "bench_inbox.py is not beside the runner: no inbox, no self-update")
    try:
        say(True, f"K6 cable {k6_cable()}")
    except Refused as e:
        say(None, str(e))
    try:
        say(True, f"IC-7100 via rigctld: {kb.rig_hz(kb.rigctl())} Hz, RF power {rig_power(kb.rigctl()):.2f}")
    except (OSError, ValueError, SystemExit, subprocess.CalledProcessError) as e:
        say(None, f"rigctld: {e}")
    say(os.path.exists(os.path.join(aura_dir(), "tools", "flash.py")) or None, f"Aura flash.py in {aura_dir()} (k6.update)")
    say(os.path.exists(os.path.join(HERE, "k6-reset-trigger.sh")) or None, "k6-reset-trigger.sh beside the runner (k6.update)")
    say(hasattr(kb, "GUARD_HZ") or False, "k6_bench.py beside the runner has channel claims")
    env = {"PATH": os.environ.get("PATH", "")}
    module_tools(env)
    cc = [shutil.which(t, path=env["PATH"]) for t in ("clang", "ld.lld", "llvm-objcopy", "llvm-nm")]
    say(all(cc) or False, "module compiler for uv380.send / uv380.m17: " + (cc[0] if all(cc) else
        "clang, ld.lld, llvm-objcopy or llvm-nm missing (PATH, $K6_ATFE, or BLOWFANG_WORK/toolchains/ATfE-*/bin)"))
    bad += not all(cc)
    say(kb.lock_free() or None, "PTT lock free")
    so = kb.serial_owner()
    say(True, "K6 cable reader: " + (f"{so['who']} (pid {so['pid']})" if so else "none"))
    print("PASS" if not bad else f"FAIL ({bad})")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n, fn in (("submit", cmd_submit), ("check", cmd_check)):
        p = sub.add_parser(n)
        p.add_argument("job")
        p.set_defaults(fn=fn)
    s = sub.add_parser("status")
    s.add_argument("--last", type=int, default=10)
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("show")
    s.add_argument("id")
    s.add_argument("--lines", type=int, default=40)
    s.set_defaults(fn=cmd_show)
    c = sub.add_parser("cancel")
    c.add_argument("id")
    c.add_argument("--by", default="?")
    c.set_defaults(fn=cmd_cancel)
    for n, fn in (("pause", cmd_pause), ("hold-ic7100", cmd_hold)):
        p = sub.add_parser(n)
        p.add_argument("why", nargs="*")
        p.set_defaults(fn=fn)
    sub.add_parser("resume").set_defaults(fn=cmd_resume)
    sub.add_parser("free-ic7100").set_defaults(fn=cmd_free)
    s = sub.add_parser("serve")
    s.add_argument("--every", type=float, default=5.0, help="queue check, s (5)")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("selftest")
    s.add_argument("--checkout", help="a checkout for the sample jobs (default ~/blowfang)")
    s.set_defaults(fn=cmd_selftest)
    s = sub.add_parser("install")
    s.add_argument("--unit", action="store_true")
    s.set_defaults(fn=cmd_install)
    for n in ("inbox", "autoupdate"):
        s = sub.add_parser(n, help=f"{n} on|off")
        s.add_argument("onoff", choices=("on", "off"))
        s.set_defaults(fn=cmd_switch)
    sub.add_parser("rollback").set_defaults(fn=cmd_rollback)
    a = ap.parse_args(argv)
    return a.fn(a)


if bi:
    bi.bind(sys.modules.get(__name__))

if __name__ == "__main__":
    sys.exit(main())
