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

`selftest` runs 33 sample jobs through the checks (10 that must pass, 23 that must be refused),
then reports this machine's setup: the module key, the K6 cable by its by-id name, rigctld and the
IC-7100's power, Aura's flash.py, the PTT lock and the cable's reader. Setup problems are warnings
(the steps that need the missing piece refuse); a wrong check is a FAIL. So is a missing module
compiler: `uv380.send`, `uv380.m17`, `uv380.ysf` and `uv380.voice` build their module from the checkout at send time, and the
service's PATH has no clang, so the runner puts `$K6_ATFE`, else the newest
`BLOWFANG_WORK/toolchains/ATfE-*/bin`, on the steps' PATH (as `tools/uv380_dfu.py` does).

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
| `pluto` | `pluto_bench.py --as WHO ARGS`; with `"background": true` an `rx` runs beside the next steps (to hear a UV-380 send) | `send` / `steps` / `fsk24`: `--hz` in the claim, every gain ≤ -30 dB; `rx` anywhere; only `rx` may run in the background; checkout has the keeper fix |
| `ic7100` | `k6_bench.py --as WHO send WAV --freq HZ` / `id` / `stop` | `--freq` required for send and in the claim, no `--no-id`; IC-7100 rules |
| `ic7100.tune` | rigctl `F HZ`, `M PKTFM 12000`, `L RFPOWER 0`, read back | 70 cm in the claim; not while held |
| `ic7100.power0` | rigctl `L RFPOWER 0`, read back | |
| `logger` | `k6_bench.py serial-hold WHO -- timeout -s INT SECONDS python3 -m modem.k6_harness listen --k6 CABLE --log RUNDIR/FILE`, in the background | ≤ 6 h; the cable's one reader |
| `logger.stop` | `k6_bench.py serial-release WHO` | |
| `wait` | `seconds`, or until `pattern` (regex) shows in the run folder's `file` (default k6.log) | ≤ 1 h |
| `uv380.flash` | `uv380_dfu.py flash DEV IMAGE --sha256 … --radio-tool …` | sha256, commit |
| `uv380.cmd` | `uv380_i2sprobe.py cmd DEV ARGS… tune listen`: the UV-380's bench settings over USB, e.g. `["listen 0", "power 0", "zero 2"]` | only `tune`, `listen [0\|1]`, `power 0`, `zero 1\|2`, `phy fsk\|rng`, `emph 0\|1`, `trim N` (±8000), `gain N` (-12..15), `freq 441850\|440850` (in the claim); nothing that writes memory or registers; every setting read back after |
| `uv380.send` | `uv380_i2sprobe.py mod DEV firmware/uv380-openrtx/mod/txreq.c` (the checkout's own module, signed by the runner): one send of the stored message as PTT sends it, ident first | no args; right before, the radio itself must read `pwr=0` and a `freq=` of its two channels inside the claim (an image without `freq=`, from before the 441.850 move, is refused) |
| `uv380.m17` | `uv380_i2sprobe.py mod DEV firmware/uv380-openrtx/mod/c6m17tx_PLAN.c` (the checkout's own module, signed by the runner): one M17 transmission through the C6000's physical layer mode, tx_tone's Morse ident first (docs/uv380-m17-scope.md) | args: one plan, `bert`, `sms` or `voice`; the same read-back as `uv380.send` (`pwr=0`, a `freq=` of its two channels inside the claim) |
| `uv380.ysf` | `uv380_i2sprobe.py mod DEV firmware/uv380-openrtx/mod/c6ysftx_PLAN.c` (the checkout's own module, signed by the runner): one YSF (C4FM, Yaesu framing) transmission through the C6000's physical layer mode, tx_tone's Morse ident first (docs/uv380-ysf.md) | args: one plan, `dn` or `data`; the same read-back as `uv380.send` (`pwr=0`, a `freq=` of its two channels inside the claim) |
| `uv380.voice` | `uv380_i2sprobe.py mod DEV firmware/uv380-openrtx/mod/MODULE` (the checkout's own module, signed by the runner): DMR voice bursts over the C6000's voice port (docs/uv380-c6000-modem.md, "Voice bursts (V0-V2)"). `check` = c6vcheck.c and `dry` = c6dvtx.c never key; `pn`, `live`, `msg` = c6dvtx_pn/live/msg.c send one voice call, tx_tone's Morse ident first | args: one plan, `check`, `dry`, `pn`, `live` or `msg`; the same read-back as `uv380.send` (`pwr=0`, a `freq=` of its two channels inside the claim), for every plan |
| `uv380.rx` | `uv380_i2sprobe.py mod DEV firmware/uv380-openrtx/mod/MODULE` (the checkout's own module, signed by the runner): a receive-only module (docs/uv380-m17-rx.md). `tap` = c6fmtap.c (the C6000's 8 kHz FM tap as audio lines, 0x34 stepped), `tap1c` = c6fmtap_1c.c (0x34 held at 0x1C, 150 s), `phy` = c6phyrx.c (the chip's own 4FSK demodulator's windows); neither keys. `"background": true` lets a Pluto `send` come while it listens; the job waits for it before it ends. Its lines go to the run folder's `uv380.log` | args: one plan, `tap`, `tap1c` or `phy` |

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
- UV-380: power 0 only (Jeff 11-16: 0 W bench sends are fine while he's away; anything above
  waits for him), on 441.850 or 440.850 inside the claim, read back from the radio before every
  send. The port is found by its USB ids 0483:5740 (`UV380_DEV` in the unit overrides), never
  guessed: ttyACM0 on hamlet is the Pluto's console.
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

## The inbox: jobs from cloud threads

Cloud threads have no hamlet shell, so the runner also takes jobs from a branch (Jeff, 2026-11-17:
"Yes, build the jobs inbox", accepting that whoever can push to this repository can queue a
transmission). `tools/bench_inbox.py` is both sides:

    python3 tools/bench_inbox.py submit runs/m17/job.json     # from any checkout, cloud or hamlet
    python3 tools/bench_inbox.py show m17-bert-1118a          # result.json, files, the log's tail
    python3 tools/bench_inbox.py get m17-bert-1118a out/      # the files that came back
    python3 tools/bench_inbox.py status                       # the runner's status.json, last results
    python3 tools/bench_inbox.py cancel m17-bert-1118a

The branch is `claude/bench-jobs` (an orphan branch; it never merges):

| path | written by | |
|---|---|---|
| `jobs/ID/job.json` | the thread | the job, as above, with the inbox's differences below |
| `jobs/ID/NAME` | the thread | a file the job names as `job:NAME` (WAVs, built modules), ≤ 16 MB, ≤ 64 files, ≤ 32 MB in all |
| `jobs/ID/cancel` | the thread | drop a waiting job, or stop a running one within about a minute |
| `results/ID/result.json` | the runner | `queued`, `refused` (with `problems`), `running`, then `done` / `failed` / `cancelled`, as `show` prints it locally |
| `results/ID/runner.log` | the runner | the run's log (its last 512 KB) |
| `results/ID/files/…`, `files.json` | the runner | the run folder's files up to 20 MB each (40 MB a job); `files.json` lists every file with its size and sha256, and where a big one stays on hamlet |
| `status.json` | the runner | its installed commit, the running job, the queue, pause and IC-7100 hold, the last error and self-update; pushed when it changes and at least hourly |

The runner fetches the branch every minute while idle (a bare clone in
`~/blowfang-work/inbox/blowfang.git`), and checks each new job twice: the inbox's own rules, then
`submit` (every check above, unchanged). What an inbox job may name differs, because the people
who can push are not the people with a hamlet shell:

- **Files** only as `job:NAME` (pushed in the job's folder; staged in `~/blowfang-work/runs/inbox/ID`)
  or `run:NAME` (the job's run folder, for outputs such as `["rx", "run:m.cf32", …]`). No hamlet paths.
- **Tools from main.** No `checkout`: the job runs from a runner-made worktree
  `~/blowfang-work/worktrees/inbox-<commit>` of main's tip, or of `tools_commit` (a commit on main).
  A branch's tools never run on hamlet.
- **UV-380 module sources from any pushed commit.** `sources_commit` (with `uv380.send`, `uv380.m17`,
  `uv380.ysf`, `uv380.voice`, `uv380.rx`) builds the module from a worktree `src-<commit>` of that commit, after a check that
  the module and every file it `#include`s stay inside that tree (no absolute or climbing includes,
  no `#embed`, `.incbin` or asm `.include`: those could put a hamlet file on the air or in the
  public log).
- **No flashes** (`k6.install`, `k6.flash`, `k6.update`, `uv380.flash`) and no `.c` K6 modules
  (push the built `.bin`; the runner signs it). Flashes stay a local `submit`.
- `submit` fills in a `job:` module's or image's `sha256` and lints the job before it pushes; the
  runner's checks still decide.

An id already used on hamlet is refused in the branch, untouched locally. The results are public
(the repository is): runner logs carry commands, paths and rig readings, never keys.

Jeff's switches: `bench_runner.py inbox off|on` (state/inbox.off; the runner stops fetching and
pushing), and `pause` as before.

## Self-update

While idle (no job running or due within two minutes, not paused, no job's logger left running),
every five minutes the runner compares main's `tools/bench_runner.py`, `bench_inbox.py`,
`k6_bench.py` and `k6-reset-trigger.sh` with its installed copies. When they differ it makes a
worktree of main, runs that main's own `selftest --checkout WORKTREE` (it must end `PASS`), keeps
the old copies in `bin/previous/`, runs main's `install` (`--unit` too when the unit exists), and
re-executes itself (same pid; a changed unit means `daemon-reload` and an exit so systemd restarts
it). A main whose selftest fails is not tried again; `status.json` and the runner log say why.
`bin/.installed.json` records the commit installed.

`bench_runner.py autoupdate off|on` turns it off and on; `bench_runner.py rollback` puts
`bin/previous/` back and turns self-update off (then restart the unit).

## Allow rules

None new. Jeff's rules from 11-17 (`Bash(~/blowfang-work/bin/*)` and
`Bash(/home/baitisj/blowfang-work/bin/*)`) already cover `bench_runner.py submit`, `check`,
`status` and `show`. Starting the service is the one step that needs Jeff's typed line, and the
inbox and self-update need it once more (install this version and restart); after that main's
runner changes install themselves.
