# UV-380: characteristics and limitations

What the TYT MD-UV380 (10 W Plus, OpenRTX `mduv3x0p` with uv380-blowfang.patch) can and can't do
for this project, with no hardware mod. **M** = measured on the bench (date, run), **I** = inferred,
**D** = from documentation (OpenRTX, datasheets). Keep this current: a new finding goes here as
well as in its own doc.

## Receive audio to the MCU (no mod)

The FM receiver's demodulated audio reaches the MCU through the HR_C6000's codec. C6000 register 26
= fd plus RX_AUDIO_MUX (PD9) high route it into the codec ADC, and the MCU reads it on I2S3 (SPI3 as
an I2S slave) by DMA. Touches no RF register. Code: firmware/uv380-openrtx/mod/rxaudio.h. (M, 11-13
14:52Z tone run, c6listen)

| | | |
| --- | --- | --- |
| Sample rate | exactly 8000 Hz (48000 halfwords in 3 s, 64000 in 4 s) | M, 11-13 rxrec and rangerx |
| Format | 16-bit two's complement, Philips, 32-bit slots, each sample sent twice a halfword apart | M |
| Alignment | the slave must be enabled on a WS edge, or every halfword is a sample's last bits and fill (white, 25% clipped); rxaudio.h retries until halfword pairs match; 6 of 6 starts aligned first try | M, 11-13 run 4/5 |
| Gain | C6000 register E1 is a digital gain on the I2S data: 0f idle, 0e x2, 0b x4, 1f x16, 4f x32. E3 is partly a status register | M, 11-13 c6codec |
| Passband (linear, 10 dB under normal level) | flat within 1 dB 400-2000 Hz; -3 dB at about 300 and 2500 Hz; -10 dB at 3000; brick wall 3.2-3.3 kHz (-24 dB at 3200, -48 at 3400); nothing below 250 Hz (-84 dB at 200) | M, 11-13 16:34Z sweep (IC-7100 FM-D sending: the figure is both radios together) |
| De-emphasis | the IC-7100-to-UV-380 chain is flat, as a pre-emphasis / de-emphasis pair is: the UV-380 gives de-emphasised voice-band audio, not the discriminator | I |
| Compression | at the IC-7100's normal level (wav peak 0.3, USB MOD 30%) 1000 Hz is 2.5 dB under linear and everything above about 700 Hz falls 6 dB an octave: the shape of a transmit limiter after pre-emphasis (likely the IC-7100's) | M (shape), I (where) |
| Headroom | at normal level the 600 Hz tone peaks at 29800 of 32767: under 1 dB of digital headroom at E1 0f | M |
| Noise, strong carrier | about 47 rms between tones, about 50 dB under the tones | M, sweep |
| Noise, no carrier | the route ignores squelch: open receiver noise about 8000-13000 rms, shaped like the passband (PSD 52 dB at 300-1000 Hz, 46 at 2000, 35 at 3000, 21 at 3500) | M |
| Sensitivity | not measured against signal level yet | - |
| RSSI | the RTX thread's cached value: -12 dBm with the IC-7100 at power 0 on the bench, -95/-96 with nothing | M |

### For modems and codecs

- Keep a modem's energy between about 300 and 2500 Hz, and its level about 10 dB under the IC-7100's
  normal 0.3 / 30% (it compresses there).
- K6 PHY R (600/1200 Hz FFSK, 1200 baud, coded) decodes 42 of 42 live (M, 11-13 16:44Z run 7):
  channel errors a packet mean 5, max 16 of 2064, steady through a burst, with the receiver's
  fractional edge timing (before it, run 5: climbing to 50-150; docs/k6-range.md).
