"""tools/k6_bench.py: the PTT lock, the CW ID keeper's rules and the state every keyer shares,
against a fake rig (no rigctld, no sound card, no serial port)."""
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time

import pytest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
spec = importlib.util.spec_from_file_location("k6_bench", os.path.join(TOOLS, "k6_bench.py"))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


@pytest.fixture(autouse=True)
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOWFANG_WORK", str(tmp_path))
    monkeypatch.setattr(b, "play", lambda wav, device=None, dry_run=False, max_s=None: None)
    return tmp_path


class Rig:
    """rigctl as the bench sees it; shift/split model the IC-7100's DUP and split (stuck: R ignored)"""

    def __init__(self, hz=440850000, shift="None", split="0", stuck=False):
        self.hz, self.ptt, self.calls = hz, 0, []
        self.shift, self.split, self.stuck = shift, split, stuck

    def __call__(self, *args):
        self.calls.append(" ".join(str(v) for v in args))
        if args[0] == "f":
            return str(self.hz)
        if args[0] == "t":
            return str(self.ptt)
        if args[0] == "T":
            self.ptt = int(args[1])
        if args[0] == "R" and not self.stuck:
            self.shift = {"+": "+", "-": "-"}.get(str(args[1]), "None")
        if args[0] == "S":
            self.split = str(args[1])
        if args[0] == "r":
            return self.shift
        if args[0] == "s":
            return f"{self.split}\nVFOA"
        return ""


KEY = ["t", "V VFOA", "S 0 VFOA", "R 0", "r", "s", "T 1", "T 0"]


def test_paths_follow_blowfang_work(work):
    assert b.work() == str(work)
    assert b.path("lock") == os.path.join(str(work), "state", "ptt.lock")
    assert b.path("cwid_log") == os.path.join(str(work), "logs", "k6-cwid.txt")


def test_default_is_blowfang_work_in_home(monkeypatch):
    monkeypatch.delenv("BLOWFANG_WORK")
    assert b.work() == os.path.expanduser("~/blowfang-work")


def test_key_and_play_records_data_and_id(work):
    r = Rig()
    b.key_and_play(r, "ctl.wav", what="ctl.wav")
    assert r.calls == KEY and r.ptt == 0
    assert b._read("last_tx") > 0 and b._read("last_id") == 0
    assert open(b.path("txlog")).read().split()[2] == "ctl.wav"
    b.key_and_play(r, "id.wav", kind="id", what="end of run", text="AG7EW")
    assert b._read("last_id") >= b._read("last_tx")
    assert "CW ID 'AG7EW' (end of run)" in open(b.path("cwid_log")).read()


def test_unkeys_when_play_fails(work, monkeypatch):
    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, "paplay")
    monkeypatch.setattr(b, "play", boom)
    r = Rig()
    with pytest.raises(subprocess.CalledProcessError):
        b.key_and_play(r, "x.wav")
    assert r.ptt == 0 and r.calls[-1] == "T 0"


def test_clears_repeater_shift_before_keying(work):
    r = Rig(shift="+", split="1")
    b.key_and_play(r, "x.wav")
    assert r.calls == KEY and r.shift == "None" and r.split == "0"


def test_refuses_when_shift_will_not_clear(work):
    r = Rig(shift="+", stuck=True)
    with pytest.raises(SystemExit):
        b.key_and_play(r, "x.wav")
    assert "T 1" not in r.calls and r.ptt == 0


def test_refuses_when_already_keyed():
    r = Rig()
    r.ptt = 1
    with pytest.raises(SystemExit):
        b.key_and_play(r, "x.wav")
    assert "T 1" not in r.calls


def test_dry_run_takes_no_lock_and_writes_no_state(work):
    b.key_and_play(Rig(), "x.wav", dry_run=True)
    assert not os.path.exists(os.path.join(str(work), "state"))


def test_lock_is_reentrant_and_shuts_out_other_processes(work):
    with b.ptt_lock():
        with b.ptt_lock():              # send's ID-first path nests the lock
            pass
        assert not b.lock_free()
        other = open(b.path("lock"), "a")    # another open file description: as another process
        with pytest.raises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        other.close()
    assert b.lock_free()


