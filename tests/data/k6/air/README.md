# On-air data from Jeff's K6

Real recordings and serial logs that `tests/test_k6_air.py` replays in CI. Serial text and
audio only: no Baofeng, Aura or Yaesu firmware, no bootloader dumps.

| File | What | When, image |
| --- | --- | --- |
| `cwprobe-20261106T0506.log.gz` | K6 serial log of the CW probe module (REG_6F/REG_64 trackers, REG_0B event log) hearing the IC-7100's AG7EW IDs | 2026-11-06 05:06-05:19 UTC, 6473ca7 |
| `cwlisten-20261106T0529.log.gz` | K6 serial log of three cwlisten runs: AG7EW at 20/13/25 wpm on 800/600/1200 Hz, plus the upload-tail bursts | 2026-11-06 05:29-05:35 UTC, 6473ca7 |
| `k6cal-1200-20261104T0148.flac` | IC-7100 recording of the K6's first CAL burst (44.1 kHz mono, 0.24 s to the end of Jeff's `test.flac`, samples unchanged): test packet 0, msg_id 66, twice in D/1200 then twice in F/1200. The first on-air test. 2/2 good in each PHY; D arrives inverted, and both carry the byte-swapped 16-bit words that the `cal_tx` packing had then (sidecar `.json`: both PHYs decode) | 2026-11-04 about 01:48 UTC (file saved 01:48:39), image not logged |
| `k6cal-2400-20261104T0928.flac` | IC-7100 FM-D USB recording (48 kHz mono, 9.6-12.4 s of the original, samples unchanged) of the K6's own CAL burst: two D/2400 then two F/2400 test packets (sidecar `.json`: both PHYs decode) | 2026-11-04 09:28 UTC, f190a46 |

Log lines are `unix time<TAB>build<TAB>K6 ...`, as hamlet's listener writes them. See
docs/k6-cwid.md and docs/k6-pager.md.

## Every on-air recording is a test

A recording with a sidecar `NAME.json` (what it is, when, the image, and what each decoder got
from it) is replayed by `tests/test_k6_air_cases.py` in CI, with no test code to write. One
recording, one sidecar, so two threads adding recordings never touch the same file. Add one on
hamlet after a bench run:

    python3 tools/k6_air.py add ~/blowfang-work/runs/RUN/rec.wav NAME \
        --what "what is on it, what it showed" --image BUILD --when "2026-11-06 18:57" \
        --from 2.0 --to 21.0 --check ft8 --check cw:mode=tone

It trims to `--from`/`--to`, writes `NAME.flac` (16-bit, at most 2 MB) and `NAME.json`, and
refuses a check that decodes nothing. Then commit both. Decoders: `ft8`, `wspr`, `cw`
(`mode=tone` for MCW, `mode=quiet` for a keyed carrier heard on FM, as `cwkey`), `fsk`
(`phy`, `baud`, `payload`) and `psk31`. `python3 tools/k6_air.py check` replays them all.

Recordings worth keeping that are still only on hamlet:

- `ft8tx` bench run 6 (2026-11-06 18:57 UTC): the K6's own FT8 `CQ AG7EW CN87` and MCW ID
  (`--check ft8:lo=1300,hi=1800 --check cw:mode=tone`).
- `cwkey`: the K6's carrier-keyed AG7EW (`--check cw:mode=quiet`).
- Any of the WSPR beacon frames (`--check wspr`).
