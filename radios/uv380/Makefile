# UV-380 firmware (TYT MD-UV380 / MD-UV390 / Retevis RT3S): Arm GNU toolchain from Ubuntu
# (apt install gcc-arm-none-eabi libnewlib-arm-none-eabi).
#   make -C radios/uv380            build/uv380.elf + .bin (radio, linked at 0x0800C000)
#   make -C radios/uv380 qemu       build/uv380-qemu.elf (QEMU netduinoplus2, at 0x08000000)
#   make -C radios/uv380 MOD_KEY_FILE=~/.config/sotto/k6-mod.key
#                                   an image that takes modules signed with that key (keep it
#                                   on your machine); without it, key a built image with
#                                   python -m modem.k6 keyimage, as a K6 image
# The radio .bin is the bare app image. TYT's updater wants it wrapped (radio_tool or
# dmr.tools); see docs/uv380-port.md. Nothing in this tree can transmit yet (src/rf.h).
# SPDX-License-Identifier: MIT
CC = arm-none-eabi-gcc
OBJCOPY = arm-none-eabi-objcopy
SIZE = arm-none-eabi-size
ARCH = -mcpu=cortex-m4 -mthumb -mfloat-abi=hard -mfpu=fpv4-sp-d16
CFLAGS = $(ARCH) -Os -g -std=c99 -Wall -Wextra -Werror -ffunction-sections -fdata-sections \
         -fno-common
ifneq ($(MOD_KEY_FILE),)
CFLAGS += -DMC_KEY=$(shell python3 -c "import sys; k = bytes.fromhex(open(sys.argv[1]).read().strip()); assert len(k) == 16; print(','.join(hex(b) for b in k))" $(MOD_KEY_FILE))
endif
LDFLAGS = $(ARCH) -nostartfiles --specs=nano.specs --specs=nosys.specs -Wl,--gc-sections \
          -Wl,-Llink
COMMON = ../../c/common
SRC = src/startup.c \
      src/main.c \
      src/uv_api.c \
      $(COMMON)/mod_core.c \
      $(COMMON)/pager_tx.c \
      $(COMMON)/ffsk_tx.c \
      $(COMMON)/cw_tx.c \
      src/sotto_arena.c

# The fixed-point sotto 1200 codec (8 kHz) from the sotto submodule, built -O2 (at -Os its
# encoder is about 25 % slower; sotto docs/c-port.md, "sotto 1200 at 16 kHz on the Cortex-M4"),
# with its calloc and free sent to src/sotto_arena.c (CCM first, then SRAM).
SOTTO = ../../sotto/c
SOTTO_SRC = $(SOTTO)/fixed/fx_math.c \
            $(SOTTO)/fixed/fx_tables.c \
            $(SOTTO)/fixed/sotto_fx_enc.c \
            $(SOTTO)/fixed/sotto_fx_dec.c \
            $(SOTTO)/fixed/sotto_fx_lr.c \
            $(SOTTO)/fixed/sotto_fx_lr_enc.c
SOTTO_CFLAGS = $(ARCH) -O2 -g -std=c99 -ffunction-sections -fdata-sections -fno-common \
               -DSOTTO_V36 \
               -DSOTTO_PITCH_THIRD \
               -Dcalloc=sotto_arena_calloc \
               -Dfree=sotto_arena_free \
               -I$(SOTTO)
SOTTO_OBJ = $(patsubst $(SOTTO)/fixed/%.c,build/sotto/%.o,$(SOTTO_SRC))

build/sotto/%.o: $(SOTTO)/fixed/%.c $(wildcard $(SOTTO)/*.h $(SOTTO)/fixed/*.h)
	@mkdir -p build/sotto
	$(CC) $(SOTTO_CFLAGS) -c $< -o $@

all: build/uv380.bin

build/uv380.elf: $(SRC) src/board_radio.c src/*.h link/*.ld $(COMMON)/*.h $(SOTTO_OBJ)
	@mkdir -p build
	$(CC) $(CFLAGS) $(LDFLAGS) -Wl,-Tlink/radio.ld \
	    -Wl,-Map=build/uv380.map -o $@ $(SRC) src/board_radio.c $(SOTTO_OBJ)
	$(SIZE) $@

build/uv380.bin: build/uv380.elf
	$(OBJCOPY) -O binary $< $@

qemu: build/uv380-qemu.elf

build/uv380-qemu.elf: $(SRC) src/board_qemu.c src/*.h link/*.ld $(COMMON)/*.h $(SOTTO_OBJ)
	@mkdir -p build
	$(CC) $(CFLAGS) -DBOARD_QEMU $(LDFLAGS) -Wl,-Tlink/qemu.ld -Wl,-Map=build/uv380-qemu.map -o $@ \
	    $(SRC) src/board_qemu.c $(SOTTO_OBJ)
	$(SIZE) $@

clean:
	rm -rf build

.PHONY: all qemu clean