def test_lock_wait_gives_up(work):
    os.makedirs(os.path.dirname(b.path("lock")), exist_ok=True)
    holder = open(b.path("lock"), "a")
    fcntl.flock(holder, fcntl.LOCK_EX)
    t = time.monotonic()
    with pytest.raises(SystemExit):
        with b.ptt_lock(wait=0.3):
            pass
    assert time.monotonic() - t < 2
    holder.close()


def test_keyer_waits_for_the_lock(work):
    os.makedirs(os.path.dirname(b.path("lock")), exist_ok=True)
    holder = open(b.path("lock"), "a")
    fcntl.flock(holder, fcntl.LOCK_EX)
    r, done = Rig(), []
    th = threading.Thread(target=lambda: (b.key_and_play(r, "x.wav"), done.append(1)))
    th.start()
    time.sleep(0.3)
    assert not done and r.calls == []       # held off while the keeper's ID is on the air
    holder.close()
    th.join(5)
    assert done and r.calls == KEY


@pytest.mark.parametrize("since_tx, since_id, why", [
    (30, 600, "9-min periodic"),          # active, last ID 10 min ago
    (30, 300, None),                      # active, ID 5 min ago
    (130, 400, "end of session"),         # quiet 2 min, no ID since the last TX
    (60, 400, None),                      # quiet 1 min: not yet
    (130, 100, None),                     # an ID came after the last TX
    (5000, 9000, None),                   # long idle
])
def test_keeper_rules(since_tx, since_id, why):
    now = 1.8e9
    assert b.keeper_due(now, now - since_tx, now - since_id) == why


def test_never_keyed_means_no_id():
    assert b.keeper_due(1.8e9, 0.0, 0.0) is None


def test_bands():
    assert b.in_band(440850000) and b.in_band(430010000)
    assert not b.in_band(425000000) and not b.in_band(146520000)     # south of Line A; 2 m
    assert b.in_band(146520000, b.BANDS_ID)
    assert not b.in_band(121500000, b.BANDS_ID)                      # airband: never


def test_k6_cfg_freq(tmp_path):
    log = tmp_path / "k6.log"
    log.write_text("1.0\tK6 CFG why=boot freq=440850000 phy=F\n2.0\tK6 PKT n=1\n"
                   "3.0\tK6 CFG why=key freq=440875000\n")
    assert b.k6_cfg_hz(str(log)) == 440875000
    log.write_text("K6 PKT n=1\n")
    assert b.k6_cfg_hz(str(log)) is None


def _send(work, monkeypatch, r, *args):
    wav = os.path.join(str(work), "s.wav")
    b.write_wav(wav, [0.0] * 4800)
    monkeypatch.setattr(b, "rigctl", lambda rigctld=None: r)
    monkeypatch.setattr(b, "cwid_wav", lambda: "cwid.wav")
    return b.main(["send", wav, *args])


def test_send_ids_first_when_due(work, monkeypatch):
    r = Rig()
    _send(work, monkeypatch, r)
    assert r.calls.count("T 1") == 2                  # the CW ID, then the data
    assert "before a data send" in open(b.path("cwid_log")).read()
    r.calls.clear()
    _send(work, monkeypatch, r)
    assert r.calls.count("T 1") == 1                  # ID just went: data only


@pytest.mark.parametrize("hz, args", [(425000000, []), (146520000, []), (440875000, ["--freq", "440850000"])])
def test_send_refuses_off_frequency(work, monkeypatch, hz, args):
    r = Rig(hz)
    with pytest.raises(SystemExit):
        _send(work, monkeypatch, r, *args)
    assert "T 1" not in r.calls


def test_send_checks_the_k6_log(work, monkeypatch, tmp_path):
    log = tmp_path / "k6.log"
    log.write_text("K6 CFG freq=440875000\n")
    r = Rig()
    with pytest.raises(SystemExit):
        _send(work, monkeypatch, r, "--k6log", str(log))
    log.write_text("K6 CFG freq=440850000\n")
    _send(work, monkeypatch, r, "--k6log", str(log), "--no-id")
    assert r.calls.count("T 1") == 1


