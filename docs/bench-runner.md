# The hamlet bench runner

`tools/bench_runner.py` is one long-lived service on hamlet that runs the threads' staged bench
jobs (flashes, module uploads, Pluto and IC-7100 sends, loggers) one at a time, under Jeff's bench
rules, checked in code. Jeff approves it once; after that a thread's hamlet session only drops a
job in the queue, and no thread needs Jeff's typed go for each flash or key-up (Jeff, 2026-11-17).

Why: every thread's hamlet session is a fresh Claude Code session in auto mode, and auto mode
won't let a session flash or key a radio until Jeff has typed an OK in that thread. The runner
is a systemd user service like the CW ID keeper (`k6-idkeeper`), not a Claude session: it never
sleeps, survives reboots (linger is on), and refuses anything outside the rules below.

## Start it (once)

From a checkout of this branch (or main once merged), on hamlet:

    python3 tools/bench_runner.py install --unit          # bin/bench_runner.py, bin/k6_bench.py, the unit
    ~/blowfang-work/bin/bench_runner.py selftest          # must end PASS
    systemctl --user daemon-reload
    systemctl --user enable --now blowfang-bench-runner

`selftest` runs 24 sample jobs through the checks (7 that must pass, 17 that must be refused),
then reports this machine's setup: the module key, the K6 cable by its by-id name, rigctld and the
IC-7100's power, Aura's flash.py, the PTT lock and the cable's reader. Setup problems are warnings
(the steps that need the missing piece refuse); a wrong check is a FAIL.

Stop it any time: `systemctl --user stop blowfang-bench-runner` (the running step gets SIGTERM:
the keying tools unkey on it, and the job is marked cancelled). Reinstall after a merge with the
same `install` line from the new checkout, then `systemctl --user restart blowfang-bench-runner`.

## For threads: submit a job

Stage everything first (images, bundle, modules, WAVs under `~/blowfang-work`, a clean worktree
under `~/blowfang-work/worktrees`), then, from the thread's hamlet session:

    ~/blowfang-work/bin/bench_runner.py check  ~/blowfang-work/runs/NAME/job.json    # the checks only
    ~/blowfang-work/bin/bench_runner.py submit ~/blowfang-work/runs/NAME/job.json
    ~/blowfang-work/bin/bench_runner.py status
    ~/blowfang-work/bin/bench_runner.py show JOB-ID          # result.json and the log's tail

Results land in `~/blowfang-work/runs/queue/JOB-ID/` (`runner.log`: every command and its output
with UTC times; `result.json`: each step's rc and times; loggers' files). The runner's own log is
`~/blowfang-work/logs/bench-runner.txt`. A job is checked at `submit`, again when it starts, and
each step's files again right before the step.

A job (JSON):

```json
{
 "id": "fsk24-flash-8a78fb5",
 "who": "fsk24",
 "freq_mhz": 440.850,
 "minutes": 20,
 "checkout": "/home/baitisj/blowfang-work/worktrees/main",
 "take_serial": true,
 "steps": [
  {"do": "k6.install", "bundle": "/home/baitisj/blowfang-work/runs/fsk24-flash/bundle",
   "manifest_sha256": "…", "commit": "8a78fb5", "args": ["--no-backup", "--verify"]},
  {"do": "logger", "seconds": 1800, "file": "k6.log"},
  {"do": "wait", "pattern": "K6 (BOOT|CFG)", "seconds": 120},
  {"do": "pluto", "args": ["send", "/home/baitisj/blowfang-work/runs/fsk24-flash/wake.wav", "--gain", "-30"]}
 ]
}
```

| field | |
|---|---|
| `id` | lower case, digits, `.` `_` `-`, unique |
| `who` | the thread, one word: the k6_bench claim's name and `K6_BENCH_AS` for every step |
| `freq_mhz` | the channel the runner claims for the job (default 440.850), or `"bench"` for all of it. Every transmit step must stay within 100 kHz of it |
| `minutes` | the claim and the job's time limit (1-240) |
| `checkout` | a clean worktree under `~/blowfang-work/worktrees`, or `~/blowfang`; the tools run from it |
| `wait_bench_min` | how long the job may wait for its channel (60) before it fails |
| `not_before` | UTC, `2026-11-17T10:00:00Z`: don't start earlier |
| `repeat` | `{"every_min": 45, "until": "2026-11-17T18:00:00Z"}`: queue itself again (within 24 h) |
| `take_serial` | a flash may ask the K6 cable's serial-hold logger to let go (else it refuses) |
| `ic7100_power_max` | the IC-7100 RF power the job accepts (0 default, 0.5 at most) |

