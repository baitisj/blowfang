/* fsk24rx_uv: the K6 end of the UV-380's flat FSK (firmware/uv380-openrtx/mod/c6phytx.c, the
   HR_C6000's physical layer mode, docs/uv380-c6phy.md). fsk24rx.c with four settings of 60 s:
   three of D/2400 (run 3's setting 2: REG_58 00e9, REG_2B +0400, 234/234 from the Pluto) and one
   of D/1200 (REG_58 00e1, the same filters), 30 s after its start. The UV-380 sends variants 20
   and 21 (2400, the two polarities) in the first three minutes, 22 and 23 (1200) in the last.
   Report: python3 -m modem.fsk24 report LOG. Receive only.
   SPDX-License-Identifier: MIT */
#define FE_S 60u
#define F24_ABS 1
#define F24_NV 4
#define F24_V \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x00E9, 24779, 1067, 0x0400}, \
  {0x00E1, 12389, 2133, 0x0400}, \

#include "fsk24rx.c"
