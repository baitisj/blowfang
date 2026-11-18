#!/usr/bin/env python3
"""The bench runner's inbox: cloud threads queue bench jobs by pushing them to the
claude/bench-jobs branch, and the runner on hamlet picks them up, checks them against the bench
rules, runs them and writes the results back to the same branch (Jeff, 2026-11-17). The runner
also keeps itself on main: while idle it installs a newer main once that main's own selftest
passes. docs/bench-runner.md, "The inbox", has the whole picture.

For threads (from any blowfang checkout, cloud or hamlet):

  bench_inbox.py submit JOB.json        push the job, and the files it names as job:NAME (found
                                        beside JOB.json), to the inbox branch
  bench_inbox.py show ID                its result.json, the files that came back, the log's tail
  bench_inbox.py get ID DEST            copy the files that came back into DEST
  bench_inbox.py status                 the runner's status.json and the last results
  bench_inbox.py cancel ID              ask the runner to drop a waiting job or stop a running one;
                                        a repeat's own id (ID-rN) stops that run, the first id stops
                                        the whole series

The branch:

  jobs/ID/job.json         the job (docs/bench-runner.md), plus its files
  jobs/ID/cancel           (any content) cancel it
  results/ID/result.json   queued | refused | running | done | failed | cancelled, with reasons
  results/ID/runner.log    the run's log, its tail if long
  results/ID/files/...     the run folder's files up to 20 MB each (files.json lists them all)
  status.json              the runner: its commit, the running job, the queue, the last poll

A job from the inbox differs from a local one in what it may name, because whoever can push to
the repository can queue it:
  - files only as "job:NAME" (a file pushed in the job's folder) or "run:NAME" (the job's run
    folder on hamlet, for outputs such as a Pluto capture); no hamlet paths
  - the tools run from main: "tools_commit" may pin a commit on main (default: main's tip). The
    UV-380 module steps may build the checkout's module sources from any pushed commit
    ("sources_commit"), once the module's #include files are checked to stay inside that tree
  - no flashes (k6.install, k6.flash, k6.update, uv380.flash) and no .c K6 modules: those stay
    a local submit on hamlet. A K6 module comes built (.bin), and the runner signs it
  - every other check is the runner's own, unchanged (bench_runner.check_job)

Standard library only. SPDX-License-Identifier: MIT
"""
import argparse
import copy
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

BRANCH = "claude/bench-jobs"
DEFAULT_URL = "https://github.com/baitisj/blowfang"
POLL_S = 60                        # the inbox fetch, idle
POLL_MAX_S = 900                   # backoff after network errors
CANCEL_POLL_S = 60                 # a running inbox job: look for jobs/ID/cancel this often
UPDATE_EVERY_S = 300               # look for a newer main this often (idle only)
HEARTBEAT_S = 3600                 # status.json pushed at least this often
MAX_FILES = 64                     # files in one job's folder
MAX_FILE = 16 << 20
MAX_JOB = 32 << 20
RETURN_FILE_MAX = 20 << 20         # a run's file that comes back in results/ID/files
RETURN_JOB_MAX = 40 << 20
LOG_MAX = 512 << 10
KEEP_WORKTREES = 6                 # inbox-* / src-* worktrees kept beside the ones in use
FINAL = ("done", "failed", "refused", "cancelled")
REMOTE_NO_STEPS = ("k6.install", "k6.flash", "k6.update", "uv380.flash")
SOURCES_STEPS = ("uv380.send", "uv380.m17", "uv380.ysf", "uv380.voice", "uv380.rx")
PATH_FIELDS = ("bundle", "image", "module", "radio_tool")
RUNNER_FILES = ("bench_runner.py", "bench_inbox.py", "k6_bench.py", "k6-reset-trigger.sh")
IDENT = ["-c", "user.name=hamlet bench runner", "-c", "user.email=bench-runner@hamlet.invalid"]
HEX_RE = re.compile(r"^[0-9a-f]{7,40}$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-][A-Za-z0-9._/-]{0,199}$")
# a module source may not read files from outside its tree: these would put a hamlet file in the
# module (and so on the air) or in the compiler's errors (and so in the public log)
SOURCE_BAD = (re.compile(r"^\s*#\s*embed\b", re.M), re.compile(r"__has_embed"),
              re.compile(r"\.incbin\b"), re.compile(r"\.include\b"), re.compile(r"\bincbin\b"))
INCLUDE_RE = re.compile(r"^\s*#\s*(include|include_next|import)\b\s*(.*)$", re.M)

BR = None                          # the bench_runner module (bind() sets it; the runner's own copy)


class InboxError(Exception):
    pass


def bind(br):
    global BR
    if br is not None:
        BR = br


def _br():
    global BR
    if BR is None:                 # a thread's checkout: the runner beside this file, for the rules
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bench_runner
        BR = bench_runner
    return BR


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


# ---- the rules for a job from the inbox (both sides: submit lints with them, the runner refuses) ----

def job_refs(job):
    """every (prefix, name) a job names as job:NAME / run:NAME, in path fields and args"""
    out = []
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        vals = [step.get(f) for f in PATH_FIELDS] + list(step.get("args") or [])
        for v in vals:
            if not isinstance(v, str):
                continue
            if v.startswith("--") and "=" in v:
                v = v.split("=", 1)[1]
            for pfx in ("job:", "run:"):
                if v.startswith(pfx):
                    out.append((pfx, v[len(pfx):]))
    return out


def safe_name(n):
    """a relative name inside a folder: no absolute path, no .., no hidden first part"""
    if not isinstance(n, str) or not NAME_RE.match(n) or n.startswith("/"):
        return False
    parts = n.split("/")
    return all(p not in ("", ".", "..") for p in parts) and not parts[0].startswith(".")


