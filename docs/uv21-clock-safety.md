# UV-21 Pro at 120 MHz: safety plan

Neon's `[CLK]` change (opt/opt-clk, in PR #306) moves the UV-21 Pro's AT32F421 from 60 MHz to
120 MHz. The UV-21's crystal doesn't start, so the clock still comes from the internal 8 MHz HICK,
which reaches the PLL halved: 4 MHz x 30. This page covers what can go wrong, how it was tried
before any flash, and how to get back.

## What changes

| | 60 MHz (main) | 120 MHz (`[CLK]`) | Limit |
|---|---|---|---|
| PLLMULT (CRM_CFG) | x15, code 13 | x30, code 29 | 120 MHz top for the AT32F421 |
| Flash wait states (FLASH_PSR) | 0x153: 3 | 0x153: 3 (unchanged) | 3 needed above 96 MHz |
| AHB / APB1 / APB2 | undivided | undivided | 120 MHz each |
| ADC clock (ADCDIV) | /2 = 30 MHz (over its limit) | /6 = 20 MHz | 28 MHz |
| USART1 (BAUDR) | 521 = 115,163 baud | 1042 = 115,163 baud | |
| SPI2, panel writes | /4 = 15 MHz | /8 = 15 MHz | ST7735S write cycle 66 ns |
| SPI1, calibration NOR | /32 = 1.9 MHz | /32 = 3.75 MHz | tens of MHz |
| TMR14 ticks, SysTick waits | from SCLK_HZ | from SCLK_HZ | |
| Switch to the PLL | direct | auto step on around it (Artery BSP) | |

The UV-5RH firmware this port comes from ran the same chip at 120 MHz with the same FLASH_PSR,
from its crystal.

## Risks, and what covers each

1. **Flash wait states.** Too few wait states would give corrupt instruction fetches. PSR keeps
   3 (it was already 3 at 60 MHz). Checked on the radio at 120 MHz: CRC-32 of the 60 KB app
   area matches the 60 MHz read, repeated through a soak. Code run from flash is exactly 2.0x
   faster, so the wait states cost nothing extra.
2. **PLL lock.** x30 from 4 MHz is one of Artery's standard settings. PLL_FREF stays 0 (the
   3.9-5 MHz input range), and auto step is on for the jump, as the BSP does. The image waits for
   the lock with no timeout. If the PLL never locked, the image would hang before its banner,
   and recovery is 6 below. Checked: the RAM module locked x30 on the radio and came back to x15.
3. **HICK accuracy.** The 8 MHz RC oscillator is factory-trimmed to about 1% at room temperature
   and drifts a few percent across the full temperature range. 120 MHz has no margin above
   the rated top, so a fast HICK runs the core slightly over spec. The error is the same one the
   60 MHz image, the bootloader (48 MHz) and the serial link already live with. The test can't
   measure it: the host's line timestamps aren't logged yet.
4. **Serial (USART1).** The divider follows SCLK_HZ: 1042. The bootloader's command-42 reset,
   the hands-free reflash path, needs it right. Checked: every module line at 120 MHz came through
   at 115,200.
5. **Panel (SPI2 and RAMRD).** Writes stay at 15 MHz (/8). Reads aren't SPI2: `scrdump.c`
   bit-bangs RAMRD with a fixed run of NOPs, so its read clock grows with the CPU clock. At 120
   MHz it would read near 6 MHz, close to the ST7735S's 6.6 MHz read limit. PR #306 doubles the
   NOPs, so it reads near 3 MHz at 120 MHz. Checked: SURF's frame grabbed at 60 and 120 MHz has
   the same CRC, and a test card drawn at 120 MHz reads back with 0 bad pixels (and again at
   60).
6. **The BK4829 interface.** The bit-banged bus waits with SysTick (BIT_DELAY_US), which follows
   SCLK_HZ, so its timing stays about the same. The edges get a little faster as the code
   around the waits speeds up. The RAM module reads the RF chip at 120 MHz with the 60 MHz
   image's delays, which are half as long: a harder test. Checked: 29 setup registers read the
   same at both clocks.
