# Portable C shared by the radios (radios/*). Host builds for the tests:
#   make -C c/common
# SPDX-License-Identifier: MIT
CC ?= cc
CFLAGS ?= -O2 -std=c99 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror

all: pager_tx_host cw_tx_host range_tx_host range_rx_host pager_rx_host fsk_rx_host

pager_tx_host: pager_tx_host.c pager_tx.c ffsk_tx.c pager_tx.h ffsk_tx.h
	$(CC) $(CFLAGS) -o $@ pager_tx_host.c pager_tx.c ffsk_tx.c

range_tx_host: range_tx_host.c range_tx.c ffsk_tx.c pager_tx.c range_tx.h ffsk_tx.h pager_tx.h
	$(CC) $(CFLAGS) -o $@ range_tx_host.c range_tx.c ffsk_tx.c pager_tx.c

range_rx_host: range_rx_host.c range_rx.c range_rx.h
	$(CC) $(CFLAGS) -o $@ range_rx_host.c range_rx.c

pager_rx_host: pager_rx_host.c pager_rx.c pager_tx.c pager_rx.h pager_tx.h
	$(CC) $(CFLAGS) -o $@ pager_rx_host.c pager_rx.c pager_tx.c

fsk_rx_host: fsk_rx_host.c fsk_rx.c pager_tx.c fsk_rx.h pager_tx.h
	$(CC) $(CFLAGS) -o $@ fsk_rx_host.c fsk_rx.c pager_tx.c

cw_tx_host: cw_tx_host.c cw_tx.c cw_tx.h
	$(CC) $(CFLAGS) -o $@ cw_tx_host.c cw_tx.c

clean:
	rm -f pager_tx_host cw_tx_host range_tx_host range_rx_host pager_rx_host fsk_rx_host

.PHONY: all clean

# Polar SSB and constant-envelope FSK for the RT-950 (docs/rt950-polar-sim.md)
all: ssb_polar_host
all: tone_fsk_host

ssb_polar_host: ssb_polar_host.c \
		ssb_polar.c \
		ssb_polar.h \
		host_fft.h
	$(CC) $(CFLAGS) -o $@ ssb_polar_host.c ssb_polar.c -lm

tone_fsk_host: tone_fsk_host.c \
		tone_fsk.c \
		tone_fsk.h \
		host_fft.h
	$(CC) $(CFLAGS) -o $@ tone_fsk_host.c tone_fsk.c -lm

clean: clean-rt950-sim
clean-rt950-sim:
	rm -f ssb_polar_host
	rm -f tone_fsk_host

.PHONY: clean-rt950-sim

# M17 receiver for the UV-380's 8 kHz FM tap (docs/uv380-m17-rx.md)
all: m17_rx_host

m17_rx_host: m17_rx_host.c \
		m17_rx.c \
		m17_rx.h
	$(CC) $(CFLAGS) -o $@ m17_rx_host.c m17_rx.c

# M17 and YSF transmit framers for the UV-380's multi-mode image (docs/uv380-multimode.md)
all: framers_host

framers_host: framers_host.c \
		m17_tx.c \
		m17_tx.h \
		ysf_tx.c \
		ysf_tx.h
	$(CC) $(CFLAGS) -o $@ framers_host.c m17_tx.c ysf_tx.c

clean: clean-framers
clean-framers:
	rm -f framers_host

.PHONY: clean-framers

# the HR_C6000 core (profiles, 4FSK and DMR transmit engines) on a fake chip
all: c6core_host

c6core_host: c6core_host.c \
		c6_core.c \
		c6_core.h \
		m17_tx.c \
		m17_tx.h \
		ysf_tx.c \
		ysf_tx.h
	$(CC) $(CFLAGS) -o $@ c6core_host.c c6_core.c m17_tx.c ysf_tx.c

clean: clean-c6core
clean-c6core:
	rm -f c6core_host

.PHONY: clean-c6core
