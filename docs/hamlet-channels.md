# Hamlet bench channels (70 cm)

On 2026-11-16 everything on the bench shared 440.850 MHz: the K6 link, the IC-7100's uploads and
CW IDs, the Pluto's tests, the UV-380's key-ups from another thread, and for a while the Pluto's
RX LO. With several threads on the bench at once, any one of them can land on another's receiver
without anyone seeing it (the K6 stopped decoding at about 22:45Z that night, and finding out
which transmitter was not to blame took two hours). Jeff, 11-16: "it's not wise to have
everything on the same frequency on the bench." So each kind of work gets its own channel.

| Channel | Who | What |
| --- | --- | --- |
| 440.850 MHz | K6 work | the K6 link: module uploads, fsk24, pager, voice tests; the K6's own TX (CW ID AG7EW) |
| UV-380 channel, picked by the survey below | UV-380 work that doesn't involve the K6 | UV-380 to Pluto or IC-7100 (DMR, c6phy, FSK, mic tests) |
| off 70 cm (2.4 GHz park) | the Pluto's receiver when idle | its RX LO leaks out of the RX port |
| TV pilots (470-600 MHz), NOAA 162 MHz | frequency reference captures | receive only |

Work that pairs the UV-380 with the K6 (the UV-380 sending to fsk24rx_uv) stays on 440.850 under
the K6 thread's claim, as one run.

## Claims name their channel

`python3 tools/k6_bench.py claim WHO MIN WHAT --freq MHZ` holds one channel instead of the whole
bench (docs/hamlet-permissions.md). A second claim within 100 kHz of it is refused, and so is
anyone else's key-up there. Claims on different channels run side by side; the PTT lock still
keeps the IC-7100 and the Pluto to one keyer at a time. A claim without `--freq` is the old
whole-bench claim and waits until every channel is free.

## Moving the UV-380

The UV-380 image transmits only on 440.850 (`SEND_HZ` and the boot channel in
firmware/uv380-openrtx/uv380-blowfang.patch, and the "440.850 only" checks behind tx_tone,
tx_mic and tx_request). Moving its work means an image change to the new channel, flashed
hands-free with tools/uv380_dfu.py, plus the bench scripts' Pluto and IC-7100 frequencies. That
is the UV-380 thread's change.

## Picking the UV-380 channel

A receive-only Pluto survey (11-17) of 440.000-441.900, 445.800-446.300 and 433.000-435.000 MHz,
3 rounds of 10 s per 240 kHz window, per 12.5 kHz channel. A candidate must be:
- at least 150 kHz from 440.850,
- outside 442-443 MHz (the IC-7100 switches on a +5 MHz shift there),
- outside 446.000 ±25 kHz (calling),
- outside 432.0-432.3 MHz (weak signal) and 435-438 MHz (satellites).

The result and the chosen channel go below.
