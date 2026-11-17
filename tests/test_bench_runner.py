"""tools/bench_runner.py: the job checks (the bench rules in code), the queue, and a run against a
fake rig and stand-in commands (no rigctld, no radios, no serial port)."""
import importlib.util
import json
import os
import subprocess
import sys
import time

import pytest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
sys.path.insert(0, TOOLS)
spec = importlib.util.spec_from_file_location("bench_runner", os.path.join(TOOLS, "bench_runner.py"))
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)
kb = br.kb
REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
KEY = b"0123456789abcdef"


@pytest.fixture(autouse=True)
def work(tmp_path, monkeypatch):
    w = tmp_path / "work"
    w.mkdir()
    monkeypatch.setenv("BLOWFANG_WORK", str(w))
    monkeypatch.setenv("BLOWFANG_REPO", REPO)
    (w / "k").write_bytes(KEY)
    monkeypatch.setenv("BLOWFANG_KEY", str(w / "k"))
    monkeypatch.setenv("K6_CABLE", "/dev/k6cable")
    monkeypatch.setattr(br, "checkout_clean", lambda co: (True, "abc1234"))
    br.STOPPING.clear()
    return w


def stage(work, name, data):
    p = work / name
    p.write_bytes(data)
    return str(p), br.sha256(str(p))


def job(jid="j1", *steps, **kw):
    return dict(dict(id=jid, who="t1", freq_mhz=440.850, minutes=10, checkout=REPO, steps=list(steps)), **kw)


def problems(j):
    return " | ".join(br.check_job(j))


# ---- the checks ----

def test_keyed_image_ota_ok(work):
    p, h = stage(work, "k.bin", b"\0" * 50 + KEY + b"\0" * 50)
    assert br.check_job(job("a", {"do": "k6.update", "image": p, "sha256": h, "commit": "8a78fb5"})) == []


def test_unkeyed_image_ota_refused(work):
    p, h = stage(work, "u.bin", b"\0" * 50 + br.PLACEHOLDER + b"\0" * 50)
    assert "not the keyed build" in problems(job("a", {"do": "k6.update", "image": p, "sha256": h, "commit": "8a78fb5"}))


def test_cable_flash_takes_unkeyed_or_ours_not_others(work):
    u, hu = stage(work, "u.bin", b"\0" * 50 + br.PLACEHOLDER)
    k, hk = stage(work, "k.bin", b"\0" * 50 + KEY)
    o, ho = stage(work, "o.bin", b"\0" * 50 + b"X" * 16)
    for p, h in ((u, hu), (k, hk)):
        assert br.check_job(job("a", {"do": "k6.flash", "image": p, "sha256": h, "commit": "8a78fb5"})) == []
    assert "another key" in problems(job("a", {"do": "k6.flash", "image": o, "sha256": ho, "commit": "8a78fb5"}))


@pytest.mark.parametrize("c", ["acd06ba", "a1a86f9", "35e982f", "35e982f0123"])
def test_never_flash(work, c):
    p, h = stage(work, "k.bin", KEY)
    assert "never-flash" in problems(job("a", {"do": "k6.update", "image": p, "sha256": h, "commit": c}))


def test_flash_needs_commit_and_matching_sha(work):
    p, h = stage(work, "k.bin", KEY)
    assert "needs commit" in problems(job("a", {"do": "k6.update", "image": p, "sha256": h}))
    assert "sha256 differs" in problems(job("a", {"do": "k6.update", "image": p, "sha256": "0" * 64, "commit": "8a78fb5"}))


def test_bundle_checks(work):
    d = work / "bundle"
    d.mkdir()
    (d / "aura-voice-explore.bin").write_bytes(b"\0" * 10 + br.PLACEHOLDER)
    (d / "mod-x.bin").write_bytes(b"m")
    m = dict(format=1, build="8a78fb5-20261117T0900Z", image="aura-voice-explore.bin",
             sha256={f: br.sha256(str(d / f)) for f in ("aura-voice-explore.bin", "mod-x.bin")})
    (d / "manifest.json").write_text(json.dumps(m))
    st = {"do": "k6.install", "bundle": str(d), "manifest_sha256": br.sha256(str(d / "manifest.json")),
          "commit": "8a78fb5", "args": ["--no-backup", "--verify"]}
    assert br.check_job(job("a", st)) == []
    assert "built from 8a78fb5" in problems(job("a", dict(st, commit="40a2ec9")))
    assert "only --verify" in problems(job("a", dict(st, args=["--fresh"])))
    (d / "mod-x.bin").write_bytes(b"changed")
    assert "doesn't match the bundle" in problems(job("a", st))