def remote_rules(job, jid=None):
    """the problems with a job from the inbox beyond the runner's own checks"""
    br = _br()
    p = []
    if not isinstance(job, dict):
        return ["the job is not a JSON object"]
    if jid is not None and job.get("id") != jid:
        p.append(f"id {job.get('id')!r} must be the folder's name ({jid})")
    for k in ("checkout", "sources", "inbox", "take_serial"):
        if k in job:
            p.append(f"{k}: the runner sets it for an inbox job (use tools_commit / sources_commit)")
    for k in ("tools_commit", "sources_commit"):
        if k in job and not (isinstance(job[k], str) and HEX_RE.match(job[k])):
            p.append(f"{k}: a commit, 7-40 lower-case hex")
    steps = job.get("steps")
    if not isinstance(steps, list):
        return p + ["steps: a list"]
    dos = [s.get("do") for s in steps if isinstance(s, dict)]
    if "sources_commit" in job and not any(d in SOURCES_STEPS for d in dos):
        p.append(f"sources_commit: only for the UV-380 module steps ({', '.join(SOURCES_STEPS)})")
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            continue
        what = f"step {i + 1} ({s.get('do')})"
        if s.get("do") in REMOTE_NO_STEPS:
            p.append(f"{what}: flashes are a local submit on hamlet (bench_runner.py submit), not the inbox")
        if s.get("do") not in br.STEP_TIMEOUT:
            p.append(f"{what}: 'do' must be one of {', '.join(br.STEP_TIMEOUT)}")
        if s.get("do") == "k6.upload" and str(s.get("module", "")).endswith(".c"):
            p.append(f"{what}: push the built module (.bin) and its sha256; the inbox doesn't compile K6 modules")
        if s.get("do") == "k6.upload" and not s.get("sha256"):
            p.append(f"{what}: needs sha256 of the module")
        vals = [s.get(f) for f in PATH_FIELDS if f in s] + [a for a in s.get("args") or [] if isinstance(a, str)]
        for v in vals:
            if not isinstance(v, str):
                continue
            x = v.split("=", 1)[1] if v.startswith("--") and "=" in v else v
            if x.startswith(("/", "~")):
                p.append(f"{what}: '{v}': name files as job:NAME or run:NAME, not hamlet paths")
    for pfx, n in job_refs(job):
        if not safe_name(n):
            p.append(f"'{pfx}{n}': a plain relative name (no /, .., or leading dot)")
    return p


def lint(job):
    """the checks a thread can run without hamlet: the inbox rules and the job's shape"""
    br = _br()
    p = remote_rules(job, job.get("id") if isinstance(job, dict) else None)
    if not isinstance(job, dict):
        return p
    if not br.ID_RE.match(str(job.get("id", ""))):
        p.append("id: lower case letters, digits, . _ -, up to 64")
    if not br.WHO_RE.match(str(job.get("who", ""))):
        p.append("who: one short word (the thread)")
    f = job.get("freq_mhz", 440.850)
    if f != "bench":
        try:
            if not br.kb.in_band(br.as_hz(f)):
                p.append(f"freq_mhz {f}: a 70 cm channel (430.010-449.990) or \"bench\"")
        except (TypeError, ValueError):
            p.append(f"freq_mhz {f!r}: MHz, such as 440.850")
    m = job.get("minutes")
    if not isinstance(m, (int, float)) or not 0 < m <= br.JOB_MAX_MIN:
        p.append(f"minutes: 1..{br.JOB_MAX_MIN}")
    return p


def scan_sources(root, rel):
    """the problems with a module source and the files it #includes, inside the tree root: every
    quoted include found must stay in root, none may be absolute or climb out with .., and no
    #embed / .incbin / asm .include (they read any file)"""
    root = os.path.realpath(root)
    todo, seen, p = [os.path.join(root, rel)], set(), []
    while todo:
        f = os.path.realpath(todo.pop())
        if f in seen:
            continue
        seen.add(f)
        if not (f == root or f.startswith(root + os.sep)):
            p.append(f"{os.path.relpath(f, root)}: outside the sources tree")
            continue
        try:
            text = open(f, errors="replace").read()
        except OSError as e:
            p.append(f"{os.path.relpath(f, root)}: {e}")
            continue
        r = os.path.relpath(f, root)
        for bad in SOURCE_BAD:
            if bad.search(text):
                p.append(f"{r}: '{bad.pattern}' is not allowed in a module from the inbox")
        for m in INCLUDE_RE.finditer(text):
            arg = m.group(2).strip()
            q = re.match(r'^"([^"]+)"|^<([^>]+)>', arg)
            if not q:
                p.append(f"{r}: '#{m.group(1)} {arg}': a computed include")
                continue
            name = q.group(1) or q.group(2)
            if name.startswith("/") or "\\" in name:
                p.append(f"{r}: #include {name}: an absolute path")
                continue
            if q.group(1):
                cand = os.path.realpath(os.path.join(os.path.dirname(f), name))
                if os.path.exists(cand):
                    todo.append(cand)       # the walk checks it stays inside root
                    continue
            if ".." in name.split("/"):
                p.append(f"{r}: #include {name}: climbs out of the include path")
    return p


# ---- git ----

def git(*args, gd=None, cwd=None, data=None, env=None, check=True, timeout=180):
    argv = ["git"] + (["--git-dir", gd] if gd else []) + list(args)
    e = dict(os.environ, GIT_TERMINAL_PROMPT="0", **(env or {}))
    r = subprocess.run(argv, cwd=cwd, input=data, capture_output=True, env=e, timeout=timeout)
    if check and r.returncode:
        raise InboxError(f"git {' '.join(args[:3])}: {(r.stderr or r.stdout).decode(errors='replace').strip()[:300]}")
    return r


def out(*args, **kw):
    return git(*args, **kw).stdout.decode(errors="replace").strip()


