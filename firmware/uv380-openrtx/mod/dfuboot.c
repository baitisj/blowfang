/* dfuboot: put the running UV-380 into TYT's flash (DFU) mode with no keys held, so hamlet can
   reflash it hands-free (docs/uv380-dfu.md, tools/uv380_dfu.py).

   How: TYT's bootloader (sectors 0-2) starts the application only when sector 3 holds a vector
   table; with sector 3 blank it waits in DFU (0483:df11). So this module erases sector 3 (the
   application's first 16 KB, which the next flash rewrites anyway) and resets. It never writes
   the bootloader's sectors: the erase command is the one constant SB_ERASE_CR (stm32boot.h,
   sector 3, no mass erase), checked at compile time.

   Until a new image is written the radio comes up in DFU at every power-on, so a failed or
   interrupted flash is retried by hamlet with no hands; the bootloader itself is never at risk.

   Safety, in order:
     1. checks: TYT's vector table at 0x08000000 is sane (stack in RAM, reset handler inside the
        bootloader), the running image's at 0x0800C000 too, VTOR is 0x0800C000, flash is 1 MB,
        sector 3 is not write protected. Any failure: "dfuboot refused ...", nothing written.
     2. handshake: "dfuboot armed nonce=N"; hamlet must answer "erase N" (or "dry N") within
        10 s, so a module sent by mistake, or input left over from earlier, erases nothing.
        Ctrl-C, PTT or no answer: "dfuboot cancelled", nothing written.
     3. "dry N": the same checks and the register values it would write, then returns 0x44.
     4. "erase N": "dfuboot erasing", 300 ms for USB to send it, then from RAM with interrupts
        and SysTick off: unlock, wait BSY, clear errors, CR = SB_ERASE_CR, STRT, wait BSY, lock,
        system reset. No OpenRTX code runs after the erase starts (it lives partly in sector 3).
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "stm32boot.h"

/* all in RAM (the module), touches no flash code or vector: never returns */
__attribute__((noinline, noreturn)) static void erase_and_reset(void) {
    __asm volatile("cpsid i" ::: "memory");
    SB_REG(SB_SYST_CSR) = 0;
    while (SB_REG(SB_FLASH_SR) & SB_SR_BSY)
        ;
    if (SB_REG(SB_FLASH_CR) & SB_CR_LOCK) {
        SB_REG(SB_FLASH_KEYR) = SB_KEY1;
        SB_REG(SB_FLASH_KEYR) = SB_KEY2;
    }
    SB_REG(SB_FLASH_SR) = SB_SR_ERRS | SB_SR_EOP;       /* write 1 to clear */
    SB_REG(SB_FLASH_CR) = SB_ERASE_CR;
    SB_REG(SB_FLASH_CR) = SB_ERASE_CR | SB_CR_STRT;
    __asm volatile("dsb" ::: "memory");
    while (SB_REG(SB_FLASH_SR) & SB_SR_BSY)
        ;
    SB_REG(SB_FLASH_CR) = SB_CR_LOCK;
    __asm volatile("dsb" ::: "memory");
    SB_REG(SB_AIRCR) = 0x05FA0004u;                     /* SYSRESETREQ */
    __asm volatile("dsb" ::: "memory");
    for (;;)
        ;
}

static int hexval(const char *s, uint32_t *v) {
    uint32_t x = 0;
    int n = 0;
    for (; *s; s++, n++) {
        char c = *s;
        uint32_t d = c >= '0' && c <= '9' ? (uint32_t)(c - '0') : c >= 'a' && c <= 'f' ? (uint32_t)(c - 'a' + 10)
                   : c >= 'A' && c <= 'F' ? (uint32_t)(c - 'A' + 10) : 99u;
        if (d > 15 || n >= 8)
            return -1;
        x = x << 4 | d;
    }
    *v = x;
    return n ? 0 : -1;
}