def test_pluto_rules(work):
    w, _ = stage(work, "x.wav", b"RIFF")
    ok = lambda *a: br.check_job(job("a", {"do": "pluto", "args": list(a)}))
    assert ok("send", w) == []
    assert ok("send", w, "--gain", "-30") == []
    assert "above -30" in " ".join(ok("send", w, "--gain", "-29"))
    assert "above -30" in " ".join(ok("steps", w, "--gains", "-70,-40,-20"))
    assert "outside 430.010" in " ".join(ok("send", w, "--hz", "146520000"))
    assert "outside the job's claim" in " ".join(ok("send", w, "--hz", "441850000"))
    assert ok("rx", str(work / "r.wav"), "--hz", "162550000") == []      # receive anywhere
    assert "one of" in " ".join(ok("iio", w))
    assert "runner's to set" in " ".join(ok("send", w, "--uri", "usb:1.2.5"))


def test_other_channel_claim(work):
    w, _ = stage(work, "x.wav", b"RIFF")
    assert br.check_job(job("a", {"do": "pluto", "args": ["send", w, "--hz", "441850000"]}, freq_mhz=441.850)) == []
    assert "70 cm channel" in problems(job("a", {"do": "wait", "seconds": 1}, freq_mhz=146.52))
    assert br.check_job(job("a", {"do": "wait", "seconds": 1}, freq_mhz="bench")) == []


def test_pluto_needs_keeper_fix(work, tmp_path):
    co = work / "worktrees" / "old"
    (co / "tools").mkdir(parents=True)
    (co / "modem").mkdir()
    (co / "tools" / "k6_bench.py").write_text("# before 3a72067\n")
    assert "keeper fix" in problems(job("a", {"do": "pluto", "args": ["quiet"]}, checkout=str(co)))


def test_no_device_paths_keys_or_outside_files(work):
    m, h = stage(work, "m.bin", b"m")
    up = lambda *a: problems(job("a", {"do": "k6.upload", "module": m, "sha256": h, "args": list(a)}))
    assert up("--via", "pluto") == ""
    assert "no device paths" in up("--device", "/dev/ttyUSB2")
    assert "runner's to set" in up("--key-file", "/home/x/k")
    assert "outside ~/blowfang-work" in up("--keep", "/tmp/x.raw")
    assert "above -30" in up("--via", "pluto", "--pluto-gain", "-10")
    assert "bench's rigctld" in up("--rigctld", "otherhost:4532")
    assert "runner's to set" in up("--south-of-line-a")
    assert "outside 430.010" in up("--freq", "146520000")


def test_ic7100_rules(work):
    w, _ = stage(work, "x.wav", b"RIFF")
    ic = lambda *a, **kw: problems(job("a", {"do": "ic7100", "args": list(a)}, **kw))
    assert ic("send", w, "--freq", "440850000") == ""
    assert "needs --freq" in ic("send", w)
    assert "no --no-id" in ic("send", w, "--freq", "440850000", "--no-id")
    assert "outside 0..0.5" in ic("id", ic7100_power_max=1.0)
    assert ic("id", ic7100_power_max=0.5) == ""
    assert ic("stop") == ""


def test_job_shape(work):
    assert "one of" in problems(job("a", {"do": "script", "args": ["go.sh"]}))
    assert "id:" in problems(job("Bad ID", {"do": "wait", "seconds": 1}))
    assert "who:" in problems(job("a", {"do": "wait", "seconds": 1}, who="two words"))
    assert "minutes" in problems(job("a", {"do": "wait", "seconds": 1}, minutes=999))
    assert "checkout" in problems(job("a", {"do": "wait", "seconds": 1}, checkout="/tmp"))
    assert "steps" in problems(job("a"))
    assert "seconds 1.." in problems(job("a", {"do": "logger", "seconds": 99999}))
    assert "plain name" in problems(job("a", {"do": "logger", "seconds": 9, "file": "../x"}))


def test_selftest_passes(work, capsys):
    assert br.main(["selftest"]) == 0
    assert "PASS" in capsys.readouterr().out


# ---- running ----

class Rig:
    def __init__(self, hz=440850000, power=0.0):
        self.hz, self.power, self.calls = hz, power, []

    def __call__(self, *a):
        a = [str(x) for x in a]
        self.calls.append(a)
        if a == ["f"]:
            return str(self.hz)
        if a == ["l", "RFPOWER"]:
            return f"{self.power:.6f}"
        if a[:2] == ["L", "RFPOWER"]:
            self.power = float(a[2])
        if a[0] == "F":
            self.hz = int(a[1])
        if a == ["m"]:
            return "PKTFM"
        return "RPRT 0"


def fake_command(cmds):
    def command(job, step, rd):
        return cmds[step["do"]], rd, False
    return command