def test_send_refuses_a_long_wav(work, monkeypatch):
    with pytest.raises(SystemExit):
        _send(work, monkeypatch, Rig(), "--max-s", "0.05")


def test_installed_copy_needs_only_the_standard_library():
    src = open(os.path.join(TOOLS, "k6_bench.py")).read()
    top = [ln for ln in src.splitlines() if ln.startswith(("import ", "from "))]
    assert all(m.split()[1].split(".")[0] in sys.stdlib_module_names for m in top), top


def test_unit_runs_the_installed_copy(work):
    u = b.unit_text()
    assert f"ExecStart=/usr/bin/python3 {work}/bin/k6_bench.py idkeeper" in u
    assert f"Environment=BLOWFANG_WORK={work}" in u and "Restart=always" in u


def test_tools_key_through_the_bench():
    """every tool that keys the IC-7100 takes the shared lock; none keys on its own"""
    for f in ("k6_upload.py", "k6_ifcap.py", "k6_afsweep.py", "k6_rxmap.py"):
        s = open(os.path.join(TOOLS, f)).read()
        assert "bench.key" in s, f
        assert '"T", 1)' not in s, f


# ---- the bench owner ----

def test_bench_is_free_with_no_claim(work, monkeypatch):
    monkeypatch.delenv("K6_BENCH_AS", raising=False)
    assert b.owner() is None
    b.check_owner()
    b.key_and_play(Rig(), "x.wav")


def test_a_claim_shuts_out_other_senders(work, monkeypatch):
    b.claim("explore", 30, "DMR run")
    o = b.owner()
    assert o["who"] == "explore" and o["what"] == "DMR run"
    monkeypatch.delenv("K6_BENCH_AS", raising=False)
    r = Rig()
    with pytest.raises(SystemExit, match="held by explore"):
        b.key_and_play(r, "x.wav")
    monkeypatch.setenv("K6_BENCH_AS", "surf")
    with pytest.raises(SystemExit, match="you are surf"):
        _send(work, monkeypatch, r, "--no-id")
    assert "T 1" not in r.calls
    _send(work, monkeypatch, r, "--no-id", "--as", "explore")
    assert r.calls.count("T 1") == 1
    monkeypatch.setenv("K6_BENCH_AS", "explore")
    b.key_and_play(r, "x.wav")
    assert r.calls.count("T 1") == 2


def test_the_keeper_ids_whoever_holds_the_bench(work, monkeypatch):
    b.claim("explore", 30)
    monkeypatch.delenv("K6_BENCH_AS", raising=False)
    r = Rig()
    b.send_id(r, "9-min periodic")
    assert r.calls.count("T 1") == 1


def test_claim_rules(work):
    b.claim("explore", 10)
    with pytest.raises(SystemExit):
        b.claim("surf", 10)
    since = b.owner()["since"]
    b.claim("explore", 60, "longer")
    assert b.owner()["since"] == since and b.owner()["until"] - time.time() > 3500
    with pytest.raises(SystemExit):
        b.release("surf")
    assert b.release("explore") and b.owner() is None
    assert not b.release("explore")
    for bad in (("two words", 10), ("x", 0), ("x", 721)):
        with pytest.raises(SystemExit):
            b.claim(*bad)


def test_force_release_and_expiry(work):
    b.claim("explore", 10)
    assert b.release("jeff", force=True) and b.owner() is None
    b.claim("surf", 1)
    assert b.owner(now=time.time() + 61) is None
    b.claim("explore", 5, now=time.time() + 61)
    assert b.owner(now=time.time() + 62)["who"] == "explore"


def test_claims_are_logged(work):
    b.claim("explore", 10, "DMR run")
    b.release("explore")
    log = open(b.path("txlog")).read()
    assert "bench claimed by explore for 10 min: DMR run" in log and "bench released by explore" in log


def test_status_and_owner_say_who_holds_it(work, capsys, monkeypatch):
    monkeypatch.setenv("K6_BENCH_AS", "surf")
    b.main(["claim", "explore", "20", "DMR", "run"])
    b.main(["owner"])
    b.main(["status"])
    out = capsys.readouterr().out
    assert "explore until" in out and ": DMR run" in out
    assert "you are surf" in out and "bench owner   explore" in out


