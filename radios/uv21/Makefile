# blowfang on the Baofeng UV-21 Pro (MIT; README.md has the plan).
#   make -C radios/uv21       build/dumper.bin: the one-page dumper (1 KB at most, checked)
#                             build/bringup.bin: the bring-up image (the 60 KB app area, checked)
# clang and lld, as the K6 modules use.
# SPDX-License-Identifier: MIT
CC = clang --target=thumbv7em-none-eabi
CFLAGS = -mcpu=cortex-m4 -mthumb -Oz -ffreestanding -fno-builtin -nostdlib -Wall -Wextra -Werror
LD = ld.lld
OBJCOPY = llvm-objcopy

# the commit the bring-up image says it was built from (its banner; -dirty for local changes)
BUILD_ID := $(shell git rev-parse --short=10 HEAD 2>/dev/null || echo local)$(shell git diff --quiet HEAD -- . 2>/dev/null || echo -dirty)

all: build/dumper.bin build/bringup.bin

build:
	mkdir -p build

build/dumper.o: dumper/dumper.c | build
	$(CC) $(CFLAGS) -c -o $@ $<

build/dumper.elf: build/dumper.o dumper/dumper.ld
	$(LD) -T dumper/dumper.ld -o $@ build/dumper.o

build/dumper.bin: build/dumper.elf
	$(OBJCOPY) -O binary $< $@
	@n=$$(wc -c < $@); echo "dumper.bin: $$n bytes"; test $$n -le 1024

build/bringup.o: bringup/bringup.c bringup/board.h | build
	$(CC) $(CFLAGS) -DBUILD_ID='"$(BUILD_ID)"' -c -o $@ $<

build/bringup.elf: build/bringup.o bringup/bringup.ld
	$(LD) -T bringup/bringup.ld -o $@ build/bringup.o

build/bringup.bin: build/bringup.elf
	$(OBJCOPY) -O binary $< $@
	@n=$$(wc -c < $@); echo "bringup.bin: $$n bytes, build $(BUILD_ID)"; test $$n -le 61440

.PHONY: all
