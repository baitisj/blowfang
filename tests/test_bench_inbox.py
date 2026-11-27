"""tools/bench_inbox.py: the inbox rules, a job pushed by a thread, picked up, run and answered in
the branch, and the runner's self-update, against a local bare repository standing in for
GitHub (no network, no radios)."""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
sys.path.insert(0, TOOLS)
br = sys.modules.get("bench_runner")             # one copy for both test files: the inbox binds to it
if br is None:
    spec = importlib.util.spec_from_file_location("bench_runner", os.path.join(TOOLS, "bench_runner.py"))
    br = importlib.util.module_from_spec(spec)
    sys.modules["bench_runner"] = br
    spec.loader.exec_module(br)
bi = br.bi
REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
KEY = b"0123456789abcdef"


def git(*a, cwd=None):
    return subprocess.run(["git"] + list(a), cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def origin_tpl(tmp_path_factory):
    """a bare 'GitHub' with this checkout's HEAD as main and an empty inbox branch"""
    d = tmp_path_factory.mktemp("origin")
    o = str(d / "origin.git")
    git("init", "--bare", "-q", o)
    m = str(d / "main")                       # main: this checkout's tools, modem and UV-380 modules
    git("init", "-q", m)
    arch = subprocess.run(["git", "-C", REPO, "archive", "HEAD", "tools", "modem", "firmware/uv380-openrtx/mod"],
                          check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", m], input=arch, check=True)
    git("add", "-A", cwd=m)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "main", cwd=m)
    git("push", "-q", o, "HEAD:refs/heads/main", cwd=m)
    seed = str(d / "seed")
    git("init", "-q", seed)
    git("checkout", "-q", "--orphan", bi.BRANCH, cwd=seed)
    open(os.path.join(seed, "README.md"), "w").write("inbox\n")
    git("add", "README.md", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "inbox", cwd=seed)
    git("push", "-q", o, f"HEAD:refs/heads/{bi.BRANCH}", cwd=seed)
    return o


@pytest.fixture
def env(tmp_path, monkeypatch, origin_tpl):
    o = str(tmp_path / "origin.git")
    git("clone", "-q", "--bare", "--mirror", origin_tpl, o)
    w = tmp_path / "work"
    (w / "state").mkdir(parents=True)
    monkeypatch.setenv("BLOWFANG_WORK", str(w))
    monkeypatch.setenv("BLOWFANG_REPO", REPO)
    (w / "k").write_text(KEY.hex() + "\n")
    monkeypatch.setenv("BLOWFANG_KEY", str(w / "k"))
    monkeypatch.setenv("K6_CABLE", "/dev/k6cable")
    monkeypatch.setenv("BENCH_INBOX_URL", o)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))          # install --unit stays in here
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"),
                 ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(k, v)
    client = str(tmp_path / "client")
    git("clone", "-q", o, client)
    br.STOPPING.clear()
    return {"origin": o, "work": w, "client": client, "tmp": tmp_path}


def submit(env, monkeypatch, job, files=()):
    d = env["tmp"] / ("j-" + job["id"])
    d.mkdir()
    for n, data in files:
        (d / n).write_bytes(data)
    (d / "job.json").write_text(json.dumps(job))
    monkeypatch.chdir(env["client"])
    return bi.main(["submit", str(d / "job.json")])


