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
| firmware | v0.30-27-gd5bb (PlutoWeb, as found on 11-16), kernel 4.14.0, iiod 0.17 |
| serial | 104473ae73930001edff2200de632ddf03 |
| crystal | 40 MHz, +-25 ppm (up to 11 kHz at 440 MHz). xo_correction was 39999914 as found (someone calibrated it once) |
| on hamlet | USB, libiio 0.26 on hamlet (`usb:1.35.5` on 11-16, the address can change; `ip:192.168.2.1` works too, hamlet is 192.168.2.254). A udev rule gives libiio devices mode 666. No pyadi-iio: the tool uses the command-line tools |
| TX port | rubber duck (Jeff, 11-16) |
| RX port | the stock stub, which is poor at 440 MHz on purpose (see below) |

**Protect the receiver.** The AD936x's RX input is rated to about +2.5 dBm. A 5 W HT a metre
away, duck to duck, puts about +10 dBm on it. So keep the stub (or a stub plus a 20-30 dB SMA
pad) on RX, keep HTs a few metres away when they key, and keep the RX gain manual and low
(30 dB by default).

## Commands

```
python3 tools/pluto_bench.py info                    state, tuning range, stored calibration
python3 tools/pluto_bench.py quiet                   TX LO down, DDS test tones off
python3 tools/pluto_bench.py send WAV [--hz F] [--dev 3000] [--gain -60] [--preemph] [--rf-off]
python3 tools/pluto_bench.py rx OUT.cf32|OUT.wav [--hz F] [--seconds 10] [--gain 30]
python3 tools/pluto_bench.py measure CAP.cf32        offset (Hz, ppm), peak deviation, level, floor
python3 tools/pluto_bench.py cal CAP.cf32 [--ref-hz F] [--save]
python3 tools/pluto_bench.py fsk24 [--rounds 20]     docs/k6-fsk24.md run 3
```

- **send:** plays any audio file as FM. Full scale is `--dev` Hz of peak deviation (3 kHz by
  default). Data goes flat; add `--preemph` (750 us, the usual narrowband FM voice curve) for
  speech an HT will de-emphasise. AG7EW in MCW goes before and after it, always.
- **rx:** captures with the TX LO down. It writes complex64 at 240 kHz and a `.json` beside it
  (frequency, rate, gain, the xo_correction in use, the UTC start). Name it `.wav` to also get the
  FM discriminator as 48 kHz audio: flat, with no de-emphasis, and 5 kHz deviation as full scale.
  It receives anywhere from 70 MHz to 6 GHz.
- **measure:** reads only where the signal is 20 dB over the capture's floor.
- **cal:** calibrates against a carrier on a known frequency; the IC-7100's TCXO is the bench
  reference. `--save` writes `$BLOWFANG_WORK/state/pluto-cal.json`, and every later command sets the
  AD9361's xo_correction from it, which moves TX and RX together. The Pluto forgets
  xo_correction when it powers off; the stored file puts it back.

Python can use it as a library: `pluto_bench.capture()`, `measure()`, `audio_track()` and
`send()`, where `send()` takes a frequency track in Hz at 48 kHz.

## Firmware

Stock ADI firmware is what the tool expects: plain iiod, with nothing on the Pluto holding the
receiver. To update, copy `pluto.frm` to the Pluto's USB drive ("PlutoSDR") and eject it. The
Pluto then writes flash for 3-4 minutes, with LED1 blinking fast (don't unplug it), and reboots.
`tools/pluto_fw_update.sh PLUTO.frm [SHA256]` does all of that: it checks the image, mounts the
drive (sudo when there's no udisks), copies, ejects, waits for the Pluto to come back, prints the
new version and runs `quiet`. An update leaves the u-boot environment alone, so the ad9364 mode and
xo_correction stay. v0.39 (2024-10-15, Linux 6.1) is staged on hamlet in
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
  PlutoWeb's autostart off, or to install stock ADI firmware.
