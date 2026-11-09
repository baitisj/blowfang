# 10. A weak-signal mode: constant-envelope MFSK on SSB

- From: Jeff's PSK idea, simulated at equal peak power (`sim/k6if/psk.py`). Differential
  PSK only ties FM-FSK with the IF detector, because RRC PSK loses 3.4 dB to its peak.
  16 tones at 100 baud from the IC-7100 in USB gain about 3.8 dB per bit and need no
  carrier sync. Coherent BPSK with a perfect carrier loop is the stretch goal (+3.7 dB
  coded).
- Buys: beacons, text and control at 200-400 bit/s, several dB below what FFSK can reach.
- Settled by: a third `gp0cap` pass with SSB 16FSK, decoded on the host.
