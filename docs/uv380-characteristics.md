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

## Where audio enters the MCU (application note)

Every input the project has recorded from, what it carries, and how a module captures it. Read this
before choosing where to record: on 11-13 and 11-14 the mic was hunted on the wrong input for a day.

| Input | Hardware | What it carries | How to capture | Use it for |
| --- | --- | --- | --- | --- |
| C6000 codec ADC on I2S3 | HR_C6000 codec, SPI3 as an I2S slave, DMA; register 26 and RX_AUDIO_MUX (PD9) route it | In receive: the FM receiver's demodulated audio. It keeps carrying the receiver with 26 and PD9 set "off" (11-14 03:12Z micrec: a station's data and its CW ident, not the mic). With the cable in, also the IC-7100's audio through the shared USB ground. While keyed (tx_mic) with the cable out and the mic preamp (MIC_PWR) settled: THE MIC (11-14 08:56Z micrec_tx: Jeff's voice, loud). With the preamp just switched on, or the cable in: only a keying step and the CW ident's tone | rxaudio.h (rx_start, rx_start_as_is); i2sprobe "cap SPI3" | Receive audio, no mod: range RX, rxrec, rangerx. The mic: only while keyed, preamp on at least ~6 s first (micrec_tx) |
| ADC2 channel 3, PA3 (AIN_MIC) | The MCU's own ADC; the mic preamp is powered by MIC_PWR (PA13) | The internal mic's loudness envelope only: the VOX detector behind a diode and a filter (OpenRTX); a 1 kHz tone shows in its mean, not as 1 kHz (micscan 11-14 08:38Z). This is what OpenRTX's M17 transmit records (platform/drivers/audio/audio_MDx.cpp: SOURCE_MIC; Jeff 11-14 03:27Z: M17 records his voice) | micadc.c and micrec_adc.c: TIM2 update at 8 kHz triggers ADC2, DMA2 stream 2 channel 1 into a circular ring in scratch, no interrupt | Only the mic's loudness (VOX); not audio |
| ADC2 channel 13, PC3 (AIN_RTX) | The MCU's own ADC | OpenRTX's receive baseband input (SOURCE_RTX), only with the hardware mod that wires the C6000 to PC3 (D) | api->adc_capture (OpenRTX's input stream) | Nothing on the stock radio |

Things that decide whether a recording means anything:
- THE CABLE: the TYT USB cable in the side accessory jack switches the internal mic out. Record
  the mic with the cable unplugged; the module can't talk over USB meanwhile (api->stop() turns 1
  when it comes out, and lines written then are lost), so it reports after the replug.
- The receiver is always on in receive and leaks into the codec input; a "mic" recording made there
  in receive is the receiver. Listen for data bursts and the 800 Hz CW ident.
- A source wired to the bench PC reaches the codec input electrically, so it can't test the mic.
- The unplug registers as a PTT press: in PAGER mode it sends the current message with its ident
  (11-14 06:30Z and 08:38Z, 4/4 packets, 11.4 s). Let the IC-7100 listen before anything else.
- i2sprobe `auto 1` starves the UI; keep `auto 0`.

## Microphone to the MCU (no mod): the ADC on PA3, as M17 reads it, with the USB cable out

Tested 11-13 with modules micprobe, micprobe2, micadc and micspk (firmware/uv380-openrtx/mod/,
scored by tools/uv380_micprobe.py against a recording of what was played):

