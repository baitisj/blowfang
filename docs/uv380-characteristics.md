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
- K6 PHY R (600/1200 Hz FFSK, 1200 baud, coded) decodes 42 of 42 live (11-13 15:48Z, rangerx);
  channel errors climb within a burst to 50-150 of 2064, which the clock tracking still has to
  tighten (docs/k6-range.md).
- sotto's 8 / 16 kHz is only its synthesizer's output rate, so this 8 kHz receive path doesn't limit
  sotto.

## RAM modules

- Signed modules load over USB serial (`tools/uv380_i2sprobe.py mod`), no reflash (M, 11-13). The
  arena is 16 KB from the heap (128 KB SRAM, DMA-able); .bss/.data are CCM (not DMA-able, no code).
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