def test_channel_claims_share_the_bench(work, monkeypatch):
    k6, uv = 440_850_000, 441_000_000
    b.claim("fsk24", 30, "sweep", hz=k6)
    b.claim("c6phy", 30, "UV-380 tests", hz=uv)              # 150 kHz away: both hold
    assert {c["who"]: c["hz"] for c in b.claims()} == {"fsk24": k6, "c6phy": uv}
    with pytest.raises(SystemExit, match="440.850 MHz is held by fsk24"):
        b.claim("surf", 10, hz=440_900_000)                  # inside the 100 kHz guard
    with pytest.raises(SystemExit, match="every channel free"):
        b.claim("surf", 10)                                  # the whole bench: not while channels are held
    b.check_owner("fsk24", k6)
    b.check_owner("c6phy", uv)
    b.check_owner("surf", 445_950_000)                       # a free channel: anyone may key there
    with pytest.raises(SystemExit, match="you are c6phy"):
        b.check_owner("c6phy", k6)
    with pytest.raises(SystemExit, match="held by"):
        b.check_owner("fsk24")                               # frequency unknown: refused
    monkeypatch.setenv("K6_BENCH_AS", "c6phy")
    r = Rig(hz=k6)
    with pytest.raises(SystemExit, match="440.850 MHz is held by fsk24"):
        b.key_and_play(r, "x.wav")                           # the rig's own frequency is checked
    assert "T 1" not in r.calls
    b.key_and_play(Rig(hz=uv), "x.wav")
    b.claim("c6phy", 60, "moved", hz=441_100_000)            # a holder moves its own claim
    assert [c["hz"] for c in b.claims() if c["who"] == "c6phy"] == [441_100_000]
    assert b.release("c6phy") and [c["who"] for c in b.claims()] == ["fsk24"]
    assert b.release("jeff", force=True) and b.claims() == []


def test_a_whole_bench_claim_shuts_out_channel_claims(work):
    b.claim("explore", 30)
    with pytest.raises(SystemExit, match="the bench is held by explore"):
        b.claim("c6phy", 10, hz=441_000_000)
    with pytest.raises(SystemExit):
        b.check_owner("c6phy", 441_000_000)


def test_claim_freq_on_the_command_line(work, capsys):
    b.main(["claim", "c6phy", "20", "UV-380", "--freq", "441.000"])
    b.main(["owner"])
    out = capsys.readouterr().out
    assert "c6phy on 441.000 MHz until" in out
    assert b.claims()[0]["hz"] == 441_000_000
    with pytest.raises(SystemExit, match="70 cm only"):
        b.main(["claim", "x", "5", "--freq", "146.520"])


def test_an_old_owner_file_is_one_whole_bench_claim(work):
    os.makedirs(os.path.dirname(b.path("owner")), exist_ok=True)
    json.dump({"who": "explore", "what": "", "since": time.time(), "until": time.time() + 600},
              open(b.path("owner"), "w"))
    assert b.owner()["who"] == "explore" and b.owner()["hz"] is None
    with pytest.raises(SystemExit):
        b.claim("c6phy", 10, hz=441_000_000)


def test_a_sigterm_while_keyed_unkeys(work):
    """pkill (SIGTERM) of a keying tool mid-transmission: the finally still sends T 0"""
    import signal
    before = signal.getsignal(signal.SIGTERM)
    r = Rig()
    with pytest.raises(SystemExit, match="unkeying"):
        with b.keyed(r, "id", "test"):
            assert r.ptt == 1
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(1)
    assert r.ptt == 0 and r.calls[-1] == "T 0"
    assert signal.getsignal(signal.SIGTERM) == before          # the old handler is back


def test_a_corrupt_owner_file_means_free(work):
    os.makedirs(os.path.dirname(b.path("owner")), exist_ok=True)
    open(b.path("owner"), "w").write("{not json")
    assert b.owner() is None


def _hold(who, cmd, out):
    """serial-hold in a thread, its return code into out"""
    t = threading.Thread(target=lambda: out.append(b.serial_hold(who, cmd, poll=0.05)))
    t.start()
    for _ in range(100):
        if b.serial_owner():
            break
        time.sleep(0.05)
    return t


