# 1. Receive audio through the FD6818's I2S port

- From: the CW ID thread, 2026-11-06 ([k6-fd6818-audio.md](../k6-fd6818-audio.md)).
- The guide's MCBSP/I2S port can output FM-demodulated audio on a GPIO, and the K6 wires
  FD6818 GP0 to MCU PB1 (the CTCSS net; pin 19, not PB0). Only one wire, so the MCU would
  have to recover the clock itself. The BK4819 map may not have the block.
- Buys: received audio samples with no hardware change, so a software modem past the chip
  FSK's 2400 baud slips, maybe live sotto 2400. It gives no mic samples.
- Settled by: the I2S probe thread (`i2sprobe`, a module that puts the frame clock on GP0
  and counts PB1 edges). Result (2026-11-06, PR #68): no I2S port, but GP0 output code 13
  is a 1-bit sigma-delta stream of the receive low IF (about 17 kHz on a carrier, moving
  with the deviation), readable on PB1. Run 5 (`gp0fm`) settled the next question: 28 ms of
  IF decimated on the MCU demodulated on the host to a clean 1000.9 Hz tone at +-4.3 kHz
  deviation, about 25 dB SINAD. PR #69 then did it on the MCU: DMA channel 1 reads GPIOB IDR
  flat out (about 5.5 MS/s; TIM17 can't pace it), and `gp0dma` demodulated a 1 kHz tone on
  the K6 for 3 s continuously (996-1006 Hz, ±4.1 kHz, nothing lost, 27% of the CPU). Open:
  a software modem on that path, and whether the always-on DMA hurts the pager's own work.