def result(env, jid):
    git("fetch", "-q", "origin", f"+refs/heads/{bi.BRANCH}:refs/remotes/origin/{bi.BRANCH}", cwd=env["client"])
    r = subprocess.run(["git", "show", f"origin/{bi.BRANCH}:results/{jid}/result.json"], cwd=env["client"],
                       capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


def J(jid, *steps, **kw):
    return dict(dict(id=jid, who="t1", freq_mhz=440.850, minutes=10), steps=list(steps), **kw)


# ---- the rules ----

def test_remote_rules():
    ok = J("a", {"do": "pluto", "args": ["send", "job:x.wav", "--gain", "-60"]})
    assert bi.remote_rules(ok, "a") == []
    assert "folder's name" in " ".join(bi.remote_rules(ok, "b"))
    for step, why in (({"do": "k6.flash", "image": "job:k.bin"}, "local submit"),
                      ({"do": "uv380.flash", "image": "job:u.bin"}, "local submit"),
                      ({"do": "k6.upload", "module": "job:m.c"}, "built module"),
                      ({"do": "k6.upload", "module": "job:m.bin"}, "sha256"),
                      ({"do": "pluto", "args": ["send", "/home/baitisj/blowfang-work/x.wav"]}, "hamlet paths"),
                      ({"do": "pluto", "args": ["send", "--out=~/x"]}, "hamlet paths"),
                      ({"do": "pluto", "args": ["send", "job:../x.wav"]}, "plain relative"),
                      ({"do": "script", "args": []}, "'do' must be")):
        assert why in " ".join(bi.remote_rules(J("a", step), "a")), step
    for k, v in (("checkout", "/x"), ("sources", "/x"), ("inbox", {}), ("take_serial", True)):
        assert "runner sets it" in " ".join(bi.remote_rules(dict(ok, **{k: v}), "a"))
    assert "only for the module steps" in " ".join(bi.remote_rules(dict(ok, sources_commit="abcdef1"), "a"))
    assert "7-40" in " ".join(bi.remote_rules(dict(ok, tools_commit="main"), "a"))


def test_scan_sources(tmp_path):
    (tmp_path / "mod").mkdir()
    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "x.h").write_text("int x;\n")
    (tmp_path / "mod" / "a.h").write_text("#include \"../common/x.h\"\n")
    (tmp_path / "mod" / "ok.c").write_text("#include \"ortx_mod.h\"\n#include <stdint.h>\n#include \"a.h\"\n")
    assert bi.scan_sources(str(tmp_path), "mod/ok.c") == []
    for body, why in (("#embed \"/home/baitisj/.ssh/id\"\n", "embed"),
                      ("asm(\".incbin \\\"/etc/passwd\\\"\");\n", "incbin"),
                      ("#include \"/home/baitisj/.config/sotto/k6-mod.key\"\n", "absolute"),
                      ("#include \"../../../../../home/x\"\n", "climbs"),
                      ("#define F \"x.h\"\n#include F\n", "computed")):
        (tmp_path / "mod" / "bad.c").write_text(body)
        assert why in " ".join(bi.scan_sources(str(tmp_path), "mod/bad.c")), body
    outside = tmp_path.parent / "secret.h"
    outside.write_text("k\n")
    (tmp_path / "mod" / "bad.c").write_text(f"#include \"../../{outside.name}\"\n")
    assert "outside the sources tree" in " ".join(bi.scan_sources(str(tmp_path), "mod/bad.c"))


def test_materialize_rewrites_names():
    j = J("a", {"do": "pluto", "args": ["rx", "run:m.cf32", "--out=run:o.json"]},
          {"do": "k6.upload", "module": "job:m.bin", "sha256": "0" * 64, "args": ["--via", "pluto"]},
          tools_commit="abcdef1")
    m = bi.materialize(j, "/w/runs/inbox/a", "/w/runs/queue/a", "/w/worktrees/inbox-x", None, {"commit": "c"})
    assert m["steps"][0]["args"] == ["rx", "/w/runs/queue/a/m.cf32", "--out=/w/runs/queue/a/o.json"]
    assert m["steps"][1]["module"] == "/w/runs/inbox/a/m.bin" and m["checkout"] == "/w/worktrees/inbox-x"
    assert "tools_commit" not in m and m["inbox"] == {"commit": "c"} and "sources" not in m


# ---- a job's round trip ----

