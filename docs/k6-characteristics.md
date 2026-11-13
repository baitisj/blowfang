# Baofeng UV-K6: characteristics and limitations

One place for what the K6 (Cortex-M0 MCU, FD6818 transceiver with BK4819 register numbering) can
and can't do for this project, with no hardware mod. **M** = measured on the bench, **I** =
inferred, **D** = documentation. Details live in the linked docs; keep this summary current when
one of them changes.

## Receiver

| | | |
| --- | --- | --- |
| Coverage | real reception 18 to about 630 MHz; the synthesizer's bottom is 15.5-16 MHz, so no HF below that; above 630 MHz the tune path receives nothing | M, k6-rxmap.md, k6-hf.md |
| Noise floors | chip's own floor -151..-154 dBm (front end off); 70 cm floor about -132 dBm (about kTB in 12.5 kHz); 2 m floor 21 dB higher at hamlet, not from the chip (site noise; the K6 itself is quiet to -135) | M, k6-fesw.md |
| GP0 IF tap | deaf: about -79 dBm for a usable signal (the 1-bit tap's own noise); needs S9+ signals; freeze the gain (AGC costs about 5 dB) | M, k6-if-fec.md, k6-aprs-clock.md |
| Plain FSK receiver | sign of the discriminator audio, 4800 a second (REG_58 00E9), clock follows zero crossings (about 3.93 samples a bit, slips now and then); up to 6348 baud; hears to about -122 dBm (DMR sync runs) | M, k6-range.md, k6-dmr.md |
| Tone (1200/2400) modem | reads Bell 202 only when mark >= space and the audio is clean: no pre-emphasised packet, nothing with noise 12 dB under | M, k6-aprs-clock.md |
| 9600 baud | the chip FSK modem can't | M, k6-ais.md |
| POCSAG | 512 baud perfect; 1200 works after the fix; 2400 never starts | M, k6-pocsag.md |
| DMR | sync detected to about -122 dBm by the plain receiver; voice and data not decoded | M, k6-dmr.md |
| PHY R (range) | 189 of 189 with no channel errors at every ladder setting, -20 down to -126 dBm meter readings (gain ladder, not the edge) | M, k6-range.md |
| AIS | works on air (upload aisships with --telemetry 0) | M, k6-ais.md |
| Front-end switch | turning the RF path off is a 48-52 dB attenuator | M, k6-fesw.md |

## Transmitter (module API level 3)

- Modules transmit on 440.850 MHz only, low power, with AG7EW CW/MCW ID; never airband. The core
  caps a module's key-up at 120 s (M, k6-own-fsk.md, tests/test_k6_txrules.py).
- The chip's FSK modem sends our packets bit-perfect (cal bursts) (M).
- Audio FSK from the tone oscillator (REG_70/71, continuous phase, about 0.097 Hz a step) reaches
  the carrier with tx_on's REG_30 0xC1FA: FT8 decoded at -14 dB on the IC-7100 (M, k6-ft8-wspr.md).
- No per-symbol crystal FSK: REG_3B/38 are latched until RX on (M, k6-own-fsk.md).
- With the PA off it still leaks about -40 dB at bench range: judge keying by edges (M). A module
  can stay keyed with the PA off (tx_pa(0)) as its lowest power: rangetx_lo does.
- tx_on takes about 1.4-1.9 s before RF is up (M).
- The tone oscillator (REG_70/71) is phase continuous across a REG_71 change, a same-word write
  does nothing, and tone 1 runs on while REG_70 has it off (M, tonewr K9 11-13). Tone 2 (REG_72,
  REG_70 bit 7) gives nothing (M).
- The MCU's clock runs about 270 ppm slow against the FD6818's crystal (and true time): the tone
  is 271 ppm fast in MCU time (M, K9). Anything that times audio on TIM17/TIM14 against the chip
  must trim for it (rangetx: RT_PPM 271). A register write takes about 117 us keyed (M).

## Memory and code

- Flash is the wall: 122,880 B for the app (core about 79 KB); voice and explorer don't fit together
  (M, k6-modules.md).
- RAM modules up to 11,776 B; SRAM 0x2000_0000-0FFF (vectors) is never touched (M).
- SPI flash isn't memory-mapped; code must be copied to RAM. Don't store sotto/messages in SPI flash
  (project rule; tables-as-ROM and the listed exceptions are OK).
- Every flashed image is a keyed build; every module is signed (project rule).

## Bench notes

- A module runs the instant its upload ends, while the IC-7100 may still be keyed: a TX module waits
  for a clear channel first (M).
- The garage lamp adds 4-10 dB to the 2 m floor for about 11 minutes after motion (M).

See docs/k6-modules.md (Insights), docs/k6-fd6818-audio.md, docs/k6-receiver.md.