def rev(ref, gd=None, cwd=None):
    r = git("rev-parse", "--verify", "-q", ref + "^{commit}", gd=gd, cwd=cwd, check=False)
    return r.stdout.decode().strip() if r.returncode == 0 else None


def ls_tree(tip, path, gd=None, cwd=None, recursive=True):
    """[(mode, type, sha, size, name)] under path in tip (names relative to path)"""
    args = ["ls-tree", "-z", "-l"] + (["-r"] if recursive else []) + [tip, "--", path.rstrip("/") + "/"]
    r = git(*args, gd=gd, cwd=cwd, check=False)
    ents = []
    for e in r.stdout.decode(errors="replace").split("\0"):
        if not e:
            continue
        meta, name = e.split("\t", 1)
        mode, typ, sha, size = meta.split()
        ents.append((mode, typ, sha, int(size) if size.isdigit() else 0, name[len(path.rstrip("/")) + 1:]))
    return ents


def blob(sha, gd=None, cwd=None):
    return git("cat-file", "blob", sha, gd=gd, cwd=cwd).stdout


# ---- the runner's side ----

class Inbox:
    """the inbox inside the runner's serve loop: tick() between jobs, started() when one starts"""

    def __init__(self, now=time.time):
        self.now = now
        self.next_poll = 0.0
        self.backoff = POLL_S
        self.next_update = now() + 60
        self.last_status = None
        self.last_status_push = 0.0
        self.last_poll = None
        self.error = None
        self.update_note = None
        self.cancel_watch = None
        self.lock = threading.Lock()      # the repo's git: the serve loop or the cancel watch, not both

    # where things are
    @staticmethod
    def root():
        return os.path.join(_br().work(), "inbox")

    def gd(self):
        return os.path.join(self.root(), "blowfang.git")

    def state_path(self):
        return os.path.join(self.root(), "state.json")

    def state(self):
        return _br().get_json(self.state_path()) or {"seen": {}, "pushed": {}}

    def save(self, st):
        _br().put_json(self.state_path(), st)

    @staticmethod
    def enabled():
        return not os.path.exists(os.path.join(_br().work(), "state", "inbox.off"))

    @staticmethod
    def autoupdate():
        return not os.path.exists(os.path.join(_br().work(), "state", "autoupdate.off"))

    def url(self):
        if os.environ.get("BENCH_INBOX_URL"):
            return os.environ["BENCH_INBOX_URL"]
        r = git("-C", _br().default_checkout(), "remote", "get-url", "origin", check=False)
        u = r.stdout.decode().strip() if r.returncode == 0 else ""
        return u or DEFAULT_URL

    def ensure_clone(self):
        if not os.path.isdir(self.gd()):
            os.makedirs(self.root(), exist_ok=True)
            git("clone", "--bare", "--quiet", self.url(), self.gd(), timeout=600)
            _br().note(f"inbox: cloned {self.url()} into {self.gd()}")

    def fetch(self):
        self.ensure_clone()
        git("fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/heads/*", gd=self.gd(), timeout=120)
        self.last_poll = utcnow()

    def tip(self, ref=BRANCH):
        return rev("refs/heads/" + ref, gd=self.gd())

    # worktrees
    def worktree(self, commit, kind):
        """a clean detached worktree of commit under worktrees/, made once and reused"""
        p = os.path.join(_br().work(), "worktrees", f"{kind}-{commit[:12]}")
        if os.path.isdir(p) and rev("HEAD", cwd=p) == commit:
            ok, _ = _br().checkout_clean(p)
            if ok:
                return p
            raise InboxError(f"{p} has changed files (someone worked in it); remove it and retry")
        git("worktree", "prune", gd=self.gd(), check=False)
        if os.path.exists(p):
            raise InboxError(f"{p} exists and isn't at {commit[:12]}")
        git("worktree", "add", "--detach", "--force", p, commit, gd=self.gd(), timeout=300)
        return p

    def prune_worktrees(self):
        """drop old inbox-* / src-* worktrees no queued or running job uses (the newest kept)"""
        br = _br()
        base = os.path.realpath(os.path.join(br.work(), "worktrees"))
        used = set()
        for j in br.waiting() + [((br.get_json(br.qdir("running.json")) or {}).get("job") or {})]:
            for k in ("checkout", "sources"):
                if j.get(k):
                    used.add(os.path.realpath(j[k]))
        mine = []
        for n in os.listdir(base) if os.path.isdir(base) else []:
            if re.match(r"^(inbox|src)-[0-9a-f]{12}$", n):
                p = os.path.join(base, n)
                mine.append((os.path.getmtime(p), p))
        for _, p in sorted(mine)[:-KEEP_WORKTREES]:
            if os.path.realpath(p) not in used:
                git("worktree", "remove", "--force", p, gd=self.gd(), check=False)

    # picking jobs up
    def poll(self):
        """fetch; queue (or refuse) every job in the branch not seen yet; look for cancels"""
        self.fetch()
        tip = self.tip()
        if tip is None:
            raise InboxError(f"no {BRANCH} branch at {self.url()}")
        st = self.state()
        done = {e[4].split("/")[0] for e in ls_tree(tip, "results", gd=self.gd()) if "/" in e[4]}
        jobs = {}
        for e in ls_tree(tip, "jobs", gd=self.gd()):
            if "/" in e[4]:
                jobs.setdefault(e[4].split("/")[0], []).append(e)
        cancels = {jid for jid, ents in jobs.items() if any(e[4] == f"{jid}/cancel" for e in ents)}
        for jid, (status, job) in self.inbox_jobs().items():
            if status in ("queued", "running") and cancel_folders(jid, job) & cancels:
                if not os.path.exists(_br().cancel_path(jid)):
                    open(_br().cancel_path(jid), "w").write("inbox")
                    _br().note(f"inbox: cancel {jid} asked in the branch")
        for jid, ents in sorted(jobs.items()):
            if jid in st["seen"] or jid in done:
                continue
            if not any(e[4] == f"{jid}/job.json" for e in ents):
                continue                      # only a cancel (a repeat's, jobs/ID-rN/cancel): not a job
            if not _br().ID_RE.match(jid):
                _br().note(f"inbox: skipped jobs/{jid}: not a job id")
                st["seen"][jid] = "skipped"
                continue
            if jid in _br().known_ids():      # a local job has the id: answer in the branch only
                st.setdefault("direct", {})[jid] = {
                    "id": jid, "status": "refused", "reason": f"id {jid} is taken on hamlet (pick a new id)",
                    "inbox": {"branch": BRANCH, "commit": tip, "path": f"jobs/{jid}"}}
                st["seen"][jid] = "refused"
                continue
            self.save(st)
            st["seen"][jid] = self.pickup(tip, jid, [(m, t, s, z, n[len(jid) + 1:]) for m, t, s, z, n in ents])
        self.save(st)

    def pickup(self, tip, jid, ents):
        br = _br()
        meta = {"branch": BRANCH, "commit": tip, "path": f"jobs/{jid}"}
        base = {"id": jid, "who": "?", "inbox": meta}
        try:
            if len(ents) > MAX_FILES:
                raise InboxError(f"{len(ents)} files in the job's folder (at most {MAX_FILES})")
            total = 0
            for mode, typ, sha, size, name in ents:
                if typ != "blob" or mode not in ("100644", "100755"):
                    raise InboxError(f"{name}: plain files only (no links or submodules)")
                if not safe_name(name):
                    raise InboxError(f"{name}: not a plain relative name")
                if size > MAX_FILE:
                    raise InboxError(f"{name}: {size} bytes (at most {MAX_FILE})")
                total += size
            if total > MAX_JOB:
                raise InboxError(f"the job's files are {total} bytes (at most {MAX_JOB})")
            jent = [e for e in ents if e[4] == "job.json"]
            if not jent:
                raise InboxError("no job.json")
            try:
                job = json.loads(blob(jent[0][2], gd=self.gd()))
            except ValueError as e:
                raise InboxError(f"job.json: {e}")
            if isinstance(job, dict) and br.WHO_RE.match(str(job.get("who", ""))):
                base["who"] = job["who"]
            probs = remote_rules(job, jid)
            if probs:
                return self.refuse(base, probs)
            stage = os.path.join(br.work(), "runs", "inbox", jid)
            shutil.rmtree(stage, ignore_errors=True)
            for mode, typ, sha, size, name in ents:
                if name in ("job.json", "cancel"):
                    continue
                dst = os.path.join(stage, name)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(blob(sha, gd=self.gd()))
            os.makedirs(stage, exist_ok=True)
            main = self.tip("main")
            tc = job.get("tools_commit")
            if tc:
                full = rev(tc, gd=self.gd())
                if not full or git("merge-base", "--is-ancestor", full, main, gd=self.gd(), check=False).returncode:
                    return self.refuse(base, [f"tools_commit {tc}: not a commit on main (the tools run from main)"])
                tc = full
            else:
                tc = main
            co = self.worktree(tc, "inbox")
            src = None
            if job.get("sources_commit"):
                sc = rev(job["sources_commit"], gd=self.gd())
                if not sc or not out("for-each-ref", "--contains", sc, "refs/heads", gd=self.gd()):
                    return self.refuse(base, [f"sources_commit {job['sources_commit']}: not on any pushed branch"])
                src = self.worktree(sc, "src")
                probs = []
                for s in job["steps"]:
                    mod = module_rel(br, s)
                    if mod:
                        if os.path.isfile(os.path.join(src, *mod)):
                            probs += scan_sources(src, os.path.join(*mod))
                        else:
                            probs.append(f"sources_commit has no {'/'.join(mod)}")
                if probs:
                    return self.refuse(base, probs)
            rd = br.run_dir(jid)
            os.makedirs(rd, exist_ok=True)
            j = materialize(job, stage, rd, co, src, meta)
            probs = br.submit(j)
            if probs:
                return self.refuse(base, probs)
            br.note(f"inbox: queued {jid} by {base['who']} (tools {tc[:12]}" +
                    (f", sources {src[-12:]})" if src else ")"))
            return "queued"
        except (InboxError, OSError, subprocess.TimeoutExpired) as e:
            return self.refuse(base, [str(e)])

    def refuse(self, base, probs):
        _br().finish(base, {"id": base["id"], "status": "refused", "reason": "; ".join(probs),
                            "problems": probs, "inbox": base["inbox"]}, "refused")
        return "refused"

    # results
    def inbox_jobs(self):
        """{id: (status, job)} for every inbox job the runner knows locally"""
        br = _br()
        found = {}
        r = br.get_json(br.qdir("running.json"))
        if r and (r.get("job") or {}).get("inbox"):
            found[r["id"]] = ("running", r["job"])
        for j in br.waiting():
            if j.get("inbox"):
                found.setdefault(j["id"], ("queued", j))
        horizon = self.now() - 3 * 86400
        for d in FINAL:
            for f in os.listdir(br.qdir(d)):
                p = os.path.join(br.qdir(d), f)
                if f.endswith(".json") and os.path.getmtime(p) > horizon:
                    j = br.get_json(p)
                    if j and j.get("inbox"):
                        found.setdefault(j["id"], (d, j))
        return found

    def result_files(self, jid, status, job):
        """[(path in the branch, bytes)] for results/ID"""
        br = _br()
        rd = br.run_dir(jid)
        res = br.get_json(os.path.join(rd, "result.json")) if status in FINAL else None
        if res is None:
            res = dict(job.get("result") or {}, id=jid, status=status)
        if status == "queued":
            res["queued"] = job.get("submitted")
        if status == "running":
            res["since"] = (br.get_json(br.qdir("running.json")) or {}).get("since")
        res["inbox"] = job.get("inbox")
        res["runner_commit"] = installed_commit(br)
        res["updated"] = utcnow()
        files = [(f"results/{jid}/result.json", (json.dumps(res, indent=1) + "\n").encode())]
        lp = os.path.join(rd, "runner.log")
        if os.path.exists(lp) and status != "queued":
            data = open(lp, "rb").read()
            if len(data) > LOG_MAX:
                data = b"(the log's first %d bytes are on hamlet)\n" % (len(data) - LOG_MAX) + data[-LOG_MAX:]
            files.append((f"results/{jid}/runner.log", data))
        if status in FINAL and os.path.isdir(rd):
            listing, budget = [], RETURN_JOB_MAX
            allf = []
            for dp, _, fs in os.walk(rd):
                for f in fs:
                    p = os.path.join(dp, f)
                    rel = os.path.relpath(p, rd)
                    if rel in ("result.json", "runner.log") or os.path.islink(p):
                        continue
                    allf.append((os.path.getsize(p), rel, p))
            for size, rel, p in sorted(allf):
                back = size <= RETURN_FILE_MAX and size <= budget
                h = hashlib.sha256()
                with open(p, "rb") as fh:
                    for b in iter(lambda: fh.read(1 << 20), b""):
                        h.update(b)
                listing.append({"name": rel, "bytes": size, "sha256": h.hexdigest(), "returned": back,
                                "hamlet": p})
                if back:
                    budget -= size
                    files.append((f"results/{jid}/files/{rel}", open(p, "rb").read()))
            files.append((f"results/{jid}/files.json", (json.dumps(listing, indent=1) + "\n").encode()))
        return files

    def status_doc(self):
        br = _br()
        r = br.get_json(br.qdir("running.json"))
        return {"runner_commit": installed_commit(br), "inbox": self.enabled(), "autoupdate": self.autoupdate(),
                "paused": open(br.qdir("PAUSE")).read().strip() if os.path.exists(br.qdir("PAUSE")) else None,
                "ic7100_held": open(br.hold_path()).read().strip() if os.path.exists(br.hold_path()) else None,
                "running": {"id": r["id"], "who": r["who"], "since": r["since"]} if r else None,
                "waiting": [{"id": j["id"], "who": j["who"], "not_before": j.get("not_before")}
                            for j in br.waiting()],
                "last_poll": self.last_poll, "error": self.error, "update": self.update_note}

    def sync(self, force_status=False):
        """push results/ID for every inbox job whose status changed, and status.json when it changed
        (or hourly); one commit, retried on a race with a thread's push"""
        st = self.state()
        changed = {jid: v for jid, v in self.inbox_jobs().items() if st["pushed"].get(jid) != v[0]}
        direct = st.get("direct") or {}
        sd = self.status_doc()
        same = {k: v for k, v in sd.items() if k != "last_poll"}
        due = force_status or same != self.last_status or self.now() - self.last_status_push > HEARTBEAT_S
        if not changed and not direct and not due:
            return False
        files = []
        for jid, (status, job) in changed.items():
            files += self.result_files(jid, status, job)
        for jid, res in direct.items():
            files.append((f"results/{jid}/result.json", (json.dumps(dict(res, updated=utcnow()), indent=1) + "\n").encode()))
            changed[jid] = (res["status"], None)
        files.append(("status.json", (json.dumps(dict(sd, updated=utcnow()), indent=1) + "\n").encode()))
        what = ", ".join(f"{j} {v[0]}" for j, v in sorted(changed.items())) or "status"
        self.commit_push(files, [f"results/{j}" for j in changed], f"bench runner: {what}")
        for jid, (status, _) in changed.items():
            st["pushed"][jid] = status
        st["direct"] = {}
        self.save(st)
        self.last_status, self.last_status_push = same, self.now()
        return True

    def commit_push(self, files, replace_dirs, msg, tries=4):
        gd = self.gd()
        for attempt in range(tries):
            if attempt:
                time.sleep(2 * attempt)
                self.fetch()
            tip = self.tip()
            with tempfile.TemporaryDirectory() as td:
                env = {"GIT_INDEX_FILE": os.path.join(td, "index")}
                git("read-tree", tip, gd=gd, env=env)
                gone = []
                for d in replace_dirs:      # a bare repo has no work tree: mode 0 lines drop entries
                    gone += [o for o in out("ls-files", "-z", "--", d + "/", gd=gd, env=env).split("\0") if o]
                if gone:
                    git("update-index", "--index-info", gd=gd, env=env,
                        data="".join(f"0 {'0' * 40}\t{o}\n" for o in gone).encode())
                for path, data in files:
                    sha = out("hash-object", "-w", "--stdin", gd=gd, data=data)
                    git("update-index", "--add", "--cacheinfo", f"100644,{sha},{path}", gd=gd, env=env)
                tree = out("write-tree", gd=gd, env=env)
            if tree == out("rev-parse", tip + "^{tree}", gd=gd):
                return tip
            c = out(*IDENT, "commit-tree", tree, "-p", tip, "-m", msg, gd=gd)
            r = git("push", "--quiet", "origin", f"{c}:refs/heads/{BRANCH}", gd=gd, check=False, timeout=90)
            if r.returncode == 0:
                git("update-ref", f"refs/heads/{BRANCH}", c, gd=gd)
                return c
            err = r.stderr.decode(errors="replace").strip()
            if "rejected" not in err and "fetch first" not in err and "non-fast-forward" not in err:
                raise InboxError(f"push: {err[:300]}")
        raise InboxError(f"push: still racing after {tries} tries")

    # the serve loop's hooks
    def started(self, job):
        """a job starts: push its running status, and watch the branch for its cancel"""
        if not job.get("inbox") or not self.enabled():
            return
        with self.lock:
            try:
                if not self.backing_off():
                    self.sync()
            except (InboxError, OSError, subprocess.TimeoutExpired) as e:
                self.fail(e)
        self.cancel_watch = threading.Event()
        threading.Thread(target=self.watch_cancel, args=(job["id"], self.cancel_watch, cancel_folders(job["id"], job)),
                         daemon=True).start()

    def finished(self):
        if self.cancel_watch:
            self.cancel_watch.set()
            self.cancel_watch = None

    def watch_cancel(self, jid, stop, folders=None):
        folders = folders or {jid}
        while not stop.wait(CANCEL_POLL_S):
            if not self.lock.acquire(blocking=False):
                continue
            try:
                git("fetch", "--quiet", "origin", f"+refs/heads/{BRANCH}:refs/heads/{BRANCH}", gd=self.gd(),
                    timeout=120)
                if any(git("cat-file", "-e", f"refs/heads/{BRANCH}:jobs/{f}/cancel", gd=self.gd(),
                           check=False).returncode == 0 for f in sorted(folders)):
                    open(_br().cancel_path(jid), "w").write("inbox")
                    _br().note(f"inbox: cancel {jid} asked in the branch")
                    return
            except (InboxError, OSError, subprocess.TimeoutExpired):
                pass
            finally:
                self.lock.release()

    def backing_off(self):
        """after a network error, no git until the next poll is due (a local job mustn't wait on it)"""
        return self.error is not None and self.now() < self.next_poll

    def fail(self, e):
        self.error = f"{utcnow()} {type(e).__name__}: {e}"[:400]
        _br().note(f"inbox: {self.error}")
        self.backoff = min(POLL_MAX_S, self.backoff * 2)
        self.next_poll = self.now() + self.backoff

    def tick(self, idle=True):
        """between jobs: poll the branch when due, push changed results, update the runner if a newer
        main passes its selftest. Never raises: the runner outlives the network"""
        if not self.enabled():
            return None
        _br().ensure_dirs()
        with self.lock:
            return self._tick(idle)

    def _tick(self, idle):
        try:
            if self.now() >= self.next_poll:
                self.poll()
                self.prune_worktrees()
                self.error = None
                self.backoff = POLL_S
                self.next_poll = self.now() + POLL_S
            if os.path.isdir(self.gd()) and not self.backing_off():
                self.sync()
            if idle and self.now() >= self.next_update and self.autoupdate():
                self.next_update = self.now() + UPDATE_EVERY_S
                return self.maybe_update()
        except (InboxError, OSError, ValueError, subprocess.TimeoutExpired) as e:
            self.fail(e)
        return None

    # keeping the runner on main
    def maybe_update(self, run=subprocess.run):
        """when nothing waits, install main if the runner's files changed and main's selftest passes.
        Returns "exec" (the caller re-executes the new runner), "restart" (exit so systemd restarts
        it on a new unit) or None"""
        br = _br()
        br.ensure_dirs()
        if os.path.exists(br.qdir("PAUSE")) or br.get_json(br.qdir("running.json")):
            return None
        if any(not j.get("not_before") or br.parse_utc(j["not_before"]) <= self.now() + 120 for j in br.waiting()):
            return None
        if any(p.poll() is None for p in br.BACKGROUND):
            return None
        main = self.tip("main")
        st = self.state()
        if not main or main == installed_commit(br) or main == st.get("update_failed"):
            return None
        binp = os.path.join(br.work(), "bin")
        changed = []
        for f in RUNNER_FILES:
            r = git("cat-file", "blob", f"{main}:tools/{f}", gd=self.gd(), check=False)
            have = open(os.path.join(binp, f), "rb").read() if os.path.exists(os.path.join(binp, f)) else None
            if r.returncode == 0 and r.stdout != have:
                changed.append(f)
        if not changed:
            write_installed(br, main, "unchanged runner files")
            return None
        wt = self.worktree(main, "inbox")
        env = dict(os.environ, BLOWFANG_WORK=br.work())
        t = run([sys.executable, os.path.join(wt, "tools", "bench_runner.py"), "selftest", "--checkout", wt],
                cwd=wt, env=env, capture_output=True, text=True, timeout=600)
        lines = (t.stdout or "").strip().splitlines()
        if t.returncode or not lines or lines[-1].strip() != "PASS":
            st["update_failed"] = main
            self.save(st)
            fails = [ln for ln in lines if ln.startswith("FAIL")][:5]
            self.update_note = f"{utcnow()} main {main[:12]} not installed: selftest " + ("; ".join(fails) or
                                                                                         f"exit {t.returncode}")
            br.note("self-update: " + self.update_note)
            return None
        prev = os.path.join(binp, "previous")
        os.makedirs(prev, exist_ok=True)
        for f in RUNNER_FILES:
            if os.path.exists(os.path.join(binp, f)):
                shutil.copy2(os.path.join(binp, f), os.path.join(prev, f))
        unit = os.path.expanduser("~/.config/systemd/user/blowfang-bench-runner.service")
        before = open(unit).read() if os.path.exists(unit) else None
        argv = [sys.executable, os.path.join(wt, "tools", "bench_runner.py"), "install"] + (["--unit"] if before else [])
        i = run(argv, cwd=wt, env=env, capture_output=True, text=True, timeout=120)
        if i.returncode:
            for f in os.listdir(prev):
                shutil.copy2(os.path.join(prev, f), os.path.join(binp, f))
            st["update_failed"] = main
            self.save(st)
            self.update_note = f"{utcnow()} main {main[:12]} install failed: {(i.stderr or i.stdout)[-200:]}"
            br.note("self-update: " + self.update_note)
            return None
        write_installed(br, main, f"self-update from {installed_commit(br) or '?'}: {', '.join(changed)}")
        self.update_note = f"{utcnow()} installed main {main[:12]} ({', '.join(changed)}); selftest PASS"
        br.note("self-update: " + self.update_note)
        try:
            self.sync(force_status=True)
        except (InboxError, OSError, subprocess.TimeoutExpired) as e:
            self.fail(e)
        after = open(unit).read() if os.path.exists(unit) else None
        if before is not None and after != before:
            run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=60)
            return "restart"
        return "exec"