def test_job_round_trip(env, monkeypatch):
    assert submit(env, monkeypatch, J("p1", {"do": "pluto", "args": ["send", "job:x.wav", "--gain", "-60"]}),
                  [("x.wav", b"RIFF....")]) == 0
    ib = bi.Inbox()
    ib.tick(idle=False)
    q = br.waiting()
    assert [j["id"] for j in q] == ["p1"], br.get_json(os.path.join(br.qdir("refused"), "p1.json"))
    jj = q[0]
    stage = os.path.join(br.work(), "runs", "inbox", "p1", "x.wav")
    assert jj["steps"][0]["args"][1] == stage and open(stage, "rb").read() == b"RIFF...."
    assert os.path.basename(jj["checkout"]).startswith("inbox-") and jj["inbox"]["path"] == "jobs/p1"
    assert git("rev-parse", "HEAD", cwd=jj["checkout"]) == git("rev-parse", "main", cwd=env["client"].replace("client", "origin.git"))
    assert result(env, "p1")["status"] == "queued"

    def command(job, step, rd):
        open(os.path.join(rd, "cap.txt"), "w").write("captured\n")
        return [sys.executable, "-c", "print('sent ident')"], rd, False
    monkeypatch.setattr(br, "command", command)
    monkeypatch.setattr(br, "checkout_clean", lambda co: (True, "abc1234"))
    started = []
    orig = ib.started
    ib.started = lambda job: started.append(job["id"]) or orig(job)
    assert br.serve_once(claim=lambda *a, **k: None, release=lambda *a: None, on_start=ib.started)
    ib.finished()
    assert started == ["p1"]
    ib.tick(idle=False)
    res = result(env, "p1")
    assert ib.error is None
    assert res["status"] == "done" and res["inbox"]["path"] == "jobs/p1"
    show, txt = bi.show_text(env["client"], bi.fetch_branch(env["client"]), "p1")
    assert "sent ident" in txt and "back" in txt and "cap.txt" in txt
    out = env["tmp"] / "got"
    monkeypatch.chdir(env["client"])
    assert bi.main(["get", "p1", str(out)]) == 0
    assert (out / "files" / "cap.txt").read_text() == "captured\n"
    status = json.loads(git("show", f"origin/{bi.BRANCH}:status.json", cwd=env["client"]))
    assert status["running"] is None and status["inbox"] is True
    # nothing new: no new commit
    tip = git("rev-parse", f"refs/heads/{bi.BRANCH}", cwd=env["origin"])
    ib.tick(idle=False)
    assert git("rev-parse", f"refs/heads/{bi.BRANCH}", cwd=env["origin"]) == tip


def test_refusals_come_back(env, monkeypatch):
    bad = J("f1", {"do": "k6.update", "image": "job:k.bin", "sha256": "0" * 64, "commit": "8a78fb5"})
    assert submit(env, monkeypatch, bad, [("k.bin", KEY)]) == 2            # the client lints it first
    d = env["tmp"] / "raw"                                                  # pushed by hand anyway
    git("fetch", "-q", "origin", bi.BRANCH, cwd=env["client"])
    git("worktree", "add", "-q", "--detach", str(d), "FETCH_HEAD", cwd=env["client"])
    (d / "jobs" / "f1").mkdir(parents=True)
    (d / "jobs" / "f1" / "job.json").write_text(json.dumps(bad))
    (d / "jobs" / "gain").mkdir()
    (d / "jobs" / "gain" / "job.json").write_text(json.dumps(J("gain", {"do": "pluto", "args": ["send", "job:x.wav", "--gain", "-10"]})))
    (d / "jobs" / "gain" / "x.wav").write_bytes(b"RIFF")
    git("add", "-A", cwd=str(d))
    git("commit", "-qm", "x", cwd=str(d))
    git("push", "-q", "origin", f"HEAD:refs/heads/{bi.BRANCH}", cwd=str(d))
    bi.Inbox().tick(idle=False)
    r = result(env, "f1")
    assert r["status"] == "refused" and "local submit" in r["reason"]
    r = result(env, "gain")                                                 # the runner's own check
    assert r["status"] == "refused" and "above -30" in r["reason"]
    assert br.waiting() == []


def test_repeats_bring_their_own_outputs_back_and_cancel(env, monkeypatch):
    import time
    until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3 * 3600))
    j = J("rc", {"do": "pluto", "args": ["rx", "run:cap.cf32", "--hz", "441900000"]},
          repeat={"every_min": 10, "until": until})
    assert submit(env, monkeypatch, j) == 0
    ib = bi.Inbox()
    ib.tick(idle=False)

    def command(job, step, rd):
        out = step["args"][1]
        return [sys.executable, "-c", f"open({out!r}, 'w').write({job['id']!r}); "
                f"open({os.path.join(rd, 'cap.json')!r}, 'w').write({job['id']!r})"], rd, False
    monkeypatch.setattr(br, "command", command)
    monkeypatch.setattr(br, "checkout_clean", lambda co: (True, "abc1234"))
    run = lambda: br.serve_once(claim=lambda *a, **k: None, release=lambda *a: None,
                                now=lambda: time.time() + 3600)    # past the repeat's not_before
    for n in ("rc", "rc-r1"):
        assert run()
        ib.tick(idle=False)
        assert result(env, n)["status"] == "done"
        out = env["tmp"] / ("got-" + n)
        monkeypatch.chdir(env["client"])
        assert bi.main(["get", n, str(out)]) == 0
        assert (out / "files" / "cap.json").read_text() == n            # each run's own text came back
        assert not (out / "files" / "cap.cf32").exists()                  # the capture stays on hamlet
        listed = {f["name"]: f for f in json.loads((out / "files.json").read_text())}
        assert listed["cap.cf32"]["returned"] is False and "not text" in listed["cap.cf32"]["kept"]
        assert listed["cap.cf32"]["hamlet"].endswith(os.path.join(n, "cap.cf32"))
    # cancel a repeat by its own id: it has only results/rc-r2 in the branch
    assert result(env, "rc-r2")["status"] == "queued"
    assert bi.main(["cancel", "rc-r2"]) == 0
    ib.next_poll = 0
    ib.tick(idle=False)
    assert os.path.exists(br.cancel_path("rc-r2"))
    assert br.waiting()[0]["id"] == "rc-r2"                           # still the queued repeat, not refused
    assert not run() and result(env, "rc-r2") and not os.path.exists(os.path.join(br.qdir("refused"), "rc-r2.json"))
    ib.tick(idle=False)
    assert result(env, "rc-r2")["status"] == "cancelled"


