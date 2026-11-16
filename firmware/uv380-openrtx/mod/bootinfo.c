/* bootinfo: a read-only look at the UV-380's flash protection and TYT's bootloader, the check
   before dfuboot (stm32boot.h, docs/uv380-dfu.md). Writes nothing anywhere; no RF.

   Lines (all "mod say"):
     chip idcode=I flash=N KB vtor=V rcc_csr=C       (idcode may read 0 without a debugger)
     optcr=O rdp=L nwrp=W sector3=writable|PROTECTED  (RDP 1: the STM32 ROM DFU can't write flash)
     flash acr=A sr=S cr=C
     boot sp=S pc=P ok|BAD                            (TYT's vector table at 0x08000000)
     app  sp=S pc=P ok|BAD                            (the running image's, at 0x0800C000)
     lit WORD n=N first=OFF                           literal words in the bootloader: who reads
                                                      the application's vectors, the usual "stack
                                                      in RAM" masks, the RTC backup registers and
                                                      backup SRAM (a magic word a bootloader might
                                                      check), offsets from 0x08000000
     bootinfo ready=yes|no                            yes: dfuboot's own checks would pass
   Returns 0x5a when ready, 0x50 when not.
   SPDX-License-Identifier: MIT */
#include "ortx_mod.h"
#include "stm32boot.h"

int uv_main(const ortx_api_t *api) {
    char line[160];
    /* words worth finding in the bootloader's literal pools */
    static const uint32_t lits[] = {
        0x0800C000u,   /* the application's vector table */
        0x0800C004u,   /* its reset vector */
        0x2FFE0000u,   /* ST's IAP check: (sp & 0x2FFE0000) == 0x20000000 */
        0x2FFC0000u,
        0x2FF80000u,
        0x20020000u,   /* top of SRAM */
        0x40002850u,   /* RTC_BKP0R */
        0x40002854u,   /* RTC_BKP1R */
        0x40002800u,   /* RTC base */
        0x40024000u,   /* backup SRAM */
        0x40007000u,   /* PWR_CR (DBP, to reach the backup domain) */
        0x40023870u,   /* RCC_BDCR */
        0xDEADBEEFu,
        0x12345678u,
    };
    uint32_t optcr = SB_REG(SB_FLASH_OPTCR);
    uint32_t kb = *(volatile uint16_t *)(uintptr_t)SB_FLASH_KB;
    uint32_t bsp = SB_REG(SB_BOOT_BASE), bpc = SB_REG(SB_BOOT_BASE + 4);
    uint32_t asp = SB_REG(SB_APP_BASE), apc = SB_REG(SB_APP_BASE + 4);
    int boot_ok = sb_vectors_ok(bsp, bpc, SB_BOOT_BASE, SB_BOOT_END);
    int app_ok = sb_vectors_ok(asp, apc, SB_APP_BASE, SB_BOOT_BASE + kb * 1024u);
    int wr = sb_sector_writable(optcr, SB_APP_SECTOR);

    api->fmt(line, sizeof line, "chip idcode=%08lx flash=%lu KB vtor=%08lx rcc_csr=%08lx",
             (unsigned long)SB_REG(SB_DBG_IDCODE), (unsigned long)kb,
             (unsigned long)SB_REG(SB_VTOR), (unsigned long)SB_REG(SB_RCC_CSR));
    api->say(line);
    api->fmt(line, sizeof line, "optcr=%08lx rdp=%u nwrp=%03lx sector3=%s", (unsigned long)optcr,
             sb_rdp_level(optcr), (unsigned long)((optcr >> 16) & 0xFFFu),
             wr ? "writable" : "PROTECTED");
    api->say(line);
    api->fmt(line, sizeof line, "flash acr=%08lx sr=%08lx cr=%08lx", (unsigned long)SB_REG(SB_FLASH_ACR),
             (unsigned long)SB_REG(SB_FLASH_SR), (unsigned long)SB_REG(SB_FLASH_CR));
    api->say(line);
    api->fmt(line, sizeof line, "boot sp=%08lx pc=%08lx %s", (unsigned long)bsp, (unsigned long)bpc,
             boot_ok ? "ok" : "BAD");
    api->say(line);
    api->fmt(line, sizeof line, "app  sp=%08lx pc=%08lx %s", (unsigned long)asp, (unsigned long)apc,
             app_ok ? "ok" : "BAD");
    api->say(line);

    for (unsigned k = 0; k < sizeof lits / sizeof lits[0]; k++) {
        uint32_t n = 0, first = 0xFFFFFFFFu;
        for (uint32_t a = SB_BOOT_BASE; a < SB_BOOT_END; a += 4) {
            if (SB_REG(a) == lits[k]) {
                if (!n)
                    first = a - SB_BOOT_BASE;
                n++;
            }
        }
        api->fmt(line, sizeof line, "lit %08lx n=%lu first=%05lx", (unsigned long)lits[k],
                 (unsigned long)n, (unsigned long)first);
        api->say(line);
        if (api->stop())
            return 0x5c;
    }

    int ready = boot_ok && app_ok && wr && kb == 1024u && SB_REG(SB_VTOR) == SB_APP_BASE;
    api->fmt(line, sizeof line, "bootinfo ready=%s", ready ? "yes" : "no");
    api->say(line);
    return ready ? 0x5a : 0x50;
}
