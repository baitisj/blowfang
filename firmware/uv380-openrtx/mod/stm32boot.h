/* stm32boot.h: what the boot modules (bootinfo, bootdump, dfuboot) know about the UV-380's
   STM32F405 flash and TYT's bootloader. Header only, for RAM modules (ortx_mod.h).

   Flash map (1 MB, one bank, docs/uv380-dfu.md):
     sectors 0-2  0x08000000-0x0800BFFF  TYT's bootloader. NOTHING here ever writes them.
     sector 3     0x0800C000-0x0800FFFF  16 KB: the application's vector table and first code
     sector 4     0x08010000-0x0801FFFF  64 KB, then sectors 5-11 of 128 KB each

   TYT's bootloader starts the application only when sector 3 holds a vector table (md380tools
   applet usb.c reboot_into_bootloader, the MD-380's TDFU_REBOOT_TO_BOOTLOADER: "erase the block
   at 0x0800C000 ... the bootloader will then launch and wait for comms"). With sector 3 blank it
   stays in its DFU mode (0483:df11) at every power-on until a new image is written, so blanking
   sector 3 and resetting is the way into flash mode with no keys held. dfuboot does exactly that
   and nothing more: one sector erase (SER, SNB 3, never MER), from RAM, interrupts off, then a
   system reset.
   SPDX-License-Identifier: MIT */
#ifndef STM32BOOT_H
#define STM32BOOT_H
#include <stdint.h>

#define SB_REG(a) (*(volatile uint32_t *)(uintptr_t)(a))

#define SB_FLASH_ACR   0x40023C00u
#define SB_FLASH_KEYR  0x40023C04u
#define SB_FLASH_SR    0x40023C0Cu
#define SB_FLASH_CR    0x40023C10u
#define SB_FLASH_OPTCR 0x40023C14u
#define SB_RCC_CSR     0x40023874u
#define SB_DBG_IDCODE  0xE0042000u
#define SB_FLASH_KB    0x1FFF7A22u          /* 16 bits: flash size in KB */
#define SB_SYST_CSR    0xE000E010u
#define SB_VTOR        0xE000ED08u
#define SB_AIRCR       0xE000ED0Cu

#define SB_KEY1 0x45670123u
#define SB_KEY2 0xCDEF89ABu

/* FLASH_SR */
#define SB_SR_EOP    (1u << 0)
#define SB_SR_ERRS   ((1u << 1) | (1u << 4) | (1u << 5) | (1u << 6) | (1u << 7))  /* OPERR WRPERR PGAERR PGPERR PGSERR */
#define SB_SR_BSY    (1u << 16)
/* FLASH_CR */
#define SB_CR_SER    (1u << 1)
#define SB_CR_MER    (1u << 2)
#define SB_CR_SNB(n) ((uint32_t)(n) << 3)
#define SB_CR_X32    (2u << 8)               /* PSIZE: 32-bit, 2.7-3.6 V (the radio's MCU runs at 3.3 V) */
#define SB_CR_STRT   (1u << 16)
#define SB_CR_LOCK   (1u << 31)

#define SB_BOOT_BASE 0x08000000u
#define SB_BOOT_END  0x0800C000u             /* first byte past the bootloader */
#define SB_APP_BASE  0x0800C000u             /* sector 3 */
#define SB_APP_SECTOR 3u

/* the one erase command dfuboot ever gives: sector 3, 32-bit, no mass erase */
#define SB_ERASE_CR  (SB_CR_X32 | SB_CR_SNB(SB_APP_SECTOR) | SB_CR_SER)
_Static_assert((SB_ERASE_CR & SB_CR_MER) == 0, "never a mass erase");
_Static_assert(((SB_ERASE_CR >> 3) & 0xFu) == 3u, "sector 3 only: sectors 0-2 hold TYT's bootloader");

/* RDP level from OPTCR: 0 (0xAA), 2 (0xCC), else 1 */
static inline unsigned sb_rdp_level(uint32_t optcr) {
    uint32_t r = (optcr >> 8) & 0xFFu;
    return r == 0xAA ? 0u : r == 0xCC ? 2u : 1u;
}

/* nWRP bit for a sector: 1 = not write protected */
static inline int sb_sector_writable(uint32_t optcr, unsigned sector) {
    return (int)((optcr >> (16 + sector)) & 1u);
}

/* a vector table's first two words: a stack in SRAM (0x2000xxxx up to 0x20020000) or CCM
   (0x1000xxxx up to 0x10010000), and a Thumb reset handler inside [lo, hi) */
static inline int sb_vectors_ok(uint32_t sp, uint32_t pc, uint32_t lo, uint32_t hi) {
    int sp_ok = (sp > 0x20000000u && sp <= 0x20020000u) || (sp > 0x10000000u && sp <= 0x10010000u);
    return sp_ok && (pc & 1u) && (pc & ~1u) >= lo && (pc & ~1u) < hi;
}

#endif