def test_only_small_text_comes_back():
    assert bi.return_why("a/levels.json", 100, bi.RETURN_JOB_MAX) is None
    assert bi.return_why("k6.LOG", 100, bi.RETURN_JOB_MAX) is None
    for name in ("y.wav", "c.flac", "p.cf32", "r.riq", "fw.itb", "region.bin", "noext"):
        assert "not text" in bi.return_why(name, 100, bi.RETURN_JOB_MAX)
    assert "KB" in bi.return_why("big.log", bi.RETURN_FILE_MAX + 1, bi.RETURN_JOB_MAX)
    assert "job's" in bi.return_why("x.log", 100, 99)


def test_cancel_by_first_id_stops_the_series(env, monkeypatch):
    import time
    until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3 * 3600))
    assert submit(env, monkeypatch, J("sr", {"do": "wait", "seconds": 0}, repeat={"every_min": 10, "until": until})) == 0
    ib = bi.Inbox()
    ib.tick(idle=False)
    monkeypatch.setattr(br, "checkout_clean", lambda co: (True, "abc1234"))
    assert br.serve_once(claim=lambda *a, **k: None, release=lambda *a: None)
    ib.tick(idle=False)
    assert [j["id"] for j in br.waiting()] == ["sr-r1"]
    monkeypatch.chdir(env["client"])
    assert bi.main(["cancel", "sr"]) == 0
    ib.next_poll = 0
    ib.tick(idle=False)
    assert os.path.exists(br.cancel_path("sr-r1"))
    assert bi.cancel_folders("sr-r1", br.waiting()[0]) == {"sr-r1", "sr"}


def test_taken_id_and_cancel(env, monkeypatch):
    br.submit(dict(J("local1", {"do": "wait", "seconds": 0}), checkout=REPO))
    assert submit(env, monkeypatch, J("local1", {"do": "wait", "seconds": 0})) == 0
    assert submit(env, monkeypatch, J("w2", {"do": "wait", "seconds": 0})) == 0
    ib = bi.Inbox()
    ib.tick(idle=False)
    assert result(env, "local1")["status"] == "refused" and "taken on hamlet" in result(env, "local1")["reason"]
    assert br.get_json(os.path.join(br.qdir("incoming"), "local1.json")).get("inbox") is None   # untouched
    assert result(env, "w2")["status"] == "queued"
    monkeypatch.chdir(env["client"])
    assert bi.main(["cancel", "w2"]) == 0
    ib.next_poll = 0
    ib.tick(idle=False)
    assert os.path.exists(br.cancel_path("w2"))
    monkeypatch.setattr(br, "checkout_clean", lambda co: (True, "abc1234"))
    while br.serve_once(claim=lambda *a, **k: None, release=lambda *a: None):
        pass
    ib.tick(idle=False)
    assert result(env, "w2")["status"] == "cancelled"


