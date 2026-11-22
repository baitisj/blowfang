# The ADALM-Pluto on the bench

*2026-11-16. Code: `tools/pluto_bench.py`, `tests/test_pluto_bench.py`.*

From 2026-11-16, Jeff's standing preference is that the Pluto is the standard instrument for 2 m
and 70 cm HT tests (the K6, the UV-380, the RT-950). That frees the IC-7100 for other projects,
Ragtime above all. Use the IC-7100 only as the frequency reference for `cal` (one CW ID at RF
power 0), or for a test that needs it, such as one that copies an earlier IC-7100 run.

The Pluto is flat both ways, where the IC-7100's USB audio isn't. The Pluto sends FM straight
from IQ, with no pre-emphasis, no low cut and no 3 kHz top, and its receiver hands back the raw
discriminator. That's the path the K6's chip FSK at 2400 baud needed (docs/k6-fsk24.md).

## The unit

| | |
| --- | --- |
| model | PlutoSDR Rev.B, ad9364 (tunes 70 MHz to 6 GHz, so 2 m works without an unlock) |
| firmware | stock ADI v0.39 (kernel 6.1) since 11-16 19:51Z (was v0.30-27-gd5bb PlutoWeb, with an auto-started OpenWebRX) |
| <!-- SITE-SPECIFIC --> serial | 104473ae73930001edff2200de632ddf03 |
| <!-- SITE-SPECIFIC --> crystal | 40 MHz, +-25 ppm. Measured 11-16 against the IC-7100: 39,999,447 Hz (-13.8 ppm; it read the IC-7100 5.15 kHz high on 440.850 with the old xo_correction 39999914). With that stored, a second check read +150 Hz (0.3 ppm) |
| <!-- SITE-SPECIFIC --> on hamlet | USB, libiio 0.26 on hamlet. The tool's default URI is `ip:192.168.2.1` (the Pluto's USB network link; hamlet is 192.168.2.254). The `usb:` address changes with each replug (1.35.5, 1.37.5, 1.38.5 on 11-16), and streaming over `usb:` fails (below). A udev rule gives libiio devices mode 666. No pyadi-iio: the tool uses the command-line tools |
| TX port | rubber duck (Jeff, 11-16) |
| <!-- SITE-SPECIFIC --> RX port | the stock stub, which is poor at 440 MHz on purpose (see below). Since 11-22 03:54Z: the big 2 m/70 cm antenna (Jeff), for repeater and control-channel captures; see "Antennas and what the receiver hears" |

**Protect the receiver.** The AD936x's RX input is rated to about +2.5 dBm. A 5 W HT a metre
away, duck to duck, puts about +10 dBm on it. So keep the stub (or a stub plus a 20-30 dB SMA
pad) on RX, keep HTs a few metres away when they key, and keep the RX gain manual and low
(30 dB by default).

## Commands

```
python3 tools/pluto_bench.py info                    state, tuning range, stored calibration
python3 tools/pluto_bench.py quiet                   TX LO down, DDS test tones off
python3 tools/pluto_bench.py send WAV [--hz F] [--dev 3000 | --peak HZ] [--gain -60] [--preemph] [--rf-off]
python3 tools/pluto_bench.py rx OUT.cf32|OUT.wav [--hz F] [--seconds 10] [--gain 30]
python3 tools/pluto_bench.py rx OUT.wav --wav-only [--wav-rate 8000]     FM audio only, no IQ kept
python3 tools/pluto_bench.py rx OUT.wav --hz LO --channels F1,F2,...    one FM WAV per channel
python3 tools/pluto_bench.py chan BIN OUTDIR --hz LO --channels F1,F2,... [--seconds 60] [--gain 50]
python3 tools/pluto_bench.py chan --probe                            the Pluto's CPU, memory, iio_readdev
python3 tools/pluto_bench.py measure CAP.cf32        offset (Hz, ppm), peak deviation, level, floor
python3 tools/pluto_bench.py cal CAP.cf32 [--ref-hz F] [--save]
python3 tools/pluto_bench.py fsk24 [--rounds 20]     docs/k6-fsk24.md run 3
python3 tools/pluto_bench.py fsk24 --sweep --follow LOG --gain -70   the K6 sensitivity sweep
python3 tools/pluto_bench.py steps WAV --gains -70,-73,... [--preemph] [--dev 3000 | --peak HZ] [--out J]
python3 tools/pluto_bench.py cue WAV --hz F --listen-hz F [--wait-s 900] [--delay-s 1] [send's options]
```

