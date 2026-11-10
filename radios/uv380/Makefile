# UV-380 firmware (TYT MD-UV380 / MD-UV390 / Retevis RT3S): Arm GNU toolchain from Ubuntu
# (apt install gcc-arm-none-eabi libnewlib-arm-none-eabi).
#   make -C radios/uv380            build/uv380.elf + .bin (radio, linked at 0x0800C000)
#   make -C radios/uv380 qemu       build/uv380-qemu.elf (QEMU netduinoplus2, at 0x08000000)
# The radio .bin is the bare app image. TYT's updater wants it wrapped (radio_tool or
# dmr.tools); see docs/uv380-port.md. Nothing in this tree can transmit yet (src/rf.h).
# SPDX-License-Identifier: MIT
CC = arm-none-eabi-gcc
OBJCOPY = arm-none-eabi-objcopy
SIZE = arm-none-eabi-size
ARCH = -mcpu=cortex-m4 -mthumb -mfloat-abi=hard -mfpu=fpv4-sp-d16
CFLAGS = $(ARCH) -Os -g -std=c99 -Wall -Wextra -Werror -ffunction-sections -fdata-sections \
         -fno-common
LDFLAGS = $(ARCH) -nostartfiles --specs=nano.specs --specs=nosys.specs -Wl,--gc-sections \
          -Wl,-Llink
COMMON = ../../c/common
SRC = src/startup.c \
      src/main.c \
      $(COMMON)/pager_tx.c \
      $(COMMON)/ffsk_tx.c

all: build/uv380.bin

build/uv380.elf: $(SRC) src/board_radio.c src/*.h link/*.ld $(COMMON)/*.h
	@mkdir -p build
	$(CC) $(CFLAGS) $(LDFLAGS) -Wl,-Tlink/radio.ld \
	    -Wl,-Map=build/uv380.map -o $@ $(SRC) src/board_radio.c
	$(SIZE) $@

build/uv380.bin: build/uv380.elf
	$(OBJCOPY) -O binary $< $@

qemu: build/uv380-qemu.elf

build/uv380-qemu.elf: $(SRC) src/board_qemu.c src/*.h link/*.ld $(COMMON)/*.h
	@mkdir -p build
	$(CC) $(CFLAGS) -DBOARD_QEMU $(LDFLAGS) -Wl,-Tlink/qemu.ld -o $@ \
	    $(SRC) src/board_qemu.c
	$(SIZE) $@

clean:
	rm -rf build

.PHONY: all qemu clean
