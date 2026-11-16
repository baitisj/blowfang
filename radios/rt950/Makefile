# blowfang on the Radtel RT-950 Pro (GPL-3, see LICENSE and README.md).
#   make -C radios/rt950               build/rt950_sim: the apps on a PC against two modelled
#                                      BK4829s (tests/test_rt950_surf.py)
#   make -C radios/rt950 target        build/*.o for the AT32F403A (Cortex-M4F), and their sizes
#   make -C radios/rt950 target-libm CROSS=arm-none-eabi-gcc   and c/common/tone_fsk.c too
#   make -C radios/rt950 glue JM=DIR   port/jm_glue.c compiled against a checkout of
#                                      jaymart1983/Radtel-RT950-Pro-Firmware at DIR
#   make -C radios/rt950 backup-image JM=DIR   the dump-only image for the backup: that
#                                      checkout with backup/backup-only.patch, built and checked
#                                      (build/rt950-backup-only.BTF; docs/rt950-backup.md)
# The target build uses clang (as the K6 modules do) unless CROSS=arm-none-eabi-gcc.
# SPDX-License-Identifier: GPL-3.0-or-later
CC ?= cc
HOSTFLAGS = -O2 -std=c99 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror
CROSS ?= clang --target=arm-none-eabi
ARCH = -mcpu=cortex-m4 -mthumb -mfloat-abi=hard -mfpu=fpv4-sp-d16 -ffreestanding
TFLAGS = $(ARCH) -Os -std=c99 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
         -ffunction-sections -fdata-sections
COMMON = ../../c/common
APP = src/surf2.c \
      src/digi_tx.c \
      $(COMMON)/tone_fsk.c
DEPS = src/rt_api.h \
       src/surf2.h \
       src/digi_tx.h \
       $(COMMON)/surf_core.inc \
       $(COMMON)/tone_fsk.h
JM ?= jm

all: build/rt950_sim

build:
	mkdir -p build

build/rt950_sim: sim/rt950_sim.c $(APP) $(DEPS) | build
	$(CC) $(HOSTFLAGS) -o $@ sim/rt950_sim.c $(APP) -lm

# the dual-chip SURF with no guard band (1 Hz), for the test that shows the guard is what keeps the
# sweeping chip out of the listener
build/rt950_sim_noguard: sim/rt950_sim.c $(APP) $(DEPS) | build
	$(CC) $(HOSTFLAGS) -DGUARD_HZ=1 -o $@ sim/rt950_sim.c $(APP) -lm

all: build/rt950_sim_noguard

target: build/surf2.o build/digi_tx.o
	llvm-size build/digi_tx.o build/surf2.o 2>/dev/null || size build/digi_tx.o build/surf2.o

# tone_fsk.c needs a C library's math.h (erff): arm-none-eabi-gcc with newlib, as CI has it
target-libm: target build/tone_fsk.o
	llvm-size build/tone_fsk.o 2>/dev/null || size build/tone_fsk.o

build/surf2.o: src/surf2.c $(DEPS) | build
	$(CROSS) $(TFLAGS) -c src/surf2.c -o $@

build/digi_tx.o: src/digi_tx.c $(DEPS) | build
	$(CROSS) $(TFLAGS) -c src/digi_tx.c -o $@

build/tone_fsk.o: $(COMMON)/tone_fsk.c $(DEPS) | build
	$(CROSS) $(TFLAGS) -c $(COMMON)/tone_fsk.c -o $@

glue: build/jm_glue.o

build/jm_glue.o: port/jm_glue.c src/rt_api.h src/surf2.h | build
	$(CROSS) $(ARCH) -Os -std=c11 -Wall -Wextra -Werror -I$(JM)/include -Isrc -c port/jm_glue.c -o $@

# the dump-only image: the pinned base firmware with every SPI erase and program, every CPS write
# and the TX value taken out, then backup/check_image.py proves it from the built image
BK = build/jm-backup
backup-image: backup/backup-only.patch backup/check_image.py | build
	rm -rf $(BK)
	mkdir -p $(BK)
	cd $(JM) && git archive HEAD | tar -x -C $(CURDIR)/$(BK)
	patch -d $(BK) -p1 --quiet < backup/backup-only.patch
	$(MAKE) -C $(BK) btf TARGET=rt950-backup-only
	cp $(BK)/build/rt950-backup-only.elf build/
	cp $(BK)/build/rt950-backup-only.bin build/
	cp $(BK)/build/rt950-backup-only.BTF build/
	python3 backup/check_image.py build/rt950-backup-only.elf build/rt950-backup-only.bin
	python3 $(BK)/tools/firmware_upload.py verify build/rt950-backup-only.BTF | tee build/rt950-backup-only.verify.txt
	grep -q "valid SRAM" build/rt950-backup-only.verify.txt
	grep -q "valid app flash" build/rt950-backup-only.verify.txt
	sha256sum build/rt950-backup-only.BTF

clean:
	rm -rf build

.PHONY: all target target-libm glue backup-image clean
