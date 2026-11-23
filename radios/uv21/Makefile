# blowfang on the Baofeng UV-21 Pro (MIT; README.md has the plan).
#   make -C radios/uv21       build/dumper.bin: the one-page dumper (1 KB at most, checked)
# clang and lld, as the K6 modules use.
# SPDX-License-Identifier: MIT
CC = clang --target=thumbv7em-none-eabi
CFLAGS = -mcpu=cortex-m4 -mthumb -Oz -ffreestanding -fno-builtin -nostdlib -Wall -Wextra -Werror
LD = ld.lld
OBJCOPY = llvm-objcopy

all: build/dumper.bin

build:
	mkdir -p build

build/dumper.o: dumper/dumper.c | build
	$(CC) $(CFLAGS) -c -o $@ $<

build/dumper.elf: build/dumper.o dumper/dumper.ld
	$(LD) -T dumper/dumper.ld -o $@ build/dumper.o

build/dumper.bin: build/dumper.elf
	$(OBJCOPY) -O binary $< $@
	@n=$$(wc -c < $@); echo "dumper.bin: $$n bytes"; test $$n -le 1024

.PHONY: all
