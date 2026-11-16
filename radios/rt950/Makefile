# blowfang on the Radtel RT-950 Pro (GPL-3, see LICENSE and README.md).
#   make -C radios/rt950               build/rt950_sim: the apps on a PC against two modelled
#                                      BK4829s (tests/test_rt950_surf.py)
#   make -C radios/rt950 target        build/*.o for the AT32F403A (Cortex-M4F), and their sizes
#   make -C radios/rt950 glue JM=DIR   port/jm_glue.c compiled against a checkout of
#                                      jaymart1983/Radtel-RT950-Pro-Firmware at DIR
# The target build uses clang (as the K6 modules do) unless CROSS=arm-none-eabi-gcc.
# SPDX-License-Identifier: GPL-3.0-or-later
CC ?= cc
HOSTFLAGS = -O2 -std=c99 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror
CROSS ?= clang --target=arm-none-eabi
ARCH = -mcpu=cortex-m4 -mthumb -mfloat-abi=hard -mfpu=fpv4-sp-d16 -ffreestanding
TFLAGS = $(ARCH) -Os -std=c99 -Wall -Wextra -Wconversion -Wno-sign-conversion -Werror \
         -ffunction-sections -fdata-sections
COMMON = ../../c/common
APP = src/surf2.c
DEPS = src/rt_api.h \
       src/surf2.h \
       $(COMMON)/surf_core.inc
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

target: build/surf2.o
	llvm-size build/surf2.o 2>/dev/null || size build/surf2.o

build/surf2.o: $(APP) $(DEPS) | build
	$(CROSS) $(TFLAGS) -c $(APP) -o $@

glue: build/jm_glue.o

build/jm_glue.o: port/jm_glue.c src/rt_api.h src/surf2.h | build
	$(CROSS) $(ARCH) -Os -std=c11 -Wall -Wextra -Werror -I$(JM)/include -Isrc -c port/jm_glue.c -o $@

clean:
	rm -rf build

.PHONY: all target glue clean
