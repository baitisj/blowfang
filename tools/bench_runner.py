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
  bench_runner.py install [--unit]     copy this file and k6_bench.py into $BLOWFANG_WORK/bin;
                                       --unit also writes the user unit

A job is a JSON file: who (the thread, one word), a channel to claim, how long, a checkout, and
steps. Every step is one of a fixed set (k6.install, k6.flash, k6.update, k6.upload, pluto,
ic7100, ic7100.tune, ic7100.power0, logger, logger.stop, wait, uv380.flash), turned into one
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
                "logger": 30, "logger.stop": 60, "wait": WAIT_MAX_S + 30, "uv380.flash": 300}
PLUTO_SUBS = ("info", "quiet", "send", "steps", "rx", "measure", "cal", "fsk24")
PLUTO_TX = ("send", "steps", "fsk24")
IC7100_SUBS = ("send", "id", "stop")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
WHO_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")


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
    if os.environ.get("UV380_DEV"):
        return os.environ["UV380_DEV"]
    hits = sorted(glob.glob("/dev/serial/by-id/*0483*5740*"))
    return os.path.realpath(hits[0]) if len(hits) == 1 else "/dev/ttyACM0"


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
    try:
        k = open(key_path(), "rb").read()
    except OSError:
        return None
    return k if len(k) == 16 else None


def check_image_keys(data, what, problems, want_keyed):
    """want_keyed: the image itself must carry our key (k6_update flashes it as it is);
    else k6_install keys it: our key, or the placeholder it writes our key over"""
    key = read_key()
    if key is None:
        problems.append(f"{what}: no 16-byte module key at {key_path()}")
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
    elif do == "uv380.flash":
        staged_file(step, "image", rs, what, problems)
        staged_file(step, "radio_tool", rs, what, problems, need_sha=False)
        check_commit(step, what, problems)
        if args:
            problems.append(f"{what}: takes no args")


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
        return [py(), tool("pluto_bench.py"), "--as", who] + args, co, False
    if do == "ic7100":
        return [py(), bench_bin(), "--as", who] + args, co, False
    if do == "logger":
        log = os.path.join(rd, step.get("file", "k6.log"))
        return [py(), bench_bin(), "serial-hold", who, "--", "timeout", "-s", "INT", "--kill-after=10",
                str(int(step["seconds"])), py(), "-m", "modem.k6_harness", "listen", "--k6", k6_cable(),
                "--log", log], co, True
    if do == "logger.stop":
        return [py(), bench_bin(), "serial-release", who], co, False
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


def step_env(job):
    env = dict(os.environ)
    env["K6_BENCH_AS"] = job["who"]
    env["BLOWFANG_WORK"] = work()
    env["BLOWFANG_REPO"] = job_checkout(job)
    env.pop("K6LOG", None)
    env["PYTHONUNBUFFERED"] = "1"
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
    ctx.say(f"job {job['id']} by {job['who']}, checkout {res['checkout']} at {head}")
    env = step_env(job)
    deadline = now() + 60 * float(job["minutes"])
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
            elif step["do"] == "ic7100.power0":
                ctx.rig("L", "RFPOWER", "0")
                p = rig_power(ctx.rig)
                ctx.say(f"IC-7100 RFPOWER {p:.2f}")
                rc, why = (0, None) if p <= POWER_SLACK else (1, f"RFPOWER reads {p:.2f}")
            else:
                argv, cwd, bg = command(job, step, rd)
                if bg:
                    ctx.say("$ (background) " + " ".join(argv))
                    BACKGROUND.append(subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                                       stdout=open(os.path.join(rd, "logger.out"), "a"),
                                                       stderr=subprocess.STDOUT, start_new_session=True))
                    time.sleep(2.0)
                    o = kb.serial_owner()
                    rc, why = (0, None) if o and o.get("who") == job["who"] else (1, "the logger did not start")
                else:
                    rc, why = run_proc(ctx, argv, cwd, timeout, env)
        except Refused as e:
            rc, why = 1, f"refused: {e}"
        except (OSError, ValueError, subprocess.CalledProcessError, SystemExit) as e:
            rc, why = 1, f"{type(e).__name__}: {e}"
        if why and not rc:
            rc = 1                            # stopped (cancel, timeout) even if the tool exited 0
        sr.update(ended=utcnow(), rc=rc, **({"why": why} if why else {}))
        ctx.say(f"step {i + 1} ({step['do']}) rc={rc}" + (f" ({why})" if why else ""))
        if rc and not step.get("continue_on_error"):
            res.update(status="cancelled" if why == "cancelled" or cancelled() else "failed",
                       reason=f"step {i + 1} ({step['do']}): {why or f'exit {rc}'}")
            break
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
            nj.update(id=f"{base}-r{n}"[:64], not_before=datetime.datetime.fromtimestamp(
                nb, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            probs = submit(nj)
            if probs:
                note(f"repeat of {job['id']} not queued: {'; '.join(probs)}")


def cancel_path(job_id):
    return os.path.join(qdir(), f"cancel-{job_id}")


def serve_once(rig=None, now=time.time, claim=kb.claim, release=kb.release):
    """start the next job that may run now; True when one ran"""
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
    while not STOPPING:
        BACKGROUND[:] = [p for p in BACKGROUND if p.poll() is None]
        if not serve_once():
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
    for f in ("bench_runner.py", "k6_bench.py", "k6-reset-trigger.sh"):
        src = os.path.join(HERE, f)
        if os.path.realpath(src) != os.path.realpath(os.path.join(b, f)):
            shutil.copy2(src, os.path.join(b, f))
        os.chmod(os.path.join(b, f), 0o755)
        print("installed", os.path.join(b, f))
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
    ]


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
        open(os.environ["BLOWFANG_KEY"], "wb").write(k)
    for name, job, want in sample_jobs(stage, co, k):
        probs = check_job(job)
        ok = (not probs) == want
        bad += not ok
        say(ok, f"{'runs' if want else 'refused'}: {name}" + ("" if ok else f"  -> got {probs or 'no problems'}"))
    shutil.rmtree(stage, ignore_errors=True)
    if key is None:
        os.environ.pop("BLOWFANG_KEY")
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
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