def test_sources_commit_must_be_pushed_and_clean(env, monkeypatch):
    j = J("m1", {"do": "uv380.m17", "args": ["bert"]}, freq_mhz=441.850, sources_commit="1234567")
    assert submit(env, monkeypatch, j) == 0
    main = git("rev-parse", "main", cwd=env["origin"])
    j2 = J("m2", {"do": "uv380.m17", "args": ["bert"]}, freq_mhz=441.850, sources_commit=main[:12])
    assert submit(env, monkeypatch, j2) == 0
    bi.Inbox().tick(idle=False)
    assert "not on any pushed branch" in result(env, "m1")["reason"]
    assert result(env, "m2")["status"] == "queued", result(env, "m2")
    q = br.waiting()[0]
    assert os.path.basename(q["sources"]) == f"src-{main[:12]}" and br.job_sources(q) == os.path.realpath(q["sources"])


# ---- self-update ----

def runner_files_installed(env, text=b"old\n"):
    b = os.path.join(br.work(), "bin")
    os.makedirs(b, exist_ok=True)
    for f in bi.RUNNER_FILES:
        open(os.path.join(b, f), "wb").write(text)


def test_self_update_gated_by_selftest(env, monkeypatch):
    runner_files_installed(env)
    ib = bi.Inbox()
    ib.fetch()
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if "selftest" in argv:
            return subprocess.CompletedProcess(argv, 1, stdout="ok    x\nFAIL  runs: something\nFAIL (1)\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
    assert ib.maybe_update(run=run) is None
    assert any("selftest" in c for c in calls) and not any("install" in c for c in calls)
    assert "not installed" in ib.update_note and open(os.path.join(br.work(), "bin", "k6_bench.py"), "rb").read() == b"old\n"
    calls.clear()
    assert ib.maybe_update(run=run) is None and calls == []              # the same main isn't retried


def test_self_update_installs_and_execs(env, monkeypatch):
    runner_files_installed(env)
    ib = bi.Inbox()
    ib.fetch()
    main = ib.tip("main")

    def run(argv, **kw):
        if "selftest" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="ok    x\nPASS\n", stderr="")
        if "install" in argv:
            return subprocess.run(argv, **kw)
        return subprocess.CompletedProcess(argv, 0, "", "")
    assert ib.maybe_update(run=run) == "exec"
    b = os.path.join(br.work(), "bin")
    wt = os.path.join(br.work(), "worktrees", f"inbox-{main[:12]}")
    assert open(os.path.join(b, "bench_runner.py"), "rb").read() == open(os.path.join(wt, "tools", "bench_runner.py"), "rb").read()
    assert bi.installed_commit(br) == main
    assert open(os.path.join(b, "previous", "k6_bench.py"), "rb").read() == b"old\n"
    assert ib.maybe_update(run=run) is None                               # up to date now


def test_self_update_waits_for_idle(env):
    runner_files_installed(env)
    ib = bi.Inbox()
    ib.fetch()
    br.submit(dict(J("busy", {"do": "wait", "seconds": 0}), checkout=REPO))
    assert ib.maybe_update(run=lambda *a, **k: pytest.fail("ran while a job waits")) is None
    os.remove(os.path.join(br.qdir("incoming"), "busy.json"))
    open(br.qdir("PAUSE"), "w").write("Jeff")
    assert ib.maybe_update(run=lambda *a, **k: pytest.fail("ran while paused")) is None


def test_inbox_off_does_nothing(env):
    open(os.path.join(br.work(), "state", "inbox.off"), "w").write("x")
    ib = bi.Inbox()
    assert ib.tick() is None and not os.path.exists(ib.gd())


def test_network_errors_back_off(env, monkeypatch):
    monkeypatch.setenv("BENCH_INBOX_URL", str(env["tmp"] / "nowhere.git"))
    ib = bi.Inbox()
    assert ib.tick(idle=False) is None and ib.error and ib.backoff == 2 * bi.POLL_S


def test_self_update_new_unit_restarts(env, monkeypatch):
    runner_files_installed(env)
    unit = os.path.join(os.environ["HOME"], ".config", "systemd", "user", "blowfang-bench-runner.service")
    os.makedirs(os.path.dirname(unit))
    open(unit, "w").write("[Unit]\nDescription=old\n")
    ib = bi.Inbox()
    ib.fetch()
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if "selftest" in argv:
            return subprocess.CompletedProcess(argv, 0, stdout="PASS\n", stderr="")
        if "install" in argv:
            assert "--unit" in argv
            return subprocess.run(argv, **kw)
        return subprocess.CompletedProcess(argv, 0, "", "")
    assert ib.maybe_update(run=run) == "restart"
    assert ["systemctl", "--user", "daemon-reload"] in calls and "ExecStart" in open(unit).read()