def cancel_folders(jid, job):
    """the jobs/ folders whose cancel stops this job: its own, and for a repeat (ID-rN) the folder it
    was pushed in, which stops the whole series"""
    out = {jid}
    p = ((job or {}).get("inbox") or {}).get("path", "")
    if p.startswith("jobs/"):
        out.add(p[len("jobs/"):])
    return out


def module_rel(br, step):
    """the checkout-relative module a UV-380 module step builds, or None"""
    do, args = step.get("do"), step.get("args") or []
    if do == "uv380.send":
        return br.UV380_SEND_MODULE
    if do == "uv380.m17" and len(args) == 1 and args[0] in br.UV380_M17_PLANS:
        return br.uv380_m17_module(args[0])
    if do == "uv380.ysf" and len(args) == 1 and args[0] in getattr(br, "UV380_YSF_PLANS", ()):
        return br.uv380_ysf_module(args[0])
    if do == "uv380.voice" and len(args) == 1 and args[0] in br.UV380_VOICE_PLANS:
        return br.uv380_voice_module(args[0])
    if do == "uv380.rx" and len(args) == 1 and args[0] in getattr(br, "UV380_RX_PLANS", {}):
        return br.uv380_rx_module(args[0])
    return None


def materialize(job, stage, rd, co, src, meta):
    """the job as the runner runs it: job:NAME -> the staged file, run:NAME -> the run folder,
    the checkout and sources worktrees, and where it came from"""
    j = copy.deepcopy(job)

    def fix(v):
        if not isinstance(v, str):
            return v
        pre, x = "", v
        if v.startswith("--") and "=" in v:
            pre, x = v.split("=", 1)
            pre += "="
        for pfx, base in (("job:", stage), ("run:", rd)):
            if x.startswith(pfx):
                return pre + os.path.join(base, x[len(pfx):])
        return v

    for s in j["steps"]:
        for f in PATH_FIELDS:
            if f in s:
                s[f] = fix(s[f])
        if isinstance(s.get("args"), list):
            s["args"] = [fix(a) for a in s["args"]]
    j.pop("tools_commit", None)
    j.pop("sources_commit", None)
    j["checkout"] = co
    if src:
        j["sources"] = src
    j["inbox"] = meta
    return j


