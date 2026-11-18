/* c6phyrx: the HR_C6000's own 4FSK demodulator as an M17 receiver front end, receive only (no RF
   out). docs/uv380-m17-rx.md, path A: c6dmrrx in the physical layer test mode
   (0x10 = 0x02, 0x40 = 0x40, 0x41 = 0x41, AT1846S in its DMR set), every demodulated 288-bit
   window from RX RAM 0x30 as an "r" line with its time, and the soft values of every
   RX_RAWSOFT-th window. M17 is 4800 baud 4FSK with DMR's dibit map: if the windows come every 30 ms
   and join up with no symbol lost or doubled, the host finds M17 syncwords in the joined stream
   (python3 -m modem.m17rx phy LOG) and the chip does the demodulation, no DSP on the MCU.
   Listens 150 s (the bench job's Pluto sends come during it).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#define RX_RAW 1
#define RX_MS 150000u
#define RX_RAWSOFT 8u
#include "c6dmrrx.c"