def test_run_job_steps_and_power(work, monkeypatch):
    monkeypatch.setattr(br, "command", fake_command({"ic7100": [sys.executable, "-c", "print('sent')"]}))
    w, _ = stage(work, "x.wav", b"RIFF")
    rig = Rig(power=0.5)
    j = job("a", {"do": "ic7100.tune", "hz": 440850000}, {"do": "ic7100", "args": ["send", w, "--freq", "440850000"]})
    res = br.run_job(j, str(work / "rd"), rig)
    assert res["status"] == "done", res
    assert rig.power == 0.0 and ["L", "RFPOWER", "0"] in rig.calls
    assert "sent" in open(work / "rd" / "runner.log").read()


def test_ic7100_hold_and_frequency(work, monkeypatch):
    monkeypatch.setattr(br, "command", fake_command({"ic7100": [sys.executable, "-c", "pass"]}))
    w, _ = stage(work, "x.wav", b"RIFF")
    j = job("a", {"do": "ic7100", "args": ["send", w, "--freq", "440850000"]})
    os.makedirs(os.path.dirname(br.hold_path()), exist_ok=True)
    open(br.hold_path(), "w").write("Jeff on 20 m")
    res = br.run_job(j, str(work / "rd"), Rig())
    assert res["status"] == "failed" and "held for Jeff" in res["reason"]
    os.remove(br.hold_path())
    res = br.run_job(j, str(work / "rd2"), Rig(hz=14074000))
    assert res["status"] == "failed" and "outside 430.010" in res["reason"]
    assert br.run_job(j, str(work / "rd3"), Rig())["status"] == "done"


def test_failed_step_stops_the_job(work, monkeypatch):
    monkeypatch.setattr(br, "command", fake_command({"pluto": [sys.executable, "-c", "raise SystemExit(3)"]}))
    j = job("a", {"do": "pluto", "args": ["quiet"]}, {"do": "wait", "seconds": 0})
    res = br.run_job(j, str(work / "rd"), Rig())
    assert res["status"] == "failed" and len(res["steps"]) == 1 and res["steps"][0]["rc"] == 3
    j["steps"][0]["continue_on_error"] = True
    assert br.run_job(j, str(work / "rd2"), Rig())["status"] == "done"


def test_cancel_stops_the_step_with_sigterm(work, monkeypatch):
    marker = work / "got-term"
    code = ("import signal,sys,time\n"
            f"signal.signal(signal.SIGTERM, lambda *a: (open({str(marker)!r},'w').write('unkeyed'), sys.exit(0)))\n"
            "time.sleep(60)\n")
    monkeypatch.setattr(br, "command", fake_command({"pluto": [sys.executable, "-c", code]}))
    t0 = time.time()
    res = br.run_job(job("a", {"do": "pluto", "args": ["quiet"]}), str(work / "rd"), Rig(),
                     cancelled=lambda: time.time() - t0 > 1.0)
    assert res["status"] == "cancelled"
    assert marker.read_text() == "unkeyed"
    assert time.time() - t0 < 30


def test_changed_file_refused_at_run_time(work):
    p, h = stage(work, "k.bin", KEY)
    j = job("a", {"do": "k6.update", "image": p, "sha256": h, "commit": "8a78fb5"})
    open(p, "ab").write(b"x")
    res = br.run_job(j, str(work / "rd"), Rig())
    assert res["status"] == "failed" and "sha256 differs" in res["reason"]


def test_dirty_checkout_refused(work, monkeypatch):
    monkeypatch.setattr(br, "checkout_clean", lambda co: (False, "changed files: M tools/k6_upload.py"))
    res = br.run_job(job("a", {"do": "wait", "seconds": 0}), str(work / "rd"), Rig())
    assert res["status"] == "refused" and "not clean" in res["reason"]


def test_wait_for_pattern(work):
    rd = work / "rd"
    rd.mkdir()
    (rd / "k6.log").write_text("K6 BOOT build=1\n")
    assert br.run_job(job("a", {"do": "wait", "pattern": "K6 BOOT", "seconds": 5}), str(rd), Rig())["status"] == "done"
    r = br.run_job(job("a", {"do": "wait", "pattern": "NEVER", "seconds": 1}), str(rd), Rig())
    assert r["status"] == "failed"


# ---- the queue ----

def test_submit_refuses_and_queues(work):
    assert br.submit(job("a", {"do": "script"}))
    assert not os.listdir(br.qdir("incoming"))
    assert br.submit(job("a", {"do": "wait", "seconds": 0})) == []
    assert "already" in " ".join(br.submit(job("a", {"do": "wait", "seconds": 0})))