def installed_path(br):
    return os.path.join(br.work(), "bin", ".installed.json")


def installed_commit(br):
    return (br.get_json(installed_path(br)) or {}).get("commit")


def write_installed(br, commit, why):
    br.put_json(installed_path(br), {"commit": commit, "at": utcnow(), "why": why})


# ---- a thread's side ----

def repo_root():
    r = git("rev-parse", "--show-toplevel", check=False)
    if r.returncode:
        raise SystemExit("run this inside a blowfang checkout")
    return r.stdout.decode().strip()


def fetch_branch(cwd):
    r = git("fetch", "--quiet", "origin", f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}", cwd=cwd, check=False)
    if r.returncode:
        raise SystemExit(f"can't fetch {BRANCH}: {r.stderr.decode(errors='replace').strip()}")
    return rev(f"refs/remotes/origin/{BRANCH}", cwd=cwd)


def cmd_submit(a):
    job = json.load(open(a.job))
    here = os.path.dirname(os.path.abspath(a.job))
    files = {}
    for pfx, n in job_refs(job):
        if pfx == "job:" and safe_name(n):
            p = os.path.join(a.dir or here, n)
            if not os.path.isfile(p):
                raise SystemExit(f"job:{n}: no file {p}")
            files[n] = p
    for s in job.get("steps") or []:                 # the sha256 of a job: module or image, when missing
        for f in ("module", "image"):
            v = s.get(f) if isinstance(s, dict) else None
            if isinstance(v, str) and v.startswith("job:") and v[4:] in files and not s.get("sha256"):
                s["sha256"] = hashlib.sha256(open(files[v[4:]], "rb").read()).hexdigest()
    probs = lint(job)
    total = sum(os.path.getsize(p) for p in files.values())
    if len(files) + 1 > MAX_FILES or total > MAX_JOB or any(os.path.getsize(p) > MAX_FILE for p in files.values()):
        probs.append(f"files: at most {MAX_FILES}, {MAX_FILE >> 20} MB each, {MAX_JOB >> 20} MB in all")
    for p in probs:
        print("REFUSED:", p)
    if probs:
        return 2
    if a.dry_run:
        print(json.dumps(job, indent=1))
        return 0
    root = repo_root()
    jid = job["id"]
    with tempfile.TemporaryDirectory() as td:
        wt = os.path.join(td, "inbox")
        for attempt in range(5):
            tip = fetch_branch(root)
            if tip is None:
                raise SystemExit(f"no {BRANCH} branch yet: the runner's inbox isn't set up")
            if ls_tree(tip, f"jobs/{jid}", cwd=root) or ls_tree(tip, f"results/{jid}", cwd=root):
                raise SystemExit(f"id {jid} is taken in the inbox: pick a new id")
            if os.path.isdir(wt):
                git("worktree", "remove", "--force", wt, cwd=root, check=False)
            git("worktree", "add", "--detach", "--quiet", wt, tip, cwd=root)
            try:
                d = os.path.join(wt, "jobs", jid)
                os.makedirs(d)
                with open(os.path.join(d, "job.json"), "w") as f:
                    json.dump(job, f, indent=1)
                    f.write("\n")
                for n, p in files.items():
                    os.makedirs(os.path.dirname(os.path.join(d, n)), exist_ok=True)
                    shutil.copyfile(p, os.path.join(d, n))
                git("add", "jobs/" + jid, cwd=wt)
                git("commit", "--quiet", "-m", f"bench job {jid} by {job['who']}", cwd=wt)
                r = git("push", "--quiet", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=wt, check=False)
                if r.returncode == 0:
                    print(f"queued {jid} in {BRANCH}: the runner picks it up within a minute or two;"
                          f" python3 tools/bench_inbox.py show {jid}")
                    return 0
                err = r.stderr.decode(errors="replace").strip()
            finally:
                git("worktree", "remove", "--force", wt, cwd=root, check=False)
            print(f"push refused ({err.splitlines()[-1] if err else '?'}), retrying", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    raise SystemExit("push kept failing")


def show_text(root, tip, jid, lines=40):
    try:
        res = json.loads(git("show", f"{tip}:results/{jid}/result.json", cwd=root).stdout)
    except InboxError:
        if ls_tree(tip, f"jobs/{jid}", cwd=root):
            return None, f"{jid}: in the inbox, not picked up yet (the runner fetches every minute while idle)"
        return None, f"no job {jid} in the inbox"
    txt = [json.dumps(res, indent=1)]
    r = git("show", f"{tip}:results/{jid}/files.json", cwd=root, check=False)
    if r.returncode == 0:
        for f in json.loads(r.stdout):
            txt.append(f"  {'back' if f['returned'] else 'hamlet'}  {f['bytes']:>11}  {f['name']}")
    r = git("show", f"{tip}:results/{jid}/runner.log", cwd=root, check=False)
    if r.returncode == 0:
        txt.append(f"--- runner.log (last {lines} lines)")
        txt.append("".join(r.stdout.decode(errors="replace").splitlines(True)[-lines:]).rstrip())
    return res, "\n".join(txt)


def cmd_show(a):
    root = repo_root()
    end = time.monotonic() + 60 * a.wait
    while True:
        res, txt = show_text(root, fetch_branch(root), a.id, a.lines)
        if not a.wait or (res and res.get("status") in FINAL) or time.monotonic() > end:
            print(txt)
            return 0 if res else 1
        time.sleep(30)


def cmd_get(a):
    root = repo_root()
    tip = fetch_branch(root)
    os.makedirs(a.dest, exist_ok=True)
    n = 0
    for mode, typ, sha, size, name in ls_tree(tip, f"results/{a.id}", cwd=root):
        if typ == "blob" and safe_name(name):
            p = os.path.join(a.dest, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(blob(sha, cwd=root))
            n += 1
    print(f"{n} files from results/{a.id} into {a.dest}")
    return 0 if n else 1


def cmd_status(a):
    root = repo_root()
    tip = fetch_branch(root)
    r = git("show", f"{tip}:status.json", cwd=root, check=False)
    print(r.stdout.decode() if r.returncode == 0 else "no status.json yet: the runner hasn't written to the inbox")
    rows = []
    for mode, typ, sha, size, name in ls_tree(tip, "results", cwd=root):
        if name.endswith("/result.json"):
            try:
                res = json.loads(blob(sha, cwd=root))
            except ValueError:
                continue
            rows.append((res.get("updated", ""), name.split("/")[0], res.get("status"), res.get("reason", "")))
    for u, jid, s, why in sorted(rows)[-a.last:]:
        print(f"{u}  {s:9s} {jid}" + (f": {why[:120]}" if why else ""))
    return 0


def cmd_cancel(a):
    root = repo_root()
    with tempfile.TemporaryDirectory() as td:
        wt = os.path.join(td, "inbox")
        for attempt in range(5):
            tip = fetch_branch(root)
            if not ls_tree(tip, f"jobs/{a.id}", cwd=root) and not ls_tree(tip, f"results/{a.id}", cwd=root):
                raise SystemExit(f"no job {a.id} in the inbox")   # a repeat (ID-rN) has only results/ID-rN
            git("worktree", "add", "--detach", "--quiet", wt, tip, cwd=root)
            try:
                os.makedirs(os.path.join(wt, "jobs", a.id), exist_ok=True)
                open(os.path.join(wt, "jobs", a.id, "cancel"), "w").write(f"{utcnow()}\n")
                git("add", f"jobs/{a.id}/cancel", cwd=wt)
                git("commit", "--quiet", "-m", f"bench job {a.id}: cancel", cwd=wt)
                if git("push", "--quiet", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=wt, check=False).returncode == 0:
                    print(f"asked: the runner drops {a.id} if it waits, or stops it within about a minute")
                    return 0
            finally:
                git("worktree", "remove", "--force", wt, cwd=root, check=False)
            time.sleep(2 * (attempt + 1))
    raise SystemExit("push kept failing")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    s.add_argument("job")
    s.add_argument("--dir", help="where the job:NAME files are (default: beside JOB.json)")
    s.add_argument("--dry-run", action="store_true", help="lint and print the job, push nothing")
    s.set_defaults(fn=cmd_submit)
    s = sub.add_parser("show")
    s.add_argument("id")
    s.add_argument("--lines", type=int, default=40)
    s.add_argument("--wait", type=float, default=0, help="minutes to wait for a final status (0)")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("get")
    s.add_argument("id")
    s.add_argument("dest")
    s.set_defaults(fn=cmd_get)
    s = sub.add_parser("status")
    s.add_argument("--last", type=int, default=15)
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("cancel")
    s.add_argument("id")
    s.set_defaults(fn=cmd_cancel)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