7. **Long waits.** One `delay_us` call loads SysTick's 24-bit reload once, so it tops out at
   139 ms at 120 MHz (279 ms at 60). The firmware's own waits are all under 15 ms. `um_wait_us`
   splits waits into 100 ms pieces. `fmprobe.c` had two plain 200 ms calls, which would have
   waited about 60 ms; PR #306 sends them through `um_wait_us`.
8. **ADC.** At 60 MHz the ADC ran at 30 MHz, over its 28 MHz limit. `[CLK]` gives it 20 MHz.
   Checked: the battery channel reads 2570 at 60 MHz and 2572 at 120 MHz.
9. **RF spurs.** Harmonics of 120 MHz are a subset of 60 MHz's. Checked: RSSI at 420, 440.85,
   446, 480, 360, 162.55 and 146.52 MHz matches within 1 dB at both clocks.
10. **Current draw.** The MCU draws more at 120 MHz, roughly twice its core current, a few mA.
    That's small next to the receiver and audio amplifier.
11. **Emulator limits.** The emulator doesn't model the PLL, baud rates or panel timing, which
    is why the RAM module ran on the real radio before any flash.

## The RAM module test (no flash)

`radios/uv21/mod/clk120.c`, run with the bench runner's `uv21.mod` step on the 60 MHz image:

1. It refuses unless the clock registers are exactly the 60 MHz port's, or the 120 MHz port's.
2. At home (60 MHz) it records the RF registers, the flash CRC, CPU timings, the battery ADC,
   RSSI, and a screen grab of SURF.
3. It starts the watchdog deadman: the independent WDT on LICK, about 26 s, fed while the
   module runs.
4. With interrupts off, it switches to x30 and sets USART1, SPI2, TMR14 and ADC for 120 MHz.
   Every wait is bounded. If the PLL doesn't lock, it goes back to x15.
5. At 120 MHz it repeats the checks, grabs SURF, draws and reads back a test card, and soaks.
6. It switches back to x15 and puts back the port's own register values. It checks those
   values, reads the RF registers and flash again, and redraws the card.
7. It returns. The image never feeds the watchdog, so about 26 s later the radio resets and
   boots its own image. A hang anywhere ends the same way, with no hands needed.

`python3 radios/uv21/mod/clk120.py uv21mod.log -o pngs/` gives the verdict and writes the grabs as
PNGs. With `CK_MULT=15` on the 120 MHz image, the module goes down to 60 MHz and back, which
tries the way home.

## Recovery if the 120 MHz image won't boot

- The KDH bootloader isn't touched: `kdh.flash` writes only the app area. Command 42 is a full
  system reset, and the bootloader sets its own 48 MHz clock, so nothing in the image's clock
  setup can reach it.
- **Hands-free:** if the image boots and its serial line works, a `kdh.probe --wait-boot-s 60`
  step resets it into the bootloader. Then `kdh.flash` writes main's 60 MHz `uv21-port` image
  (radios/uv21/README.md, "Reflashing the port hands-free").
- **One hand:** if the image hangs (a PLL that never locks, a fault) or its serial line is
  wrong, run the same probe step and switch the radio off and on with the knob while it listens.
  The bootloader answers at power-on and the reflash goes ahead. Bench hours apply: not Tuesday
  to Friday, 8 AM to 4:30 PM PT.
- Nothing can brick the radio short of the bootloader itself, which no step writes.

## After the flash

The job flashes PR #306's `uv21-port` CI artifact, then:

1. Listens 60 s: `boot done`, `sclk_mhz=78` in the banner, `alive surf ... stk=`.
2. hello (`um end 00004829`) and apiprobe (`api 2/1 ... t16=~1000 ms=~100`, `um end 00000055`):
   the ticks and the delays are right at 120 MHz.
3. scrdump: SURF's screen grabbed at 120 MHz and compared with the emulator's picture.
4. clk120 with `CK_MULT=15`: down to 60 MHz and back.
5. Rollback is the same two steps with main's 60 MHz image.
