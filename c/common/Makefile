# Portable C shared by the radios (radios/*). Host builds for the tests:
#   make -C c/common
# SPDX-License-Identifier: MIT
CC ?= cc
CFLAGS ?= -O2 -std=c99 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror

all: pager_tx_host cw_tx_host range_tx_host

pager_tx_host: pager_tx_host.c pager_tx.c ffsk_tx.c pager_tx.h ffsk_tx.h
	$(CC) $(CFLAGS) -o $@ pager_tx_host.c pager_tx.c ffsk_tx.c

range_tx_host: range_tx_host.c range_tx.c ffsk_tx.c pager_tx.c range_tx.h ffsk_tx.h pager_tx.h
	$(CC) $(CFLAGS) -o $@ range_tx_host.c range_tx.c ffsk_tx.c pager_tx.c

cw_tx_host: cw_tx_host.c cw_tx.c cw_tx.h
	$(CC) $(CFLAGS) -o $@ cw_tx_host.c cw_tx.c

clean:
	rm -f pager_tx_host cw_tx_host range_tx_host

.PHONY: all clean