/* "erase N" -> 1, "dry N" -> 2, anything else 0 */
static int answer(const char *ln, uint32_t nonce) {
    uint32_t v;
    int kind = 0;
    if (ln[0] == 'e' && ln[1] == 'r' && ln[2] == 'a' && ln[3] == 's' && ln[4] == 'e' && ln[5] == ' ') {
        kind = 1;
        ln += 6;
    } else if (ln[0] == 'd' && ln[1] == 'r' && ln[2] == 'y' && ln[3] == ' ') {
        kind = 2;
        ln += 4;
    }
    return kind && hexval(ln, &v) == 0 && v == nonce ? kind : 0;
}

int uv_main(const ortx_api_t *api) {
    char line[160], in[48];
    uint32_t optcr = SB_REG(SB_FLASH_OPTCR);
    uint32_t kb = *(volatile uint16_t *)(uintptr_t)SB_FLASH_KB;
    uint32_t bsp = SB_REG(SB_BOOT_BASE), bpc = SB_REG(SB_BOOT_BASE + 4);
    uint32_t asp = SB_REG(SB_APP_BASE), apc = SB_REG(SB_APP_BASE + 4);
    uint32_t vtor = SB_REG(SB_VTOR);

    const char *why = 0;
    if (!sb_vectors_ok(bsp, bpc, SB_BOOT_BASE, SB_BOOT_END))
        why = "bootloader vectors";
    else if (kb != 1024u)
        why = "flash size";
    else if (!sb_vectors_ok(asp, apc, SB_APP_BASE, SB_BOOT_BASE + kb * 1024u))
        why = "app vectors";
    else if (vtor != SB_APP_BASE)
        why = "vtor";
    else if (!sb_sector_writable(optcr, SB_APP_SECTOR))
        why = "sector 3 write protected";
    else if (SB_REG(SB_FLASH_SR) & SB_SR_BSY)
        why = "flash busy";
    if (why) {
        api->fmt(line, sizeof line, "dfuboot refused: %s (boot %08lx %08lx app %08lx %08lx vtor %08lx optcr %08lx %lu KB)",
                 why, (unsigned long)bsp, (unsigned long)bpc, (unsigned long)asp, (unsigned long)apc,
                 (unsigned long)vtor, (unsigned long)optcr, (unsigned long)kb);
        api->say(line);
        return 0x52;
    }

    uint32_t nonce = (SB_REG(0xE000E018u) * 2654435761u) ^ (api->ms() << 16) ^ (uint32_t)api->rssi();
    nonce = nonce ? nonce : 0x5eed;
    api->fmt(line, sizeof line, "dfuboot armed nonce=%08lx rdp=%u boot pc=%08lx", (unsigned long)nonce,
             sb_rdp_level(optcr), (unsigned long)bpc);
    api->say(line);

    unsigned n = 0;
    int kind = 0;
    uint32_t t0 = api->ms();
    while (!kind) {
        if (api->stop() || api->ms() - t0 > 10000u) {
            api->say("dfuboot cancelled: nothing written");
            return 0x43;
        }
        char b[32];
        unsigned got = api->usb_read(b, sizeof b);
        for (unsigned i = 0; i < got && !kind; i++) {
            if (b[i] == 3) {
                api->say("dfuboot cancelled: nothing written");
                return 0x43;
            }
            if (b[i] == '\n' || b[i] == '\r') {
                in[n] = 0;
                if (n)
                    kind = answer(in, nonce);
                if (n && !kind) {
                    api->say("dfuboot wrong answer: nothing written");
                    return 0x57;
                }
                n = 0;
            } else if (n < sizeof in - 1) {
                in[n++] = b[i];
            }
        }
        if (!kind)
            api->sleep_ms(20);
    }

    if (kind == 2) {
        api->fmt(line, sizeof line, "dfuboot dry: would erase sector 3 (cr=%08lx) at %08lx, then aircr=05fa0004",
                 (unsigned long)SB_ERASE_CR, (unsigned long)SB_APP_BASE);
        api->say(line);
        return 0x44;
    }

    api->say("dfuboot erasing sector 3, then reset into TYT's bootloader");
    api->sleep_ms(300);
    erase_and_reset();
}