- The receive path de-emphasises: on a flat source (the K6's tone) about +4 dB at 600 Hz, -2 at
  1200, -5 at 1450 against 1000 Hz; a pre-emphasised one (the IC-7100's FM) comes out flat
  (+-0.7 dB 350-2300 Hz). Phase, the same for both: a steep high-pass under 450 Hz (-160 deg at
  400), then rising above 1 kHz (+23 at 1200, +54 at 1400) (M, K10 and U7 11-13,
  tests/data/uv380/rx-response-20261113.txt). A flat FFSK sender reads 0 of 6; send pre-emphasised.
- The receiver (range_rx.c) follows a sender whose tones walk against its bits (the K6: 270-380
  ppm, wandering): 6 of 6 in simulation from -400 to +500 ppm (I), and on air from the K6 at
  about 540 ppm wandering +-100 degrees, every packet with no errors (M, K12 11-13 18:46Z).
- rangetap's scratch has 52 bytes free (M, K12): the range modules are at the 16 KB arena's edge.
- sotto's 8 / 16 kHz is only its synthesizer's output rate, so this 8 kHz receive path doesn't limit
  sotto.

## Microphone to the MCU (no mod): not found yet

Tested 11-13 with modules micprobe, micprobe2, micadc and micspk (firmware/uv380-openrtx/mod/,
scored by tools/uv380_micprobe.py against a recording of what was played):

| | | |
| --- | --- | --- |
| As found | C6000 26=fe, E0-E5 = c9 0f 02 30 23 1a; MIC_PWR (PA13) an output, low; PD9 high | M |
| PA3 (OpenRTX's AIN_MIC, ADC2 ch 3) | sits at 0 V with MIC_PWR on: only the preamp's switch-on transient (about 850 counts, gone in 5 s), nothing from the room. OpenRTX's mic route isn't wired on this Plus | M, micadc 19:53Z |
| C6000 codec input on I2S3 | open with register 26 bit 6 or 7 cleared (7d, bd, 7c, 3d; also fd and fe without PD9's receive audio): carries about 100-240 rms of the IC-7100's demodulated audio through the bench's shared USB ground, mic preamp on or off, IC-7100 speaker silent; nothing acoustic | M, micprobe2 19:57-20:01Z |
| Codec sets for the mic | OpenGD77's DMR (26 7d, E0 8b, E2 06, E4 4a), OpenRTX's FM mic (E0 c1) and M17 line in (E0 83), and every single bit of E0, E2, E4, E5 and 26: no mic in receive | M |
| Own speaker as the source | no rise with the speaker on (but the speaker may have been silent at 26=7d: unconfirmed) | M, micspk 20:05Z |
| Keyed (OpenRTX FM mic transmit: E0 c1, pre-emphasis) | 3 s at 1 W on 440.850 (module mictx, 11-14 01:37Z): the IC-7100 heard a fully quieting but silent carrier, 300-3400 Hz at about -47 dB below the open-squelch noise, no room ambience. I2S3 while keyed: only a keying step below 300 Hz, nothing in the voice band. The CW ident's 800 Hz tone came through clean on air and on I2S3, so the transmit audio chain works | M, mictx 01:37Z |
| Left to try | someone talking into it during a keyed run (the room was quiet, so a live but very insensitive mic isn't fully ruled out) | I |

A source wired to the bench PC can't test the mic: its audio reaches the codec input electrically.

## RAM modules

- Signed modules load over USB serial (`tools/uv380_i2sprobe.py mod`), no reflash (M, 11-13). The
  arena is 16 KB from the heap (128 KB SRAM, DMA-able); .bss/.data are CCM (not DMA-able, no code).
- Heap (Plus, probe and range image): 72,192 B from _end 0x2000e600 to 0x20020000; Miosix thread
  stacks come from it too. About 16 KB goes at boot and 16 KB to the module arena, leaving about
  40 KB. The sotto 1200 encoder (24.4 KB state plus a 16 KB thread stack) didn't fit: the radio
  reset at the encoder's start (M, 11-14 01:27Z), so its state now comes from a 25 KB arena in CCM
  .bss (PR #101; CCM has about 37 KB free).
- Modules: -fropi thumbv7em, no writable statics, no pointer tables, no 64-bit divide, no libc.
- The loader flashed on 11-13 drops USB bytes past 256 while a module calls stop() (fixed in source,
  next flash): a module that reads data over USB reads the Ctrl-C byte itself (rrinject).
- Never run c6sweep again: register B9 bit 2 puts the C6000 to sleep until a power cycle (M).

## Transmit and other limits

- Mic and RF to the MCU need hardware mods per OpenRTX (D); the no-mod receive path above is the
  exception found here. No-mod transmit paths: MCU PWM into the C6000's line input, C6000 DAC (D).
- Pager / range transmit: FFSK from the MCU through the C6000 with pre-emphasis (PAGER mode);
  deviation not yet checked on air (I).
- Only Plus images (`mduv3x0p`) on Jeff's radio; a plain `mduv3x0` image is wrong for it (M).
- Calibration isn't dumpable; the stock firmware isn't readable (M, 11-13 backup).

See docs/uv380-port.md, firmware/uv380-openrtx/README.md, docs/k6-range.md.
