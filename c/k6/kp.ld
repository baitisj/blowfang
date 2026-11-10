/* A code page (kp.h), linked by tools/k6_pages.py at KP_ORG (the image's decoder pool + the
   module header) with the image's symbols (--just-symbols): the table first, then the code, its
   constants and its initialised data. A page keeps nothing of its own in RAM: no .bss.
   SPDX-License-Identifier: MIT */
SECTIONS
{
  .page KP_ORG : {
    KEEP(*(.kp_tab))
    *(.text .text.*)
    *(.rodata .rodata.*)
    *(.data .data.*)
    . = ALIGN(4);
  }
  .bss (NOLOAD) : { *(.bss .bss.* COMMON) }
  /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) }
}
ASSERT(SIZEOF(.bss) == 0, "a code page keeps no RAM of its own (no .bss)")
