# UV-380

blowfang on the TYT MD-UV380 (also the MD-UV390 and Retevis RT3S): STM32F405, AT1846S,
HR_C6000. The study and plan are in [docs/uv380-port.md](../../docs/uv380-port.md).

So far the firmware sends a sotto message as K6 voice-pager packets in FFSK (1200/1800 Hz, 1800
baud, XL), the format the K6 receives. That runs on QEMU's STM32F405 today. The radio image
builds but **cannot transmit yet**: `src/rf.h` has no RF driver, and the app only blinks red.

The firmware also carries the fixed-point sotto 1200 encoder and decoder (8 kHz) from the `sotto`
submodule, built -O2 with their state in the CCM (`src/sotto_arena.c`). On QEMU, an 8 kHz PCM
file `msg.raw` on the host is encoded on the emulated radio and then sent;
`tests/test_uv380_codec.py` checks that the K6 receiver model gets exactly the bits the x86
encoder writes. The radio has no microphone path to the MCU yet, so it does not encode live
speech. Cycle counts come from sotto's Cortex-M4 bench (QEMU is not cycle-accurate): the 8 kHz
encoder's worst 40 ms call is about 70 MHz of the 168.

```sh
sudo apt install gcc-arm-none-eabi libnewlib-arm-none-eabi qemu-system-arm
make -C radios/uv380          # build/uv380.bin, linked at 0x0800C000 (after TYT's bootloader)
make -C radios/uv380 qemu     # build/uv380-qemu.elf for qemu-system-arm -M netduinoplus2
make -C c/common              # host build of the portable transmitter
make -C sotto/c fixed         # x86 sotto, for the codec test (git submodule update --init first)
python -m pytest -q tests/test_uv380_pager.py tests/test_uv380_emu.py tests/test_uv380_codec.py
```

## Where things are

- `src/board.h`: the board API the apps use (audio out, PTT, LEDs, time)
- `src/board_radio.c`: the radio (clocks, GPIO, TIM3 PWM audio out paced by TIM7 and DMA)
- `src/board_qemu.c`: QEMU (audio to `audio.raw` and `msg.sot` from the host, via semihosting)
- `src/rf.h`: the RF side, not written yet
- `src/main.c`: the app (FFSK pager sender; on QEMU it can encode `msg.raw` first)
- `src/sotto_arena.c`: the codec's memory, CCM first, then SRAM
- `link/`: `radio.ld` and `qemu.ld` set the memory map, `sections.ld` is shared
- `../../c/common/`: portable code (pager packets, FFSK modulator)