def test_serve_runs_claims_and_releases(work):
    br.submit(job("a", {"do": "wait", "seconds": 0}))
    seen = []
    claim = lambda who, m, what, hz=None: seen.append(("claim", who, hz))
    release = lambda who: seen.append(("release", who))
    assert br.serve_once(Rig(), claim=claim, release=release)
    assert seen == [("claim", "t1", 440850000), ("release", "t1")]
    assert os.path.exists(os.path.join(br.qdir("done"), "a.json"))
    assert json.load(open(os.path.join(br.run_dir("a"), "result.json")))["status"] == "done"
    assert not br.serve_once(Rig(), claim=claim, release=release)


def test_serve_waits_for_a_busy_channel_then_gives_up(work):
    kb.claim("other", 30, "busy", hz=440_850_000)
    br.submit(job("a", {"do": "wait", "seconds": 0}, wait_bench_min=1))
    assert not br.serve_once(Rig())
    assert os.path.exists(os.path.join(br.qdir("incoming"), "a.json"))
    later = lambda: time.time() + 120
    assert not br.serve_once(Rig(), now=later)
    assert os.path.exists(os.path.join(br.qdir("failed"), "a.json"))


def test_busy_channel_lets_another_channel_run(work):
    kb.claim("other", 30, "busy", hz=440_850_000)
    br.submit(job("a", {"do": "wait", "seconds": 0}))
    br.submit(job("b", {"do": "wait", "seconds": 0}, who="t2", freq_mhz=441.850))
    assert br.serve_once(Rig())
    assert os.path.exists(os.path.join(br.qdir("done"), "b.json"))
    assert os.path.exists(os.path.join(br.qdir("incoming"), "a.json"))
    assert [c["who"] for c in kb.claims()] == ["other"]


def test_pause_cancel_not_before_and_repeat(work):
    br.submit(job("a", {"do": "wait", "seconds": 0}, not_before="2099-01-01T00:00:00Z"))
    assert not br.serve_once(Rig())
    br.main(["cancel", "a", "--by", "test"])
    assert not br.serve_once(Rig())
    assert os.path.exists(os.path.join(br.qdir("cancelled"), "a.json"))
    br.main(["pause", "night"])
    until = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3 * 3600))
    br.submit(job("r", {"do": "wait", "seconds": 0}, repeat={"every_min": 45, "until": until}))
    assert not br.serve_once(Rig())
    br.main(["resume"])
    assert br.serve_once(Rig())
    nxt = json.load(open(os.path.join(br.qdir("incoming"), "r-r1.json")))
    assert br.parse_utc(nxt["not_before"]) - time.time() == pytest.approx(45 * 60, abs=10)


def test_stale_running_job_marked_failed(work, capsys, monkeypatch):
    br.ensure_dirs()
    br.put_json(br.qdir("running.json"), {"id": "x", "who": "t1", "since": "?", "job": job("x", {"do": "wait"})})
    monkeypatch.setattr(br, "serve_once", lambda: br.STOPPING.append(1) or False)
    br.main(["serve", "--every", "0"])
    assert "stopped during" in json.load(open(os.path.join(br.qdir("failed"), "x.json")))["result"]["reason"]


def test_command_lines(work):
    j = job("a")
    rd = str(work / "rd")
    m, h = stage(work, "m.bin", b"m")
    argv, _, bg = br.command(j, {"do": "k6.upload", "module": m, "args": ["--via", "pluto"]}, rd)
    assert argv[2] == os.path.realpath(m) and argv[3:5] == ["--key-file", br.key_path()] and not bg
    argv, _, bg = br.command(j, {"do": "logger", "seconds": 900, "file": "k6.log"}, rd)
    assert bg and argv[2:5] == ["serial-hold", "t1", "--"] and "/dev/k6cable" in argv
    argv, _, _ = br.command(j, {"do": "pluto", "args": ["quiet"]}, rd)
    assert argv[-3:] == ["--as", "t1", "quiet"]
    argv, _, _ = br.command(j, {"do": "k6.flash", "image": m}, rd)
    assert argv[2:6] == ["--port", "/dev/k6cable", "--key-file", br.key_path()] and argv[6] == "flash"


def test_unit_and_install(work):
    assert "bench_runner.py serve" in br.unit_text()
    br.main(["install"])
    for f in ("bench_runner.py", "k6_bench.py", "k6-reset-trigger.sh"):
        assert os.access(os.path.join(work, "bin", f), os.X_OK)
    out = subprocess.run([sys.executable, os.path.join(work, "bin", "bench_runner.py"), "status"],
                         capture_output=True, text=True, env=dict(os.environ))
    assert "NOT running" in out.stdout