Steps (`timeout` and `continue_on_error` on any of them):

| `do` | runs | checks |
|---|---|---|
| `k6.install` | `k6_install.py --port CABLE --key-file KEY install BUNDLE [--no-backup] [--verify]` | manifest sha256, every file against the manifest, image unkeyed or keyed with our key, commit = manifest build, not never-flash; an update only (no `--fresh`, no `--yes`) |
| `k6.flash` | `k6_install.py … flash IMAGE` (the cable session; keys it with our key) | sha256, our key or the placeholder, commit |
| `k6.update` | `k6_update.py IMAGE --port CABLE --aura … --trigger "sh bin/k6-reset-trigger.sh" --window 0.6` | the image itself keyed with our key (no placeholder), sha256, commit; IC-7100 rules (the reset packet) |
| `k6.upload` | `k6_upload.py MODULE --key-file KEY ARGS` | `--freq` in the claim, `--pluto-gain` ≤ -30, no `--key-file` from the job; IC-7100 rules (it sets the rig's mode and records its audio) |
| `pluto` | `pluto_bench.py --as WHO ARGS` | `send` / `steps` / `fsk24`: `--hz` in the claim, every gain ≤ -30 dB; `rx` anywhere; checkout has the keeper fix |
| `ic7100` | `k6_bench.py --as WHO send WAV --freq HZ` / `id` / `stop` | `--freq` required for send and in the claim, no `--no-id`; IC-7100 rules |
| `ic7100.tune` | rigctl `F HZ`, `M PKTFM 12000`, `L RFPOWER 0`, read back | 70 cm in the claim; not while held |
| `ic7100.power0` | rigctl `L RFPOWER 0`, read back | |
| `logger` | `k6_bench.py serial-hold WHO -- timeout -s INT SECONDS python3 -m modem.k6_harness listen --k6 CABLE --log RUNDIR/FILE`, in the background | ≤ 6 h; the cable's one reader |
| `logger.stop` | `k6_bench.py serial-release WHO` | |
| `wait` | `seconds`, or until `pattern` (regex) shows in the run folder's `file` (default k6.log) | ≤ 1 h |
| `uv380.flash` | `uv380_dfu.py flash DEV IMAGE --sha256 … --radio-tool …` | sha256, commit |

There is no step that runs a script. A script could do anything; the runner's worth is that it
checks every key-up before it happens. A test that needs a new kind of step gets one here, with
its checks and a test.

## The rules, in code

- Transmit only in 430.010-449.990 MHz and within the job's claimed channel. Receive anywhere.
- K6 images keyed with `~/.config/sotto/k6-mod.key` (k6_install writes it over the placeholder;
  k6_update needs the keyed build); modules signed with it (`--key-file` comes from the runner).
- Never flash acd06ba, a1a86f9 or 35e982f. Every staged file's sha256 is in the job and checked
  again right before its step, so a file restaged after the check is refused.
- The K6 cable only through k6_install, k6_update and a serial-hold logger, found by its by-id
  name (`/dev/serial/by-id/*Prolific*`; `K6_CABLE` in the unit overrides). No job argument may
  name a device.
- Pluto gain at most -30 dB; Pluto steps only from a checkout with `note_self_ided` (3a72067 on).
- IC-7100: no step while `bench_runner.py hold-ic7100 WHY` is set (Jeff on HF or Ragtime;
  `free-ic7100` ends it); before each IC-7100 step the RF power is read back and, if above the
  job's limit, set to 0 and read again; the rig must read 70 cm inside the claim.
- One job at a time, under a k6_bench channel claim the other threads see (a job whose channel is
  busy waits; a job on another free channel may go first).
- Files only under `~/blowfang-work` or the checkout; the checkout must have no changed tracked
  files (the commit it ran at is in result.json).
- Ident is the tools' own: Pluto MCW first and last, IC-7100 sends through k6_bench (ID first
  when due, the keeper's IDs), k6_upload's closing CW ID.

`pause [WHY]` / `resume`: start no new job. `cancel ID`: a waiting job never starts; the running
one gets SIGTERM at once.

## Allow rules

None new. Jeff's rules from 11-17 (`Bash(~/blowfang-work/bin/*)` and
`Bash(/home/baitisj/blowfang-work/bin/*)`) already cover `bench_runner.py submit`, `check`,
`status` and `show`. Starting the service is the one step that needs Jeff's typed line.
