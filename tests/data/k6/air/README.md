# On-air data from Jeff's K6

Real recordings and serial logs that `tests/test_k6_air.py` replays in CI. Serial text and
audio only: no Baofeng, Aura or Yaesu firmware, no bootloader dumps.

| File | What | When, image |
| --- | --- | --- |
| `cwprobe-20261106T0506.log.gz` | K6 serial log of the CW probe module (REG_6F/REG_64 trackers, REG_0B event log) hearing the IC-7100's AG7EW IDs | 2026-11-06 05:06-05:19 UTC, 6473ca7 |
| `cwlisten-20261106T0529.log.gz` | K6 serial log of three cwlisten runs: AG7EW at 20/13/25 wpm on 800/600/1200 Hz, plus the upload-tail bursts | 2026-11-06 05:29-05:35 UTC, 6473ca7 |
| `k6cal-1200-20261104T0148.flac` | IC-7100 recording of the K6's first CAL burst (44.1 kHz mono, 0.24 s to the end of Jeff's `test.flac`, samples unchanged): test packet 0, msg_id 66, twice in D/1200 then twice in F/1200. The first on-air test. 2/2 good in each PHY; D arrives inverted, and both carry the byte-swapped 16-bit words that the `cal_tx` packing had then | 2026-11-04 about 01:48 UTC (file saved 01:48:39), image not logged |
| `k6cal-2400-20261104T0928.flac` | IC-7100 FM-D USB recording (48 kHz mono, 9.6-12.4 s of the original, samples unchanged) of the K6's own CAL burst: two D/2400 then two F/2400 test packets | 2026-11-04 09:28 UTC, f190a46 |

Log lines are `unix time<TAB>build<TAB>K6 ...`, as hamlet's listener writes them. See
docs/k6-cwid.md and docs/k6-pager.md.