- **Match an upload's level with `--peak`.** `--dev` is full scale, and `modem.k6` wavs are half
  scale, so `--dev 3700` sends them at about 1850 Hz peak, 4 dB under `k6_upload --via pluto`'s
  F/1800 (3000 Hz). That cost F/1800 test packets most of their packets on 11-16 and 11-17
  (docs/k6-fsk24.md). `--peak 3000` scales the wav by its own peak instead.
- **send:** plays any audio file as FM. Full scale is `--dev` Hz of peak deviation (3 kHz by
  default). Data goes flat; add `--preemph` (750 us, the usual narrowband FM voice curve) for
  speech an HT will de-emphasise. AG7EW in MCW goes before and after it, always.
- **rx:** captures with the TX LO down. It writes complex64 at 240 kHz and a `.json` beside it
  (frequency, rate, gain, the xo_correction in use, the UTC start). Name it `.wav` to also get the
  FM discriminator as 48 kHz audio: flat, with no de-emphasis, and 5 kHz deviation as full scale.
  It receives anywhere from 70 MHz to 6 GHz.
- **rx --wav-only:** streams the FM audio to the WAV (8 kHz by default) and keeps no IQ, plus the
  carrier level each 0.1 s in the `.json`: long receive captures (20 minutes is 19 MB).
