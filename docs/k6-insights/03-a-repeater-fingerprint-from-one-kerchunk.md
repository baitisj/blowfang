# 3. A repeater fingerprint from one kerchunk

- From: the CW ID thread.
- The CW ID gives the call, tone and speed; the chip's CTCSS/DCS scan (REG_68/69 in UV-K5
  firmware) gives the PL tone.
- Buys: one listener module that fills a table entry's call, PL and CW tone/speed. The
  tone and speed pair identifies a repeater even when the call won't decode (table byte 51
  has room).