def test_serial_reader_lets_go_when_asked(work):
    out = []
    t = _hold("dcs", [sys.executable, "-c", "import time; time.sleep(60)"], out)
    o = b.serial_owner()
    assert o["who"] == "dcs" and o["pid"] > 0
    assert b.serial_release("font", wait=10) is True
    t.join(10)
    assert out == [0] and b.serial_owner() is None
    assert not os.path.exists(b.path("serial_release"))
    log = open(b.path("txlog")).read()
    assert "serial reader started by dcs" in log and "released for font" in log


def test_one_serial_reader_at_a_time(work):
    out = []
    t = _hold("dcs", [sys.executable, "-c", "import time; time.sleep(60)"], out)
    with pytest.raises(SystemExit, match="dcs reads the port"):
        b.serial_hold("aprs", [sys.executable, "-c", "pass"])
    b.serial_release("aprs", wait=10)
    t.join(10)


def test_same_who_takes_over_its_own_serial_reader(work):
    """a reader left running by the same WHO (a cancelled job's logger) lets go to the new one"""
    out, out2 = [], []
    t = _hold("ysf", [sys.executable, "-c", "import time; time.sleep(60)"], out)
    first = b.serial_owner()["pid"]
    t2 = threading.Thread(target=lambda: out2.append(b.serial_hold(
        "ysf", [sys.executable, "-c", "import time; time.sleep(60)"], poll=0.05)))
    t2.start()
    t.join(20)
    assert out == [0]
    for _ in range(100):
        o = b.serial_owner()
        if o and o["pid"] != first:
            break
        time.sleep(0.05)
    assert o and o["who"] == "ysf" and o["pid"] != first
    b.serial_release("x", wait=10)
    t2.join(10)
    assert out2 == [0]


def test_serial_reader_that_ends_frees_the_port(work):
    assert b.serial_hold("dcs", [sys.executable, "-c", "raise SystemExit(3)"], poll=0.05) == 3
    assert b.serial_owner() is None
    assert b.serial_release("font", wait=1) is True


def test_a_stale_serial_owner_file_means_free(work):
    b._put_json("serial_owner", {"who": "dead", "pid": 1, "cmd": "x", "since": 0})
    open(b.path("serial_lock"), "a").close()
    assert b.serial_owner() is None
    assert b.serial_release("font", wait=1) is True


def test_a_starting_reader_waits_out_an_owner_probe(work):
    """serial_owner() takes the lock for a moment to see whether a reader holds it; a reader
    starting in that moment must wait for it, not refuse (it did: a CI race in the test below)"""
    p = b.path("serial_lock")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    probe = open(p, "a")
    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    threading.Timer(0.1, lambda: (fcntl.flock(probe, fcntl.LOCK_UN), probe.close())).start()
    assert b.serial_hold("dcs", [sys.executable, "-c", "pass"], poll=0.05) == 0


def test_an_old_release_request_does_not_stop_a_new_reader(work):
    b._put_json("serial_release", {"by": "old", "at": 0})
    out = []
    t = _hold("dcs", [sys.executable, "-c", "import time; time.sleep(60)"], out)
    time.sleep(0.3)
    assert b.serial_owner() is not None
    b.serial_release("font", wait=10)
    t.join(10)
    assert out == [0]


# ---- --as and the Pluto launcher (Jeff 11-17: allow rules can't match an env prefix) ----

def test_as_flag_sets_the_holder(work, monkeypatch):
    monkeypatch.delenv("K6_BENCH_AS", raising=False)
    b.main(["claim", "fsk24", "5", "test"])
    b.main(["--as", "fsk24", "owner"])
    assert os.environ["K6_BENCH_AS"] == "fsk24"
    b.check_owner()                        # the holder passes with no env prefix


def test_install_writes_a_pluto_launcher(work):
    t = b.launcher_text("/home/x/blowfang")
    assert "runpy.run_path" in t and "'/home/x/blowfang', 'tools', 'pluto_bench.py'" in t
    compile(t, "pluto_bench.py", "exec")