- **rx --channels:** several FM channels from one capture (a channelizer, the idea behind
  RTLSDR-Airband and trunk-recorder's conventional mode). `--hz` is the LO; every channel must sit
  within +-1.16 MHz of it (the 2.4 MS/s stream) and 15 kHz or more away from it (the DC spur). Each
  channel is mixed down, filtered to +-8 kHz at 32 kS/s, discriminated and written as
  `OUT_<MHz>.wav` with its own `.json` of levels, as `--wav-only` does. Six channels take about 1.5
  times the CPU of one `--wav-only` capture; the last line says whether it kept up. One band at a
  time: the Pluto has one RX LO, so 2 m and 70 cm need separate captures.
- **chan:** the same split done on the Pluto's own ARM (Jeff 2026-11-22: "an on device
  channelizer", with HamFLAC to cut the bandwidth). `tools/pluto_chan/` is a small C program
  (`build.sh`, static armhf). The tool sets the radio up from hamlet as `rx` does, copies BIN into
  the Pluto's RAM over SSH on the USB Ethernet (192.168.2.1, root, `$PLUTO_PASS` or the stock
  password), and runs `iio_readdev | pluto_chan -f` there. Each channel is squelch gated and
  each over comes back as its own HamFLAC stream, so idle channels cost a few bytes a second
  (`tools/pluto_chan/flacout.cpp` has the format). OUTDIR gets `<MHz>_<UTC>.flac` an over,
  `levels.json`, `overs.json` and `chan.json` (timing, the Pluto's own report). BIN must be listed
  in `tools/pluto_chan/allowed.sha256`: a queued job ships the binary as a job file, and root on
  the Pluto could key its TX, so only a reviewed build may go there. Nothing is flashed; a
  reboot or the tool's own cleanup removes it.
- **measure:** reads only where the signal is 20 dB over the capture's floor.
- **cal:** calibrates against a carrier on a known frequency; the IC-7100's TCXO is the bench
  reference. `--save` writes `$BLOWFANG_WORK/state/pluto-cal.json`, and every later command sets the
  AD9361's xo_correction from it, which moves TX and RX together. The Pluto forgets
  xo_correction when it powers off; the stored file puts it back.

- **fsk24 --sweep:** the stepped-level signal for `c/k6/mod/fsk24rx_sweep.c` (docs/k6-fsk24.md).
  The level steps are digital, and the signal sits 250 kHz above the TX LO (`--if-hz`), so the
  LO's leak and the IQ image stay off the channel; the tool checks that all three stay in 70 cm.

- **steps:** one audio file sent once at each TX gain, each send a transmission of its own (ID
  first and last), with the times in `--out` for a report against a receiver's log: a
  sensitivity ladder through any receiver, with no module on it.

- **cue:** the over-the-air trigger for a module running with its programming cable out, when
  the job can't read its log (Jeff 2026-11-21, after the micgain run mgn1b). It listens on
  `--listen-hz` for one burst 1.5-15 s long (the K6's CW ID, its "ready" call), ignores shorter
  or longer ones, then sends WAV on `--hz` as `send` does. One stage per job step, so steps in
  between (the IC-7100's AF) run between stages. The ear is the parrot's (tools/pluto_parrot.py):
  RX LO 50 kHz above the channel, channel power 10 dB over the floor. The runner wants both
  frequencies inside the job's claim and `--wait-s` at most 1700.

Python can use it as a library: `pluto_bench.capture()`, `measure()`, `audio_track()` and
`send()`, where `send()` takes a frequency track in Hz at 48 kHz.

## Firmware

Stock ADI firmware is what the tool expects: plain iiod, with nothing on the Pluto holding the
receiver. To update, copy `pluto.frm` to the Pluto's USB drive ("PlutoSDR") and eject it. The
Pluto then writes flash for 3-4 minutes, with LED1 blinking fast (don't unplug it), and reboots.
`tools/pluto_fw_update.sh PLUTO.frm [SHA256]` does all of that: it checks the image, mounts the
drive (sudo when there's no udisks), copies, ejects, waits for the Pluto to come back, prints the
new version and runs `quiet`. An update leaves the u-boot environment alone, so the ad9364 mode and
xo_correction stay. v0.39 (2024-10-15, Linux 6.1), installed 11-16 with this script, is kept on hamlet in
`~/blowfang-work/pluto-fw/v0.39/`; the sha256 of its pluto.frm is
6506786f9055282acf031a2af057e563deb6bdb4abed6820ddcc67845312e69f.

## Transmit rules (the tool enforces them)

- Only 430.010-449.990 MHz (440.850, the K6 bench frequency, by default). Transmitting on 2 m or
  anywhere outside 70 cm needs Jeff's OK and a change in the tool.
- Gain at most -30 dB. The default -60 is about -55 dBm into the antenna, plenty for an HT a
  metre or two away.
- Only under the bench's PTT lock and owner check (`tools/k6_bench.py`, the same ones the
  IC-7100 uses), so one bench user at a time still holds. Every transmission goes into the bench's
  txlog and ID log.
- AG7EW in MCW (800 Hz at 2.5 kHz deviation) before and after every transmission.
- The TX LO is powered down before the stream starts and after it ends, and on any error, so there
  is never a bare carrier outside a transmission. `--rf-off` streams with it left down: a
  rehearsal that checks USB keeps up at 2.4 MS/s (it did on 11-16: 25.4 s in 25.4 s).

## Gotchas found on 11-16

- **The RX LO leaks out of the RX port.** After a capture it stayed on 440.849998 MHz, 2 Hz under
  the K6's channel, until it was moved. `quiet` and every capture now park it at 2.4 GHz.

- **Don't stream over `usb:`.** iio_writedev claims the Pluto's USB interface for itself, so the
  tool's iio_attr calls, which power the TX LO up and down, can't open their own context
  ("Unable to claim interface"). Over `ip:192.168.2.1` both work at once. The tool refuses to
  stream over `usb:`.

- **It boots transmitting.** Out of the box the Pluto powers up with its TX LO on at 2.45 GHz,
  gain -10 dB and all four DDS test tones keyed. Run `quiet` after every replug or power cycle;
  every command also does it first.
- **libiio 0.26's `iio_attr -q` prints nothing for a read.** Reads go without `-q`, and the tool
  takes the value from the `value '...'` line.
- **`voltage0` is both an RX and a TX channel on ad9361-phy.** A write without `-i` or `-o` goes
  to both. A gain of +30 then fails on the TX side, which only takes -89.75 to 0. The tool always
  passes one or the other.
- **"Unable to allocate buffer: Device or resource busy" on every capture:** something on the
  Pluto holds the RX buffer, and a replug doesn't clear it. On 11-16 this unit ran PlutoWeb
  firmware (the web page on port 80 says "PlutoWeb"). It auto-starts the original OpenWebRX (Python
  2.7, port 8073) at boot: 460.25 MHz, 600 kS/s, gain 73. Transmit stays free. The fix is to turn
  PlutoWeb's autostart off, or to install stock ADI firmware. Jeff installed v0.39 at 19:51Z,
  and receive has worked since.

## Antennas and what the receiver hears (11-22)

How to read an antenna with the Pluto, passively: compare captures at the same frequency and
the same RX gain, on steady signals (TV pilots, NOAA, control channels, broadcast carriers);
read the receiver's own floor at high gain on an empty frequency well above the bands the
antenna covers; and check sample peaks for clipping before trusting a floor. Levels are the
Pluto's own scale, dB re full scale minus the RX gain, so they compare with each other but are
not dBm.

<!-- SITE-SPECIFIC: begin -->

On 11-22 03:54Z Jeff moved the big 2 m/70 cm antenna onto the RX port. A passive study ran beside
the other jobs that night (jobs antstudy-1122a/b, bigant-1122a-d, all receive only). The stub sat on the bench and the big antenna is wherever Jeff mounted it, so "gain
over the stub" mixes antenna gain and location, and it varies with the station's direction.

Same frequency, same RX gain, stub (11-18) against the big antenna (11-22):

| signal | gain | stub | big antenna | change |
| --- | --- | --- | --- | --- |
| P25 control channel 770.31875 (WSDOT site 48) | 40 | at the noise | 47 dB over the floor | at least +22 dB |
| other 769-773 MHz control and voice channels | 40 | at the noise | 39-49 dB over the floor | at least +13 to +21 dB |
| 863.819 narrow carrier | 65 | 18 dB over the floor | 44 dB over the floor | +16 dB |
| TV channel 14 pilot (470.309) | 30 | -125 (+-3 dB over 13 captures, 11-18 06-10Z) | -119 | +6 dB |
| TV channel 35 pilot (596.309) | 30 | at the noise (-132) | -123 | at least +10 dB |
| weak signals 426.3-426.7 | 65 | -130..-133 | -124 | +6 to +9 dB (they come and go) |

The receiver's own floor, at gain 70, is -154/-155 on this scale (900, 1300 and 2000 MHz with the
big antenna). At 433.5 MHz the floor reads -149: the antenna brings in about 6 dB of outside noise
over the Pluto's own, so on 70 cm the site sets the floor and more RX gain buys nothing. At 852
and 855 MHz (gain 65) the floor is the Pluto's own, -154.

On 11-18 the stub's floor at 851-869 MHz (gain 65) read -127, 27 dB higher than now, with sample
peaks at 0.2-0.3 of full scale against 0.01 now. Something impulsive was on the bench that day,
which fits the broadband site noise first seen 11-18/19 (project memory hamlet-vhf-noise).

**Overload on VHF.** At gain 70 the 2 m and 222 MHz captures clip (sample peaks 0.48 at 145.0, 0.58
at 147.0, 0.98 at 223.5 MHz). With this antenna, keep VHF captures at gain 50 or below; at gain 40
NOAA reads cleanly.

What it hears (one pass, 11-22 04:44-04:46Z):

| band | heard |
| --- | --- |
| FM broadcast (gain 20) | all ten channels tried; strongest 88.1, 92.9, 105.7 MHz |
| NOAA Weather Radio (gain 40) | 162.400, 162.475 and 162.550 all, 36-44 dB over the floor |
| UHF TV (gain 30) | pilots on channels 14, 17, 22, 23, 24, 28 (strongest, 25 dB over the floor), 30-32. Channels 14, 24, 26 and 35 also carry a flat wideband signal with only a weak pilot on top, likely ATSC 3.0 (OFDM, no pilot) sharing the channel with a distant ATSC 1.0 station; not checked further |
| 700 MHz | LTE downlinks at 739, 751 (strongest) and 763 MHz; WSDOT P25 at 769-775 |
| 800 MHz | 851.8125 and 863.819 MHz only |
| above 1 GHz (gain 30) | PCS 1960 MHz moderate; AWS 2120 and Wi-Fi 2437 MHz weak |

Scripts and job files: project files position/antenna/ (antana.py) and position/p25/.

<!-- SITE-SPECIFIC: end -->