| | | |
| --- | --- | --- |
| As found | C6000 26=fe, E0-E5 = c9 0f 02 30 23 1a; MIC_PWR (PA13) an output, low; PD9 high | M |
| PA3 (OpenRTX's AIN_MIC, ADC2 ch 3) | sits at 0 V with MIC_PWR on: only the preamp's switch-on transient (about 850 counts, gone in 5 s), nothing from the room. Run with the USB cable in, which switches the mic out, so this says nothing about PA3: retest with it out (micrec_adc) | M, micadc 19:53Z |
| C6000 codec input on I2S3 | open with register 26 bit 6 or 7 cleared (7d, bd, 7c, 3d; also fd and fe without PD9's receive audio): carries about 100-240 rms of the IC-7100's demodulated audio through the bench's shared USB ground, mic preamp on or off, IC-7100 speaker silent; nothing acoustic | M, micprobe2 19:57-20:01Z |
| Codec sets for the mic | OpenGD77's DMR (26 7d, E0 8b, E2 06, E4 4a), OpenRTX's FM mic (E0 c1) and M17 line in (E0 83), and every single bit of E0, E2, E4, E5 and 26: no mic in receive | M |
| Own speaker as the source | no rise with the speaker on (but the speaker may have been silent at 26=7d: unconfirmed) | M, micspk 20:05Z |
| Keyed (OpenRTX FM mic transmit: E0 c1, pre-emphasis) | 3 s at 1 W on 440.850 (module mictx, 11-14 01:37Z): the IC-7100 heard a fully quieting but silent carrier, 300-3400 Hz at about -47 dB below the open-squelch noise, no room ambience. I2S3 while keyed: only a keying step below 300 Hz, nothing in the voice band. The CW ident's 800 Hz tone came through clean on air and on I2S3, so the transmit audio chain works | M, mictx 01:37Z |
| Keyed with someone talking | 5 s at 1 W (mictx, 11-14 02:43:04-02:43:09Z, Jeff talking into it): 300-3400 Hz at 30.6 rms, the same as the silent carrier, flat envelope (no syllables). The mic doesn't reach the modulator on this path | M, mictx5 02:43Z |
| THE CABLE | The TYT USB cable in the side accessory jack switches the internal mic out. FM PTT with the cable in: a silent carrier (02:46Z); with it unplugged Jeff's voice went out (02:47Z). Every result above ran with the cable in | M, Jeff 11-14 02:47Z |
| Cable out, codec input (micrec) | 11-14 03:12:59-03:13:51Z, Jeff talking, 3 s a setting: 26=7d mic on rms 665 pk 6252; 26=bd 400; OpenGD77 DMR set (E0 8b E2 06 E4 4a, +30 dB) 4405, pk 32768 (clips); OpenRTX FM mic (E0 c1) 840 pk 4788, clean; 26 as found 352. The "mic off" control read rms 1401 (not silent). 15 s recorded on the DMR set and encoded on the radio: 375 frames, no ring overruns (the encoder keeps up in real time). It was NOT the mic: Jeff heard a data stream and distorted CW (03:24Z), and the audio holds an 800 Hz keyed ident from 7.5 to 11 s: the receiver (the K6 upload on 440.850) leaking into the codec input whatever 26 and PD9 say | M, micrec 03:12Z |
| Cable out, PA3 (micrec_adc) | 11-14 03:42:15-03:42:51Z (d7f5404), Jeff talking on the orange LED: preamp off, PA3 at 0 V (mean 3 of 4095). Preamp on: a switch-on surge (100 ms means up to 736) decaying to about 85 over 6 s. Talking: the 100 ms ADC means swing 19-882 with the syllables, so the voice IS on PA3, but the pin sits at 0 V and only swings up: half of each waveform, most energy under 300 Hz (decoded: 65 dB at 100-300 Hz, 50 at 700-1500, 48 at 2500-3500). Fits OpenRTX's note that PA3 is the VOX detector behind diode D102, and that a clean mic needs a mod. 375 frames encoded, ov 0. 03:34Z take before it: gain from the surge, recorded after Jeff stopped talking (stop() missed the unplug; USB DSTS.SUSPSTS catches it) | M, micrec_adc 03:34Z and 03:42Z |
| Acoustic scan (micscan) | 11-14 08:38-08:41Z (add979e), cable out, IC-7100 sending a 1 kHz tone keyed by a 31-chip m-sequence at power 0, the UV-380 speaker playing it. Correlation with the pattern (noise about 170, max 1000): PA3's mean 807-821 with the preamp on and the speaker on (26=7d, bd, DMR set, PD9 low), 227 with the speaker off, 89-168 with the preamp off: an acoustic path. But PA3's 1 kHz content stays at level 1 while its mean swings 517 (rms 40): PA3 is the VOX detector's smoothed envelope, not audio. Codec input: r 970-995 at level about 10000 with the speaker and the preamp off: the receiver's electrical leak, no mic share in any setting. E0 c1 and 26=fe silence the speaker | M, micscan 08:38Z |
| Keyed, preamp powered at key-up (micrec_tx) | 11-14 08:46Z (ae83020), cable out, Jeff talking on the red LED: a clean 1 W key-up and ident, but a silent carrier (300-3400 Hz rms 30-37, the baseline) and I2S3 "set 0" rms 73. tx_mic's radio setup is the same as OpenRTX's FM PTT (startAnalogTx(MIC, pre-emphasis), E0 c1, MIC_PWR from audioPath_request(SOURCE_MIC, SINK_RTX)), but MIC_PWR came on only as it keyed, and the preamp settles over about 6 s (micrec_adc), longer than the 5 s carrier | M, micrec_tx 08:46Z |
| VOICE: keyed, preamp powered 26 s early (micrec_tx) | 11-14 08:56:24-08:56:32Z (1fadd13: MIC_PWR on at the unplug), cable out, stub on the IC-7100, 1 W: the IC-7100 heard Jeff's speech, 300-3400 Hz rms 750-3530 per 0.25 s (25-110x the silent carrier's 31), syllabic envelope, then the ident. AND the codec input on I2S3 carries the mic while keyed: 2.8 s recorded and sotto 1200 encoded on the radio (70 frames, ov 0), decoded rms about 11000 at gain 64, 2.4% clipped, strongest bins at 94-101 Hz (his voice's F0). Keyed gain now fixed at 16 (MR_TXGAIN). The orange LED (green + TX red) shows while keyed when no unplug send ran first | M, micrec_tx 08:56Z |
| Next | live sotto from the mic as PHY R range packets: record while keyed (as above), then send the recording; find whether a C6000/AT1846S setting gives the codec the mic without keying | I |

A source wired to the bench PC can't test the mic: its audio reaches the codec input electrically.

## RAM modules

- Signed modules load over USB serial (`tools/uv380_i2sprobe.py mod`), no reflash (M, 11-13). The
  arena is 16 KB from the heap (128 KB SRAM, DMA-able); .bss/.data are CCM (not DMA-able, no code).
- Heap (Plus, probe and range image): 72,192 B from _end 0x2000e600 to 0x20020000; Miosix thread
  stacks come from it too. About 16 KB goes at boot and 16 KB to the module arena, leaving about
  40 KB. The sotto 1200 encoder (24.4 KB state plus a 16 KB thread stack) didn't fit: the radio
  reset at the encoder's start (M, 11-14 01:27Z), so its state now comes from a 25 KB arena in CCM
  .bss (PR #101; CCM has about 37 KB free).
- OpenRTX's RTX thread has a 512 B stack, and the pager's send runs on it. The CW ident fits;
  the range send's packet coder needs about 460 B more, and the radio reset half a second after
  key-up, at the end of the lead (M, 11-14 01:59Z). The patch raises it to 2 KB (PR #101).
- Modules: -fropi thumbv7em, no writable statics, no pointer tables, no 64-bit divide, no libc.
- The loader flashed on 11-13 drops USB bytes past 256 while a module calls stop() (fixed in source,
  next flash): a module that reads data over USB reads the Ctrl-C byte itself (rrinject).
- The I2S probe's auto mode (`auto 1`: a pass every 5 s that takes 8 s) starves the UI thread: the
  screen and keys freeze while USB keeps working (M, 11-14 02:30-02:43Z). Leave it at `auto 0`.
- Never run c6sweep again: register B9 bit 2 puts the C6000 to sleep until a power cycle (M).

## Transmit and other limits

- RF to the MCU needs a hardware mod per OpenRTX (D); the no-mod receive path above is the
  exception found here. The mic needs no mod: OpenRTX's M17 reads it on PA3 (see the table above). No-mod transmit paths: MCU PWM into the C6000's line input, C6000 DAC (D).
- Pager / range transmit: FFSK from the MCU through the C6000 with pre-emphasis (PAGER mode);
  deviation not yet measured (I).
- Range send of a message the radio encoded itself works on air (M, 11-14 02:29:39-02:30:02Z,
  1a918c4): 8 s of ve9qrp in over USB, sotto 1200 on the radio, tx_request at 1 W on 440.850.
  The 22.4 s burst was 10 PHY R packets (02:29:40.2-02:29:56.9Z) then the CW ident (AG7EW, 20 wpm).
  The IC-7100 at RF power 0 on the same bench received 10/10 CRC ok with 0 channel errors, and all
  200 superframes matched the PC encoder byte for byte. A 1200 message costs about 1.7 s of air
  per second of speech this way.
- Its own PTT in range mode (PAGER, cable out; 11-14 02:48:40-02:51:42Z): 9 sends of the built-in
  message, 48 packets CRC ok, all e=0 except one corrected (e=92); three complete (10/10), the rest
  cut short by a second PTT press. Packets 1.86 s apart; the FFSK's polarity varies from send to
  send (the receiver takes either).
- Only Plus images (`mduv3x0p`) on Jeff's radio; a plain `mduv3x0` image is wrong for it (M).
- Calibration isn't dumpable; the stock firmware isn't readable (M, 11-13 backup).

See docs/uv380-port.md, firmware/uv380-openrtx/README.md, docs/k6-range.md.
