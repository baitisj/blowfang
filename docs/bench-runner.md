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
| `pluto` | `pluto_bench.py --as WHO ARGS`; with `"background": true` an `rx` runs beside the next steps (to hear a UV-380 send) | `send` / `steps` / `fsk24` / `parrot`: `--hz` in the claim, every gain ≤ -30 dB; `parrot` (tools/pluto_parrot.py: each YSF or M17 sotto message heard goes back in the same mode) names its `--hz`, runs `--minutes` ≤ 25, no `--save`; `rx` anywhere; only `rx` may run in the background; checkout has the keeper fix |
| `pluto.fw` | `pluto_fw.py --as WHO ARGS`: the Pluto's boot image and earshot's channelizer (doctor, info, backup, ramboot, fpga, psd, riq, reboot; see the tool's docstring) | args[0] one of those; every image, script and producer it puts on the Pluto is pinned by sha256 in pluto_fw.py; receive only |
| `pluto.flash` | `pluto_fw.py --as WHO flash IMAGE.itb BACKUPDIR`: a pinned FIT into the firmware partition, read back and compared | exactly two args; local submit only, never the inbox |
| `ic7100` | `k6_bench.py --as WHO send WAV --freq HZ` / `id` / `stop` | `--freq` required for send and in the claim, no `--no-id`; IC-7100 rules |
| `ic7100.tune` | rigctl `F HZ`, `M PKTFM 12000`, `L RFPOWER 0`, read back | 70 cm in the claim; not while held |
| `ic7100.power0` | rigctl `L RFPOWER 0`, read back | |
| `ic7100.af` | rigctl `L AF LEVEL`, read back: the IC-7100's speaker as a sound source at the bench (it plays what it receives); `"level": "restore"` puts back what the job's first `ic7100.af` found, and the job's end does that anyway, even after a failed step | level 0..0.3; not while held |
| `logger` | `k6_bench.py serial-hold WHO -- timeout -s INT SECONDS python3 -m modem.k6_harness listen --k6 CABLE --log RUNDIR/FILE`, in the background | ≤ 6 h; the cable's one reader |
| `logger.stop` | `k6_bench.py serial-release WHO` | |
| `wait` | `seconds`, or until `pattern` (regex) shows in the run folder's `file` (default k6.log) | ≤ 1 h |
| `uv380.flash` | `uv380_dfu.py flash DEV IMAGE --sha256 … --radio-tool …` | sha256, commit |
| `uv380.cmd` | `uv380_i2sprobe.py cmd DEV ARGS… tune listen`: the UV-380's bench settings over USB, e.g. `["listen 0", "power 0", "zero 2"]` | only `tune`, `listen [0\|1]`, `power 0`, `zero 1\|2`, `phy fsk\|rng\|m17\|ysf\|dmr` (PAGER's sub-mode: in M17 and YSF the image listens for sotto, milestone 5), `emph 0\|1`, `trim N` (±8000), `gain N` (-12..15), `freq 441850\|440850` (in the claim); nothing that writes memory or registers; every setting read back after |
| `uv380.send` | `uv380_i2sprobe.py mod DEV firmware/uv380-openrtx/mod/txreq.c` (the checkout's own module, signed by the runner): one send of the stored message as PTT sends it, ident first | no args; right before, the radio itself must read `pwr=0` and a `freq=` of its two channels inside the claim (an image without `freq=`, from before the 441.850 move, is refused) |
| `uv380.m17` | `uv380_i2sprobe.py mod DEV firmware/uv380-openrtx/mod/c6m17tx_PLAN.c` (the checkout's own module, signed by the runner): one M17 transmission through the C6000's physical layer mode, tx_tone's Morse ident first (docs/uv380-m17-scope.md) | args: one plan, `bert`, `sms` or `voice`; the same read-back as `uv380.send` (`pwr=0`, a `freq=` of its two channels inside the claim) |
| `uv380.ysf` | `uv380_i2sprobe.py mod DEV firmware/uv380-openrtx/mod/c6ysftx_PLAN.c` (the checkout's own module, signed by the runner): one YSF (C4FM, Yaesu framing) transmission through the C6000's physical layer mode, tx_tone's Morse ident first (docs/uv380-ysf.md). `echo` = c6ysftx_echo.c, the FTM parrot: it listens on the radio's FM tap for up to 15 min and sends each C4FM call it hears back as it came (up to 3 calls, about 2.5 s each; its lines give counts and the caller's callsign, never voice); give the step `"timeout": 1000` | args: one plan, `dn`, `data` or `echo`; the same read-back as `uv380.send` (`pwr=0`, a `freq=` of its two channels inside the claim) |
| `uv380.voice` | `uv380_i2sprobe.py mod DEV firmware/uv380-openrtx/mod/MODULE` (the checkout's own module, signed by the runner): DMR voice bursts over the C6000's voice port (docs/uv380-c6000-modem.md, "Voice bursts (V0-V2)"). `check` = c6vcheck.c and `dry` = c6dvtx.c never key; `pn`, `live`, `msg` = c6dvtx_pn/live/msg.c send one voice call, tx_tone's Morse ident first | args: one plan, `check`, `dry`, `pn`, `live` or `msg`; the same read-back as `uv380.send` (`pwr=0`, a `freq=` of its two channels inside the claim), for every plan |
| `uv380.rx` | `uv380_i2sprobe.py mod DEV firmware/uv380-openrtx/mod/MODULE` (the checkout's own module, signed by the runner): a receive-only module (docs/uv380-m17-rx.md). `tap` = c6fmtap.c (the C6000's 8 kHz FM tap as audio lines, 0x34 stepped), `tap1c` = c6fmtap_1c.c (0x34 held at 0x1C, 150 s), `phy` = c6phyrx.c (the chip's own 4FSK demodulator's windows), `m17` = c6m17rx.c (M17 decoded on the radio from the tap: LSF callsigns, SMS, stream frames, BERT errors, 150 s), `m17tap` = c6m17rx_tap.c (the same plus the tap's audio lines), `scan` = atscan.c (an RSSI sweep: the AT1846S's receive frequency stepped across 400-480 MHz and put back; never sets its tx bit), `dmr` = c6dmrrx_long.c (the C6000 as a DMR receiver for 60 s: `b` lines with each burst's sync, data type and payload, for `tools/uv380_dmrburst.py rx`; the K6's DMR stream, docs/k6-c4fm.md); none keys. `"background": true` lets a Pluto `send` come while it listens; the job waits for it before it ends. Its lines go to the run folder's `uv380.log` | args: one plan, `tap`, `tap1c`, `phy`, `m17`, `m17tap`, `scan`, `ysf`, `ysftap`, `dmr`, `m17sotto` or `ysfsotto` |
| `kplug.read` | `bench_kplug.py read --port CABLE --out RUNDIR --private ~/blowfang-work/private/kplug/ID ARGS`: a read-only clone of a stock Baofeng on the K6 cable that speaks the UV-17 Pro family's programming protocol (UV-21 Pro, UV-17 Pro, UV-5RM, ...): the ident handshake, the F / M / SEND replies, the codeplug as CHIRP reads it, and any `--extra` 64-byte blocks. It sends only the handshake and `R` reads, never a write. It listens first and refuses if it hears the K6's report lines. The codeplug (`clone.img`, CHIRP opens it) holds the channel list, so it stays in the private folder on hamlet; the run folder gets `ident.json`, `summary.json` (channel counts per band, region hashes, strings) and the regions outside the channel memories | only `--ident normal\|color\|gmrs5rh\|f8hppro`, `--model` (CHIRP's tag), `--extra 0xADDR[:0xEND]` (0x40-aligned, ≤ 512 blocks), `--listen-s` ≤ 10; never in the background; the cable's serial-hold logger must let go first (`take_serial`) |
| `kdh.probe` | `uv21_kdh.py probe --port CABLE --out RUNDIR ARGS`: the UV-21 Pro's KDH bootloader handshake, nothing else (with `--wait-boot-s S` the tool sends command 42 every 50 ms until someone switches the radio off and on, which puts it in flash mode with no keys; proven on the UV-21 Pro). Nothing is erased or written. `probe.json` says whether it answered | only `--listen-s` ≤ 10, `--wait-boot-s` ≤ 200; never in the background; the cable's logger must let go first |
| `kdh.dump` | `uv21_kdh.py dump --port CABLE --out RUNDIR --private ~/blowfang-work/private/uv21/ID ARGS`: captures what the one-page dumper (`radios/uv21/dumper`) sends: a taste of the SPI flash first (`T`: JEDEC ID and 256 bytes per SPI mode; no chip answering ends the step with exit 4), then passes asked for with `D` until enough complete ones agree. The MCU flash, unique ID and SPI flash stay in the private folder; the run folder gets `summary.json` (sizes, hashes, agreement, the JEDEC ID) | only `--passes 1\|2\|3`, `--timeout-s` 60..3000; never in the background |
| `serial.list` | the runner itself: every `/dev/serial/by-id` port's tty, USB ids, maker and product into `ports.json` and the log. It opens no port. Serial numbers are masked (`<serial>`), since inbox results land on a public branch | no args; never in the background |
| `rt950.info` | `radios/rt950/tools/rt950_backup.py info PORT ARGS`: the RT-950 Pro's CPS handshake on its programming cable, and its identity and model | `cable` (required): the cable's USB ids, such as `1a86:7523`, from `serial.list`; exactly one by-id port may have them, and the runner refuses the K6 cable (PL2303), the IC-7100, any ttyACM port (Pluto, UV-380) and any port a process has open (`RT950_CABLE` in the unit overrides the lookup). Only `--addr 2\|3`, `--timeout` 0.5-10; never in the background |
| `rt950.read` | `rt950_backup.py read PORT ~/blowfang-work/private/rt950/ID --summary RUNDIR -v ARGS`: the read-only backup (docs/rt950-backup.md). The tool has no write command and refuses CPS write opcodes; `--addr 2` checks the radio says `RT-950` first, `--addr 3` that it runs the dump-only image. The bytes (Radtel's data, the radio's calibration) stay in the private folder on hamlet; the run folder gets `manifest.json` (identity, each pass's sha256, and the sha256 of each CPS region, calibration included) and `SHA256SUMS` | `cable` as for `rt950.info`; only `--addr 2\|3`, `--passes 2\|3\|4`, `--block 16\|32\|64\|128`, `--timeout` 0.5-10, `--cps-regions`; never in the background. Use `"freq_mhz": "bench"` |
| `rt950.compare` | `rt950_backup.py compare STAGE_A ~/blowfang-work/private/rt950/BACKUP/cps64k.bin --json RUNDIR/compare.json`: an earlier `rt950.read`'s 64 KB against Stage A's Bluetooth read (`~/blowfang-work/rt950/runs/rt950-backup-20261117/stageA/cps64k.bin`), per CPS region and 4 KB page: how many bytes differ and the first and last offset, never the bytes. Opens no port | `backup` (required): the earlier job's id; `against`: another `rt950.read` job's id instead of Stage A; only `--keys` (for each differing region, which pair of CPS session-key indexes, -1 for none, makes the two reads agree: a region the stock firmware sends without the session XOR is scrambled by each read's own key); never in the background |
| `sim.soapyfang` | `python3 -m sim.soapyfang --source pluto --host 127.0.0.1 --port 5058 ARGS`: the UV-21 port image on the emulated board, its RF chip fed by the Pluto's live I/Q (sim/soapyfang/README.md); the workbench page on hamlet's 127.0.0.1:5058 (`ssh -L 5058:127.0.0.1:5058 hamlet`). Receive only: the source sets the Pluto up through pluto_bench (quiet first, TX LO down) and opens only `iio_readdev`; the LO is parked at 2.4 GHz at the end | `--minutes` ≤ 55 (required), `--image` and `--syms` (required: a prebuilt port.bin and its `llvm-nm` listing; hamlet can't build one), `--gain` 0..71, `--center` 70 MHz-6 GHz, `--rate` 0.6-4 MS/s, `--dbm-offset`; nothing else (the runner sets source, host and port); never in the background. Use `"freq_mhz": "bench"`: it holds the Pluto for its minutes |
| `kdh.flash` | `uv21_kdh.py flash --port CABLE --out RUNDIR --image IMAGE --sha256 …`: writes a staged image to the UV-21 Pro's app area through its bootloader, one 1 KB package per page from 0x08001000, region 1 only (never region 2). A local submit only | image staged under `~/blowfang-work` or the checkout, ≤ 60 KB, sha256 pinned, commit; no args; never in the background |

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
| `jobs/ID/cancel` | the thread | drop a waiting job, or stop a running one within about a minute. For a `repeat`, `cancel ID-rN` stops that run (it has only `results/ID-rN`), and `cancel ID` (the first id) stops the whole series |
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
- **No flashes** (`k6.install`, `k6.flash`, `k6.update`, `uv380.flash`, `pluto.flash`, `kdh.flash`) and no `.c` K6 modules
  (push the built `.bin`; the runner signs it). Flashes stay a local `submit`.
- `submit` fills in a `job:` module's or image's `sha256` and lints the job before it pushes; the
  runner's checks still decide.

Each run of a `repeat` gets its own run folder (`run:NAME` moves to `runs/queue/ID-rN`), so every run's outputs come back in its own `results/ID-rN`. An id already used on hamlet is refused in the branch, untouched locally. The results are public
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
